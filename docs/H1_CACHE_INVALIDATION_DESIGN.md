# H-1 Phase 2 — Cache invalidation design (Option D)

**Status: design for review. Nothing implemented.**
Architecture selected by the owner: **Option D — hybrid generation
versioning**. Baseline and Phase 1 evidence live in
`docs/HARDENING_BACKLOG.md` and must not be re-baselined.

---

## 0. What this design has to fix

From the Phase 1 map (35 cache-key formats, measured against real Redis):

| Problem | Evidence |
|---|---|
| Over-invalidation | `*user*` matches **29 of 35** families; every `CustomUser`/`Settings` save is a de-facto full flush |
| Amplification | 29 SCAN + 432 Redis commands **per imported row**; a 25-row import destroyed all 10,000 keys |
| Redundancy | 67 pattern-matches for 35 keys; one key matched by 4 patterns |
| Under-invalidation | 4 dashboard responses reached by **no** pattern — stale up to 15 min |
| Collateral risk | Only a naming convention keeps billing locks / throttles / presence out of range |

---

## 1. Where counters live and how they are named

A generation counter is a Redis integer, one per invalidation *entity*.

```
cachegen:usr:<user_uuid>
cachegen:sch:<school_uuid>
cachegen:crs:<course_uuid>
cachegen:global
```

**Written through the Django cache** (so `KEY_PREFIX="gaplus"` applies and
they live in the same Redis DB as the cache — see §6 for why that shared
fate is deliberate, not incidental).

Counters are **never invalidated**, only `INCR`ed.

> **CORRECTION (stage 2, 2026-09-10).** This section previously said the
> counters were "namespaced under `gen:` which no existing or proposed
> pattern matches". **That was wrong, and it was a live defect** — found by
> the end-to-end wiring tests, after every isolated stage-1 unit test had
> passed.
>
> The legacy wildcards are SUBSTRING globs. `delete_pattern("*user*")`
> matches `gen:user:<id>`; `*school*` matches `gen:school:<id>`; `*course*`
> matches `gen:course:<id>`. So the old mechanism was **deleting the new
> mechanism's counters** on every user save — and a deleted counter reads
> back as `DEFAULT_GENERATION`, which makes every superseded cache entry
> reachable again. That is exactly the stale-revival failure §6 exists to
> prevent, reintroduced by the coexistence strategy itself.
>
> Measured, per counter, against the 16 live patterns:
>
> | Counter key | Destroyed by |
> |---|---|
> | `gen:user:<id>` | `*user*` |
> | `gen:school:<id>` | `*school*` |
> | `gen:course:<id>` | `*course*` |
> | `gen:global` | nothing |
> | `cachegen:usr:<id>` | **nothing** |
> | `cachegen:sch:<id>` | **nothing** |
> | `cachegen:crs:<id>` | **nothing** |
> | `cachegen:global` | **nothing** |
>
> Fixed by renaming the namespace to `cachegen:` with scope abbreviations
> `usr` / `sch` / `crs`, which contain none of the matched substrings.
>
> This is a naming constraint — the very kind of fragile guarantee this
> project exists to replace — so it is **enforced by a test**, not a comment:
> `test_no_live_invalidation_pattern_can_destroy_a_counter` writes each
> counter, runs all 16 live patterns against it, and fails if any removes it.
> Adding a new wildcard that reaches the counters now fails CI instead of
> silently reviving stale data. The constraint disappears at stage 3.
>
> **Why the unit tests missed it:** they exercised the counter module in
> isolation, where no receiver runs. The bug only exists in the interaction
> between the two mechanisms, which is what stage 2's wiring tests are for.

Read path uses `INCR`-free access: `cache.get_or_set(gen_key, 1, None)`.
A missing counter reads as 1 rather than erroring.

## 2. Cache key format, before and after

```
before   courses:user_id__<uid>:query__<md5>
after    courses:user_id__<uid>:g<uid_gen>:query__<md5>

before   schooladmins:user_id__<uid>:view__summary
after    schooladmins:user_id__<uid>:g<uid_gen>:s<school_gen>:view__summary

before   teacher_performance_<school>_<page>_<size>          (never invalidated)
after    dashboards:school_id__<school>:g<school_gen>:view__teacher_performance:<page>:<size>
```

Two changes at once for the four orphaned dashboard keys: they gain a
generation **and** move onto the `<entity>:<scope>__<id>:` convention, which
is what makes them reachable at all (Phase 1 finding).

Invalidation becomes: `INCR gen:<entity>:<id>`. Old keys are not deleted —
they are simply unreachable and expire by TTL. **No SCAN, no DEL, O(1).**

## 3. Counter ownership — the dependency map

Each cached response embeds the generation of **every entity it depends
on**. Multi-dependency keys embed multiple generations.

| Cache family (Phase 1 label) | Depends on | Generations in key |
|---|---|---|
| `user:user_id__*`, `settings:*` | that user | `user` |
| `courses:*`, `sessions:*`, `topics:*`, `studentcourses:*`, `schools:*`, `coursecategorys:*` (UserCacheMixin) | that user's visible set | `user` |
| `studentsubmissions:*` | that user | `user` |
| `superadmins:*:view__*` | global | `global` (a single `gen:global` counter) |
| `schooladmins:*:view__*` | that user + their school | `user`, `school` |
| `teacheradmins:*:instance_id__<course>:*` | that user + that course | `user`, `course` |
| `dashboards:school_id__*` (the 4 fixed keys) | that school | `school` |
| `assignmentpdf:*` | **unchanged** — owns its own invalidation | — |
| `grading_answer_cache:*` | **unchanged** — content-addressed | — |

**Which mutations bump which counter** (the invalidation contract):

| Mutation | Bumps |
|---|---|
| `CustomUser` / `Settings` save | `gen:user:<id>` — replaces today's global flush. **Superseded for CustomUser (Stage 3 item 7, 2026-09-14):** a change to a viewer-visible field also bumps `sch` of the user's school, the previous school on a move, and, for students, `usr` + `sch` of their courses' teachers. See `docs/evidence/H1_USER_FANOUT_EVIDENCE.md`. |
| `StudentCourse` save/delete | `gen:user:<student>`, `gen:user:<course.teacher>`, `gen:course:<course>`, `gen:school:<teacher.school>` |
| `Course` / `Topic` / `Session` save | `gen:user:<teacher>`, `gen:course:<course>`, `gen:school:<teacher.school>` |
| `Assignment` save | `gen:course:<course>`, `gen:school:<...>` |
| `StudentSubmission` save | `gen:user:<student>`, `gen:course:<...>`, `gen:school:<...>` |
| `School` save | `gen:school:<id>`, `gen:global` |

Bumps are a small fixed set of `INCR`s per mutation (≤4), independent of row
count — which is what makes bulk import O(1) per affected entity rather than
O(rows).

## 4. How tenant isolation is guaranteed

Structurally, not by naming. `INCR gen:user:A` changes exactly one integer;
no key belonging to user B contains that integer, so B's entries remain
reachable. There is no wildcard and no keyspace walk, so there is no
mechanism by which one tenant's mutation can reach another's entries.

This replaces the current situation, where isolation depends on no key
happening to contain a matched substring.

**Adversarial test to write:** bump every counter for tenant A in a loop
while asserting that a fixed set of tenant-B keys is byte-identical
throughout.

## 5. Concurrent mutations

`INCR` is atomic in Redis. Two concurrent mutations on the same entity both
increment; the counter ends at +2 and both readers see a value at least as
new as their own write. No lost update, no lock, no read-modify-write.

The cost of concurrency is an occasional extra generation, which orphans one
more set of entries — harmless.

**Ordering caveat to test:** a request that read generation *n*, then took
600ms to build its response, may `SET` a key at generation *n* after a
mutation bumped to *n+1*. That entry is written to an already-dead key and
is never read. Correct, but wasteful — worth measuring, not fixing.

## 6. Redis unavailable during invalidation

The established principle holds: **a cache failure must never fail a
database write** (`classrooms/signals.py`). A failed `INCR` is logged at
ERROR and swallowed.

Consequence: the generation does not advance, so entries cached before the
outage stay reachable until TTL. That is the same exposure as today's failed
`delete_pattern`, and TTL remains the backstop.

**The one new risk, and it is the most important line in this document:**

> If the counter is lost but cached entries survive, the counter resets to a
> value already used, and **stale entries become live again**.

Counter and cache share a Redis instance, so a full flush or restart loses
both — safe by shared fate. The dangerous case is **partial** loss: eviction
under `maxmemory` pressure could evict a counter (a small, rarely-read key)
while leaving cached entries (larger, frequently-read).

**PRE-CHECK RESOLVED (owner, 2026-09-09): production is `volatile-lru`.**
That is the favourable answer, and it makes the failure mode structurally
impossible **provided counters are written with no TTL**:

* `volatile-lru` evicts only keys that *have* an expiry.
* Verified against Redis directly: `cache.set(key, 1, None)` produces
  `ttl = -1` (no expiry), and `cache.incr()` on it **preserves** `ttl = -1`
  rather than resetting one.
* Every cache entry has a TTL and is therefore evictable; **no counter is**.

So under memory pressure Redis evicts cached data and leaves the counters
that guard it — exactly the right order. The partial-loss scenario that would
revive stale entries cannot occur. Local development is `noeviction`
(verified), which is also safe.

**Two consequences to carry into implementation:**

1. `timeout=None` on every counter write is now **load-bearing, not
   stylistic**. A counter accidentally written with a TTL becomes evictable
   and reintroduces stale revival. This needs a mutation test: give a counter
   a TTL and assert a test fails.
2. Non-expiring counters accumulate — one per user/school/course ever seen,
   and `volatile-lru` cannot reclaim them. They are tiny (key + small
   integer, order of 60–100 bytes), so ~100k entities is single-digit MB, but
   it is unbounded over the system's lifetime and should be monitored. If it
   ever matters, the fix is a sweep of counters for deleted entities, not a
   TTL.

**Implementation note:** django-redis `incr()` raises `ValueError` on a
missing key, so the bump path needs create-or-increment semantics
(`incr`, on failure `set(key, 1, None)`), and that fallback must itself be
race-safe.

## 7. Read racing an increment

A reader may fetch generation *n* microseconds before a writer bumps to
*n+1*, then serve a slightly stale response — a window bounded by the
duration of one request.

This is strictly better than today: the current `delete_pattern` has the same
race with a much wider window (the SCAN is not atomic across the keyspace, so
keys are deleted progressively while readers are repopulating them).

**Not** solved by locking, and locking is not proposed: the window is
sub-request-duration and the data is dashboard aggregates, not authorization
decisions. Access control is enforced at the queryset level on every request
and is never served from cache.

## 8. TTL and orphaned entries

Orphans are entries at a superseded generation. They are unreachable and
expire on their existing TTLs (300s–3600s), so steady-state memory rises by
roughly *(entries written during one TTL window) × (generations per window)*.

Existing TTLs are unchanged by this design. Memory impact must be **measured**
under the bulk-import workload, not estimated — it is an explicit acceptance
criterion (§12).

If measurement shows unacceptable growth, the mitigation is a shorter TTL on
high-churn families, not a redesign.

## 9. Deployment and migration

No backfill and no flush required. Old-format keys simply stop being read
(nothing generates their key shape any more) and expire within one TTL —
maximum 60 minutes for the longest-lived family.

Deploy order:

1. Ship counter helpers + bump-on-mutation, with reads still on old keys.
   Counters start advancing; nothing depends on them yet. Reversible.
2. Ship read/write of the new key format, app by app, starting with
   `classrooms` (smallest surface, best test coverage) and finishing with
   `dashboard` (24 of the 35 families).
3. Remove the wildcard `delete_pattern` receivers once no family depends on
   them — the coverage test in §13 is the gate for "no family depends on
   them."

Steps 1 and 2 can run in production simultaneously: old and new key shapes
coexist harmlessly, at the cost of a transient cache-hit-rate dip.

## 10. Rollback

Per stage, and cheap at every stage:

* Stage 1: revert; counters are ignored and go stale. No user impact.
* Stage 2: revert the app's read/write change; it returns to the old key
  shape and the old receivers still work because they have not been removed.
  Cost is one TTL window of cache misses.
* Stage 3 (receivers removed) is the only one-way door. It must not ship in
  the same release as stage 2, and requires the §13 proof first.

**Rollback is why stage 3 is separate.** Removing the wildcard receivers
while any family still relies on them reintroduces under-invalidation, which
is the failure this project exists to fix.

## 11. Every one of the 35 families

§3 maps all 35. The two self-managing caches keep their mechanisms with a
recorded rationale (PDF clears its own prefix; grading cache is
content-addressed — a changed input is a different key). The four orphaned
dashboard families are brought in via §2's rename. The remaining 29 map onto
`user`, `school`, `course` or `global` counters.

## 12. The four under-invalidated dashboard caches

They are fixed **by the rename in §2**, not by adding a pattern. Moving them
onto `dashboards:school_id__<id>:g<school_gen>:…` makes a `gen:school` bump
invalidate them, closing a live freshness bug (stale up to 15 minutes)
alongside the performance work.

`test_the_four_dashboard_keys_are_ttl_only` must be **inverted** as part of
this change — it currently asserts they are unreachable.

## 13. Proving every read site participates

The risk that would make this design fail in practice: one read site left on
the old key shape, serving a permanently stale response that no bump can
reach.

Three defences, in order of strength:

1. **One constructor.** All key building goes through a single helper that
   takes the entity ids and returns the versioned key. A site that does not
   call it cannot produce a valid key.
2. **Inverted coverage test.** `tests_cache_invalidation_coverage.py`
   currently asserts which families are unreachable. After implementation it
   asserts the opposite: **every** family is reachable by its owning
   mechanism, with the list of 35 as the checklist.
3. **A grep gate.** A test that fails if `cache.set(` appears outside the
   approved helpers, so a new cache site cannot be added without joining the
   scheme. This is the only defence that catches families added *after* this
   work.

---

## Cache stampede — evaluated, not assumed

### Measured, on real gunicorn + Postgres + Redis

Endpoint: `school-admin/dashboard/summary` — the most expensive cached
response (22 queries; **30ms SQL but 577ms wall**, i.e. CPU-bound in Python
aggregation, not DB-bound).

| Scenario | Wall | p50 | max |
|---|---|---|---|
| Warm cache hit | 5ms | 5ms | 5ms |
| Cold, 1 request | 233ms | 233ms | 233ms |
| Cold, 5 simultaneous | 1,439ms | 836ms | 1,438ms |
| Cold, 10 simultaneous | 1,108ms | 907ms | 1,107ms |
| Cold, 20 simultaneous | 2,347ms | 1,573ms | 2,341ms |

**A cache hit is 47× cheaper than a rebuild.** At 20 concurrent cold
requests, latency degrades ~10× versus a single rebuild. Every response was
200 — it degrades, it does not fail.

Two distinct effects are present and worth separating:

* **Duplicate work on one key** — the 20 requests shared 5 users, so ~4
  requests per key each performed the same full rebuild. This is what
  single-flight fixes.
* **Aggregate load** — many distinct keys rebuilding at once saturates
  workers. This is what jitter and capacity fix.

### Fan-out per invalidation, which bounds the risk

Under Option D, a bump invalidates one entity's family. Production
composition (read earlier this session; **DNS failed on re-check, so treat as
unverified**): 122 students, 81 teachers, **12 school admins**, 1 superadmin.

So a `gen:school` bump invalidates the summaries of the **one or two admins
of that school** — not a herd. The realistic stampede exposure is not
per-mutation; it is **cold start** (deploy, Redis restart, mass TTL expiry),
where many families miss at once.

That measurement is the argument against over-engineering here.

### Options

| | Jittered TTL | Lock-and-rebuild (single-flight) | Stale-while-revalidate |
|---|---|---|---|
| Correctness | No effect | No effect | **Serves knowingly stale data** |
| Latency (hit) | unchanged | unchanged | unchanged |
| Latency (miss) | unchanged | **waiters block on the leader** | **best — instant stale response** |
| Redis load | none | +1 lock key per rebuild | +1 key per entry |
| DB/CPU load | unchanged | **best — one rebuild, not N** | best, rebuild is async |
| Concurrency | none | lock contention; needs a timeout | background task per stale key |
| Failure behaviour | none to fail | **lock left behind if a worker dies → must expire** | stale served indefinitely if refresh keeps failing |
| Stale tolerance | none needed | none needed | must be explicitly accepted |
| Lock recovery | n/a | TTL on the lock; waiters must not wait forever | n/a |
| UX | occasional slow request | one slow request, others wait | fastest, possibly wrong |
| Complexity | trivial | moderate | **highest** — needs a background refresh path |

### Recommendation

**Jitter + single-flight on the expensive families only. Not
stale-while-revalidate.**

Reasoning from the measurements, not from fashion:

* **Single-flight** is justified specifically because the measurement shows
  duplicate rebuilds of the *same* key (4 requests × 233ms of identical
  work). It converts N rebuilds into 1. Apply it where a rebuild exceeds a
  threshold (~200ms measured) — today that is `schooladmin summary` and
  little else; `department_overview` rebuilds in 7ms and would only gain lock
  overhead.
* **Jitter** (±10% on TTL) is nearly free and addresses the cold-start case
  the fan-out analysis identifies as the *real* exposure. It does nothing for
  bump-driven invalidation, which is inherently synchronised — stated so the
  limit is understood.
* **Stale-while-revalidate is rejected** for now: this project exists to fix
  a freshness bug, and deliberately serving known-stale data on the same
  endpoints is in tension with that. It is also the most complex option. It
  should be reconsidered only if measurement after the above shows the
  remaining stampede is unacceptable — with an explicit product decision on
  tolerated staleness per endpoint.

**Lock design if approved:** `SET NX` on `lock:rebuild:<cache key>` with a
TTL of 2× the measured p99 rebuild; waiters poll briefly then **fall through
to rebuilding themselves** rather than blocking indefinitely — a lock failure
must degrade to today's behaviour, never to an error or an unbounded wait.

---

## Acceptance criteria and measurement plan

Everything below is measured against the **immutable Phase 1 baseline** in
`docs/HARDENING_BACKLOG.md`.

### Over-invalidation

| Criterion | How proven |
|---|---|
| No global flush from an unrelated tenant's mutation | Seed tenant-B keys; run tenant-A mutations and a 2,000-row import; assert B's keys byte-identical |
| No keyspace SCAN in ordinary invalidation | `INFO commandstats` delta: `scan` calls == 0 for a mutation |
| No collateral deletion | `tests_cache_collateral_damage.py` unchanged and passing; plus the `*billing*` mutation still fails it |
| No redundant invalidation | Each mutation performs ≤4 `INCR`s; assert exact command counts |

### Under-invalidation

| Criterion | How proven |
|---|---|
| Every family has a documented invalidation path | §3 table + inverted coverage test |
| Every relevant mutation invalidates every affected response | Per-family test: cache, mutate, assert the response changed |
| The 4 dashboard gaps are fixed | Invert `test_the_four_dashboard_keys_are_ttl_only` |
| No family stale due to naming | The grep gate (§13.3) |
| Cross-tenant isolation | Adversarial suite (§4) |

### Concurrency and reliability

Concurrent mutations safe (real threads, barrier-synchronised, assert final
generation == number of mutations); concurrent rebuilds controlled (assert
one rebuild, not N, under single-flight); Redis down / slow / recovering
(writes still succeed; no stale revival after recovery — §6); no lock left
behind (kill a holder mid-rebuild, assert the lock expires and progress
resumes); existing functionality green during the migration window (both key
shapes live).

### Performance — re-run the exact baseline

| Baseline | Target |
|---|---|
| 29 SCAN/row | **0** |
| 432 Redis commands/row | < 10 |
| 10,000 keys destroyed by a 25-row import | 0 keys destroyed outside the affected entities |
| ~58,000 SCANs / ~864,000 commands for 2,000 rows | < 100 invalidation commands total |
| 67 pattern-matches per sweep | n/a — no patterns |

Plus: multi-tenant concurrent load through gunicorn, the 2,000-row import
(not the 25-row case that exposed the problem), latency percentiles, DB
query counts, Redis memory before/after (orphan growth, §8), and worker CPU.

### PIPELINED `bump_many` — implemented and measured (owner-approved Option 1)

Sequential and pipelined paths measured against real Redis, counters
pre-warmed so this is steady-state cost, not first-creation cost.

**Commands per single bump** (measured, not assumed):

| Path | Redis commands | Round trips |
|---|---|---|
| Sequential (`cache.incr`) | **3** — `EXISTS` + `INCRBY` + `EVAL` (django-redis wraps incr in a Lua script) | 1 per counter |
| Pipelined (`SET NX` + `INCRBY`) | **2** | **1 per batch** |

Pipelining is therefore better on *both* axes: fewer commands (2N vs 3N) and
one round trip instead of N.

**Scaling, measured on loopback Redis:**

| Existing users | Sequential | Pipelined | Speedup |
|---|---|---|---|
| 1 | 0.1ms / 3 cmds | 0.1ms / 2 cmds | 1.1× |
| 10 | 1.2ms / 30 | 0.7ms / 20 | 1.8× |
| 100 | 10.2ms / 300 | 4.8ms / 200 | 2.1× |
| 500 | 36.7ms / 1,500 | 10.2ms / 1,000 | 3.6× |
| 2,000 | 159.4ms / 6,000 | 43.7ms / 4,000 | 3.6× |
| 5,000 | 350.1ms / 15,000 | 206.9ms / 10,000 | 1.7× |
| 10,000 | 856.9ms / 30,000 | 323.9ms / 20,000 | 2.6× |

**Read this table with its caveat.** The measured speedup is only 1.7–3.6×
because **loopback RTT is 0.048ms** (median PING over 500 samples) — there is
almost no round-trip cost here for pipelining to remove. These numbers
understate the production benefit, and it would be misleading to quote them
as the result.

**Projection to a remote managed Redis** — the round trips are measured, the
per-RTT cost is assumed at a typical same-region 1ms:

| Existing users | Sequential (N round trips) | Pipelined (1 round trip) |
|---|---|---|
| 500 | ~500ms of pure network | ~1ms |
| 2,000 | ~2,000ms | ~1ms |
| 10,000 | ~10,000ms | ~1ms |

This is a **projection, not a measurement** — it must be re-measured against
the real production Redis before the H-1 close report claims it. What *is*
measured is the round-trip count, which is the architectural property:
sequential is O(N) round trips, pipelined is O(1).

### ACCEPTANCE CRITERION — restated (owner-approved Option 2)

The `< 100 commands` target is withdrawn: it was only ever achievable for the
easy case, and preserving it would have required skipping legitimate
per-student invalidation (Option 3, explicitly rejected — that trades a
correctness regression for a cosmetic number).

> **O(1) in shared/entity-level invalidation, O(affected existing users) for
> legitimate per-user invalidation, zero SCANs, and zero collateral key
> destruction.**

Verified against this criterion:

| Clause | Evidence |
|---|---|
| O(1) shared invalidation | 10 commands flat from 1 to 2,000-row imports of new students |
| O(affected existing users) | 2 commands per existing student, in **one** round trip |
| Zero SCANs | asserted by `test_bumping_never_issues_a_keyspace_scan` and measured 0 at every volume |
| Zero collateral destruction | 0 keys destroyed in every scenario, against 10,000 destroyed by the 25-row baseline |

## STAGE 2 — read-site migration: coverage matrix

**Source of truth for stage 3.** A family is **DONE** only when the whole
chain is proved by test on real Redis + Postgres:

    mutation -> signal/service -> generation bump -> subsequent read
             -> new generation key -> FRESH DATA

Freshness is asserted on the **response body** — the numbers a user reads.
Asserting that a counter incremented proves the bump fired, not that the
staleness is gone; only the second is the bug.

### ACCEPTANCE CRITERION FOR EVERY REMAINING FAMILY (owner, 2026-09-10)

> **A migrated family is DONE only when its freshness is independently
> proven with the legacy invalidation mechanism DISABLED.**

Not negotiable per family, and it applies retroactively: families already
marked DONE were re-proved under `LegacyDisabledFreshnessTests`.

The rule exists because dual-running silently does the new mechanism's work
(see below). A test that passes while both mechanisms run is evidence about
the *pair*, not about the generation graph — and stage 3 removes one half of
that pair.

**Required per family, before the row may read DONE:**

1. mutation actually applied (verify by checksum/content, not by the script
   reporting success);
2. every mutant restoration verified by checksum;
3. freshness asserted on the **response body**, not on a counter;
4. legacy invalidation **disabled** for the decisive freshness test;
5. the relevant tenant/entity boundary asserted (another tenant unaffected);
6. genuinely redundant links recorded **as redundant**, with the covering
   path named — never counted as independently load-bearing.

### DASHBOARD-WIDE LEGACY-DISABLED PASS (required after families 15-29)

Per-family tests are necessary but not sufficient: 33 isolated proofs do not
show that the dashboard as a whole behaves. After 15-29 are migrated, one
combined pass must:

* populate **every** migrated dashboard cache;
* disable legacy wildcard invalidation;
* perform each relevant mutation;
* verify every **affected** response changes;
* verify unrelated **tenants'** responses are byte-identical;
* verify unrelated **families** are not needlessly invalidated (over-
  invalidation would pass a freshness test while reintroducing the original
  performance bug);
* re-enable the legacy mechanism and verify generation counters survive all
  16 live patterns (the collision test).

This is the strongest available evidence short of production, and it is a
precondition for stage 3.

### DUAL-RUNNING MASKS THE PROOF — and what "DONE" therefore requires

Found while mutation-testing families 30-33, and it changes the evidence
standard for the rest of stage 2.

The migrated dashboard keys are named `dashboards:school_id__<id>:...`,
which **contains the substring "school"**. The legacy
`delete_pattern("*school*")` — fired by `clear_user_cache` and
`clear_course_cache` — therefore still sweeps them. So a freshness test that
passes while both mechanisms run does **not** tell you which one refreshed
the data.

Proved directly: with the USER generation deliberately removed from the
teacher-detail key, a teacher rename **still** refreshed the response,
because the legacy sweep had deleted the entry. The mutation survived, the
test passed, and nothing about the new mechanism had been demonstrated.

Two consequences, both now applied:

1. **A family is DONE only when its freshness is proved with the legacy
   mechanism DISABLED.** `LegacyDisabledFreshnessTests` patches
   `delete_cache_patterns` to a no-op in all four signal modules (patching
   one leaves the others live and silently restores the masking) and
   requires the generation counters to carry the guarantee alone. It carries
   its own guard — `test_the_legacy_mechanism_really_is_disabled` — because
   a patch that missed would restore the masking invisibly.
2. **This is also the stage-3 rehearsal.** Disabling the wildcards is
   exactly the state stage 3 creates permanently, so these tests are the
   de-risking evidence for that release, not only proof for this one.

Note the contrast that exposed it: mutations driven by an *assignment* save
were caught immediately, because `clear_assignment_cache` does **not**
include `*school*`. Only the user- and course-driven paths were masked. The
masking was therefore partial and would have been easy to miss.

### Migrated (14 of 33 applicable)

| # | Cache key format | Read path | Depends on | Mutation sources | Gen scope | Bump location | Freshness test | Adversarial test | Failure behaviour | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-2 | `courses:user_id__<u>:{query\|instance_id}__*:g.usr=<n>` | `UserCacheMixin.list/retrieve` | user | Course, Topic, Session, StudentCourse | `usr` | `classrooms/signals` | `MigratedFamiliesTests` rename/add/delete | other user's key unchanged | bump swallowed, TTL backstop | **DONE** |
| 3 | `studentcourses:user_id__<u>:*:g.usr=<n>` | `UserCacheMixin` | user | StudentCourse save/delete | `usr` | `clear_student_course_cache` | via mixin chain | cross-user | as above | **DONE** |
| 4 | `sessions:user_id__<u>:*:g.usr=<n>` | `UserCacheMixin` | user | Session save | `usr` | `clear_session_cache` | via mixin chain | cross-user | as above | **DONE** |
| 5 | `topics:user_id__<u>:*:g.usr=<n>` | `UserCacheMixin` | user | Topic save | `usr` | `clear_topic_cache` | via mixin chain | cross-user | as above | **DONE** |
| 6 | `schools:user_id__<u>:*:g.usr=<n>` | `UserCacheMixin` | user | School save | `usr` | `clear_school_cache` | via mixin chain | cross-user | as above | **DONE** |
| 7 | `coursecategorys:user_id__<u>:*:g.usr=<n>` | `UserCacheMixin` | user | CourseCategory save | `usr` | — (viewset unrouted, H-6) | via mixin chain | cross-user | as above | **DONE** |
| 8 | `studentsubmissions:user_id__<u>:query__*:g.usr=<n>` | `UserCacheMixin` | user | StudentSubmission | `usr` | `students/signals` | via mixin chain | cross-user | as above | **DONE** |
| 9 | `customusers:user_id__<u>:*:g.usr=<n>` | `UserCacheMixin` | user | CustomUser save | `usr` | `users/signals` | `test_saving_a_user_bumps_only_that_user` | asserts others UNCHANGED | as above | **DONE** |
| 10 | `settingss:user_id__<u>:*:g.usr=<n>` | `UserCacheMixin` | user | Settings save | `usr` | `users/signals` | via mixin chain | cross-user | as above | **DONE** |
| 30 | `dashboards:school_id__<s>:view__teacher_performance:<pg>:<sz>:g.sch=<n>` | `dashboard:1848` | school | Assignment, StudentSubmission, CustomUser, Course | `sch` | `assignments`/`students`/`classrooms` signals | new assignment changes the row; new teacher appears | cross-school gen unchanged | mutation survives bump failure | **DONE** |
| 31 | `dashboards:school_id__<s>:view__teacher_detail:<t>:g.sch=<n>.usr=<m>` | `dashboard:1964` | school **+ teacher** | as 30 | `sch`,`usr` | as 30 | assignment AND teacher-rename both refresh | cross-school | as above | **DONE** |
| 32 | `dashboards:school_id__<s>:view__assignment_activity:<year>:g.sch=<n>` | `dashboard:2606` | school | Assignment save/delete | `sch` | `_bump_assignment_scopes` | create and delete both refresh | cross-school | as above | **DONE** |
| 33 | `dashboards:school_id__<s>:view__department_overview:g.sch=<n>` | `dashboard:2712` | school | Course, StudentCourse, final_grade | `sch` | `classrooms/signals` | new course; grade change moves avg_grade | cross-school cache byte-identical | as above | **DONE** |

Families **30-33 were the four that NO invalidation mechanism reached** —
stale up to 300-900s. Migrating them closes a live production correctness
bug, which is why they were taken ahead of the lower-risk families.

## FAMILIES 15-22 — dependency review (code-derived, BEFORE implementation)

Owner decision: **(b) bounded-staleness caching**, with an explicit
instruction not to force all eight into one policy. This section is the
dependency review that must precede any cache-key change.

Every row below comes from reading the queries in each cache-miss branch,
not from the family name — the rule established after three matrix errors.

| # | Dashboard | Objects actually queried | Writers | Churn |
|---|---|---|---|---|
| 15 | adoption | CustomUser, Assignment, Course | signup, assignment create, course create | medium |
| 16 | usage | Assignment, StudentSubmission, Course, CustomUser | **every submission** | **high** |
| 17 | ai_performance | Assignment, StudentSubmission | **every grading** | **high** |
| 18 | scaling_signals | CustomUser, School | signup, school create | low |
| 19 | schools | **School only** | school create/edit | **very low** |
| 20 | teachers | **CustomUser only** | signup, profile edit | medium |
| 21 | students | Course, CustomUser, StudentCourse, StudentSubmission | enrolment, **every submission** | **high** |
| 22 | concurrency | **none — reads the Redis presence SET** | every authenticated request | **continuous** |

### Two findings that change the policy

**1. Families 19 and 20 do NOT need bounded staleness at all.**

They were lumped in with the rest because they are labelled "superadmin".
But 19 reads *only* `School` and 20 reads *only* `CustomUser`. Those are
low-churn tables. Given an entity-class counter — "any school changed",
"any user changed" — both can be **precisely invalidated AND genuinely
cached**, with zero staleness. A global counter bumped by every submission
would destroy their cacheability for no correctness benefit.

This is exactly the case the owner anticipated: a coarse policy applied to
all eight would have been wrong for two of them.

**2. Family 22 is not an analytics dashboard at all.**

`concurrency` reads the Redis presence set (`users/services.py` ->
`cache.smembers(ONLINE_SET_KEY)`) and touches **no database object**. Its
data changes on every authenticated request, and presence is defined over a
300-second window.

It therefore has no DB mutation to hang a generation off, and a
**900-second TTL on a live concurrency figure is already questionable** — the
number can be three windows out of date. This is the "real-time operational
dashboard" the owner warned might need different treatment; bounded
staleness is the wrong frame for it entirely. Proposed separately below
rather than folded into the analytics policy.

### MEASURED (10 schools / 360 users / 240 courses / 1,920 assignments / 7,200 enrolments / 17,464 submissions)

Real Postgres, real Redis, through the live endpoints.

| # | Dashboard | cold | warm | queries | sql | bytes | cache worth |
|---|---|---|---|---|---|---|---|
| 15 | adoption | 45ms* | 6.6ms | 10 | 19ms | 311 | 7x |
| 16 | usage | 181ms | 5.7ms | 9 | 122ms | 356 | **32x** |
| 17 | ai_performance | 38ms | 6.4ms | 10 | 19ms | 428 | 6x |
| 18 | scaling_signals | 59ms | 6.5ms | 10 | 39ms | 343 | 9x |
| 19 | schools | 207ms | 6.7ms | 5 | 191ms | 1,662 | **31x** |
| 20 | teachers | 875ms | 9.2ms | 45 | 786ms | 4,257 | **95x** |
| 21 | students | **1,938ms** | 5.6ms | **487** | 433ms | 413 | **199x** |
| 22 | concurrency | 18ms | 6.9ms | 5 | 4ms | 277 | 3x |

\* an earlier run measured adoption at 1,166ms; re-measured it is 45ms. The
first figure was first-request warm-up (imports), not a real cost. Corrected
rather than quoted.

### THE CONCLUSION IS NOT A TTL POLICY — IT IS THAT (a) vs (b) BARELY MATTERS

Put the churn and the TTL side by side:

* mutations touching a global counter: **~6.5/day** (measured in production)
* a 900-second TTL expires: **96/day**

**The TTL causes ~15x more rebuilds than every mutation in the system
combined.** Choosing (b) over (a) buys roughly a **7% reduction in misses** —
about 6.5 rebuilds a day avoided. That is not worth a staleness contract.

### A better answer than either (a) or (b)

The measurements point somewhere neither option anticipated.

**Once generation versioning guarantees freshness, the TTL stops being a
correctness mechanism and becomes purely a memory knob.** Today the TTL is
doing double duty: it bounds staleness *because nothing else does*. With
versioning in place, staleness is impossible regardless of TTL — so the TTL
can be raised.

| TTL | rebuilds/day (expiry + mutation) | staleness |
|---|---|---|
| 900s (today) | 96 + 6.5 = **102.5** | up to 900s |
| 3600s | 24 + 6.5 = **30.5** | **none** (versioned) |
| 86400s | 1 + 6.5 = **7.5** | **none** (versioned) |

Raising the TTL to 24 hours cuts rebuilds **~14x** *and* removes staleness
entirely. That is strictly better than (b), which accepts staleness to chase
a 7% gain.

**Recommendation, per group:**

* **19, 20** — entity-class counters (`any:school`, `any:user`; their
  dependencies are a single low-churn table each) + 24h TTL. Precise, fresh,
  ~99% hit rate.
* **15, 16, 17, 18, 21** — global counter + 24h TTL. Zero staleness,
  ~7.5 rebuilds/day.
* **22** — **do not version, and consider not caching.** It reads the Redis
  presence set, costs 18ms cold vs 6.9ms warm (3x), and presence is defined
  over a 300s window. A 900s TTL on a live concurrency figure is worse than
  useless; caching saves ~11ms.

This needs owner sign-off because it **supersedes decision (b)**: it proposes
no staleness at all, rather than bounded staleness.

### SEPARATE DEFECT FOUND: family 21 has a 480-query N+1

`super-admin/dashboard/students` issues **487 queries** for a 413-byte
response — `240x COUNT(*) assignments` + `240x COUNT(*) studentcourse`, one
pair per course. 1,938ms wall, of which 1,505ms is Python.

No caching strategy fixes this; caching only hides it on the one request in
a hundred that misses. It scales linearly with course count, so a 100-school
tenant would see ~10x. Tracked separately as **H-10** — it is a performance
defect in `dashboard`, not a cache-invalidation question.

### (superseded) Emerging shape (to be confirmed by measurement)

* **19, 20** — dedicated entity-class counters. Precise, fresh, cacheable.
* **15, 18** — medium/low churn; candidates for entity-class counters too.
* **16, 17, 21** — genuinely depend on submission-rate data; these are the
  real bounded-staleness candidates.
* **22** — not a generation problem. Either shorten the TTL to match the
  presence window or stop caching it; decide on measurement.

Measurements (rebuild latency, warm latency, query count/time, response
size, mutation frequency, Redis ops, hit rate at several TTLs, worst-case
staleness, key footprint) follow before any policy is fixed.


### (superseded) original framing: a tension to decide, not to silently resolve

Investigated before implementing. All eight superadmin dashboards aggregate
across **every school**:

| Family | Aggregates over |
|---|---|
| adoption | Assignment, CustomUser |
| usage | Assignment, Course, CustomUser, StudentSubmission |
| ai_performance | Assignment, StudentSubmission |
| scaling_signals | CustomUser, School |
| schools | School |
| teachers | CustomUser |
| students | Course, CustomUser, StudentCourse, StudentSubmission |
| concurrency | presence data |

So their honest dependency is "essentially every mutation in the system",
and `gen:global` would have to be bumped by `CustomUser`, `Assignment`,
`StudentSubmission` and `StudentCourse` saves — none of which bump it today.

**The tension:** a counter bumped by every write is a counter that never
lets these responses cache. Their 900-second TTL would become decorative.

**But this is NOT a regression, and that is the key measurement.** Every one
of those mutations *already* invalidates all eight, because `*superadmin*` is
swept by six receivers today — `clear_school_cache`, `clear_session_cache`,
`clear_course_cache`, `clear_student_course_cache`, `clear_topic_cache`,
`clear_student_submission_cache`, plus `clear_user_cache` and
`clear_assignment_cache`. These dashboards are effectively uncached in
production **right now**.

**Decision required (owner), because it is a staleness/product trade, not an
engineering one:**

* **(a) Faithful migration** — bump `global` on every relevant mutation.
  Behaviour identical to today, no caching benefit for superadmin views, no
  new staleness. This is what will be implemented unless directed otherwise,
  because a migration should not quietly change semantics.
* **(b) Coarser dependency** — bump `global` only on School/CustomUser
  create+delete, and let counts lag by up to the TTL (15 min). These are
  internal analytics dashboards, so bounded staleness may be perfectly
  acceptable — and it would make them genuinely cached for the first time.

Option (b) is a real performance opportunity precisely because these are the
expensive aggregate endpoints, but it trades freshness for it, so it is
recorded here rather than chosen unilaterally.

### FAMILY 24 — a dependency the matrix never had

Re-reading the code before implementing 23-29 (per the "derive dependencies
from code, never from the family name" rule) found a dependency that was
missing from the matrix entirely, not merely mis-labelled.

`schooladmins:*:view__at_risk_trend` reads **`SchoolAtRiskSnapshot`**, which
is written **once per day by a Celery task** (`dashboard/tasks.py`, via
`update_or_create`) and by no request path at all. `dashboard` had **no
signals module**, so nothing could invalidate this chart when its data
changed.

Consequence before the fix: the at-risk trend served stale data until its
**3600-second TTL** expired — the longest stale window of any family in the
project, on a chart whose entire purpose is showing change over time.

Fixed by creating `dashboard/signals.py` with a `SchoolAtRiskSnapshot`
receiver that bumps only `sch`. Written as a receiver rather than as a bump
inside the task on purpose: the task is the only writer *today*, and a
receiver keeps that from being load-bearing — a backfill, a management
command or an admin edit would otherwise write snapshots that never
invalidate the chart.

Four tests pin it: creating a snapshot bumps the school, updating one bumps
it again, another school's snapshot does **not** bump this one, and a
snapshot bumps **no user** generation (bumping users would be
over-invalidation dressed up as safety).

**Process note.** This is the second dependency error found by reading code
rather than names — after rows 26-29 were found to key on a *session* and an
*assignment* rather than a course. Both were in a matrix that had already
been reviewed. Families 15-22 must get the same treatment before they are
implemented.

### Families 11-14 — the four bespoke read sites (DONE)

Not served by `UserCacheMixin`; each builds its own key in its own view, so
each dependency was read off the code.

| # | Key format | Read path | Depends on | Gen scope | Freshness test | Status |
|---|---|---|---|---|---|---|
| 11 | `courses:user_id__<u>:g.usr=<n>.global=<m>` | `classrooms` my_courses | that student + teacher-owned course content | `usr`, **`global`** | enrolment appears; a TEACHER's rename reaches the student; withdrawal empties it | **DONE** |
| 12 | `studentsubmissions:user_id__<u>:instance_id__<s>:g.usr=<n>` | `students/views.py` | that submission only | `usr` | a regrade is visible; an assignment retitle provably does **not** change it | **DONE** |
| 13 | `user:user_id__<u>:g.usr=<n>` | `users/views.py` | that user's own row | `usr` | a profile edit is visible; another user's edit does **not** invalidate it | **DONE** |
| 14 | `settings:user_id__<u>:view__my_settings:g.usr=<n>` | `users/views.py` | that user's Settings | `usr` | a settings toggle is visible | **DONE** |

**Family 11 carries `global` deliberately, on measurement.** Its payload
serialises TEACHER-owned course names, topics and assignments, and a
teacher's edit bumps the *teacher's* generation, not their students'. The
precise alternative — bumping every enrolled student on a course edit (~30
pipelined INCRs) — was rejected because `global` moves ~6.5 times/day in
production while this key's 5-minute TTL expires 288 times/day, so the extra
invalidation is ~2% of misses. The test asserts the consequence that matters:
a teacher's rename reaches the student.

**Family 12 was CORRECTED by a test, and it is the third scope error of this
kind.** `global` was added here first on the same reasoning as family 11 —
that the payload renders the teacher's assignment. It does not: `assignment`
is serialised as a bare UUID, and `raw_input` is a snapshot materialised once
on first GET and persisted on the submission row. A retitle provably changes
nothing. The scope is now `usr` alone, and the test that disproved the
assumption is kept, asserting the payload is **unchanged** by a retitle — so
if the view ever starts live-rendering, it fails and forces the scope back.

Running total of scope errors caught by testing rather than review:
`crs` on families 27/29 (did nothing), family 24's missing
`SchoolAtRiskSnapshot` dependency, and now `global` on family 12
(unnecessary). All three were in a matrix that had already been reviewed.

### Not applicable (2, recorded not migrated)

| # | Family | Why it is N/A |
|---|---|---|
| 34 | `assignmentpdf:<v>:<assignment>:*` | Owns its own invalidation — `assignments/pdf_cache.py` clears its exact prefix, deliberately so that saving one assignment cannot discard every other assignment's PDFs. |
| 35 | `grading_answer_cache:<digest>` | Content-addressed: the key IS a digest of the input, so changed input means a different key and there is nothing to invalidate. |

**Progress: 33 of 33 applicable families DONE (100%), 2 N/A.**

Stage 2's migration is complete. Stage 3 remains BLOCKED on work that is
not family migration:

1. the **dashboard-wide legacy-disabled pass** (33 isolated proofs are not
   the same as the dashboard behaving as a whole): **DONE**, see below;
2. **stampede protection**: jitter + single-flight, approved but not built.
   **OPEN. Owner decision (2026-09-13): fix H-10 first, then re-measure.**
   Broad stampede protection must NOT be built from the 200ms threshold
   alone. Most school and teacher dashboards are under it at the tested
   size, and the clearly expensive superadmin dashboards are dominated by
   the H-10 N+1. After H-10, scope is decided per operation from three
   things: measured rebuild cost, concurrency behaviour (how many
   simultaneous viewers the page realistically has), and
   production-representative scale;
3. **H-9** Redis test isolation: **FIXED**, see `HARDENING_BACKLOG.md` H-9;
4. **H-2** (13 leaked test DB sessions): **FIXED on the working tree**, see
   `docs/evidence/H2_TEST_TEARDOWN_EVIDENCE.md`;
5. a **clean cross-app regression** from the repaired tree: **DONE.** Three
   consecutive full runs (gate3/4/5) of
   `AutoGrader classrooms users students assignments dashboard`, each
   1,804 tests OK (13 skipped), exit 0, 0 leftover sessions and DBs (same
   evidence file, §4). These are dirty-tree runs, so they do not replace item 6;
6. the **repository-wide release gate from the committed tree**: **OPEN.**
   The gate ran on committed `1373eae` and PASSED: 3,859 tests OK, exit 0,
   clean teardown, 261 migrations from empty, identical fingerprint before
   and after. Evidence: `docs/evidence/H1_H2_RELEASE_GATE_EVIDENCE.md`. It
   cannot close this item, because of item 7: the fix will be a new commit
   that needs its own gate;
7. **NEW BLOCKER (2026-09-14): user-row changes do not reach other users'
   cached views.** A legacy-disabled probe found 16 stale endpoint/mutation
   pairs. Examples: a teacher rename leaves family 30 stale, a student
   rename leaves family 29 and the teacher's course/submission lists stale,
   and a teacher changing school leaves 23/25/30/33 stale.
   `clear_user_cache` bumps only `usr(self)`/`anyusr`/`global`, while those
   views are keyed on the school or on the viewing teacher. Dual-running
   hides it in production. The families involved were marked DONE on proofs
   that never mutated a user row, so their DONE status is qualified until
   this is fixed. Full table:
   `docs/evidence/H1_H2_RELEASE_GATE_EVIDENCE.md` §3.
   **Owner decision (2026-09-14): precise fan-out, no global flush.** A
   change to a viewer-visible CustomUser field invalidates:
   - the user's current school;
   - on a move, the old school as well as the new one;
   - for students, the teachers whose views show them (and those teachers'
     schools).
   It must be proven against real Redis, including isolation, meaning
   unrelated schools and users are not invalidated. H-1 stays OPEN until the
   fix passes the applicable gates and a new committed-tree final gate.

Only after those does removing the legacy wildcards become a reviewable
change.

## STAGE 3, ITEM 1 — dashboard-wide legacy-disabled pass: DONE

`AutoGrader/tests_cache_dashboard_wide.py`, 11 tests, real Redis + real
Postgres. Two tenants, all 13 migrated dashboard views warmed at once, legacy
wildcard invalidation disabled throughout.

**Why this is not 33 family tests repeated.** Each family proof used its own
fixture and exercised one endpoint against one mutation. This suite asserts
three things those cannot:

1. **every affected view refreshes** — assignment activity after a new
   assignment, the students dashboard after an enrolment, the at-risk trend
   after its daily snapshot;
2. **the other tenant is byte-identical** — all six of tenant B's dashboards
   compared after each of tenant A's mutations;
3. **unrelated families are NOT invalidated** — asserted on generations,
   because over-invalidation is invisible to a freshness test. A response
   rebuilt to an identical payload looks fine while the cache was thrown
   away for nothing, and that is precisely the original defect.

The precision assertions are the valuable half:

| Mutation | Must move | Must NOT move |
|---|---|---|
| new assignment | `global`, `sch:A`, `usr:teacherA` | `anysch`, `anyusr`, `sch:B`, `usr:teacherB` |
| new school | `anysch`, `global` | `anyusr`, `sch:A`, `sch:B` |
| new user | `anyusr`, `global` | `anysch`, `sch:A`, `sch:B` |
| three tenant-A mutations | — | `sch:B`, `usr:teacherB`, `usr:adminB` |

A separate class re-enables the legacy mechanism and confirms the counters
survive all 16 live wildcard patterns, and that the dashboards still behave
with **both** mechanisms running — which is the state that actually ships
until Stage 3 removes the wildcards.

### Verification gate

The unchanged 13-point standard:

> implement → real PostgreSQL/Redis → real service/API endpoints → realistic
> load/concurrency → adversarial attack → mutation testing → failure
> simulation → regression testing → before/after measurement → document
> evidence → close

Mutation testing must show that removing each of these fails a test:
generation embedding, per-entity scoping, the bump on each mutation type,
counter-eviction protection, single-flight, and the grep gate.

---

## Open questions for the owner

1. ~~Production Redis `maxmemory-policy`~~ — **RESOLVED: `volatile-lru`.**
   Counters written without a TTL are not evictable; see §6. No longer
   blocking.
2. **Single-flight threshold and design-target scale.** The ~200ms threshold
   is approved. Which families cross it depends on the data size you design
   for, and that is the open decision:
   * **Seed at 24 courses/school** (10 schools, 60 teachers, 300 students).
     No school or teacher family (23–33) crosses 200ms; the slowest is 23
     (summary) at 161ms cold. Superadmin 20 (875ms) and 21 (1,938ms, which
     is mostly the H-10 N+1) cross it; 19 is borderline at 207ms.
   * **Seed at 240 courses/school** (an earlier seed bug put every course in
     one school). 23, 25, 26 and 30 also cross it.
   * **Production (read-only, 2026-09-13).** 122 active students, 45 active
     teachers, 7 active school admins, at most 1 per school, and no school
     with courses. That is below both seeds.

   Fixing H-10 first would change which families qualify, so single-flight
   should be classified against post-H-10 measurements.
   **DECIDED (2026-09-13): H-10 first, then re-measure.** See Stage 3 item 2.
3. **Stage 3 timing** — removing the wildcard receivers is the only
   irreversible step; it should be a separate release after the coverage
   proof.
4. **Staleness tolerance** — only needed if stale-while-revalidate is
   revisited; not required by this design.
5. ~~**How to fan out user-row changes (Stage 3 item 7).**~~ **DECIDED
   (2026-09-14): precise fan-out.** See Stage 3 item 7.
