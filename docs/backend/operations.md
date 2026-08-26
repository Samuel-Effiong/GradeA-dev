# Operations — deploy, CI, migrations, logging, monitoring

> Part of the [backend reference](README.md). Related: [project-config.md](project-config.md), [async-and-infrastructure.md](async-and-infrastructure.md), [pdf-pipeline.md](pdf-pipeline.md).

## In plain terms

The app runs on Railway as **three services built from one image**: the web server, the background workers, and a scheduler that must never run more than one copy of itself. Before code merges, three GitHub checks run — the tests, the formatting/security hooks, and a guard that refuses a database change which could break a worker still running the previous release. Two settings in two different files must always hold the same number, and a script fails the build if they drift. This document is the reference for all of that, plus what the logs will and won't tell you when something goes wrong.

---

## Deployment topology

Three Railway services, **all built from the same [Dockerfile](../../Dockerfile) and image** ([docs/ops/railway-services.md](../ops/railway-services.md)). They differ only in their Custom Start Command:

| Service | Start command | Replicas |
|---|---|---|
| **web** | *(none — uses the Dockerfile `CMD`)* | scale as needed |
| **worker** | `./scripts/start-worker.sh` | scale as needed |
| **beat** | `./scripts/start-beat.sh` | **1, always** |

The start commands live in `scripts/` rather than only in the Railway dashboard *"so that recreating a deleted service — or auditing what's actually running — doesn't depend on someone remembering the right `celery -A AutoGrader ...` flags."*

### The web service

```
gunicorn AutoGrader.wsgi:application --bind 0.0.0.0:${PORT}
  --timeout 100 --workers 9 --threads 4 --worker-class gthread
  --max-requests 1000 --max-requests-jitter 200 --keep-alive 5
```
([Dockerfile:86](../../Dockerfile#L86))

**36 concurrent request slots** (9 × 4). `--max-requests 1000` with jitter recycles workers, which is also what bounds Chromium memory growth in practice.

> `--timeout 100` is mirrored by `WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS`. The Dockerfile comment says why: *"If you raise the timeout here, raise that constant too, or a slow-but-alive request can have its claim stolen and run concurrently with the thief (**which can duplicate non-refundable Stripe side effects**)."*

`CMD` also does **not** run migrations, despite the boilerplate comment above it — see [Migrations](#migrations).

### The worker service

```sh
celery -A AutoGrader worker \
  --concurrency="${CELERY_WORKER_CONCURRENCY:-4}" \
  --loglevel="${CELERY_WORKER_LOGLEVEL:-info}"
```

**One queue** — no routing is configured, so a long grading run and a one-second email share the same worker pool. With `CELERY_WORKER_PREFETCH_MULTIPLIER = 1` and the default prefork pool, each worker holds one task at a time.

> **UNVERIFIED:** no task routing or queue separation exists. Whether that is deliberate (simplicity) or unaddressed would need a team answer. The practical consequence: a burst of 25-minute grading tasks can starve notification emails on the same worker.

### The Beat service — exactly one replica

`scripts/start-beat.sh` carries the warning in the file itself:

> *"**WARNING: this service must run at exactly 1 replica, always.** Beat has no leader election — a second instance double-fires every job in `CELERY_BEAT_SCHEDULE`, **including the billing reconciliation and credit-expiry tasks**. Railway will not stop you from scaling this service; **nothing in code stops it either**. Check the replica count in the Railway dashboard whenever this service is touched."*

Neither health layer detects a duplicated Beat ([async-and-infrastructure.md](async-and-infrastructure.md#beat-health)) — both Beats update `last_run_at`, so the gap check looks healthy while every task fires twice. **Detect it by duplicate side effects, or by looking at the dashboard.**

### The image

Python 3.12 slim (Debian bookworm), runs as the non-root `wagtail` user.

| System package | Needed by |
|---|---|
| `poppler-utils` | `pdf2image` / `convert_from_path` |
| `libjpeg62-turbo-dev`, `zlib1g-dev`, `libwebp-dev` | Pillow |
| `libgl1`, `libglib2.0-0` | image libraries |
| `libpango-1.0-0`, `libpangoft2-1.0-0` | **WeasyPrint leftovers** — the PDF renderer no longer uses it ([pdf-pipeline.md](pdf-pipeline.md#why-chromium)) |
| `libpq-dev` | psycopg2 |
| `libmariadb-dev` | unused — nothing connects to MySQL |

Chromium is installed with `playwright install --with-deps chromium` then `chmod -R o+rX`, and the comment explains both halves: `--with-deps` *"auto-detects this image's OS and installs exactly the apt packages Chromium needs, rather than hand-maintaining a fragile list"*; `PLAYWRIGHT_BROWSERS_PATH` points at a fixed, non-`/tmp` location *"so the browser persists in the image and stays readable by the non-root 'wagtail' user."*

`HOME` and `TMPDIR` are both `/tmp`, which is what makes the PDF rasterisation temp directories work for a non-root user.

---

## CI

Three GitHub Actions workflows, all on `pull_request` and `push` to `main`, `beta`, `dev`.

| Workflow | Runs |
|---|---|
| `tests.yml` | `makemigrations --check --dry-run`, then `coverage run manage.py test`, then `coverage report -m` |
| `pre-commit.yml` | every pre-commit hook |
| `migration-safety.yml` | `scripts/check_migration_safety.py` (**pull_request only** — it needs a base ref to diff against) |

`tests.yml`'s comment records the motivation: *"Before this existed, the project's ~1,500 tests only"* ran locally.

Both workflows supply placeholder env vars, because `settings.py` reads a dozen with no default at import time. The placeholders carry `# pragma: allowlist secret` so `detect-secrets` does not flag them.

> **Contradiction to flag:** `ai_processor/tasks.py:23-25` states *"There is no CI in this repo, so Celery Beat is the only scheduler available"*, and `extraction_benchmark.py` describes `--mode replay` as *"what CI runs"*. **CI does exist** (three workflows). The nightly benchmark replay could move there, as its own comment suggests it should: *"If CI is ever added, the nightly replay belongs there instead — it needs no credentials and no database of its own."*

### Pre-commit hooks

| Category | Hooks |
|---|---|
| Hygiene | `check-yaml`/`toml`/`json`/`xml`, `end-of-file-fixer`, `trailing-whitespace`, `check-ast`, `check-docstring-first`, `check-added-large-files`, `check-merge-conflict`, `check-case-conflict`, `check-builtin-literals`, `fix-byte-order-marker`, `check-symlinks`, `requirements-txt-fixer` |
| Correctness | `debug-statements`, `name-tests-test --django` |
| Format | `black --line-length=88`, `isort --profile=black` |
| Types | `mypy --ignore-missing-imports --check-untyped-defs --disable-error-code=var-annotated` |
| Lint | `flake8` max-line 120, plus bugbear, comprehensions, docstrings, import-order, print, rst-docstrings, **eradicate** (commented-out code) |
| Security | `detect-private-key`, `bandit`, `detect-secrets --baseline .secrets.baseline` |
| **Local** | `check-gunicorn-timeout-sync` |

`name-tests-test` needs `--django` because *"This repo names tests `test_*.py` / `tests_*.py` (Django's convention, 48 files). The hook's default expects the opposite (`*_test.py`), so without `--django` it rejects every test file."*

flake8 **excludes migrations** and ignores `W503`, `E800`, `E203` *"Align with Black formatting"`. Note `E800` is eradicate's commented-out-code check — ignored globally, which is why several large commented-out blocks survive in the codebase.

A `run-tests` pre-commit hook exists but is **commented out**.

### The gunicorn-timeout guard

`scripts/check_gunicorn_timeout_sync.py` compares two integers:

```
Dockerfile:  gunicorn ... --timeout 100
webhooks.py: WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS = 100
```

It runs only when `Dockerfile` or `billing/webhooks.py` changes, and is *"Plain text parsing on purpose, not a Django import: this only needs to compare two integers, so it runs with no dependencies and no settings/env vars required."*

Its failure message names the fix directly. **This is the only cross-file constant coupling in the codebase that is machine-enforced**; the others (`ANSWERS_EXTRACTION_PAGES_PER_CHUNK` → `MAX_PAGE_COUNT` → `time_limit` → `visibility_timeout`, and `GRADING_TASK_TIME_LIMIT_SECONDS` → `GRADING_CLAIM_STALE_AFTER`) rely on comments at both ends.

---

## Migrations

The house rule is in [docs/MIGRATIONS.md](../MIGRATIONS.md), and it exists because of *"one incident-shaped question: **what happens if a migration fails halfway through, or a worker running old code hits a column a migration just removed?** Right now the answer is 'we find out live on Railway.'"*

| | Practice |
|---|---|
| **Old way — stop** | `ENVIRONMENT=prod` in a local shell, `manage.py migrate` by hand against the live Railway database |
| **New way** | Railway's **Pre-Deploy Command**, set per service |

The old way *"has no coupling to the deploy itself. A migration can run while the previous release's workers are still serving traffic against the new schema, or a deploy can go out before anyone remembers to migrate at all. There's also **no audit trail**."*

### The additive-only rule

`scripts/check_migration_safety.py` fails a PR whose new migration is not safely additive **unless** the file carries:

```python
# expand-contract-step: expand | migrate | contract
```

| Operation | Why it is refused |
|---|---|
| `RemoveField` | *"drops a column — a worker still running the previous release will error the moment it reads or writes this field"* |
| `RenameField` | *"old code referencing the previous name breaks immediately, and Django implements this as a rename at the DB level, not a copy, so it isn't reversible by re-adding a column"* |
| `RenameModel` | old code/queries break immediately |
| `DeleteModel` | *"irreversible without a backup restore"* |
| `AlterUniqueTogether` | *"rebuilding this constraint can hold a lock for the duration on larger tables"* |
| `AlterIndexTogether` | same |
| `AddField` that is neither nullable nor defaulted | *"safe on an existing, populated table only if every existing row can get a value **without a human picking one at migration time**"* |

Exit codes: `0` clean, `1` unacknowledged risk, **`2` a migration could not be inspected** — *"treated as a failure rather than silently skipped."*

The check `importlib`-imports each new migration and inspects `Migration.operations`, so it sees what Django will actually do rather than pattern-matching the source.

**179 migrations exist** across nine apps (billing 58, assignments 38, users 35, students 25, classrooms 16, ai_processor 5, dashboard 2; `grading` and `ocr_processor` have none).

### Index creation

A related discipline appears at the model layer. `Assignment.rigor_*` columns are **deliberately unindexed**, and the reason is deploy safety as much as query planning: *"its non-concurrent `CREATE INDEX` would hold an **exclusive write lock over the whole table at deploy time**"* ([assignments/models.py:73-79](../../assignments/models.py#L73-L79)).

---

## Postgres guard rails

`lock_timeout` and `idle_in_transaction_session_timeout` are **not in `settings.py`**, and [docs/ops/postgres-guard-rails.md](../ops/postgres-guard-rails.md) plus a long comment at [settings.py:415-437](../../AutoGrader/settings.py#L415-L437) explain why.

Setting them as psycopg2's `options` connection parameter made **every connection attempt die at connect time**:

```
FATAL: unsupported startup parameter in options: idle_in_transaction_session_timeout
```

*"taking down web and Celery alike. **Local and dev talk to Postgres directly, so the breakage was invisible outside production.**"*

The cause: production connects through **pgbouncer in transaction pooling mode**, and *"a pooler exists precisely to hand one server connection to many clients in turn. Anything session-scoped that the app sets would therefore leak into some later request, so pgbouncer refuses to pass startup parameters it does not track."*

The rule left behind:

> *"connection-level Postgres configuration is **infrastructure, not application config**. It belongs on the role (or the database), never in `DATABASES["default"]["OPTIONS"]`. Role defaults survive the pooler because pgbouncer's `server_reset_query` issues `DISCARD ALL`, whose `RESET ALL` restores each parameter to its session-start default — which is exactly what `ALTER ROLE` sets."*

```sql
-- connect DIRECTLY (port 5432), not through pgbouncer (6432)
ALTER ROLE <app_user> SET lock_timeout = '10s';
ALTER ROLE <app_user> SET idle_in_transaction_session_timeout = '60s';
```

These take effect on **new** sessions; existing pooled connections keep the old values until pgbouncer recycles them (`RECONNECT;` from the pgbouncer admin console forces it).

### Other pooler consequences

| Setting | Value | Reason |
|---|---|---|
| `disable_server_side_cursors` | `True` | *"a server-side cursor outlives the transaction that declared it, but the pooler hands that server connection to another client at commit, so the cursor is gone by the time `.iterator()` fetches the next chunk."* **Must** be in the per-database dict — the old module-level `DISABLE_SERVER_SIDE_CURSORS` *"silently did nothing"* |
| `conn_max_age` | `600` | at 0, every request paid TCP+TLS+auth before its first query |
| `conn_health_checks` | `True` | a connection dropped by the pooler becomes a retry, not a 500 |

`DATABASE_URI_ENV_VAR` keeps one construction path across environments *"means a fix like the OPTIONS placement below cannot be applied to some environments and missed in others — **which is exactly how production ended up as the only environment carrying a broken OPTIONS key**"* ([settings.py:400-407](../../AutoGrader/settings.py#L400-L407)).

---

## Logging

`LOGGING` ([settings.py:32-94](../../AutoGrader/settings.py#L32-L94)) has one handler — `console` at INFO, with the `verbose` formatter and the `request_id` filter.

```
{name} {levelname} {asctime} {module} {process:d} {thread:d} [request_id={...}] {message}
```

`RequestIDLogFilter` runs on **every** record regardless of which logger emitted it, so third-party libraries get the correlation id too ([AutoGrader/request_context.py:84-96](../../AutoGrader/request_context.py#L84-L96)).

| Logger | Level | Propagate |
|---|---|---|
| `django` | `ERROR` | — |
| `ERROR_REPORT` | `ERROR` | — |
| `ai_processor` | `GRADING_LOG_LEVEL` (INFO) | **False** |
| `students` | `GRADING_LOG_LEVEL` (INFO) | **False** |

`ai_processor` and `students` are configured explicitly because *"there is no 'root' logger entry here: without this, every logger in `ai_processor/` (and `students/`) inherits nothing from Django. Until this was added, that handler came from a **stray `logging.basicConfig()` at import time in `ai_processor/validators.py`** — which also meant the whole pipeline logged under the name `ai_processor.validators`, making it impossible to tell which stage produced a line."*

> **A gap worth knowing:** `billing`, `assignments`, `classrooms`, `dashboard`, and `users` have **no explicit logger entry and no root logger**, so they fall through to Python's `lastResort` handler (WARNING to stderr, no formatter, **no request id**). A `logger.info(...)` in `billing/tasks.py` is silently dropped; a `logger.error(...)` appears without a correlation id.

### What is logged at ERROR

The house style is that ERROR means *"a human needs to look at this"*. The ones that page:

| Message | Source |
|---|---|
| *"N Stripe webhook event(s) FAILED and are past Stripe's ~3 day retry window… a customer may have paid without receiving anything"* | `sweep_stale_stripe_events` |
| *"Beat schedule drift detected: …"* naming each overdue task | `check_beat_health` |
| *"Stripe live QA scenario X FAILED against real Stripe… mocked tests CANNOT catch this"* | `nightly_stripe_live_qa` |
| *"Grading benchmark REGRESSED against baseline"* | the benchmark tasks |
| *"License N renewal failed for all M teachers. Deactivating license."* | `process_license_renewal` |
| *"Failed to refund credits for billing task X. **Credits remain consumed — manual reconciliation required.**"* | `_refund_all` |

**Every one of them names the repair command or the next step.** That is the convention to follow when adding a new one.

Notable WARNINGs that are signals rather than problems: *"stealing an abandoned PROCESSING claim"* (if frequent, the staleness window is too tight), *"rubric_snap count=N"* (the model is ignoring the discrete-scores rule), *"objective_deferred count=N"* (answer keys may be malformed), *"grader_disagreement count=N"*, and *"Second opinion skipped: no candidate model differs"* (the review queue's safety net is dark).

### Sentry

Enabled only when `SENTRY_DSN` is set **and** `ENVIRONMENT` is `prod` or `dev` ([settings.py:146](../../AutoGrader/settings.py#L146)).

| Setting | Value | Reason |
|---|---|---|
| Integrations | Django, Celery, Logging | |
| Logging integration | breadcrumbs from INFO, events from **ERROR** | *"so a report arrives with the log lines that led up to it"* |
| `traces_sample_rate` | `0.05` | *"this project's grading requests are long-running, and full tracing on every one would be costly without telling us much. Errors are always captured."* |
| `send_default_pii` | **`False`** | *"These carry student work, grades, and billing identifiers."* |
| `profile_session_sample_rate` | `1.0` | |

Both the import and the middleware's `set_tag` are guarded against `ImportError`, so *"a deployment which has not installed the package yet degrades to 'no error reporting' rather than failing to start."*

**Sentry is the only thing watching `logger.error`.** Without a DSN, an ERROR goes to stdout and, under systemd or a container, *"is written to a file nobody is watching. A production failure was, in practice, invisible"* ([settings.py:133-143](../../AutoGrader/settings.py#L133-L143)).

---

## Monitoring

| Endpoint | Checks | Poll from |
|---|---|---|
| `GET /api/v1/health` | database `SELECT 1`, cache write-then-read | **Railway's per-service healthcheck** (gates deploy cutover) |
| `GET /api/v1/health/beat` | `check-beat-health`'s `last_run_at` gap | **an external uptime monitor** |

They are separate on purpose: *"Railway's per-service Healthcheck Path gates that service's own deploy cutover. `health` backs the web service's deploy gate, so it must only ever reflect whether the web service itself can serve traffic. Folding in 'is Celery Beat alive' would mean a Beat outage — a completely different service — **blocks unrelated web deploys from succeeding**"* ([AutoGrader/health.py:12-21](../../AutoGrader/health.py#L12-L21)).

Both are `AllowAny`, no auth classes, and **throttle-exempt** — *"a health check that starts 429-ing under load reads as an outage to the monitor and can take a healthy node out of rotation."*

`_check_cache` writes **and reads back**, because *"a write that silently no-ops would otherwise pass."*

`RAILWAY_PUBLIC_DOMAIN` and `RAILWAY_PRIVATE_DOMAIN` are appended to `ALLOWED_HOSTS` *"so a new service doesn't need a code change to pass its own healthcheck"* — Railway's healthcheck hits the private domain.

### Coverage

`.coveragerc` and a committed `.coverage` file exist; CI runs `coverage report -m` after the tests. **No minimum threshold is enforced.**

---

## Runbook

### An ERROR fired — what to do

| Message contains | Action |
|---|---|
| *"past Stripe's ~3 day retry window"* | Billing → Stripe events → `status=FAILED` in the admin. **Inspect the event in Stripe first** (did a refund already issue?), then `manage.py replay_stripe_events --event-id evt_… --apply` |
| *"Beat schedule drift"* | Check the beat service is running and at **exactly 1** replica |
| *"live QA scenario X FAILED"* | `manage.py run_stripe_live_qa --scenario X` — **Stripe's behaviour has changed**, not ours |
| *"benchmark REGRESSED"* | `manage.py grading_benchmark --mode replay --baseline …` — **our grading code changed** |
| *"renewal failed for all N teachers"* | The licence is **deactivated**. Fix the cause, reactivate, re-run |
| *"manual reconciliation required"* | Find the `task_id` in `CreditUsageLog` and re-run `refund_credits` |

### Something is stuck

| Symptom | Likely cause | Fix |
|---|---|---|
| Submission un-gradable, `grading_state = RUNNING` | worker killed holding the claim | wait **30 min** for the claim to go stale — there is **no manual release endpoint** |
| Tracked task stuck `PENDING` > 1h | the Celery result expired before reconciliation | update the row by hand |
| Grades not updating on a dashboard | wildcard cache not swept, or `.update()` without a manual purge | wait `CACHE_TTL`, or touch a row |
| PDFs 503-ing | renderer shedding load | raise `PDF_RENDERER_MAX_QUEUED_RENDERS`/`_CONCURRENT_RENDERS`, or add web replicas |
| Concurrency figure climbing forever | `record_concurrent_users` stopped | check Beat |
| Emails not arriving | broker was down at `safe_delay` time | **they are lost, not queued** — re-trigger |

### Read-only audits worth running periodically

Neither is scheduled — running them is currently a manual act.

```bash
manage.py audit_email_track_separation --strict   # accounts on the wrong track, or both
manage.py audit_school_admins --strict            # licences administered by a superadmin
manage.py backfill_assignment_rigor --dry-run     # rigor columns drifted?
manage.py grading_benchmark_history --trends      # is grading quality moving?
manage.py grading_eval --days 90                  # is the second opinion earning its cost?
```

### Data repair commands

All `--dry-run`-first, idempotent, and written with `bulk_update` so the `post_save` cascade does not fire for a silent repair:

```bash
manage.py repair_question_blooms_levels --dry-run
manage.py strip_duplicate_option_letters --dry-run
manage.py strip_html_from_assignment_titles --dry-run
manage.py backfill_assignment_rigor --dry-run
```

---

## Configuration

### Runtime env vars

| Var | Default | Effect |
|---|---|---|
| `PORT` | 8000 | gunicorn bind |
| `CELERY_WORKER_CONCURRENCY` | 4 | worker processes per replica |
| `CELERY_WORKER_LOGLEVEL` | `info` | |
| `CELERY_BEAT_LOGLEVEL` | `info` | |
| `GRADING_LOG_LEVEL` | `INFO` | the `ai_processor` and `students` loggers |
| `SENTRY_DSN` | `""` | error reporting |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.05` | |
| `SECURE_SSL_REDIRECT` | `False` | opt-in |
| `RAILWAY_PUBLIC_DOMAIN` / `_PRIVATE_DOMAIN` | injected | appended to `ALLOWED_HOSTS` |
| `PLAYWRIGHT_BROWSERS_PATH` | set in the Dockerfile | |

### Deploy checklist

1. Every required env var is set for the target `ENVIRONMENT` ([project-config.md](project-config.md#configuration)).
2. `ENVIRONMENT` is spelled correctly — **a typo reaches production Redis and Postgres**.
3. Beat is at **exactly 1** replica.
4. Migrations run via the **Pre-Deploy Command**, not by hand.
5. Any non-additive migration carries `# expand-contract-step:`.
6. If gunicorn's `--timeout` changed, `WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS` changed with it (CI enforces this).
7. Postgres role guard rails are applied (`ALTER ROLE`, direct connection, not through pgbouncer).
8. The Stripe webhook URLs are registered with a signing secret matching `STRIPE_WEBHOOK_SECRET`.
9. QA flags (`ENABLE_STRIPE_LIVE_QA`, `ENABLE_BILLING_TIME_TRAVEL`, `ENABLE_AI_LIVE_QA`, `BILLING_TEST_CLOCK_EMAIL_DOMAINS`, `EXEMPT_EMAIL_DOMAINS`) are **off/empty in production**.
10. Memory budget accounts for Chromium: ~9 × (165 MB + 4 × 20 MB) ≈ **2.2 GB** on the web service at saturation, plus one browser per worker replica that pre-renders PDFs.

### Repo hygiene notes

The repository root contains a number of stray files that are not part of the application: `.DS_Store`, `elf):`, `requieremnt update`, `assignent.html`, three `google*auth*test.html` files, `celerybeat-schedule.{bak,dat,dir}` (a leftover file-based Beat schedule — the app uses `DatabaseScheduler`), a committed `.coverage`, and `benchmark_artifacts/` (referenced by code comments, so intentional). Twelve loose `*.md` files at the root predate `docs/` — see the [README](README.md#relationship-to-the-root-level-docs).
