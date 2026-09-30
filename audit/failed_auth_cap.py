"""Epic A S1b: a bound on failed-sign-in audit volume (SM rulings, 2026-09-30).

Per-IP throttles bound one address, not a spray from many. Without this, a
distributed attack could write an unbounded number of failed-auth rows.

What is capped: failed AUTH_LOGIN / ACCOUNT_REGISTER events (outcome FAILURE)
whose requester is ANONYMOUS. Never capped:
- a success;
- a DENIED event (a locked or deactivated account - the signal an account is
  under attack);
- a failure by a signed-in requester (change-password): it needs a valid
  session and is already bounded by the throttles and the login lock, and a
  suppressed event would make S1's middleware fall back to an uncapped
  STATE_CHANGE.

The caps, per fixed window of FAILED_AUTH_WINDOW_SECONDS (default 1 h):
- a KNOWN account's first FAILED_AUTH_TARGET_FLOOR (5) failures are ALWAYS
  written, whatever the global count, so a junk-email spray can't blind real
  accounts;
- past the floor, at most FAILED_AUTH_TARGET_LIMIT (30) per account, and at
  most FAILED_AUTH_GLOBAL_LIMIT (300) across everything;
- a failure with no target (an unknown email, a malformed body) is under the
  global cap from the first event.
Keys hold the account id, never an email.

A suppressed event is not written. Instead, a summary event (the same action,
FAILURE, reason FAILED_AUTH_CAPPED, metadata cap / suppressed_so_far / limit /
window_seconds) is written when a bucket's suppressed count reaches 1, 10,
100, 1000 ... in the window. The latest summary is therefore a LOWER bound on
what was suppressed (within x10); the exact count is the
`audit_failed_auth_suppressed_total` metric. A fixed window lets up to twice a
limit through across a window boundary.

Counters are cache `add` + `incr`, atomic on the shared Redis cache, so under
concurrency each threshold is reached - and its summary written - exactly
once. Any cache error fails OPEN: the event is written as if uncapped.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache

from . import metrics as audit_metrics

logger = logging.getLogger(__name__)

FAILED_AUTH_CAPPED = "FAILED_AUTH_CAPPED"
CAP_TARGET = "target"
CAP_GLOBAL = "global"


@dataclass(frozen=True)
class Summary:
    """A summary event to write in place of a suppressed one."""

    cap: str
    target_id: Optional[str]
    suppressed_so_far: int
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class Verdict:
    write: bool
    summary: Optional[Summary] = None


WRITE = Verdict(write=True)


def _window():
    seconds = settings.FAILED_AUTH_WINDOW_SECONDS
    return seconds, int(time.time() // seconds)


def _count(key, timeout):
    """Atomic increment of `key`, created at 0 for `timeout` seconds."""
    cache.add(key, 0, timeout=timeout)
    try:
        return cache.incr(key)
    except ValueError:  # expired between add and incr: start again
        cache.set(key, 1, timeout=timeout)
        return 1


def _is_threshold(n):
    """1, 10, 100, 1000 ..."""
    while n >= 10 and n % 10 == 0:
        n //= 10
    return n == 1


def admit(target_id) -> Verdict:
    """Decide whether one capped-scope failure may be written. `target_id` is
    the targeted account's id, or None."""
    try:
        seconds, bucket = _window()
        ttl = seconds * 2
        prefix = f"audit:failed_auth:{bucket}"
        global_count = _count(f"{prefix}:global", ttl)

        if target_id is not None:
            target_count = _count(f"{prefix}:target:{target_id}", ttl)
            if target_count <= settings.FAILED_AUTH_TARGET_FLOOR:
                return WRITE
            if target_count > settings.FAILED_AUTH_TARGET_LIMIT:
                return _suppress(prefix, ttl, seconds, CAP_TARGET, str(target_id))
        if global_count > settings.FAILED_AUTH_GLOBAL_LIMIT:
            return _suppress(prefix, ttl, seconds, CAP_GLOBAL, None)
        return WRITE
    except Exception as exc:  # noqa: BLE001 - fail open: never lose the event
        logger.error(
            "failed-auth cap unavailable, event written uncapped: %s",
            type(exc).__name__,
            extra={"audit_kind": "failed_auth_cap_failed"},
        )
        return WRITE


def _suppress(prefix, ttl, seconds, cap, target_id):
    bucket = target_id if cap == CAP_TARGET else CAP_GLOBAL
    suppressed = _count(f"{prefix}:suppressed:{cap}:{bucket}", ttl)
    audit_metrics.count("audit_failed_auth_suppressed_total", tags={"cap": cap})
    if not _is_threshold(suppressed):
        return Verdict(write=False)
    limit = (
        settings.FAILED_AUTH_TARGET_LIMIT
        if cap == CAP_TARGET
        else settings.FAILED_AUTH_GLOBAL_LIMIT
    )
    return Verdict(
        write=False,
        summary=Summary(
            cap=cap,
            target_id=target_id,
            suppressed_so_far=suppressed,
            limit=limit,
            window_seconds=seconds,
        ),
    )
