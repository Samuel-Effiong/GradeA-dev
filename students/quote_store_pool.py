"""Run upload quote file stores a few at a time (UPLOAD_QUOTE_STORE_CONCURRENCY).

Only the storage call runs in the pool threads: each call is handed what it
needs and never touches the database, so every DB write stays on the
caller's thread (the request, or the store task). Results come back in the
order the calls were given, each either the call's return value or the
exception it raised, so one failed file never fails the others.

With a concurrency of 1, or a single call, the calls run inline on the
caller's thread.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from django.conf import settings


@dataclass(frozen=True)
class Outcome:
    value: Any = None
    error: Exception | None = None


def _run(call: Callable[[], Any]) -> Outcome:
    try:
        return Outcome(value=call())
    except Exception as exc:  # noqa: BLE001 - handed back to the caller
        return Outcome(error=exc)


def run_all(calls: Sequence[Callable[[], Any]]) -> list[Outcome]:
    """One Outcome per call, in order."""
    workers = max(1, int(settings.UPLOAD_QUOTE_STORE_CONCURRENCY))
    if workers == 1 or len(calls) <= 1:
        return [_run(call) for call in calls]
    with ThreadPoolExecutor(
        max_workers=min(workers, len(calls)), thread_name_prefix="quote-store"
    ) as pool:
        futures = [pool.submit(_run, call) for call in calls]
        return [future.result() for future in futures]
