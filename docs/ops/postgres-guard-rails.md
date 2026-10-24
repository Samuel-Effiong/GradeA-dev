# Postgres guard rails (lock_timeout, idle_in_transaction_session_timeout)

**Status:** applied by hand against the production database. Not in code, and
deliberately so — see "Why not in settings.py" below.

## What to run

Connect to Postgres **directly**, not through pgbouncer. The pooler listens on
`6432`; Postgres itself listens on `5432`. Use the app's database user (the
role in `DATABASE_URI`), substituted for `<app_user>`:

```sql
ALTER ROLE <app_user> SET lock_timeout = '10s';
ALTER ROLE <app_user> SET idle_in_transaction_session_timeout = '60s';
```

These take effect on **new** sessions. Existing pooled server connections keep
the old values until pgbouncer recycles them, so either wait, or force it:

```sh
# from a psql session on the pgbouncer admin console
RECONNECT;
```

## How to verify

From the application's own connection — i.e. through pgbouncer on `6432`, not
directly — so you are checking what the app actually gets:

```sql
SHOW lock_timeout;                          -- expect 10s
SHOW idle_in_transaction_session_timeout;   -- expect 1min
```

`python manage.py dbshell` uses `DATABASE_URI`, so it goes through the pooler
and is a valid way to check this.

## What each one does

**`lock_timeout = 10s`** — a statement waits at most 10 seconds to acquire a
lock, then fails. This is the one that earns its keep. Without it, a
migration's `ALTER TABLE` waiting on an `ACCESS EXCLUSIVE` lock will queue
every subsequent query against that table behind itself, turning one slow
statement into a site-wide stall. 10s is deliberately generous so ordinary row
contention (e.g. the `select_for_update` in `students/task_tracking.py`) never
trips it.

**`idle_in_transaction_session_timeout = 60s`** — kills a session that opened a
transaction and then went idle, since an abandoned transaction holds its locks
indefinitely and feeds the same pile-up. Safe at 60s because this codebase
deliberately keeps slow external work (AI calls) outside transactions.

Scoped to the app role rather than set with `ALTER DATABASE` on purpose: a 10s
`lock_timeout` should not kill an intentional maintenance session, and a 60s
idle timeout should not drop a `psql` window during an incident.

## Why not in settings.py

They were, briefly, as psycopg2's `options` connection parameter. It took
production down completely:

```
OperationalError: connection to server at "pgbouncer", port 6432 failed:
FATAL:  unsupported startup parameter in options:
        idle_in_transaction_session_timeout
```

psycopg2 sends `options` in the PostgreSQL **startup packet**. pgbouncer
refuses any startup parameter outside its allow-list (`client_encoding`,
`datestyle`, `timezone`, `standard_conforming_strings`, `application_name`,
plus `track_extra_parameters`), because a pooled server connection is reused
across clients and a session setting from one client would leak into the next.
The rejection happens at connect time, so it took down web and Celery
together. Local and dev connect straight to Postgres, so nothing failed there.

Role defaults sidestep this entirely. pgbouncer's `server_reset_query` runs
`DISCARD ALL` between clients, which performs a `RESET ALL`, which restores
every parameter to its **session-start default** — and role defaults *are* the
session-start defaults. So the values re-establish themselves after every
reset, in every pooling mode.

Two alternatives were considered and rejected:

- **A `connection_created` signal issuing `SET`.** Keeps it in code, but under
  transaction pooling the `SET` is scoped to that transaction and silently
  stops applying. Configured-looking but inert.
- **pgbouncer `track_extra_parameters`.** Works (pgbouncer ≥ 1.21), but the
  config lives on a service outside this repo, so it drifts out of sight and
  only production has it.

**The rule:** connection-level Postgres configuration is infrastructure, not
application config. It belongs on the role or the database, never in
`DATABASES["default"]["OPTIONS"]`.

## Related pooler facts worth knowing

Production pgbouncer runs in **transaction pooling mode**. Consequences already
handled in `AutoGrader/settings.py`:

- `disable_server_side_cursors = True` is required, not optional — a
  server-side cursor does not survive the connection being handed to another
  client at commit.
- `psycopg2` does not use server-side prepared statements, so the other classic
  transaction-mode landmine does not apply. **This changes if the project ever
  moves to psycopg3**, which prepares by default; it would need
  `prepare_threshold=None`.
- `LISTEN`/`NOTIFY` and session-level advisory locks do not work through the
  pooler. Nothing uses them today.

**Client connection budget.** Django connections are thread-local and
`conn_max_age = 600` holds them open. The web container runs
`gunicorn --workers 9 --threads 4`, so one web instance can hold up to **36**
client connections against pgbouncer, before counting Celery workers and beat.
pgbouncer's `max_client_conn` defaults to 100. One instance is comfortable; a
second instance plus Celery is not. If the web service is ever scaled out,
raise `max_client_conn` first — the failure mode is pgbouncer refusing
connections under load, which presents as an unrelated-looking outage.
