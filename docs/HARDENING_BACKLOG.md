# Hardening Backlog

Tracked follow-up work carried out of the codebase audit
(`docs/CODEBASE_AUDIT_SECTIONS.md`). Everything here is **work to be done**,
not debt to be admired: each item carries a scope, acceptance criteria, and
the verification evidence required before it can be closed.

An item is closed only when its acceptance criteria are met **and** the
evidence below has been produced and recorded in the item's own notes.
"Tests pass" is not evidence on its own — the audit repeatedly found suites
that passed while the behaviour they claimed to cover was broken.

## Verification standard — the gate, not a wish-list

Set by the owner, 2026-09-09. **No item closes on "the tests pass."** Every
fix is treated as a production-ready change and must be battle-tested against
the actual stack and realistic failure conditions.

The required order is:

> **implement → verify on real infrastructure → stress → attack →
> mutation-test → failure-test → regression-test → measure →
> document evidence → close**

| # | Requirement | Means |
|---|---|---|
| 1 | **Real infrastructure** | Real PostgreSQL and real Redis. Mocks and LocMem are not evidence — a LocMem run of a `delete_pattern` test passes while proving nothing, because LocMem has no `delete_pattern`. |
| 2 | **Real service layer / live endpoints** | Exercised through the actual services and running API (gunicorn), not only the Django test client. |
| 3 | **Functional** | Normal paths, asserted on values and row identity — never on status codes alone. A leaking endpoint returns 200 like a correct one. |
| 4 | **Adversarial / security** | Written from the attacker's side; the test attempts the breach and asserts it failed. |
| 5 | **Concurrency & stress** | Realistic volumes, real threads, barrier-synchronised, multiple tenants operating simultaneously, including large imports. |
| 6 | **Mutation** | Revert or weaken each protection; the relevant tests must fail. Record the counts. An unproven fix is not a fix. |
| 7 | **Failure simulation** | Redis unavailable / slow / recovering, DB contention, retries, partial failures, interrupted operations. |
| 8 | **Integrity properties** | Transactional integrity, idempotency, race-condition safety, recovery behaviour. |
| 9 | **Measurement** | Before/after Redis commands and SCANs, DB queries, latency percentiles, memory, CPU, connection usage. Numbers, not adjectives. |
| 10 | **Targeted attacks** | Cross-tenant leakage, stale data, missed invalidation, collateral deletion, cache stampedes, duplicate operations, authorization bypasses. |
| 11 | **Production-scale cases** | Realistic volumes, not small fixtures. |
| 12 | **Regression + integrated run** | Relevant suites green, then a final full-stack verification. |
| 13 | **Documented evidence** | Acceptance criteria written down BEFORE the work; evidence recorded against them at close. |

Where a requirement genuinely does not apply to an item, the item must
**state so and why** — silence is not an exemption.

## Owners

Owners are assigned by **management**. The "Proposed" column is a suggestion
based on which section the work sits in, not an assignment — it is there to
speed that decision up, not to pre-empt it.

| ID | Item | Priority | Proposed owner | Status |
|---|---|---|---|---|
| H-1 | System-wide cache invalidation architecture | **Highest** | Backend/infra lead | **Stage 2: COMPLETE (33/33 applicable migrated). Stage 3 item 7 (user-row fan-out): FIXED, committed-tree gate passed on `f593be1`. H-1 OVERALL: OPEN.** Stampede-protection scope (after H-10 reaches beta) and wildcard removal remain. |
| H-2 | Full-suite exit code / test DB connection leaks | High | Whoever owns CI | **Closed** — fixed, verified with three consecutive clean full runs and failure/mutation simulation |
| H-3 | `student123!` account remediation | High | Product + backend | Data gathered, deferred by owner |
| H-4 | Duplicated `delete_cache_patterns` implementations | Medium | Folds into H-1 | Not started |
| H-5 | `full_clean()` on the grading hot path | Medium | Section 7 (students) | Not started |
| H-6 | `CourseCategoryViewSet` — unrouted and broken | Medium | Section 3 (classrooms) | Not started |
| H-7 | `direct_add_student` response shape and status code | Low | Section 3 + frontend | Not started |
| H-8 | Test file naming / stray docs | Low | Section 3 | Not started |
| H-9 | Test suite shares one Redis DB (isolation) | High | Whoever owns CI | **FIXED — per-process prefix + prefix-scoped clear(); 4/4 tests pass, two concurrent runs verified** |
| H-10 | `super-admin/dashboard/students` 480-query N+1 | High | Section 8 (dashboard) | **CLOSED (2026-09-14)** — Section 8 remediation merged to beta `2715c64`; strict gate passed there (4,031 OK); query count flat |
| H-11 | Synchronous billed AI calls inside `students` request handlers (`upload`, `grade`, `PATCH raw_input`) | **High - release-blocking** | Section 7 (students) + frontend | Open - tracked here from the §7 review pass, 2026-09-13 |
| H-12 | Commented-out code (flake8 E800) burn-down - 20 files still carved out of the rule | Low | Each file's section owner (register in H-12) | Rule ON since 2026-09-13; `students` and `dashboard` clean; 20 files / 286 hits remain; whole repository in scope (owner decision 2026-09-14) |
| H-13 | Uploads while grading is RUNNING | Medium | Product + Section 7 | **DECIDED 2026-09-14: refuse (409). Implemented and gated in the §7 branch.** |

---

# H-1 — System-wide cache invalidation architecture

> ## STATUS, stated precisely
> **H-1 Stage 2: COMPLETE — 33/33 applicable families migrated.**
> **H-1 overall: OPEN — Stage 3 hardening outstanding.**
>
> The 100% migration figure does **not** mean H-1 is production-complete.
> Stage 3 contains substantive engineering, not paperwork:
>
> 1. dashboard-wide legacy-disabled verification (the dashboard as a system,
>    not 33 isolated family proofs);
> 2. selective stampede protection — jitter + single-flight for the measured
>    expensive families only, no locking on cheap ones;
> 3. **H-9** Redis test isolation;
> 4. **H-2** PostgreSQL test teardown / leaked connections;
> 5. clean cross-app regression from the repaired tree;
> 6. final repository-wide release gate from the committed tree.
>
> Only after all six does removing the legacy wildcards become reviewable.

**Priority: highest. Treat as an architecture task, not a tuning exercise.**

> **Architecture decided (owner, 2026-09-09): Option D — hybrid generation
> versioning.** The detailed design, the measured cache-stampede evaluation,
> and the acceptance/measurement plan are in
> **`docs/H1_CACHE_INVALIDATION_DESIGN.md`**. Its one blocking pre-check —
> production Redis's `maxmemory-policy` — is **RESOLVED: `volatile-lru`**,
> which is the favourable answer: counters written without a TTL are not
> eligible for eviction, so the stale-revival failure mode is structurally
> impossible. See §6 of that document.

## What is actually wrong

Measured against **real Redis** (not LocMem) with a realistic 10,000-key
cache and a real bulk import through the live service layer:

| Measurement | Value |
|---|---|
| SCAN commands per imported row | **29** |
| Total Redis commands per imported row | **432** |
| Cache keys destroyed by a **25-row** import | **10,000 — the entire keyspace** |
| Extrapolated to a 2,000-row import | **~58,000 SCANs, ~864,000 Redis commands** |

The earlier "~22,000 scans" figure quoted during the Section 3 audit was an
underestimate derived from signal counts. The measured figure is worse.

The headline is not the scan count. It is the third row: **a 25-row roster
import flushed every cached entry for every tenant in the system.** Patterns
like `*user*` and `courses:*` match every user's cached page, so invalidation
triggered by one teacher's course discards the cache of every unrelated
school. That converts a routine import into a system-wide cold cache.

### Contributing defects, each independently confirmed

1. **Wildcard patterns are unbounded by tenant.** `*user*`, `*school*`,
   `*course*` match across every user and school. Nothing scopes an
   invalidation to the rows that actually changed.

2. **Patterns overlap heavily.** Static analysis of the four signal modules
   found a single key matched by up to **four** distinct patterns:
   `studentcourses:user_id__1:query__abc` is matched by `*course*`,
   `*studentcourse*`, `*user*` and `studentcourses:*`. Each is a separate
   full keyspace SCAN deleting the same key.

3. **Per-row invalidation.** Every `StudentCourse` save fires a receiver
   clearing ~11 patterns. Bulk paths save row-by-row, so the cost is
   multiplied by the row count. `AutoGrader.cache_utils.batched_cache_invalidation`
   exists precisely to coalesce this and **no bulk path uses it**.

4. **Four separate implementations** of `delete_cache_patterns`
   (`AutoGrader/cache_utils.py`, `classrooms/signals.py`,
   `students/signals.py` via the shared helper, `assignments/signals.py`),
   which have already drifted: only some batch, only some catch Redis
   errors, only some warn on a backend without pattern support. See H-4.

5. **Wildcards can and do hit non-cache functionality.** `KEY_PREFIX="gaplus"`
   keeps Celery broker/result keys safe, but *anything written through the
   Django cache* is in range. This has already caused a live defect:
   `users/middleware.py` carries a 20-line comment explaining that the
   activity heartbeat and presence-set keys are **deliberately named to avoid
   the substring "user"**, because `clear_user_cache`'s `delete_pattern("*user*")`
   was wiping both on every unrelated user save — defeating a write throttle
   and silently zeroing the concurrent-user metric. The current mitigation is
   a naming convention with no enforcement: the next key containing "user"
   reintroduces it. DRF throttle keys (`throttle_<scope>_<ident>`) are in the
   same keyspace and are one scope-name away from the same collision.

6. **Cache stampede is unmitigated.** After a flush, every concurrent user
   misses simultaneously and rebuilds at once. The dashboard and course
   endpoints fan out into many sub-queries per page, so the rebuild is
   expensive precisely when everything requests it together.

7. **Cache and Celery share one Redis.** The same import run issued 25
   `lpush`/`subscribe`/`unsubscribe` pairs (task dispatch) interleaved with
   the 10,000 `del`s. Cache invalidation load and task dispatch contend for
   the same instance.

## Scope

Do the design work before the code work. The instruction stands: **first
establish what must actually be invalidated, then design the strategy.**

1. **Inventory** — every cache write site, key shape, TTL, and every
   invalidation call site with the patterns it clears and the model events
   that trigger it.
2. **Derive the true dependency map** — for each cached response, which
   model changes can actually invalidate it. Most current patterns are far
   wider than this map.
3. **Design** the replacement. Options to evaluate explicitly, with the
   trade-offs recorded:
   - **Key versioning / generation counters** (e.g. a per-user or per-course
     version integer folded into the key) — invalidation becomes an `INCR`,
     O(1), with no SCAN and no collateral damage. Stale entries expire by TTL.
   - **Tag-based invalidation** via sets of keys per entity.
   - **Targeted key deletion** where the key set is enumerable.
   - Keep `delete_pattern` only where a bounded, tenant-scoped prefix makes
     it cheap and safe.
4. **Namespace design** — cache keys must be structurally separated from
   throttles, locks, counters and presence data so that no invalidation can
   reach them by construction, replacing today's naming convention.
5. **Batching** — bulk import/create/update/delete paths must coalesce
   invalidation into one operation.
6. **Stampede control** — decide and implement (staggered TTL/jitter,
   lock-and-rebuild, or serve-stale-while-revalidate).
7. **Consolidate** to one implementation (subsumes H-4).

## Acceptance criteria

- A 2,000-row bulk import issues **O(1) invalidation operations, not O(rows)** —
  target: fewer than 100 Redis commands attributable to invalidation, down
  from ~864,000.
- Invalidation triggered by one tenant **cannot** evict another tenant's
  cached entries. Proven by assertion on surviving keys, not by inspection.
- No invalidation path can delete a throttle, lock, counter, presence or
  Celery key. Proven structurally (namespace separation), not by naming.
- A mutation that changes data **always** invalidates the responses that
  depend on it — no stale read survives a committed mutation.
- Redis unavailable or slow: every database operation still succeeds; the
  system degrades to stale reads, never to failed writes (the principle
  already established in `classrooms/signals.py`).
- Cache rebuild after a flush does not produce a thundering herd — bounded
  concurrent rebuilds under the load harness.
- Exactly one `delete_cache_patterns` implementation remains.

## Required evidence

- **Functional**: cached and uncached responses are byte-identical; every
  mutation type invalidates what it should.
- **Adversarial**: deliberately attempt stale reads, cross-tenant cache
  leakage, incorrect entries, missed invalidations, races between a mutation
  and a concurrent read, throttle/lock interference, and stampedes. Each
  attack asserts it failed.
- **Stress/concurrency**: real Postgres + real Redis; large bulk import
  concurrent with active readers; measured Redis command counts, CPU, memory,
  latency percentiles, and connection counts before and after.
- **Mutation**: removing batching, removing namespace separation, widening a
  pattern, and removing stampede control must each fail specific tests, with
  counts recorded.
- **Live stack**: measured through gunicorn against real Postgres/Redis, as
  the Section 3 load test was — not the Django test client alone.
- **Failure simulation**: Redis down during import; Redis recovering
  mid-import; Redis slow (latency injection); invalidation failing partway.
- **Regression**: full suite green, with before/after counts.

## Phase 1 progress — dependency map (started 2026-09-09)

The redesign cannot begin until it is known what must actually be
invalidated. This is that work; it is **partially complete**.

### Cache write sites, by app

| App | `cache.set` / `get_or_set` sites |
|---|---|
| `dashboard` | **24** |
| `users` | 4 |
| `billing` | 2 |
| `students`, `classrooms`, `assignments`, `ai_processor`, `AutoGrader` | 1 each |

`dashboard` holds the large majority of cached responses (TTLs of 5, 15 and
60 minutes) and is also where the heaviest aggregate queries live — so it is
both the biggest beneficiary of caching and the biggest victim of a flush.
The redesign must start from `dashboard`'s key shapes, not `classrooms`'.

### Key namespaces in use

`courses:`, `schooladmins:`, `studentadmins:`, `superadmins:`,
`teacheradmins:`, `studentsubmissions:`, `settings:`, `user:`, `image_url:`,
plus `UserCacheMixin`'s `<model>s:user_id__<id>:query__<md5>` shape.

Most are already `<entity>:user_id__<id>` — i.e. **the per-user scoping the
patterns then throw away** by matching `*user*` across all of them. A
per-user generation counter would fit the existing key shape with no
restructuring, which makes key-versioning the leading candidate.

### Non-cache keys sharing the keyspace — CONFIRMED BY TEST

`AutoGrader/tests_cache_collateral_damage.py` (9 tests, real Redis) seeds
every non-cache key shape the project writes through the Django cache, fires
each invalidation receiver, and asserts survival:

| Key | Purpose | Consequence if deleted |
|---|---|---|
| `billing:planchange:<user id>` | idempotency lock | a second concurrent billing mutation gets through |
| `billing:license_overage:<sub id>` | idempotency lock | duplicate overage grant |
| `presence:beat:<type>:<id>` | write throttle | activity rows written on every request |
| `presence:online` | presence set | concurrent-user metric silently zeroed |
| `throttle_<scope>_<ident>` | DRF rate limits | rate limit reset |
| `healthcheck` | liveness probe | health check flaps |

**Current result: no live collateral damage — all 9 pass.** The billing
locks and presence keys survive today only because none of their names
contain a substring any pattern matches. That is a naming convention with
nothing enforcing it, which is why these tests now enforce it.

Two findings from this:

* **Mutation-proved load-bearing**: adding a single `"*billing*"` pattern to
  `users/signals.py` destroys **both billing idempotency locks** and the
  suite fails. One careless pattern is all it takes to turn cache
  invalidation into a double-billing bug.
* **A latent defect is pinned**: `throttle_user_<pk>` — DRF's built-in
  `UserRateThrottle` scope — **is** matched by `*user*` and is deleted today.
  It is harmless only because that throttle class is not enabled. A test
  asserts this, so enabling `UserRateThrottle` fails loudly instead of
  silently resetting every user's rate limit on every user save.

### Coverage map — COMPLETE (measured, real Redis)

Every cache-key format the application writes, tested against every wildcard
pattern the signal receivers fire, with **Redis itself** doing the glob
matching (`AutoGrader/tests_cache_invalidation_coverage.py`, 5 tests).

| Metric | Value |
|---|---|
| Distinct cache-key formats | **35** |
| Never reached by any pattern | **6** |
| Reached by more than one pattern (redundant) | **27** |
| Total pattern-matches for 35 keys | **67** (~1.9 scans per key, per invalidation event) |
| Keys matched by `*user*` alone | **29 of 35** |

**The single most important number is the last one.** `*user*` matches 29 of
the 35 key families, so `users/signals.py::clear_user_cache` — which fires on
**every `CustomUser` and `Settings` save** — is a de-facto full cache flush.
User saves are among the commonest writes in the system (registration,
profile edit, settings change, and the `create_default_settings_and_wallet`
signal chain each trigger one). The 25-row-import result is not a bulk-import
quirk; it is what happens on ordinary traffic.

Worst redundancy, confirmed on a key the app really writes:
`studentcourses:user_id__<id>:query__<md5>` is matched by `*user*`,
`*course*`, `*studentcourse*` **and** `studentcourses:*` — four full keyspace
SCANs, three of them deleting a key an earlier one already deleted.

### UNDER-invalidation — the finding this sweep existed to catch

Six key formats are reached by **no pattern at all**. Two are fine; four are
a correctness bug.

**Genuine gaps — four `dashboard` responses that no mutation ever clears:**

| Key | TTL | Goes stale when |
|---|---|---|
| `teacher_performance_<school>_<page>_<size>` | 300s | any teacher's assignments/grades change |
| `teacher_detail_<school>_<teacher>` | 300s | that teacher's data changes |
| `assignment_activity_<school>_<year>` | 900s | any assignment is created/edited |
| `department_overview_<school>` | 300s | roster or course changes |

They break the `<entity>:user_id__<id>` naming convention every pattern is
written against, so nothing reaches them. A school admin can watch a teacher
publish an assignment and see the old numbers for up to 15 minutes.

Severity: **stale-within-tenant, not cross-tenant.** The keys are correctly
scoped by `school.id`, so one school cannot read another's numbers — this is
a freshness bug, not a disclosure bug. That distinction matters for
prioritisation and is asserted, not assumed.

**Not gaps — two caches with their own invalidation, recorded so the
distinction is not lost:**

* `assignmentpdf:<version>:<assignment id>:<view>:<stamp>` — `assignments/pdf_cache.py`
  clears its own prefix directly.
* `grading_answer_cache:<digest>` — content-addressed: the key *is* a digest
  of the input, so changed input means a different key and there is nothing
  to invalidate.

### Cross-tenant cached data — swept, no leak found

Every key format is scoped by `user_id` or by `school.id`. No cached response
is keyed only by a page number or a filter that two tenants could share, so
**no cached entry is readable across a tenant boundary**. The cross-tenant
exposure in this system is the opposite direction: one tenant's write
*destroys* another tenant's cache (availability/performance), rather than
exposing it (confidentiality). Asserted by
`test_over_invalidation_one_users_key_is_cleared_by_another`.

**Phase 1 is complete.** The two open pieces from the previous pass — the
dashboard map and the under-invalidation sweep — are done above.

---

## Design options — for review before implementation

Compared objectively; the recommendation is at the end, but the decision is
yours. "Ops" = Redis operations per invalidation event.

### Option A — per-entity generation counters (key versioning)

Fold a version integer into the key (`courses:v<n>:user_id__<id>:...`);
invalidate by `INCR`ing that entity's counter. Old keys are orphaned and
expire by TTL.

| Dimension | Assessment |
|---|---|
| Correctness | Strong. A bumped counter changes every dependent key atomically. |
| Tenant isolation | Strong — a counter is per user/school, so a bump cannot reach another tenant. |
| Redis ops | **O(1)**: one `INCR`, no SCAN. Best of the four. |
| Latency | One round trip per invalidation; reads need the counter, so +1 GET per read unless cached in-process. |
| Memory | Orphaned entries live until TTL — a transient increase after heavy churn. |
| Concurrency | `INCR` is atomic; no lost updates. |
| Bulk imports | Naturally O(1) — 2,000 rows still bump one counter per affected entity. |
| Stale risk | Low, provided every dependent key includes the counter. Missing one is a silent stale bug. |
| Stampede | Unchanged — a bump invalidates a whole family at once. Needs separate mitigation. |
| Complexity | Moderate: every read site must fetch and embed the counter. |
| Migration | Easy — new keys simply have a new shape; old ones expire. No flush needed. |
| Failure behaviour | Redis down: counter read fails → treat as cache miss → serve from DB. Degrades correctly. |

### Option B — targeted key deletion

Compute the exact affected keys and `DEL` them.

| Dimension | Assessment |
|---|---|
| Correctness | Only as good as the enumeration; anything forgotten goes stale. |
| Tenant isolation | Strong — you delete exactly what you name. |
| Redis ops | O(affected keys); a single `DEL` can take many keys, so usually small. |
| Latency | Very low when the set is small. |
| Memory | Best — no orphans. |
| Concurrency | Fine. |
| Bulk imports | **Poor unless batched** — the per-row problem returns as a per-row DEL list. |
| Stale risk | **Highest of the four.** The 35-key map above shows the enumeration is already non-obvious; the four dashboard keys were missed by exactly this kind of reasoning. |
| Stampede | Unchanged. |
| Complexity | High: every key format must be derivable from the mutation, including paginated and query-hashed variants — `UserCacheMixin` keys embed an **MD5 of the query params**, which is not enumerable at all. |
| Migration | Incremental. |
| Failure behaviour | Same as today. |

**The `UserCacheMixin` query hash is close to disqualifying for B on its
own**: you cannot enumerate keys you cannot predict.

### Option C — tag / set-based invalidation

Maintain a Redis SET per entity holding the keys that depend on it;
invalidate by reading the set and deleting its members.

| Dimension | Assessment |
|---|---|
| Correctness | Strong — solves B's enumeration problem by recording dependencies at write time. |
| Tenant isolation | Strong. |
| Redis ops | O(1) SMEMBERS + one DEL of N keys — no SCAN. |
| Latency | Two round trips; still far below today. |
| Memory | Extra: a set per entity, and sets need pruning or they grow unboundedly as keys expire underneath them. |
| Concurrency | Set writes race with key writes; a key can be cached but not yet tagged (small stale window) unless done in a transaction/pipeline. |
| Bulk imports | Good — one set read per entity, batchable. |
| Stale risk | Low-moderate: the race above, plus tag drift if a write path forgets to tag. |
| Stampede | Unchanged. |
| Complexity | **Highest** — every cache write must also tag, and tag GC must be built and operated. |
| Migration | Harder — needs backfill or a period where untagged keys are unreachable. |
| Failure behaviour | Redis down: tagging fails → untagged keys become un-invalidatable until TTL. Worst failure mode of the four. |

### Option D — hybrid: versioning for user/school families, dedicated prefixes elsewhere

A for the 29 `user_id`/`school`-scoped families; keep the two
self-managing caches (PDF, grading digest) as they are; give the four
orphaned dashboard keys the standard scoped shape so they join the scheme.

| Dimension | Assessment |
|---|---|
| Correctness | Strong, and it fixes the four under-invalidated keys as a side effect. |
| Tenant isolation | Strong. |
| Redis ops | O(1) for the common case; unchanged for the two specialised caches. |
| Complexity | Moderate — one mechanism plus two documented exceptions, rather than one mechanism forced everywhere. |
| Migration | Same as A. |
| Everything else | As A. |

### Recommendation, with the reasoning exposed

**D (hybrid, versioning-based)** — but on evidence, not convenience:

* 29 of 35 key families are already `<entity>:user_id__<id>`, so the scoping
  a counter needs **already exists in the key shape**; A/D fit the codebase
  as written rather than requiring it to be rewritten.
* B is undermined by the `UserCacheMixin` MD5 query hash — unpredictable keys
  cannot be enumerated for targeted deletion.
* C is the most powerful but has the worst failure behaviour (a Redis blip
  during tagging silently creates un-invalidatable keys) and the largest
  operational surface, for a system whose current problem is over-eager
  invalidation rather than under-reach.
* D handles the two self-managing caches honestly instead of forcing them
  into a scheme that buys them nothing.

**Not decided by this analysis, and needing your call:** cache stampede.
None of A–D addresses it — invalidating a family still expires everything at
once. It should be chosen separately (jitter, lock-and-rebuild, or
serve-stale-while-revalidate), and `dashboard`'s expensive aggregates are the
place it matters.

**Open risk for whichever option is chosen:** every read site must adopt the
new scheme. A single missed read site is a permanently stale response. The
35-key map above is the checklist that makes that auditable, and the coverage
test should be inverted after implementation to assert that every key format
is reachable by its owning mechanism.

## Baseline — preserve for the before/after comparison

These are the numbers the redesign must be measured against. **Do not
re-baseline after the change**; the point is to prove improvement, not to
move the goalposts.

| Measurement | Baseline (2026-09-08/09) |
|---|---|
| SCAN commands per imported row | 29 |
| Total Redis commands per imported row | 432 |
| Cache keys destroyed by a 25-row import | 10,000 (entire keyspace) |
| Extrapolated 2,000-row import | ~58,000 SCANs, ~864,000 commands |
| Distinct cache-key formats | 35 |
| Key families matched by `*user*` | 29 of 35 |
| Never-invalidated key formats | 6 (4 are gaps) |
| Redundantly-invalidated key formats | 27 |
| Total pattern-matches per invalidation sweep | 67 |

Post-implementation testing must additionally cover **realistic multi-tenant
scenarios and large bulk operations**, not the 25-row case that exposed the
problem: reproduce the original failure at scale first, then demonstrate the
redesign removes the flush/performance cliff.

## Reproduction

The measurements above came from a `TransactionTestCase` with
`override_settings(CACHES=...)` pointed at a dedicated Redis DB, seeding
10,000 realistic keys, calling `redis.config_resetstat()`, running
`import_roster` through the real service layer, then reading
`INFO commandstats` and `DBSIZE`. Rebuild this as a permanent, committed
benchmark so the improvement is measured rather than asserted.

---

# H-2 — Full-suite exit code / test DB connection leaks

**The suite passes and still exits non-zero, so CI would go red on a green
run.**

Observed: `classrooms students users assignments dashboard` →
**1,422 tests, OK, 0 failures**, then teardown fails with
`database "test_..." is being accessed by other users — There are 13 other
sessions using the database`, exit 1.

Isolated: **`classrooms` alone runs 235 tests and exits 0**, so this section
is not the source. The leak comes from threaded / `LiveServerTestCase`
suites elsewhere — `users/tests_activity_middleware_load.py`,
`users/tests_login_lockout.py`, `students/tests_grading_idempotency.py`,
`assignments/tests_load.py`, `assignments/tests_security.py`.

The pattern that fixes it is already in the tree:
`classrooms/tests_concurrency_and_resilience.ThreadSafeTransactionTestCase`
closes connections in `tearDown` as well as in each worker thread.

> **CORRECTION (2026-09-13): the diagnosis above was wrong.** The leak comes
> from one test, not five suites.
> `assignments/tests_security.py` `ConcurrentAccessRevocationTest` starts
> 13 threads and none of them closed its own connection. That is the
> "13 other sessions". Two earlier fixes were tried, disproven and reverted:
> `CONN_MAX_AGE=0` in test mode (still 13 sessions), and a shared
> `tearDown` mixin (`connections.close_all()` is thread-local, so it cannot
> close a worker thread's connection). The actual fix is
> `try/finally: connection.close()` inside each worker. Full evidence is in
> `docs/evidence/H2_TEST_TEARDOWN_EVIDENCE.md`.
>
> **Status: Closed — fixed, verified with three consecutive clean full runs
> and failure/mutation simulation.** (Owner sign-off 2026-09-13.)
> gate3/4/5: 1,804 tests OK each, exit 0, 0 leftover sessions and 0
> leftover DBs. Mutant with the two `close()` calls removed: all 58 tests
> still pass, exit 1 with "13 other sessions". Restore verified by checksum.
> The fingerprint helper skipped six untracked documentation files with
> spaces in their paths. The owner reviewed this and does not consider it
> grounds to invalidate the runs, since those files are not code. All
> future gates use the NUL-safe fingerprint.

**Scope**: ~~promote that base class to a shared location and adopt it in
every threaded/live-server suite~~ (superseded, see the correction); confirm
each worker closes its own connection.

**Acceptance**: the full suite exits **0**, repeatably, including after the
threaded suites run; no lingering `test_*` connections in `pg_stat_activity`
after a run.

**Evidence**: regression (three consecutive clean full runs); failure
simulation (a deliberately leaking test is detected rather than silently
tolerated). Adversarial/stress/live-stack: not applicable — record why.

---

# H-9 — the whole test suite shares one Redis DB (test isolation)

**Found 2026-09-12 while investigating a cross-app regression failure.**

`REDIS_LOCAL_URL=redis://127.0.0.1:6379/0`, so **every pre-existing test
suite in the project shares Redis DB 0** with any other process using it -
including a second concurrent development session running its own tests.

## The failure it produces

A 6-app regression run reported one failure:
`assignments.tests_pdf_cache.InvalidationScopeTest.test_a_whole_course_of_saves_does_not_cool_one_warm_assignment`,
asserting `None != b'%PDF-warm'`.

Isolation results — the test is **not** broken:

| Scenario | Result |
|---|---|
| the test alone | OK |
| the whole `assignments` app (521 tests) | OK |
| `students` -> `tests_pdf_cache` | OK |
| H-1 cache suites -> `tests_pdf_cache` | OK |
| **6-app run (~30 min)** | **FAILS** |

Ruled out by A/B revert: reverting H-1's assignment generation bump does
**not** fix it, so H-1 is not the cause.

**Mechanism demonstrated, not hypothesised.** A `cache.clear()` issued
between `store_pdf` and `get_cached_pdf` reproduces the exact assertion:

```
DEMO no interference                 -> b'%PDF-warm'
DEMO after concurrent cache.clear()  -> None   (matches the observed failure)
```

Any concurrent process calling `cache.clear()` or a wildcard
`delete_pattern` on DB 0 can therefore fail an unrelated suite. A 30-minute
run gives a wide window; every short run passes, which is exactly the
observed pattern.

## Second, related finding

Redis DB 0 currently holds live `gaplus:1:cachegen:usr:*` keys left by an
earlier test run. Generation counters are written with **no TTL** by design
(so `volatile-lru` cannot evict them - see
`docs/H1_CACHE_INVALIDATION_DESIGN.md` §6), which in the test environment
means they **accumulate across runs and are never reclaimed**. Harmless
today because entity UUIDs differ per test database, but it is unbounded
litter in the shared dev instance and a source of cross-run state.

## FIXED (2026-09-12)

`AutoGrader/test_cache.py` + a settings branch under `manage.py test`:

* each test **process** gets its own `KEY_PREFIX` (`gaplus-t<pid>`);
* `PrefixScopedRedisCache.clear()` deletes only that prefix instead of
  issuing `FLUSHDB`.

**A prefix, not a Redis database slot.** Redis ships with 16 databases, so a
PID-modulo scheme collides at ~1/16 for two concurrent runs and cannot scale
to CI parallelism. A prefix has no ceiling.

**Why a test-only backend is acceptable**: only `clear()` differs, and no
production code path calls it (grep-verified - production invalidation goes
through `delete_pattern` or generation bumps). Every other operation,
including `delete_pattern` and the `incr`/`SET NX` behaviour H-1's counters
depend on, is the real django-redis implementation against real Redis.

### Evidence

* both acceptance tests flipped from `expectedFailure` to passing;
* **two genuinely concurrent runs** - `assignments.tests_pdf_cache` beside
  the H-1 cache suites - both returned OK, which is the failure this was
  diagnosed from;
* 4/4 tests in `tests_redis_test_isolation.py`.

### One correction worth recording

The acceptance test did not pass immediately after the fix, and the reason
was the test, not the fix: its helper subprocess ran as a plain script, so
`"test" in sys.argv` was false and it picked up the **production** backend,
whose `clear()` is still `FLUSHDB`. It was measuring "a non-test process
flushes us", not "a concurrent test session flushes us". The helper now sets
`sys.argv` to look like a test run, which is the scenario the requirement
actually describes.

### Residual exposure, out of scope by design

A non-test process - a `manage.py shell`, a stray script - calling
`cache.clear()` still issues `FLUSHDB` and would wipe a running suite. That
is a developer action against the development cache, not a concurrent test
session, and is documented in the test module rather than guarded.

## Original requirement (owner, 2026-09-12) — stronger than changing one URL

> **Concurrent test sessions must not be able to modify, flush, or
> invalidate one another's Redis-backed test state.**

The property to satisfy is **concurrent-process isolation**, not sequential
test isolation. It covers everything Redis-backed that a test run touches:

* the Django cache;
* H-1 generation counters (`cachegen:*`);
* cache-based locks (`billing:planchange:*`, `billing:license_overage:*`);
* throttle state (`throttle_*`);
* presence/heartbeat keys (`presence:*`);
* any Celery broker/result state sharing the instance.

## Scope

* give the test suite its own Redis DB, or better a per-run DB, the way the
  H-1 suites already do - they use dedicated DBs 6-12 and **none of them
  flaked** in the run that exposed this;
* flush the chosen DB at session start, never mid-run;
* verify the isolation holds with **multiple concurrent processes**, not one
  process running suites in sequence.

## Acceptance

* two concurrent full-suite runs on one machine do not interfere;
* the PDF invalidation-scope test passes in a 6-app run, three times running;
* `AutoGrader/tests_redis_test_isolation.py`'s two `expectedFailure` tests
  become **unexpected successes** - which turns the suite red and forces the
  markers to be removed. That is the signal the fix landed.

## The reproduction is retained as the regression test

`AutoGrader/tests_redis_test_isolation.py` holds the deterministic
demonstration, per the owner's instruction to keep it:

| Test | Today | Why |
|---|---|---|
| `test_the_mechanism_a_cache_clear_destroys_a_warm_entry` | passes | documents the mechanism in-process |
| `test_generation_counters_are_also_destroyed_by_a_flush` | passes | shows the blast radius reaches H-1's invariant |
| `test_a_concurrent_process_cannot_flush_our_cache` | **expectedFailure** | the acceptance test: a separate OS process, using the project's own settings, flushes our state |
| `test_a_concurrent_process_cannot_reset_our_generation_counters` | **expectedFailure** | same requirement for the H-1 counters |

The two acceptance tests spawn a **real second interpreter** and are
deterministic - the other process clears, then we read. No race, no sleep.
`expectedFailure` rather than `skip` so the fix cannot land silently.

## Generation-counter accumulation: do NOT add a TTL

Explicit owner instruction. The non-expiring counter is part of H-1's
correctness invariant: under `volatile-lru` a TTL'd counter becomes
evictable while the entries it guards survive, resetting the generation and
reviving stale data - the exact failure class H-1 was designed to remove
(`docs/H1_CACHE_INVALIDATION_DESIGN.md` §6).

Solve the accumulation through **isolated, disposable test Redis state**
plus teardown of the test instance after a run. For a long-lived development
Redis, track counter cardinality and memory as their own line item. Do not
change production semantics to tidy a shared development database.

---

# H-10 — `super-admin/dashboard/students` issues 487 queries

Found while measuring H-1 families 15-22. **Not a cache defect** — caching
only hides it on the requests that hit.

Measured at 10 schools / 240 courses / 17,464 submissions, through the live
endpoint on real Postgres:

| Metric | Value |
|---|---|
| Queries | **487** for a **413-byte** response |
| Wall | 1,938ms |
| SQL | 433ms |
| **Python** | **1,505ms** |
| Signature | `240x COUNT(*) assignments` + `240x COUNT(*) studentcourse` |

One pair of COUNT queries per course. It scales linearly with course count,
so a tenant with 10x the courses sees ~10x the queries — and the Python time
dominates, so it is not fixed by a faster database.

**Scope**: replace the per-course counts with annotated aggregates
(`Count(..., distinct=True)` on a single queryset), the same fix applied to
the classrooms N+1s in Section 3.

**Acceptance**: query count does not grow with course count (assert
flatness, not an absolute budget — an absolute number drifts and gets
bumped); response byte-identical before and after; wall time reduced.

**Evidence**: functional (identical payload), performance (before/after
query count and latency at the measured scale), mutation (reverting the
annotation fails the flatness test), regression. Adversarial/failure
classes: not applicable — record why.

## Owner decision (2026-09-14): the fix is the Section 8 remediation

H-10 points at the Section 8 dashboard remediation (session
grade-automator-plus-01), so there is no competing H-1 fix. Per that session,
the remediation covers three places:
- `_expected_submission_total(courses)` replaces the per-course
  `assignments.count() * enrollments.count()` in the super-admin and
  school-admin students views;
- `TeacherPerformanceStatsService().build(teachers)` replaces the
  per-teacher N+1 in teacher_performance, teacher_detail and the weekly
  digest;
- `dashboard/tests_dashboard_remediation.py` adds flatness, parity and
  tenant tests.

**Closed only when all three hold** (owner):
1. the dashboard fix's own tests pass;
2. the H-1 cache suites pass against the resulting code;
3. a fresh real measurement shows the **query count stays effectively flat
   as the dataset grows**. A single faster request is not evidence.

Recorded so far, not closing: on the uncommitted reconciled tree
(`1373eae` + Section 8 patch), the six H-1 cache suites passed (80 OK), and
an interim full suite passed (3,931 OK, 14 skipped). That full run used
`--keepdb`, so it is **not** final-gate evidence: `--keepdb` skips the
test-DB drop, which is the only point where a leaked connection shows up.

**Condition 3 — fresh real measurement (2026-09-14).** The same test was run
on `1373eae` (before) and `ec67363` (Section 8 remediation). Requests were
uncached, on real Postgres: one school, 2 courses per teacher, 3 students
per course.

| Endpoint | Queries at 2 / 6 / 18 teachers, `1373eae` | Queries at 2 / 6 / 18 teachers, `ec67363` |
|---|---|---|
| super-admin students | 14 / 30 / 78 | **6 / 6 / 6** |
| school-admin students | 13 / 29 / 77 | **5 / 5 / 5** |
| school-admin teacher_performance | 24 / 60 / 168 | **10 / 10 / 10** |
| super-admin teachers | 8 / 16 / 40 | **8 / 8 / 8** |
| school-admin teacher_detail | 19 / 19 / 19 | 14 / 14 / 14 |

The query count is flat on `ec67363` and grew linearly before it, so
condition 3 is met **for `ec67363`**. At 18 teachers, teacher_performance went
from 169.5ms to 20.9ms; latency is indicative only, as other runs were active.

## CLOSED (2026-09-14)

The owner told us to merge the dashboard work and close H-10 once the
resulting `beta` tree passed verification. Sections 7 and 8 were merged into
`beta` as `2715c642fc4b` (tree `2a68fe26…`), and the owner's strict gate
passed on that exact commit: **4,031 tests OK** (14 skipped), exit 0, fresh
DB with no `--keepdb`, DB dropped, 0 connections, machine kept awake, tree
unchanged.

All three conditions now hold **on `beta`**:
1. **Dashboard tests pass:** `tests_dashboard_remediation` 47,
   `tests_dashboard_audit_fixes` 23, `tests_rigor` 35, `dashboard.tests` 73,
   all ok.
2. **H-1 cache suites pass** inside the same gate: fan-out 29,
   dashboard-wide 11, wiring 15, plus the rest.
3. **Query count is flat** (table above). `dashboard/` on `beta` is
   byte-identical to the measured code.

Evidence: `docs/evidence/H10_INTEGRATION_GATE_EVIDENCE.md`.

---

# H-3 — `student123!` account remediation

Deferred by the owner as non-urgent and isolated; tracked here so it does not
vanish. **Data already gathered (production, read-only):**

- **116 of 122 student accounts authenticate with `student123!`** — verified
  by running `check_password` against every row, not inferred from the email
  pattern.
- All 116 are `is_active=True`, and login has **no email-verification gate**,
  so these are live credentials.
- **97** have generated `@student.local` addresses; **19 have real email
  addresses** — the exposed cohort, since an attacker needs no guessing.
- They hold **124 enrollments and 558 submissions**, so deletion is not an
  option.
- **`last_login IS NULL` on all 116** — not one has ever signed in.

That last fact is the whole plan: resetting all 116 to an unusable password
has **zero user impact**. The code fix is already shipped
(`set_unusable_password()`), so only existing rows are affected.

**Scope**: a management command, dry-run by default, requiring `--execute`,
reporting the affected count and writing an auditable record of what it
changed.

**Acceptance**: zero accounts authenticate with the literal afterwards; no
account is deleted; enrollments and submissions are untouched; the command is
idempotent.

**Evidence**: functional (dry-run reports without writing); adversarial (the
literal no longer authenticates on the live login endpoint); failure
simulation (interrupted mid-run leaves a consistent state and can be
re-run); regression. Mutation: removing the reset must fail the test that
asserts no account authenticates.

---

# H-4 — Duplicated `delete_cache_patterns` implementations

Four implementations exist and have already drifted: only some batch, only
some catch Redis connection errors, only some warn when the backend lacks
pattern support, and they differ in how broadly they catch exceptions
(`classrooms/signals.py` deliberately narrow so real bugs still surface;
`AutoGrader/cache_utils.py` catches bare `Exception`).

**Folds into H-1** — consolidation is part of that design, not a separate
change. Listed separately so it cannot be lost if H-1 is staged.

**Acceptance**: exactly one implementation; every caller uses it; the narrow
exception behaviour is preserved (a `TypeError` from a bad pattern must still
propagate, as pinned today).

---

# H-5 — `full_clean()` on the grading hot path

`StudentCourse.save()` calls `full_clean()` unconditionally, so every
`save(update_fields=["final_grade"])` from the grading signal runs full model
validation — including the name-conflict lookup — **inside a
`select_for_update()` block on the grading hot path.**

**Scope**: measure the cost under concurrent grading first, then decide
whether to scope validation to the fields being written or move it to the
serializer boundary. Do not change validation semantics blindly — the
name-conflict rule is load-bearing for roster integrity.

**Acceptance**: no reduction in validation coverage for user-supplied input;
measured reduction in queries and lock-hold time on the grading path.

**Evidence**: functional (name-conflict rules still enforced on every user
entry point); stress/concurrency (concurrent grade recalculation, real
threads, measuring lock contention); mutation (removing the remaining
validation fails roster-integrity tests); regression.

---

# H-6 — `CourseCategoryViewSet` is unrouted and broken

Not registered in any `urls.py`, and its `category_courses` action reads
`category.courses` — a relation that **does not exist**: `CourseCategory` has
no link to `Course` at all. It would raise `AttributeError` if ever routed.
Left untouched pending your decision on whether categories have a future.

**Scope**: decide first — build the relation and route it, or remove the
viewset and serializer. Both are small; the decision is the work.

**Acceptance**: either a working, tenant-scoped, tested endpoint, or the dead
code removed with the model's fate recorded (the `CourseCategory` model and
its admin registration also become questionable if the endpoint goes).

**Evidence**: if built — functional, adversarial (tenant scoping, as every
other classrooms endpoint now has), regression. If removed — regression plus
a grep-proof that nothing references it.

---

# H-7 — `direct_add_student` response shape and status code

Returns `{"message", "detail"}` where every sibling endpoint returns
`{"detail"}` alone, and returns **200 for a creation** where 201 is correct.
Untouched because response-shape changes are an API contract change.

**Scope**: confirm with the frontend which fields are consumed, then align.

**Acceptance**: consistent error envelope across classrooms endpoints; 201 on
creation; frontend updated in the same release.

**Evidence**: functional; regression; live-stack confirmation against the
real frontend. Adversarial/stress: not applicable — record why.

---

# H-8 — Test file naming and stray docs

`classrooms/` mixes `test_*.py` and `tests_*.py`; the standards doc
(`docs/CODE_REVIEW_STANDARDS.md` §9) specifies `tests.py` / `tests_<topic>.py`.
`classrooms/test_documentation.md` is a markdown doc living in an app
package and probably belongs under `docs/`.

**Scope**: rename with `git mv` so history follows; move the doc.

**Acceptance**: naming consistent across the app; the pre-commit
`name-tests-test` exclusion still correct; suite green.

**Evidence**: regression only — record that the other classes do not apply.

---

## Note on scope discipline

# H-11 — Synchronous billed AI calls inside `students` request handlers

**Found by the §7 review pass (2026-09-13) while auditing
`students/views.py`, which no audit section had listed.** Recorded here so
it cannot fall between sections again: it is release-blocking, it has an
owner, and it has its own gate.

Three actions on `StudentSubmissionViewSet` run the billed AI pipeline
inside the HTTP request, against standards §7 ("nothing in a
request/response cycle does synchronous work that belongs in a Celery
task - AI grading calls..."):

| Action | What runs in the request | Async twin that already exists |
|---|---|---|
| `POST submissions/<assignment>/upload` (`upload_answers`) | file → AI answer extraction → save | `upload-async` |
| `POST submissions/<pk>/grade` (`grade`) | the full grading pipeline (several sequential AI calls with retries, up to `GRADING_TASK_TIME_LIMIT_SECONDS`) | `grade-async` |
| `PATCH submissions/<pk>` (`partial_update`) | raw text → AI re-extraction → save | none |

Consequences today: a gunicorn worker is held for the whole AI run
(minutes for `grade`), the request can outlive the proxy timeout while
the charge has already been made, a client retry after a timeout is a
second billed run (the grading claim stops the double *grade*, but the
sync `grade` view then answers 409 for a run the client cannot poll), and
the sync `upload` path has no tracked task, so nothing the frontend can
poll records its failure.

Also recorded from the same audit, all in `students/views.py` /
`serializers.py`, to be resolved with this item because they share the
endpoints:

* **V-2** `upload_answers` and `partial_update` answer HTTP 500 for every
  failure, including user-caused ones (bad file, extraction returned no
  answers) - only the two post-grading closure errors now map to 409.
* **V-3** `get_permissions` routes `partial_update` (PATCH) to
  teacher-only while its docstring and the OpenAPI text describe it as the
  student's edit path. Either the docstring or the mapping is wrong;
  decide which before the endpoint moves async.
* **V-4** `partial_update` ends with a full-row `submission.save()` - the
  same stale-instance clobber class fixed in the service layer (F-4).
* **V-5** `StudentViewSet` is defined but not routed (`students/urls.py`
  registers only submissions); dead or missing, decide which.
* **V-6** `teacher_feedback` declares `IsTeacherOrReadOnly` on the action
  but `get_permissions` overrides it to teacher+credits (already commented
  in code; the dead kwarg should go once V-3 is decided).

**Owner:** Section 7 (students) for the backend; the frontend owner for
the client switch-over. Proposed by the §7 reviewer; assignment is
management's.

**Scope:** make the three sync actions either (a) thin dispatchers that
create a tracked task and return 202 with a task id (the `-async` twins
already do this), or (b) removed once the frontend has switched - decided
with the frontend, since (a) changes their response contract. Map
user-caused failures to 4xx with the existing `describe_user_error` text.
Resolve V-2..V-6 in the same change.

**Acceptance criteria:**
1. No `students` view calls `ai_processor.*`, `grade_engine` or
   `upload_answers_engine` synchronously (static check: a test that greps
   `students/views.py` for those names, so it cannot regress silently).
2. Every submission-mutating action creates a `BackgroundProcessingTask`
   the frontend can poll, and its failure is recorded on that row with a
   user-safe message.
3. A client retry after a timeout cannot produce a second billed run
   (idempotency proven under redelivery, as for R-1).
4. V-2..V-6 each closed with a test.

**Required evidence (per the verification standard above):** functional
through the live API; adversarial (retry/replay after timeout, the same
tenancy probes as `students/tests_submission_tenancy.py`); concurrency
(N parallel clients, one billed run); failure simulation (broker down →
503, not 500; AI provider timeout → refund, task FAILURE); mutation;
regression; evidence recorded in `docs/evidence/`.

**Until closed:** the sync endpoints keep working exactly as today, with
the §7 pass's server-side rules applied to them (post-grading lock,
tenancy scoping, 409 for closure errors).

# H-12 — Commented-out code burn-down (flake8-eradicate E800)

`docs/CODE_REVIEW_STANDARDS.md` §2 lists flake8-eradicate as enforced, but
`.pre-commit-config.yaml` had `E800` in its `--ignore` list, so it never was.

**History:**

- **§7 pass:** turned the rule ON and carved out, by name, the files that
  still carried legacy commented-out blocks. That was 930 E800 hits
  repo-wide, 500+ of them in `dashboard/`.
- **§8 pass:** cleaned all of `dashboard/` (84 hits on the merged tree) and
  removed its six entries.
  - Four files were cleaned: `views.py`, `serializers.py`, `tests_rigor.py`,
    and `urls.py`, which was already clean.
  - Two entries were for files §8 deleted (`at_risk_improvements.py`,
    `AT_RISK_IMPLEMENTATION_GUIDE.py`), so that also settles the
    documentation-as-code question.
  - Every removal was proved comment-only by an AST comparison.

**Item 9 progress (repository-wide burn-down, owner decision 2026-09-14):**- §10 templates: `templates/assignment_to_prosemirror.py` (2 hits) cleaned; AST-identical, E800 0

**Item 9 progress (repository-wide burn-down, owner decision 2026-09-14):**- §0 cross-cutting (AutoGrader/urls.py): `AutoGrader/urls.py` (6 hits) cleaned; AST-identical, E800 0

**Item 9 progress (repository-wide burn-down, owner decision 2026-09-14):**- §1 users: `users/models.py`, `users/serializers.py`, `users/services.py`, `users/tests_throttle_client_identity.py`, `users/views.py` (27 hits) cleaned; AST-identical, E800 0

**Item 9 progress (repository-wide burn-down, owner decision 2026-09-14):**- §3 classrooms: `classrooms/models.py`, `classrooms/serializers.py`, `classrooms/test_bulk_enrollment.py`, `classrooms/test_views.py`, `classrooms/views.py` (26 hits) cleaned; AST-identical, E800 0

**Owner decision (2026-09-14):**

- The rule covers the **whole repository**. The carve-out list is a
  temporary register, not a policy.
- Every remaining exemption must have a reason, an owner and a cleanup plan.
  They are listed below.
- Entries are removed progressively as each area is cleaned.
- No whole directory may be exempted just to make the check pass.
- The rule is not achieved while unexplained exemptions remain.

**Rules for the list (enforced by review):**

- Nothing may be added.
- A file's entry is removed in the same commit that cleans it.
- The list must always equal exactly the set of files that still have E800
  hits. The §8 commit verified this. A stale entry for an already-clean file
  counts as a defect.

**Evidence each cleanup must include:**

1. `flake8 --select=E800 <file>` is clean;
2. an AST comparison of the file before and after showing identical code,
   which proves only comments were removed. Import statements are normalised
   if isort reflows them. For §8's version, see
   `docs/evidence/SECTION_8_DASHBOARD_REMEDIATION_EVIDENCE.md` §8;
3. a regression run of that app's tests.

A block that records something intentional, such as an alternative
configuration, is rewritten as prose, not deleted.

**Why these files are exempt:** each still contains commented-out code from
before E800 was enforced, and nobody has reviewed it yet. It is not known
whether any block is intentional. That is the reason for every row below.
The "What is commented out" column shows what each file holds.

**Staged plan:**

- **Stage 1:** ≤ 6 hits — 8 files, quick and low risk.
- **Stage 2:** 7–21 hits — 8 files.
- **Stage 3:** ≥ 27 hits — 4 files, which need careful review.

Each stage is done by the owning section, in coordination with any session
currently changing that app.

Counts are kept live: every item 9 cleanup commit recounts with
`flake8 --select=E800` and removes the files it cleaned:
**20 files, 286 hits**.

| File | Hits | What is commented out | Owner | Plan | Notes |
|---|---|---|---|---|---|
| `billing/license_service.py` | 4 | 4 statements | §2 billing | Stage 1 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/live_qa/invariants_individual.py` | 1 | 1 statements | §2 billing | Stage 1 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/management/commands/backfill.py` | 1 | 1 imports | §2 billing | Stage 1 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/stripe_view_schemas.py` | 4 | 4 statements | §2 billing | Stage 1 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/tasks.py` | 1 | 1 statements | §2 billing | Stage 1 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/tests/tests.py` | 5 | 3 imports, 2 statements | §2 billing | Stage 1 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `assignments/admin.py` | 4 | 4 statements | §4 assignments | Stage 1 |  |
| `assignments/tests_rigor.py` | 2 | 2 statements | §4 assignments | Stage 1 | Test file: low risk. |
| `billing/access_control.py` | 14 | 12 statements, 2 imports | §2 billing | Stage 2 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/license_views.py` | 14 | 13 statements, 1 imports | §2 billing | Stage 2 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/models.py` | 10 | 9 statements, 1 imports | §2 billing | Stage 2 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/services.py` | 12 | 11 statements, 1 imports | §2 billing | Stage 2 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/stripe_service.py` | 18 | 15 statements, 2 dict keys, 1 imports | §2 billing | Stage 2 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `billing/views.py` | 21 | 11 statements, 7 imports, 3 dict keys | §2 billing | Stage 2 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `assignments/serializers.py` | 11 | 11 statements | §4 assignments | Stage 2 |  |
| `assignments/views.py` | 20 | 12 statements, 8 imports | §4 assignments | Stage 2 |  |
| `AutoGrader/settings.py` | 29 | 14 statements, 14 dict keys, 1 imports | §0 cross-cutting | Stage 3 | Settings values and dict keys: some may be deliberate environment alternatives, so move anything intentional into prose or the env docs rather than deleting it blindly. |
| `billing/serializers.py` | 39 | 39 statements | §2 billing | Stage 3 | Billing: comments only, and no billing logic may change. The AST proof is mandatory. |
| `assignments/tasks.py` | 27 | 22 statements, 2 imports, 2 dict keys, 1 prints | §4 assignments | Stage 3 |  |
| `ai_processor/services.py` | 49 | 42 statements, 7 imports | §5 ai_processor | Stage 3 |  |

**The one directory-wide exclusion:** `exclude: (^|/)migrations/` on the
whole flake8 hook, not only E800. It predates H-12. Migrations are generated
by `makemigrations`, and hand-editing them to satisfy a linter risks changing
schema history, so this exclusion is justified and is **not** part of the
burn-down. It is recorded here so the register accounts for every exception.

**Acceptance:**

- `--per-file-ignores` is empty and removed from `.pre-commit-config.yaml`;
- `flake8 --select=E800 .` is clean, with only migrations excluded;
- this register is deleted.

# H-13 — Uploads while grading is RUNNING — DECIDED

**Owner decision (2026-09-14): refuse additional uploads while grading is
in progress.** No implicit replacement or re-grade. The same decision
extends the graded-row lock to **teacher proxy uploads**: a graded
submission is immutable through every ordinary upload path, because
"new answers + old grade" is not an acceptable production state. A
correction after grading needs a future explicit replace/re-grade
workflow with its own authorization, audit trail, credit behaviour and
concurrency rules.

**Implemented** (`students.services._check_submission_open`):
* graded (`graded_at` set) → `SubmissionAlreadyGradedError`, every path;
* live grading claim (RUNNING and younger than `GRADING_CLAIM_STALE_AFTER`,
  the same staleness rule the claim itself uses, so a dead worker's claim
  does not lock the row out) → `SubmissionBeingGradedError`, every path;
* attempt limit → students only.
Applied under the row lock for student and proxy uploads, pre-checked
before the billed extraction where the student is known (student paths),
and mapped to 409 at `upload`, `upload-async` and `PATCH raw_input`; the
batch task records it as a final, non-retried failure.

**Evidence:** `students/tests_post_grading_submission_lock.py` (service,
API, task, 8-thread proxy and student concurrency, grade-commit race,
stale-claim exception) and mutation checks M22-M24 in
`docs/evidence/SECTION_7_GATE_EVIDENCE.md` §11.

Several of these were found during Section 3 but are **not** Section 3
changes — H-1 spans four apps, H-2 lives in `users`/`assignments`/`students`,
H-5 is Section 7. They were deliberately kept out of the security work so
that diff stayed reviewable. That decision is what this document exists to
make safe: the work was postponed, not dropped.
