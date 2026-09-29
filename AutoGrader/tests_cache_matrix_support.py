"""H-1 Stage 3: machinery for the viewer x mutation freshness matrix.

This module holds no tests; the matrix suites import it.

The matrix answers one question for every cached read and every write path:

    after a real mutation, does each viewer's CACHED response equal what the
    database says right now?

Per (viewer, endpoint):

* read the endpoint normally, which warms that viewer's cache entry;
* perform the mutation through the real endpoint or service;
* read again normally (the cached answer);
* read once more with the cache bypassed for that single request (the truth).

A pair is **FRESH** when the truth changed and the cached answer changed with
it. It is **UNAFFECTED** when neither changed. It is **STALE** when the truth
changed but the cached answer did not. Status codes are part of the compared
response, so a viewer losing access (200 -> 403/404) is a change like any
other, and a cached 200 served after revocation is STALE.

**Real Redis and real PostgreSQL only.** Fixtures must create rows the way
production does (see docs/H1_STAGE3_WILDCARD_REMOVAL_PLAN.md §0).

**The matrix runs against the real code, unpatched.** Until H-1 step 4 it
ran inside `legacy_wildcards_disabled()`, which patched the wildcard
`delete_cache_patterns` out of every signal module so only the generation
bumps were measured. Step 4 deleted the wildcards, so there is nothing left
to patch and the patch is gone: what the matrix measures IS production.
In its place (plan §4 step 4) every mutation runs under a Redis SCAN spy,
and `run_matrix` fails if the mutation issues any keyspace SCAN other than
the one documented exception, the PDF cache's exact-prefix clear of a
single assignment (`assignments/pdf_cache.py`, plan §2). A wildcard that
came back would fail every matrix row it runs in, as well as the static
guard `AutoGrader/tests_no_wildcard_invalidation.py`.
"""

import json
import re
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

import redis
from django.core.cache import cache
from rest_framework.test import APIClient

from AutoGrader.tests_cache_generation import redis_commands_sent_by_this_process

#: The only keyspace SCAN production may issue during a mutation: the PDF
#: cache clearing ONE assignment's own renders,
#: `<key prefix>:<version>:assignmentpdf:<pdf version>:<assignment uuid>:*`
#: (the key prefix and version are added by django-redis `make_key`). A
#: trailing `*` after an exact assignment id, and no other glob character.
PDF_EXACT_PREFIX_SCAN = re.compile(
    r"^[^*?\[]*:assignmentpdf:[^:*?\[]+:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:\*$"
)

FRESH = "FRESH"
UNAFFECTED = "UNAFFECTED"
STALE = "STALE"
SPURIOUS = "SPURIOUS"  # cached answer changed although the truth did not


def _decode(value):
    return value.decode() if isinstance(value, bytes) else str(value)


@contextmanager
def scan_patterns_sent_by_this_process():
    """Record the MATCH pattern of every SCAN this process sends.

    django-redis `delete_pattern` is `scan_iter(match=...)`, which sends
    `SCAN <cursor> MATCH <pattern> COUNT <n>` through `send_command`, one
    command per page. A SCAN with no MATCH is recorded as "<no MATCH>" -
    that would walk every key and is never allowed.
    """
    patterns = []
    connection_class = redis.connection.AbstractConnection
    send_one = connection_class.send_command

    def recording_send_command(self, *args, **kwargs):
        if args and _decode(args[0]).upper() == "SCAN":
            parts = [_decode(a) for a in args]
            upper = [p.upper() for p in parts]
            if "MATCH" in upper:
                patterns.append(parts[upper.index("MATCH") + 1])
            else:
                patterns.append("<no MATCH>")
        return send_one(self, *args, **kwargs)

    with patch.object(connection_class, "send_command", recording_send_command):
        yield patterns


def disallowed_scan_patterns(patterns):
    """Every SCAN pattern that is not the documented PDF exact-prefix clear."""
    return sorted({p for p in patterns if not PDF_EXACT_PREFIX_SCAN.match(p)})


@contextmanager
def cache_bypassed():
    """Compute the truth for one read without disturbing any cache entry.

    Every cache read misses and every write is dropped, so the view builds
    its response from the database and leaves all cached entries,
    generation counters included, exactly as they were.
    """
    with patch.object(cache, "get", lambda *a, **k: None), patch.object(
        cache, "set", lambda *a, **k: True
    ), patch.object(cache, "add", lambda *a, **k: True):
        yield


def _canonical(response):
    data = getattr(response, "data", None)
    try:
        body = json.dumps(data, sort_keys=True, default=str)
    except TypeError:
        body = repr(data)
    return (response.status_code, body)


@dataclass
class Read:
    """One cached read: a label, the viewing user, and the URL."""

    label: str
    viewer: object
    url: str


@dataclass
class Outcome:
    label: str
    verdict: str
    before: tuple
    cached_after: tuple
    truth_after: tuple


@dataclass
class MatrixResult:
    mutation: str
    outcomes: list = field(default_factory=list)
    scans_during_mutation: int = 0
    commands_during_mutation: dict = field(default_factory=dict)
    scan_patterns_during_mutation: list = field(default_factory=list)

    def by_verdict(self, verdict):
        return [o for o in self.outcomes if o.verdict == verdict]

    def table(self):
        width = max([len(o.label) for o in self.outcomes] + [8])
        lines = [
            f"mutation: {self.mutation}  (SCANs during mutation: {self.scans_during_mutation})"
        ]
        for o in self.outcomes:
            lines.append(
                f"  {o.label:<{width}}  {o.verdict:<10}  "
                f"status {o.before[0]} -> cached {o.cached_after[0]} / truth {o.truth_after[0]}"
            )
        return "\n".join(lines)


class FreshnessMatrixMixin(unittest.TestCase):
    """Mix into a real-Redis TransactionTestCase.

    Inherits `unittest.TestCase` only so mypy sees `self.fail` and
    `self.assertEqual`; the real assertion behaviour comes from whatever
    Django test case the concrete subclass also inherits.
    """

    def get_as(self, user, url):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(url)

    def run_matrix(self, mutation_label, reads, mutate):
        """Warm every read, run `mutate`, then classify each read."""
        before = {}
        for read in reads:
            before[read.label] = _canonical(self.get_as(read.viewer, read.url))
            # A payload that differs between two reads of unchanged data
            # (a clock, a random order) would be misreported as STALE or
            # SPURIOUS. Refuse to classify it rather than guess.
            with cache_bypassed():
                uncached = _canonical(self.get_as(read.viewer, read.url))
            if uncached != before[read.label]:
                self.fail(
                    f"{read.label}: the cached read differs from an uncached read "
                    "BEFORE any mutation, so this read cannot be classified "
                    f"(cached status {before[read.label][0]}, uncached {uncached[0]})"
                )

        with redis_commands_sent_by_this_process() as sent:
            with scan_patterns_sent_by_this_process() as scan_patterns:
                mutate()

        result = MatrixResult(
            mutation=mutation_label,
            scans_during_mutation=sent["SCAN"],
            commands_during_mutation=dict(sent),
            scan_patterns_during_mutation=list(scan_patterns),
        )
        # The no-wildcard guard, on every matrix row (plan §4 step 4). Checked
        # before any verdict so a wildcard that came back cannot hide behind
        # a FRESH result it produced itself.
        disallowed = disallowed_scan_patterns(scan_patterns)
        if disallowed:
            self.fail(
                f"{mutation_label}: the mutation issued a keyspace SCAN other "
                f"than the PDF exact-prefix clear: {disallowed}. Wildcard "
                "invalidation was removed in H-1 step 4; invalidate with a "
                "generation bump (AutoGrader/cache_generation.py) instead."
            )
        for read in reads:
            cached_after = _canonical(self.get_as(read.viewer, read.url))
            with cache_bypassed():
                truth_after = _canonical(self.get_as(read.viewer, read.url))
            truth_changed = truth_after != before[read.label]
            if truth_changed:
                verdict = FRESH if cached_after == truth_after else STALE
            else:
                verdict = UNAFFECTED if cached_after == before[read.label] else SPURIOUS
            result.outcomes.append(
                Outcome(
                    read.label, verdict, before[read.label], cached_after, truth_after
                )
            )
        return result

    def assert_no_stale(self, result, expect_changed=()):
        """Fail listing EVERY stale viewer, not just the first.

        `expect_changed` names reads whose truth MUST change for this
        mutation. That guards against a matrix row that silently tests
        nothing because the mutation did not affect the payload.
        """
        problems = []
        for o in result.outcomes:
            if o.verdict in (STALE, SPURIOUS):
                problems.append(o.label)
        missing_change = [
            label
            for label in expect_changed
            if next(o for o in result.outcomes if o.label == label).verdict
            == UNAFFECTED
        ]
        if problems or missing_change:
            self.fail(
                "\n"
                + result.table()
                + (f"\nSTALE/SPURIOUS: {problems}" if problems else "")
                + (
                    f"\nexpected a change but the truth did not move: {missing_change}"
                    if missing_change
                    else ""
                )
            )

    def assert_no_scan(self, result):
        """Strict: not even the PDF exact-prefix clear. For mutations that do
        not save an Assignment, so no SCAN of any kind is legitimate."""
        self.assertEqual(
            result.scans_during_mutation,
            0,
            f"{result.mutation} issued {result.scans_during_mutation} keyspace "
            f"SCAN(s): {result.scan_patterns_during_mutation}",
        )
