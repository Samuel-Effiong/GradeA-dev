"""Generation counters for cache invalidation (H-1, Option D, stage 1).

Invalidation by *versioning* rather than by deletion. Each cacheable entity
(a user, a school, a course, plus one global scope) owns an integer
generation. A cache key embeds the generations it depends on, so bumping a
counter makes every dependent key unreachable in **one `INCR`** - no
keyspace SCAN, no DEL, and no way to touch a key belonging to anyone else.

This replaces wildcard `delete_pattern` invalidation, which was measured
(see `docs/HARDENING_BACKLOG.md`) at 29 SCAN + 432 Redis commands per
imported row, and which destroyed all 10,000 cached keys - every tenant's
cache - on a 25-row import, because `*user*` matches 29 of the 35 cache-key
families in the project.

Design: `docs/H1_CACHE_INVALIDATION_DESIGN.md`.

STAGE 1 SCOPE: counters and key construction only. Nothing reads these keys
yet; the existing wildcard receivers are still in place and still the live
invalidation mechanism. This module is inert until stage 2 wires read sites
to it, which is what makes stage 1 reversible.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

#: Namespace for counters, and the abbreviations below are LOAD-BEARING.
#:
#: The obvious names (`gen:user:<id>`, `gen:school:<id>`, `gen:course:<id>`)
#: are actively unsafe while the old mechanism is still live, and this was
#: found the hard way: the legacy wildcards are SUBSTRING globs, so
#: `delete_pattern("*user*")` matches `gen:user:<id>` and deletes the
#: counter itself. A deleted counter reads back as DEFAULT_GENERATION, which
#: means every superseded cache entry becomes reachable again - precisely
#: the stale-revival failure this design exists to prevent.
#:
#: `usr`/`sch`/`crs` contain none of the substrings the live patterns match
#: ("user", "school", "course", "settings", "admin"). This is a naming
#: constraint, which is exactly the kind of fragile guarantee this project
#: is replacing - so it is enforced by a test
#: (`test_no_live_invalidation_pattern_can_destroy_a_counter`), not by this
#: comment. The constraint disappears at stage 3 when the wildcards go.
GENERATION_KEY_PREFIX = "cachegen"

#: The generation a counter is assumed to hold when Redis has never seen it.
#: Reads fall back to this rather than erroring, so a cold Redis serves
#: coherent keys immediately instead of failing.
DEFAULT_GENERATION = 1

#: The value a counter is CREATED at by the first bump.
#:
#: Two, not one - and this is the subtlest correctness requirement in the
#: module. A missing counter reads as DEFAULT_GENERATION (1), so entries
#: cached before the first bump already carry `g1`. Creating the counter at
#: 1 would leave those entries reachable and the invalidation would silently
#: do nothing. Starting at 2 guarantees the first bump invalidates.
FIRST_BUMP_GENERATION = DEFAULT_GENERATION + 1

#: Entity scopes. `global` exists for responses that genuinely depend on the
#: whole dataset (the superadmin dashboards), so they are not forced into a
#: per-user counter that would not capture their real dependency.
SCOPE_USER = "usr"
SCOPE_SCHOOL = "sch"
SCOPE_COURSE = "crs"
SCOPE_GLOBAL = "global"

#: ENTITY-CLASS scopes: "any row of this model changed", rather than "this
#: particular row changed". They exist for the superadmin dashboards whose
#: dependency really is a whole table but NOT the whole dataset.
#:
#: `super-admin/dashboard/schools` reads only `School`; `.../teachers` reads
#: only `CustomUser`. Hanging those off SCOPE_GLOBAL - which every mutation
#: in the system bumps - would invalidate them on activity they do not
#: depend on at all. A per-table counter keeps them precisely invalidated
#: AND genuinely cacheable, because both tables are low-churn (measured in
#: production: School 0.03 writes/day, CustomUser 0.8/day).
#:
#: Abbreviated for the same reason as the scopes above: `anysch`/`anyusr`
#: contain neither "school" nor "user", so no legacy wildcard pattern can
#: reach them while both mechanisms coexist. Enforced by
#: tests_cache_generation.CounterNamespaceSafetyTests, not by this comment.
SCOPE_ANY_SCHOOL = "anysch"
SCOPE_ANY_USER = "anyusr"

VALID_SCOPES = frozenset(
    {
        SCOPE_USER,
        SCOPE_SCHOOL,
        SCOPE_COURSE,
        SCOPE_GLOBAL,
        SCOPE_ANY_SCHOOL,
        SCOPE_ANY_USER,
    }
)

#: Scopes that have exactly one counter, so they take no entity id.
SINGLETON_SCOPES = frozenset({SCOPE_GLOBAL, SCOPE_ANY_SCHOOL, SCOPE_ANY_USER})


def generation_key(scope, entity_id=None):
    """Redis key holding one entity's generation counter.

    `entity_id` is omitted for SCOPE_GLOBAL, which has exactly one counter.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"unknown cache generation scope {scope!r}")
    if scope in SINGLETON_SCOPES:
        return f"{GENERATION_KEY_PREFIX}:{scope}"
    if entity_id is None:
        raise ValueError(f"scope {scope!r} requires an entity id")
    return f"{GENERATION_KEY_PREFIX}:{scope}:{entity_id}"


def get_generation(scope, entity_id=None):
    """Current generation for an entity. Never raises.

    A missing counter, an unreachable Redis, or a corrupted value all read
    as DEFAULT_GENERATION. That is deliberate: a read path must degrade to
    serving a coherent (possibly stale) key rather than failing the request,
    and the caller cannot distinguish "never bumped" from "Redis is down" -
    nor should it need to.
    """
    key = generation_key(scope, entity_id)
    try:
        value = cache.get(key)
    except Exception:
        logger.error(
            "Cache generation read failed for %s; falling back to the "
            "default generation, so responses may be served from a stale "
            "key until Redis recovers.",
            key,
            exc_info=True,
        )
        return DEFAULT_GENERATION

    if value is None:
        return DEFAULT_GENERATION
    try:
        return int(value)
    except (TypeError, ValueError):
        # A non-integer here means something else wrote to the counter
        # namespace. Treating it as the default keeps reads coherent; the
        # log is what makes the collision findable.
        logger.error(
            "Cache generation key %s holds a non-integer value %r; using "
            "the default generation.",
            key,
            value,
        )
        return DEFAULT_GENERATION


def bump_generation(scope, entity_id=None):
    """Advance an entity's generation, invalidating everything keyed on it.

    Returns the new generation, or None if Redis was unreachable.

    NEVER RAISES. Callers are `post_save`/`post_delete` receivers, which
    Django runs inside the caller's transaction - an exception escaping here
    would fail the database write that triggered it. A Redis blip must cost
    a stale cache (bounded by TTL), never a lost write. This is the same
    principle already established in `classrooms/signals.py`.
    """
    key = generation_key(scope, entity_id)
    try:
        return _incr_or_create(key)
    except Exception:
        logger.error(
            "Cache generation bump failed for %s; entries keyed on it will "
            "serve stale data until their TTL expires.",
            key,
            exc_info=True,
        )
        return None


def _incr_or_create(key):
    """Increment, creating the counter if it does not exist yet.

    Race-safe without a lock:
      * `incr` is atomic and wins whenever the counter exists;
      * `add` is SET NX, so exactly one racer creates it;
      * a racer that loses the `add` falls through to `incr`, which now
        succeeds - so its bump is not swallowed.

    `timeout=None` on the create is LOAD-BEARING, not stylistic. Production
    Redis runs `volatile-lru`, which evicts only keys that have an expiry.
    A counter with no TTL is therefore never evictable, while every cache
    entry is - so under memory pressure Redis discards cached data and keeps
    the counters guarding it. Give a counter a TTL and it can be evicted
    while the entries it guards survive, resetting the generation and
    reviving stale data. There is a test that fails if this argument is
    removed.
    """
    try:
        return cache.incr(key)
    except ValueError:
        if cache.add(key, FIRST_BUMP_GENERATION, None):
            return FIRST_BUMP_GENERATION
        # Lost the creation race; the winner set it, so increment theirs.
        return cache.incr(key)


def bump_many(scopes):
    """Bump several counters in ONE network round trip where possible.

    `scopes` is an iterable of (scope, entity_id) pairs. Duplicates are
    collapsed, and None entity ids are skipped, so callers can pass
    `("school", teacher.school_id)` without first checking whether the
    teacher has a school.

    Returns the number of counters successfully bumped.

    Why pipelining matters here: enrolling a student legitimately
    invalidates that student's own cache, so a bulk import of N *existing*
    students is inherently O(N) counter bumps - that is correctness, not
    waste, and skipping it was explicitly rejected. What pipelining removes
    is the N network ROUND TRIPS, which is the part that was actually
    expensive (measured: 305ms for 2,000 sequential bumps).
    """
    unique = {
        (scope, entity_id)
        for scope, entity_id in scopes
        if entity_id is not None or scope in SINGLETON_SCOPES
    }
    if not unique:
        return 0

    pipelined = _bump_pipelined(unique)
    if pipelined is not None:
        return pipelined

    # Backend has no pipeline (LocMem), or the pipeline failed. Fall back to
    # one round trip per counter. A counter bumped by a partially-executed
    # pipeline is simply bumped twice here, which costs one extra generation
    # and is harmless - unlike skipping it, which would leave stale data.
    return sum(1 for scope, eid in unique if bump_generation(scope, eid) is not None)


def _bump_pipelined(pairs):
    """Bump every counter in `pairs` in a single round trip.

    Returns the number bumped, or None if pipelining is unavailable or
    failed - in which case the caller falls back.

    Each counter needs TWO Redis commands, not one, and the reason is
    subtle. `cache.incr()` raises on a missing key, but the RAW Redis
    `INCR` silently creates it at 1 - which is exactly the value a missing
    counter already reads as, so a naive pipelined `INCR` would make the
    first bump invalidate NOTHING. `SET key 1 NX` followed by `INCR` gives
    2 for a new counter and n+1 for an existing one, preserving the
    invariant while still costing a single round trip.

    `SET ... NX` without an expiry keeps the counter non-evictable under
    production's `volatile-lru`, same as the non-pipelined path.
    """
    try:
        client = cache.client.get_client(write=True)
        make_key = cache.client.make_key
    except Exception:
        # Not a Redis backend (LocMem in some suites). Not an error.
        return None

    try:
        keys = [str(make_key(generation_key(scope, eid))) for scope, eid in pairs]
        pipeline = client.pipeline(transaction=False)
        for key in keys:
            pipeline.set(key, DEFAULT_GENERATION, nx=True)
            pipeline.incr(key)
        pipeline.execute()
        return len(keys)
    except Exception:
        logger.warning(
            "Pipelined cache generation bump failed for %d counter(s); "
            "falling back to individual bumps.",
            len(pairs),
            exc_info=True,
        )
        return None


def versioned_key(base, scopes):
    """Build a cache key carrying the generations it depends on.

    `scopes` is an ordered iterable of (scope, entity_id) pairs. Order is
    preserved so a given base always produces a stable key shape.

        versioned_key("schooladmins:user_id__A:view__summary",
                      [("user", "A"), ("school", "S")])
        -> "schooladmins:user_id__A:view__summary:g.user=7.school=3"

    A key built this way is unreachable the moment ANY of its generations
    advances, which is what lets one response depend on several entities
    without needing a pattern that spans them.
    """
    parts = [
        f"{scope}={get_generation(scope, entity_id)}" for scope, entity_id in scopes
    ]
    if not parts:
        raise ValueError(
            "a versioned key needs at least one scope; a key that depends on "
            "nothing can never be invalidated, which is the bug this module "
            "exists to remove"
        )
    return f"{base}:g.{'.'.join(parts)}"
