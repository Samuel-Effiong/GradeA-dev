# Railway services

This repo backs three Railway services, all built from the same
[Dockerfile](../../Dockerfile) and image. They differ only in their Custom
Start Command (Railway → service → Settings → Deploy → Custom Start
Command), which overrides the Dockerfile's `CMD` at runtime.

| Service | Custom Start Command       | Replicas |
|---------|-----------------------------|----------|
| web     | *(none — uses Dockerfile `CMD`)* | scale as needed |
| worker  | `celery -A AutoGrader worker -l info --pool=gevent --concurrency=200 --prefetch-multiplier=1` | scale as needed |
| beat    | `./scripts/start-beat.sh`   | **1, always** |

The worker/beat commands live in [scripts/](../../scripts/) instead of only
in the Railway dashboard so that recreating a deleted service — or auditing
what's actually running — doesn't depend on someone remembering the right
`celery -A AutoGrader ...` flags. If a service is ever rebuilt from scratch,
its Custom Start Command is the one line above; the actual flags are in git
history like everything else.

## Worker: the actual start command, and its queues

The worker service does **not** run `./scripts/start-worker.sh`. Its Custom
Start Command (owner, 2026-11-23) is:

    celery -A AutoGrader worker -l info --pool=gevent --concurrency=200 --prefetch-multiplier=1

Do not switch it to `start-worker.sh`: that script runs the default prefork
pool at 4 slots (`CELERY_WORKER_CONCURRENCY`), far below 200 gevent slots.
The script stays for local runs and for a service rebuilt from scratch.

Neither command passes `-Q`, and neither needs to. The queues are declared in
`CELERY_TASK_QUEUES` (`AutoGrader/settings.py`): `celery` (the default) and
`quote_measure` (the upload quote measure, `students.tasks.measure_quote_file`).
A worker started without `-Q` consumes every declared queue, so this command
covers both. `AutoGrader/tests_worker_queues.py` builds a worker from this
exact command and checks it. A new queue must be added to `CELERY_TASK_QUEUES`,
or no worker consumes it.

The measure runs at most 2 at once. That limit is the Redis counter
`UPLOAD_QUOTE_MEASURE_CONCURRENCY` (`students/upload_quote_measure.py`),
shared by every worker process, and does not depend on the pool type or the
concurrency.

## Beat must stay at exactly 1 replica

`CELERY_BEAT_SCHEDULER` is `django_celery_beat.schedulers:DatabaseScheduler`
(`AutoGrader/settings.py`), which has no leader election. A second Beat
instance does not fail or coordinate with the first — it independently
fires every entry in `CELERY_BEAT_SCHEDULE` (`AutoGrader/settings.py`,
~line 563) on its own schedule, duplicating each job. That list includes:

- `process-license-renewals`, `reconcile-subscriptions-daily`,
  `process-annual_plan-credit-grants` — billing side effects against Stripe.
- `cleanup-expired-credit-buckets`, `expire-active-trials` — irreversible
  state transitions on user wallets/trials.
- `sweep-stale-stripe-events` — reprocessing webhook claims.

Railway does not prevent scaling the beat service's replica count above 1.
**Whenever the beat service is touched — redeployed, its plan changed, its
start command edited — check its replica count in the Railway dashboard is
still 1** before moving on. There is no code-level guard against this; it
is an operational invariant, not a technical one.
