"""
Liveness/readiness endpoints.

`health` is deliberately unauthenticated and cheap: it is meant to be
polled by a load balancer, uptime monitor, or deploy script, none of which
hold a token. It touches each backing service with the smallest possible
operation so that a failing dependency shows up as a failing check rather
than as a slow one. Returns 200 only when every dependency answers;
otherwise 503 with a per-service breakdown, so an alert says *which* thing
is down.

`beat_health_check` is a second, separate endpoint rather than one more
check folded into `health`: Railway's per-service Healthcheck Path gates
that service's own deploy cutover (see docs/ops - Railway does not poll
it continuously once live). `health` backs the *web* service's deploy
gate, so it must only ever reflect whether the web service itself can
serve traffic. Folding in "is Celery Beat alive" would mean a Beat outage
- a completely different service - blocks unrelated web deploys from
succeeding. This endpoint exists for an external uptime monitor (per
Railway's own docs, e.g. their Uptime Kuma template) to poll on its own,
independent of any deploy.
"""

from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def _check_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_cache():
    # Round-trip rather than a bare set(): a write that silently no-ops
    # (misconfigured backend, evicted immediately) would otherwise pass.
    cache.set("healthcheck", "ok", 10)
    if cache.get("healthcheck") != "ok":
        raise RuntimeError("cache did not return the value just written")


def _run(name, check, results):
    """Run one check, recording either "ok" or the failure reason."""
    try:
        check()
        results[name] = "ok"
        return True
    except Exception as exc:  # noqa: BLE001 - report any failure, don't crash
        results[name] = f"error: {exc}"
        return False


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
# Exempt from throttling: a health check that starts 429-ing under load
# reads as an outage to the monitor and can take a healthy node out of
# rotation.
@throttle_classes([])
def health(request):
    results = {}

    # Called through module globals (rather than a lookup table built at
    # import time) so each check resolves at request time and stays
    # straightforward to substitute in tests.
    healthy = all(
        [
            _run("database", _check_database, results),
            _run("cache", _check_cache, results),
        ]
    )

    return Response(
        {"status": "ok" if healthy else "degraded", "checks": results},
        status=(status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE),
    )


# The watchdog itself runs every 15 minutes (see
# AutoGrader.settings.CELERY_BEAT_SCHEDULE's "check-beat-health" entry).
# Comfortably larger than that so a routine few-minutes scheduling delay
# doesn't false-alarm - same "expected interval vs. alert threshold"
# philosophy as BEAT_HEALTH_EXPECTATIONS, just for the watchdog's own
# schedule since it can't watch itself.
BEAT_WATCHDOG_MAX_GAP_MINUTES = 45


def _check_beat_watchdog():
    from django.utils import timezone
    from django_celery_beat.models import PeriodicTask

    watchdog = PeriodicTask.objects.filter(name="check-beat-health").first()
    if watchdog is None:
        raise RuntimeError("check-beat-health is not registered in Beat's schedule")

    if not watchdog.enabled:
        raise RuntimeError("check-beat-health is disabled")

    reference_time = watchdog.last_run_at or watchdog.date_changed
    gap_minutes = (timezone.now() - reference_time).total_seconds() / 60

    if gap_minutes > BEAT_WATCHDOG_MAX_GAP_MINUTES:
        raise RuntimeError(
            f"check-beat-health last ran {gap_minutes:.0f} minutes ago "
            f"(expected every 15); Beat may be down or duplicated"
        )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def beat_health_check(request):
    results = {}
    healthy = _run("beat", _check_beat_watchdog, results)

    return Response(
        {"status": "ok" if healthy else "degraded", "checks": results},
        status=(status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE),
    )
