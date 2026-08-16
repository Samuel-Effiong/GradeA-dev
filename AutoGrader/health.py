"""
Liveness/readiness endpoint.

Deliberately unauthenticated and cheap: it is meant to be polled by a load
balancer, uptime monitor, or deploy script, none of which hold a token. It
touches each backing service with the smallest possible operation so that a
failing dependency shows up as a failing check rather than as a slow one.

Returns 200 only when every dependency answers; otherwise 503 with a
per-service breakdown, so an alert says *which* thing is down.
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
