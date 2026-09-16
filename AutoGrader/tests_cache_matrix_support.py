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
"""

import importlib
import json
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

from django.core.cache import cache
from rest_framework.test import APIClient

from AutoGrader.tests_cache_generation import redis_commands_sent_by_this_process

#: Every module that has ever held a `delete_cache_patterns` reference used
#: by production invalidation. Before Stage 3 removes them, disabling the
#: legacy mechanism means patching all of them; after removal they are gone
#: and there is nothing to patch.
LEGACY_MODULES = (
    "AutoGrader.cache_utils",
    "classrooms.signals",
    "users.signals",
    "students.signals",
    "assignments.signals",
)

FRESH = "FRESH"
UNAFFECTED = "UNAFFECTED"
STALE = "STALE"
SPURIOUS = "SPURIOUS"  # cached answer changed although the truth did not


@contextmanager
def legacy_wildcards_disabled():
    """Neutralise wildcard invalidation wherever it still exists.

    Patches only modules that still define `delete_cache_patterns`, so the
    same matrix runs before and after Stage 3 removes the mechanism. Yields
    the list of modules actually patched, so a suite can assert which state
    it ran in.
    """
    patched = []
    with ExitStack() as stack:
        for name in LEGACY_MODULES:
            module = importlib.import_module(name)
            if hasattr(module, "delete_cache_patterns"):
                stack.enter_context(
                    patch.object(module, "delete_cache_patterns", lambda *a, **k: None)
                )
                patched.append(name)
        yield patched


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
            mutate()

        result = MatrixResult(
            mutation=mutation_label,
            scans_during_mutation=sent["SCAN"],
            commands_during_mutation=dict(sent),
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
        self.assertEqual(
            result.scans_during_mutation,
            0,
            f"{result.mutation} issued {result.scans_during_mutation} keyspace SCAN(s)",
        )
