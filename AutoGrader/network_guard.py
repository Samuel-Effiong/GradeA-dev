"""Block real outbound network calls during tests (H-39); test-only, never
imported by production code.

WHY
---
A test that forgets to mock a third-party call (Stripe, an AI provider, ...)
can silently succeed locally against real credentials and only fail later,
on CI, against fake ones. That exact gap cost a two-CI-run diagnosis:
`test_stripe_timeout_on_the_allowed_checkout_leaves_no_local_change` mocked
`stripe.checkout.Session.create` but not `stripe.Customer.create` — the real
call silently succeeded locally (`.env`'s `LOCAL_STRIPE_SECRET_KEY` is a real
Stripe test-mode key) and deterministically failed on CI (a fake placeholder
key, rejected by Stripe's own auth check). See docs/HARDENING_BACKLOG.md H-39.

This patches `socket.socket.connect`/`connect_ex` process-wide for the
duration of the test run, refusing any AF_INET/AF_INET6 connection whose
destination is not loopback (127.0.0.1/::1/localhost — the local Postgres
and Redis this suite talks to, and Django's own `LiveServerTestCase`) or the
exact host:port this settings module resolved for the default database and
the Redis cache/Celery broker/result backend (a Docker-networked CI service
container is not reachable over loopback, so its real address must be
allowed explicitly — see H-41's note on this). AF_UNIX sockets are never
touched: a Postgres Unix-socket connection is not a network call.

Complements, does not replace, `scripts/isolated-test-env.sh` (which gives
CI-matching fake credentials but doesn't itself stop a stray real call from
a differently-named env var).

Escape hatch: set `H39_ALLOW_REAL_NETWORK=1` to disable this guard entirely,
for a deliberate real-service run (Gate 7) that isn't a standalone script.
"""

import contextlib
import os
import socket
from urllib.parse import urlparse


class BlockedNetworkCallError(ConnectionError):
    """A test tried to make a real outbound network call. Mock it instead."""


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

_allowed_hosts: set[tuple[str, int]] = set()


def _parse_host_port(url):
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        return None
    return (parsed.hostname, parsed.port)


def _resolve_allowed_hosts():
    from django.conf import settings

    allowed = set()

    db = settings.DATABASES.get("default", {})
    host, port = db.get("HOST"), db.get("PORT")
    if host and port:
        allowed.add((str(host), int(port)))

    location = settings.CACHES.get("default", {}).get("LOCATION")
    locations = location if isinstance(location, (list, tuple)) else [location]
    for loc in locations:
        parsed = _parse_host_port(loc)
        if parsed:
            allowed.add(parsed)

    for url in (
        getattr(settings, "CELERY_BROKER_URL", None),
        getattr(settings, "CELERY_RESULT_BACKEND", None),
    ):
        parsed = _parse_host_port(url)
        if parsed:
            allowed.add(parsed)

    return allowed


def _is_allowed(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return True  # not a host/port destination - nothing this guard covers
    host, port = address[0], address[1]
    return host in _LOOPBACK_HOSTS or (host, port) in _allowed_hosts


def _guarded_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_allowed(address):
        raise BlockedNetworkCallError(
            f"Blocked real outbound network call to {address!r} during tests "
            "(H-39). Mock the client that makes this call, or set "
            "H39_ALLOW_REAL_NETWORK=1 for a deliberate real-service run."
        )
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_allowed(address):
        raise BlockedNetworkCallError(
            f"Blocked real outbound network call to {address!r} during tests "
            "(H-39). Mock the client that makes this call, or set "
            "H39_ALLOW_REAL_NETWORK=1 for a deliberate real-service run."
        )
    return _real_connect_ex(self, address)


@contextlib.contextmanager
def block_real_network_calls():
    if os.environ.get("H39_ALLOW_REAL_NETWORK") == "1":
        yield
        return

    global _allowed_hosts
    previous = (socket.socket.connect, socket.socket.connect_ex, _allowed_hosts)
    _allowed_hosts = _resolve_allowed_hosts()
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    try:
        yield
    finally:
        # Restore what was there on entry, not the module originals: the
        # test runner already holds this guard open, so a nested use must
        # not switch the outer one off.
        socket.socket.connect = previous[0]  # type: ignore[method-assign]
        socket.socket.connect_ex = previous[1]  # type: ignore[method-assign]
        _allowed_hosts = previous[2]
