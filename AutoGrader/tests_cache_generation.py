"""H-1 stage 1: the generation-counter core, verified against real Redis.

Every test here runs on a real Redis instance on a dedicated DB. LocMem is
explicitly not used: this module's correctness rests on `INCR` atomicity,
`SET NX` semantics, and TTL behaviour under `volatile-lru`, none of which
LocMem reproduces - a LocMem run would pass while proving nothing.

Covers the gate's functional, adversarial, concurrency, failure-simulation
and integrity classes. Load/stress and live-endpoint classes do not apply at
stage 1, because nothing reads these keys yet; they arrive with stage 2,
when read sites are wired up.
"""

import threading
from unittest.mock import patch

import redis
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from AutoGrader.cache_generation import (
    DEFAULT_GENERATION,
    FIRST_BUMP_GENERATION,
    SCOPE_COURSE,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    bump_generation,
    bump_many,
    generation_key,
    get_generation,
    versioned_key,
)
from AutoGrader.test_cache import real_redis_caches

REDIS_URL = "redis://127.0.0.1:6379/9"
REDIS_CACHE = real_redis_caches(REDIS_URL)

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@override_settings(CACHES=REDIS_CACHE)
class GenerationCoreTests(SimpleTestCase):
    def setUp(self):
        self.redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        cache.clear()

    def tearDown(self):
        cache.clear()

    def raw(self, scope, entity_id=None):
        return cache.make_key(generation_key(scope, entity_id))

    # ---- functional ----

    def test_an_unknown_entity_reads_as_the_default_generation(self):
        self.assertEqual(get_generation(SCOPE_USER, A), DEFAULT_GENERATION)

    def test_the_first_bump_creates_the_counter_past_the_default(self):
        """The subtlest requirement: a missing counter reads as 1, so the
        first bump must land on 2 or it invalidates nothing."""
        self.assertEqual(bump_generation(SCOPE_USER, A), FIRST_BUMP_GENERATION)
        self.assertGreater(get_generation(SCOPE_USER, A), DEFAULT_GENERATION)

    def test_the_first_bump_actually_invalidates_pre_existing_keys(self):
        """The property the previous test protects, stated end-to-end."""
        before = versioned_key("courses:user_id__" + A, [(SCOPE_USER, A)])
        bump_generation(SCOPE_USER, A)
        after = versioned_key("courses:user_id__" + A, [(SCOPE_USER, A)])
        self.assertNotEqual(before, after)

    def test_bumps_are_monotonic(self):
        seen = [bump_generation(SCOPE_USER, A) for _ in range(5)]
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), 5)

    def test_the_global_scope_needs_no_entity_id(self):
        self.assertEqual(generation_key(SCOPE_GLOBAL), "cachegen:global")
        self.assertEqual(bump_generation(SCOPE_GLOBAL), FIRST_BUMP_GENERATION)

    def test_a_versioned_key_changes_when_any_dependency_bumps(self):
        scopes = [(SCOPE_USER, A), (SCOPE_SCHOOL, B)]
        first = versioned_key("dash:summary", scopes)

        bump_generation(SCOPE_SCHOOL, B)
        second = versioned_key("dash:summary", scopes)
        self.assertNotEqual(first, second)

        bump_generation(SCOPE_USER, A)
        self.assertNotEqual(second, versioned_key("dash:summary", scopes))

    def test_bump_many_collapses_duplicates_and_skips_missing_ids(self):
        bumped = bump_many(
            [
                (SCOPE_USER, A),
                (SCOPE_USER, A),  # duplicate
                (SCOPE_SCHOOL, None),  # teacher with no school
                (SCOPE_COURSE, B),
            ]
        )
        self.assertEqual(bumped, 2)

    # ---- adversarial / tenant isolation ----

    def test_bumping_one_entity_cannot_change_another_entitys_key(self):
        """The property the whole architecture rests on."""
        key_b = versioned_key("courses:user_id__" + B, [(SCOPE_USER, B)])
        for _ in range(50):
            bump_generation(SCOPE_USER, A)
        self.assertEqual(
            versioned_key("courses:user_id__" + B, [(SCOPE_USER, B)]),
            key_b,
            "another tenant's key changed when this tenant's counter moved",
        )

    def test_bumping_a_user_does_not_touch_school_or_course_scopes(self):
        school_before = get_generation(SCOPE_SCHOOL, A)
        course_before = get_generation(SCOPE_COURSE, A)
        bump_generation(SCOPE_USER, A)
        self.assertEqual(get_generation(SCOPE_SCHOOL, A), school_before)
        self.assertEqual(get_generation(SCOPE_COURSE, A), course_before)

    def test_bumping_never_issues_a_keyspace_scan(self):
        """The entire point: O(1) invalidation, no SCAN at any volume."""
        self.redis.config_resetstat()
        for i in range(50):
            bump_generation(SCOPE_USER, f"{A}-{i}")
        stats = {
            k.replace("cmdstat_", ""): v["calls"]
            for k, v in self.redis.info("commandstats").items()
        }
        self.assertEqual(
            stats.get("scan", 0), 0, "generation bumping issued a keyspace SCAN"
        )

    def test_an_unknown_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            generation_key("tenant", A)

    def test_a_non_global_scope_requires_an_entity_id(self):
        with self.assertRaises(ValueError):
            generation_key(SCOPE_USER, None)

    def test_a_key_with_no_dependency_is_refused(self):
        """A cache key that depends on nothing can never be invalidated -
        which is exactly the four-dashboard-key bug this project exists to
        fix. Refuse to build one."""
        with self.assertRaises(ValueError):
            versioned_key("dash:summary", [])

    def test_a_corrupted_counter_reads_as_the_default_rather_than_erroring(self):
        cache.set(generation_key(SCOPE_USER, A), "not-an-integer", None)
        self.assertEqual(get_generation(SCOPE_USER, A), DEFAULT_GENERATION)

    # ---- counter persistence: load-bearing under volatile-lru ----

    def test_a_created_counter_has_no_expiry(self):
        """Production Redis is `volatile-lru`, which evicts ONLY keys that
        have an expiry. A counter with a TTL becomes evictable while the
        cache entries it guards survive - the counter resets and stale
        entries revive. This assertion is what makes `timeout=None`
        enforceable rather than a convention."""
        bump_generation(SCOPE_USER, A)
        self.assertEqual(
            self.redis.ttl(self.raw(SCOPE_USER, A)),
            -1,
            "the generation counter has a TTL and is therefore evictable "
            "under volatile-lru",
        )

    def test_incrementing_does_not_introduce_an_expiry(self):
        bump_generation(SCOPE_USER, A)
        for _ in range(3):
            bump_generation(SCOPE_USER, A)
        self.assertEqual(self.redis.ttl(self.raw(SCOPE_USER, A)), -1)

    def test_cache_entries_by_contrast_do_expire(self):
        """The other half of the volatile-lru argument: entries must be
        evictable, so the eviction order is entries-first."""
        cache.set("courses:user_id__" + A, "payload", 300)
        self.assertGreater(self.redis.ttl(cache.make_key("courses:user_id__" + A)), 0)

    # ---- failure simulation ----

    def test_a_bump_survives_redis_being_unreachable(self):
        """Receivers run inside the caller's transaction: raising here would
        fail the database write that triggered the invalidation."""
        with patch(
            "django.core.cache.cache.incr",
            side_effect=redis.exceptions.ConnectionError("down"),
        ):
            with patch(
                "django.core.cache.cache.add",
                side_effect=redis.exceptions.ConnectionError("down"),
            ):
                self.assertIsNone(bump_generation(SCOPE_USER, A))

    def test_a_failed_bump_is_logged_as_an_error(self):
        with patch(
            "django.core.cache.cache.incr",
            side_effect=redis.exceptions.ConnectionError("down"),
        ), patch(
            "django.core.cache.cache.add",
            side_effect=redis.exceptions.ConnectionError("down"),
        ):
            with self.assertLogs("AutoGrader.cache_generation", "ERROR") as captured:
                bump_generation(SCOPE_USER, A)
        self.assertIn("stale", " ".join(captured.output).lower())

    def test_a_read_survives_redis_being_unreachable(self):
        with patch(
            "django.core.cache.cache.get",
            side_effect=redis.exceptions.ConnectionError("down"),
        ):
            self.assertEqual(get_generation(SCOPE_USER, A), DEFAULT_GENERATION)

    def test_the_fallback_path_continues_past_an_individual_failure(self):
        """Failure tolerance lives in the FALLBACK path - the pipelined path
        is all-or-nothing by construction and falls back on failure. This
        test therefore forces the fallback, then fails one counter inside
        it."""
        calls = {"n": 0}
        real_incr = cache.incr

        def flaky(key, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise redis.exceptions.ConnectionError("transient")
            return real_incr(key, *a, **kw)

        bump_generation(SCOPE_USER, A)
        bump_generation(SCOPE_USER, B)
        with patch("AutoGrader.cache_generation._bump_pipelined", return_value=None):
            with patch("django.core.cache.cache.incr", side_effect=flaky):
                bumped = bump_many([(SCOPE_USER, A), (SCOPE_USER, B)])
        self.assertEqual(bumped, 1, "one failure aborted the whole batch")

    def test_a_counter_lost_entirely_does_not_revive_stale_keys(self):
        """If Redis loses the counter it has also lost the cache (they share
        an instance and counters are the LAST thing evicted), so a reset
        counter must not resurrect an entry that outlived it. Simulated by
        deleting the counter while a cached entry survives - the worst case."""
        bump_generation(SCOPE_USER, A)
        live_key = versioned_key("courses:user_id__" + A, [(SCOPE_USER, A)])
        cache.set(live_key, "payload", 300)

        self.redis.delete(self.raw(SCOPE_USER, A))

        # Generation falls back to the default, so the key shape reverts.
        reverted = versioned_key("courses:user_id__" + A, [(SCOPE_USER, A)])
        self.assertNotEqual(
            reverted,
            live_key,
            "a counter reset produced the SAME key as a live cached entry, "
            "which would serve stale data - this is why counters must be "
            "non-evictable under volatile-lru",
        )
        self.assertIsNone(cache.get(reverted))


@override_settings(CACHES=REDIS_CACHE)
class GenerationConcurrencyTests(SimpleTestCase):
    """Real threads against real Redis - `INCR` atomicity is the claim."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _race(self, target, count):
        barrier = threading.Barrier(count)
        results = [None] * count

        def wrapped(i):
            barrier.wait(timeout=30)
            results[i] = target(i)

        threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        return results

    def test_concurrent_bumps_lose_nothing(self):
        n = 25
        self._race(lambda _i: bump_generation(SCOPE_USER, A), n)
        self.assertEqual(
            get_generation(SCOPE_USER, A),
            DEFAULT_GENERATION + n,
            "concurrent bumps were lost - INCR is not being relied on",
        )

    def test_concurrent_bumps_each_return_a_distinct_generation(self):
        n = 25
        results = self._race(lambda _i: bump_generation(SCOPE_USER, B), n)
        self.assertEqual(
            len(set(results)), n, "two racers were handed the same generation"
        )

    def test_racing_creation_of_a_brand_new_counter_is_safe(self):
        """The create-or-increment path: exactly one racer wins `add`, and
        the losers must still have their bump counted, not swallowed."""
        n = 20
        results = self._race(lambda _i: bump_generation(SCOPE_COURSE, A), n)
        self.assertEqual(len(set(results)), n, "a racer's bump was swallowed")
        self.assertEqual(get_generation(SCOPE_COURSE, A), DEFAULT_GENERATION + n)

    def test_a_reader_racing_a_writer_never_sees_a_torn_value(self):
        errors = []

        def read_or_bump(i):
            try:
                if i % 2:
                    bump_generation(SCOPE_USER, A)
                else:
                    value = get_generation(SCOPE_USER, A)
                    if not isinstance(value, int) or value < DEFAULT_GENERATION:
                        errors.append(value)
            except Exception as exc:  # noqa: BLE001 - recorded and asserted
                errors.append(exc)

        self._race(read_or_bump, 30)
        self.assertEqual(errors, [])


@override_settings(CACHES=REDIS_CACHE)
class PipelinedBumpTests(SimpleTestCase):
    """`bump_many` must collapse N round trips into one WITHOUT weakening any
    invariant the per-key path guarantees."""

    def setUp(self):
        self.redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_a_pipelined_first_bump_still_lands_past_the_default(self):
        """The invariant most at risk from pipelining: raw Redis INCR
        creates a missing key at 1, which is exactly what a missing counter
        already reads as - so a naive pipelined INCR would invalidate
        nothing."""
        bump_many([(SCOPE_USER, A)])
        self.assertEqual(get_generation(SCOPE_USER, A), FIRST_BUMP_GENERATION)
        self.assertGreater(get_generation(SCOPE_USER, A), DEFAULT_GENERATION)

    def test_a_pipelined_bump_invalidates_a_pre_existing_key(self):
        before = versioned_key("courses:user_id__" + A, [(SCOPE_USER, A)])
        bump_many([(SCOPE_USER, A)])
        self.assertNotEqual(
            before, versioned_key("courses:user_id__" + A, [(SCOPE_USER, A)])
        )

    def test_pipelined_counters_are_not_evictable(self):
        bump_many([(SCOPE_USER, A), (SCOPE_SCHOOL, B)])
        for scope, eid in ((SCOPE_USER, A), (SCOPE_SCHOOL, B)):
            self.assertEqual(
                self.redis.ttl(cache.make_key(generation_key(scope, eid))),
                -1,
                f"{scope} counter gained a TTL via the pipelined path",
            )

    def test_repeated_pipelined_bumps_are_monotonic(self):
        seen = []
        for _ in range(5):
            bump_many([(SCOPE_USER, A)])
            seen.append(get_generation(SCOPE_USER, A))
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), 5)

    def test_it_uses_one_round_trip_not_one_per_counter(self):
        """The whole point. Measured as Redis command count: 2 per counter
        (SET NX + INCR) issued in a single pipeline, versus the per-key path
        which also pays a round trip each."""
        self.redis.config_resetstat()
        bump_many([(SCOPE_USER, f"{A}-{i}") for i in range(100)])
        stats = {
            k.replace("cmdstat_", ""): v["calls"]
            for k, v in self.redis.info("commandstats").items()
        }
        self.assertEqual(stats.get("scan", 0), 0)
        self.assertEqual(
            stats.get("set", 0), 100, "expected exactly one SET NX per counter"
        )
        # redis-py's .incr() issues INCRBY, so that is the stat name.
        self.assertEqual(
            stats.get("incrby", 0), 100, "expected exactly one INCRBY per counter"
        )

    def test_it_falls_back_when_the_pipeline_fails(self):
        """A pipeline failure must degrade to individual bumps, not to
        skipping invalidation - stale data is the worse outcome."""
        with patch("AutoGrader.cache_generation._bump_pipelined", return_value=None):
            self.assertEqual(bump_many([(SCOPE_USER, A)]), 1)
        self.assertEqual(get_generation(SCOPE_USER, A), FIRST_BUMP_GENERATION)

    def test_the_fallback_still_bumps_every_counter(self):
        # Patch the pipeline helper, NOT cache.client.get_client: Django's
        # cache.incr() resolves through the same client, so patching that
        # disables the fallback this test exists to exercise.
        with patch("AutoGrader.cache_generation._bump_pipelined", return_value=None):
            bumped = bump_many(
                [(SCOPE_USER, A), (SCOPE_SCHOOL, B), (SCOPE_GLOBAL, None)]
            )
        self.assertEqual(bumped, 3)
        self.assertEqual(get_generation(SCOPE_USER, A), FIRST_BUMP_GENERATION)
        self.assertEqual(get_generation(SCOPE_SCHOOL, B), FIRST_BUMP_GENERATION)
        self.assertEqual(get_generation(SCOPE_GLOBAL), FIRST_BUMP_GENERATION)

    def test_a_pipelined_bump_of_one_entity_spares_every_other(self):
        others = {
            f"{B}-{i}": versioned_key(f"k{i}", [(SCOPE_USER, f"{B}-{i}")])
            for i in range(20)
        }
        bump_many([(SCOPE_USER, f"{A}-{i}") for i in range(200)])
        for eid, key in others.items():
            self.assertEqual(
                versioned_key(f"k{list(others).index(eid)}", [(SCOPE_USER, eid)]),
                key,
                "a pipelined batch changed an unrelated entity's key",
            )


@override_settings(CACHES=REDIS_CACHE)
class HighConcurrencyCounterTests(SimpleTestCase):
    """Pre-Stage-2 gate: this mechanism becomes foundational infrastructure,
    so its atomicity claims are stressed before its usage multiplies.

    Real threads, real Redis. Every test asserts the FINAL generation equals
    the number of successful mutations - a lost increment is a stale cache
    entry that is reachable again, which is the failure this whole project
    exists to remove.
    """

    def setUp(self):
        self.redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _race(self, target, count):
        barrier = threading.Barrier(count)
        results = [None] * count
        errors = []

        def wrapped(i):
            try:
                barrier.wait(timeout=60)
                results[i] = target(i)
            except Exception as exc:  # noqa: BLE001 - asserted by the caller
                errors.append(exc)

        threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        return results, errors

    def assert_no_ttl(self, *pairs):
        for scope, eid in pairs:
            self.assertEqual(
                self.redis.ttl(cache.make_key(generation_key(scope, eid))),
                -1,
                f"{scope}:{eid} counter acquired a TTL under concurrency",
            )

    def test_many_concurrent_bumps_of_the_SAME_user_lose_nothing(self):
        n = 100
        _, errors = self._race(lambda _i: bump_generation(SCOPE_USER, A), n)
        self.assertEqual(errors, [])
        self.assertEqual(get_generation(SCOPE_USER, A), DEFAULT_GENERATION + n)
        self.assert_no_ttl((SCOPE_USER, A))

    def test_many_concurrent_bumps_of_the_SAME_school_lose_nothing(self):
        n = 100
        self._race(lambda _i: bump_generation(SCOPE_SCHOOL, B), n)
        self.assertEqual(get_generation(SCOPE_SCHOOL, B), DEFAULT_GENERATION + n)
        self.assert_no_ttl((SCOPE_SCHOOL, B))

    def test_concurrent_bumps_of_MANY_DIFFERENT_users_are_each_exact(self):
        n = 60
        self._race(lambda i: bump_generation(SCOPE_USER, f"{A}-{i}"), n)
        for i in range(n):
            self.assertEqual(
                get_generation(SCOPE_USER, f"{A}-{i}"),
                FIRST_BUMP_GENERATION,
                f"user {i} did not land on exactly one bump",
            )

    def test_concurrent_bumps_of_MANY_DIFFERENT_schools_are_each_exact(self):
        n = 60
        self._race(lambda i: bump_generation(SCOPE_SCHOOL, f"{B}-{i}"), n)
        for i in range(n):
            self.assertEqual(
                get_generation(SCOPE_SCHOOL, f"{B}-{i}"), FIRST_BUMP_GENERATION
            )

    def test_mixed_scopes_do_not_contaminate_each_other(self):
        """user / school / course / global bumped concurrently: each counter
        must end at exactly its own share, with no cross-entity bleed."""
        per_scope = 40
        scopes = [SCOPE_USER, SCOPE_SCHOOL, SCOPE_COURSE, SCOPE_GLOBAL]

        def target(i):
            scope = scopes[i % len(scopes)]
            return bump_generation(scope, None if scope == SCOPE_GLOBAL else A)

        _, errors = self._race(target, per_scope * len(scopes))
        self.assertEqual(errors, [])
        for scope in scopes:
            eid = None if scope == SCOPE_GLOBAL else A
            self.assertEqual(
                get_generation(scope, eid),
                DEFAULT_GENERATION + per_scope,
                f"{scope} counter was contaminated by another scope",
            )

    def test_concurrent_pipelined_batches_lose_nothing(self):
        """The pipelined path under contention - SET NX must not clobber a
        concurrent INCR."""
        batches, per_batch = 20, 5
        self._race(
            lambda _i: bump_many([(SCOPE_USER, f"{A}-{j}") for j in range(per_batch)]),
            batches,
        )
        for j in range(per_batch):
            self.assertEqual(
                get_generation(SCOPE_USER, f"{A}-{j}"),
                DEFAULT_GENERATION + batches,
                f"counter {j} lost increments under concurrent pipelines",
            )

    def test_a_counter_never_resets_under_concurrency(self):
        """The dangerous failure: a reset would make superseded keys
        reachable again.

        Stated as a bound rather than as ordering. Threads append in
        COMPLETION order, not read order, so the observed sequence is not
        required to be sorted - an earlier reader can append after a later
        one. What must hold is that no reader ever saw a value below the
        starting generation or above the final one.
        """
        observed = []
        bumps = 60
        readers = 30

        def target(i):
            if i % 3 == 0:
                observed.append(get_generation(SCOPE_USER, A))
            else:
                bump_generation(SCOPE_USER, A)

        self._race(target, bumps + readers)
        final = get_generation(SCOPE_USER, A)

        self.assertEqual(
            final, DEFAULT_GENERATION + bumps, "increments were lost or doubled"
        )
        self.assertTrue(
            all(DEFAULT_GENERATION <= v <= final for v in observed),
            f"a reader saw a generation outside [{DEFAULT_GENERATION}, {final}]: "
            f"{[v for v in observed if not DEFAULT_GENERATION <= v <= final]}",
        )

    def test_a_superseded_key_never_becomes_reachable_again(self):
        """Stated as the property that actually matters, end to end."""
        seen_keys = set()

        def target(_i):
            bump_generation(SCOPE_USER, A)
            seen_keys.add(versioned_key("courses:u", [(SCOPE_USER, A)]))

        self._race(target, 50)
        final = versioned_key("courses:u", [(SCOPE_USER, A)])
        # Every key ever produced is superseded except the final one, and the
        # final generation is the highest, so no earlier key can recur.
        self.assertEqual(
            final,
            max(seen_keys, key=lambda k: int(k.rsplit("=", 1)[1])),
            "the current key is not the highest generation produced",
        )

    def test_concurrent_bumps_survive_intermittent_redis_failure(self):
        """Failure injected DURING contention, not in isolation. Bumps that
        hit the failure return None; the rest must still be exact, and
        nothing may raise into the caller."""
        real_incr = cache.incr
        state = {"n": 0}
        lock = threading.Lock()

        def flaky(key, *a, **kw):
            with lock:
                state["n"] += 1
                fail = state["n"] % 4 == 0
            if fail:
                raise redis.exceptions.ConnectionError("intermittent")
            return real_incr(key, *a, **kw)

        bump_generation(SCOPE_USER, A)  # ensure the counter exists
        before = get_generation(SCOPE_USER, A)

        with patch("django.core.cache.cache.incr", side_effect=flaky):
            results, errors = self._race(lambda _i: bump_generation(SCOPE_USER, A), 40)

        self.assertEqual(errors, [], "a Redis failure escaped into the caller")
        succeeded = sum(1 for r in results if r is not None)
        self.assertEqual(
            get_generation(SCOPE_USER, A),
            before + succeeded,
            "the final generation does not equal the number of SUCCESSFUL bumps",
        )

    def test_recovery_after_a_failure_window_resumes_exactly(self):
        bump_generation(SCOPE_USER, A)
        with patch(
            "django.core.cache.cache.incr",
            side_effect=redis.exceptions.ConnectionError("down"),
        ), patch(
            "django.core.cache.cache.add",
            side_effect=redis.exceptions.ConnectionError("down"),
        ):
            self._race(lambda _i: bump_generation(SCOPE_USER, A), 20)
        during = get_generation(SCOPE_USER, A)

        n = 20
        self._race(lambda _i: bump_generation(SCOPE_USER, A), n)
        self.assertEqual(
            get_generation(SCOPE_USER, A),
            during + n,
            "counter did not resume exactly after Redis recovered",
        )
        self.assert_no_ttl((SCOPE_USER, A))


@override_settings(CACHES=REDIS_CACHE)
class CounterNamespaceSafetyTests(SimpleTestCase):
    """The counter namespace must not be reachable by the OLD mechanism.

    Both mechanisms run side by side through stage 2. The legacy wildcards
    are substring globs, so a counter named `gen:user:<id>` is matched and
    DELETED by `delete_pattern("*user*")` - and a deleted counter resets to
    the default generation, reviving every superseded entry it guarded.

    This was a real defect, found by end-to-end wiring tests after the
    isolated unit tests had passed: the counters were being destroyed by the
    very receivers they run alongside. The namespace was renamed to avoid
    every matched substring; this test is what keeps that true, so adding a
    new wildcard pattern that reaches the counters fails here rather than
    silently reviving stale data in production.
    """

    #: Every wildcard pattern any live receiver fires today.
    LIVE_PATTERNS = [
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "*school*",
        "*course*",
        "*studentcourse*",
        "*settings*",
        "schools:*",
        "sessions:*",
        "courses:*",
        "studentcourses:*",
        "topics:*",
        "assignments:*",
        "studentsubmissions:*",
    ]

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_no_live_invalidation_pattern_can_destroy_a_counter(self):
        counters = [
            generation_key(SCOPE_USER, A),
            generation_key(SCOPE_SCHOOL, A),
            generation_key(SCOPE_COURSE, A),
            generation_key(SCOPE_GLOBAL),
        ]
        destroyed = {}
        for key in counters:
            for pattern in self.LIVE_PATTERNS:
                cache.set(key, 7, None)
                cache.delete_pattern(pattern)
                if cache.get(key) is None:
                    destroyed.setdefault(key, []).append(pattern)
            cache.delete(key)

        self.assertEqual(
            destroyed,
            {},
            "a live wildcard pattern deletes a generation counter. A deleted "
            "counter resets to the default generation, which makes every "
            "superseded cache entry reachable again.",
        )

    def test_a_counter_survives_a_real_wildcard_sweep_end_to_end(self):
        bump_generation(SCOPE_USER, A)
        expected = get_generation(SCOPE_USER, A)

        for pattern in self.LIVE_PATTERNS:
            cache.delete_pattern(pattern)

        self.assertEqual(
            get_generation(SCOPE_USER, A),
            expected,
            "the generation went backwards after a legacy invalidation sweep",
        )
