#!/usr/bin/env sh
# A prefork worker (4 slots by default) for local runs or a rebuilt service.
# Railway's "worker" service does NOT use this script: it runs its own gevent
# command (200 slots), recorded in docs/ops/railway-services.md. Do not switch
# it to this script.
set -eu

# No queue option: the worker consumes every queue declared in
# CELERY_TASK_QUEUES (AutoGrader/settings.py), as Railway's command does.
exec celery -A AutoGrader worker \
    --concurrency="${CELERY_WORKER_CONCURRENCY:-4}" \
    --loglevel="${CELERY_WORKER_LOGLEVEL:-info}"
