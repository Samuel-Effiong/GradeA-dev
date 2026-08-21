#!/usr/bin/env sh
# Railway "worker" service Custom Start Command should be exactly:
#   ./scripts/start-worker.sh
# See docs/ops/railway-services.md for why this lives in git instead of
# only in the Railway dashboard.
set -eu

exec celery -A AutoGrader worker \
    --concurrency="${CELERY_WORKER_CONCURRENCY:-4}" \
    --loglevel="${CELERY_WORKER_LOGLEVEL:-info}"
