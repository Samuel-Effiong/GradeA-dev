#!/usr/bin/env sh
# Railway "beat" service Custom Start Command should be exactly:
#   ./scripts/start-beat.sh
#
# WARNING: this service must run at exactly 1 replica, always. Beat has no
# leader election — a second instance double-fires every job in
# AutoGrader/settings.py CELERY_BEAT_SCHEDULE, including the billing
# reconciliation and credit-expiry tasks. Railway will not stop you from
# scaling this service; nothing in code stops it either. Check the replica
# count in the Railway dashboard whenever this service is touched. See
# docs/ops/railway-services.md.
set -eu

exec celery -A AutoGrader beat \
    --loglevel="${CELERY_BEAT_LOGLEVEL:-info}" \
    --scheduler django_celery_beat.schedulers:DatabaseScheduler
