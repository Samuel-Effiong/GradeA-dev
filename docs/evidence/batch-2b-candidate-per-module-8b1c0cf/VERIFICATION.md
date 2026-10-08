# Verification: batch-2b candidate (H-1 stage 3 Design A + step 4) @ 24d5ec0

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Hardening (d5).
**Base:** frozen batch-2a 755aa27 (beta). The previous step 4 review was at 8063c44 (archive tag `archive/h1-step4-pre-cc14bb0-8063c44`). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** Stage 3's Design A and step 4 are verified together. Nothing is required before merge. The batch still gets its own Gate 10 strict full run (0b).

## What I checked
My own detached checkout with its own test DB, **no `RACE_COST_*` env**, and every run under `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`.

| Check | Result |
|---|---|
| History | 755aa27 and 8063c44 are both ancestors of 24d5ec0. |
| Merges since 8063c44 | 6767b50, edb9413, 6dfeb40, bff770a and dba87fe are **clean** (merge-tree equals the merge tree). **4421e73 and 24d5ec0 are resolved, in test files only.** `tests_cache_commit_race_cost.py` keeps both sides: H-25's PENDING test, the budget tests, `BudgetIsArmedForCostTests` (8b1c0cf) and the default pin. `users/tests_last_login_stamp.py` drops its `delete_cache_patterns` recorder because step 4 removed that function; the no-wildcard guard now keeps it gone. |
| Source changed since 8063c44 | Design A only (cc14bb0): `AutoGrader/cache_generation.py` (`get_generations`, and `versioned_key(batched=True)`), `classrooms/signals.py` (`_course_owner_scopes`; an enrolment bumps a fixed 5 scopes), `classrooms/views.py` (`CourseViewSet.extra_cache_scopes`; a comment on my-courses' `global` dependency) and `users/mixins.py` (the `extra_cache_scopes` hook). The rest comes from verified batch-2a code. The no-wildcard guard file is **unchanged** since 8063c44. |
| Design A reasoning | A classmate sees an enrolment only through the roster (`students`, `student_count`). A student's cached course list and detail key on each course's `crs` generation, and the list derives its courses from a live query, so the student's own status changes also change the key. My-courses keys on `global`. The enrolment bumps `usr`, `global`, `crs`, the teacher and the school: O(1) instead of O(class size). Course and topic writes keep the per-student fan-out, which is correct for single writes. `get_generations` never raises and falls back to the defaults. |
| Static sweep allow-list | 4 entries. `SchoolViewSet` is `IsSuperAdmin`; its one `IsAuthenticated` action (`monthly-token-usage`) returns no roster, and a student gets 400 or 403. `SchoolAdminDashboardView` is `IsSchoolAdmin`. `SuperAdminDashboardView` is superadmin-only. `CourseCategoryViewSet` is never routed. All are sound. |
| Step 4 adaptations | b9f4e2d: the roster tests run on the real code (no wildcard patch), and the enrolment cost is pinned at `{SET: 10, INCR: 10}`, because H-25 replays the 5 in-transaction bumps at commit; the same at 30 and 300. e7d7b09 renames the command INCRBY. 01da8bc updates the coverage map. |
| Changed modules (roster scope, sweep, matrix g5 and g6, matrix self-test, no-wildcard guard, cache generation, H-25 commit race and cost, last-login stamp) | **Ran 103, OK**, 301 MB peak, 3:22 wall |
| Author regression (rule 15) | Relied on: d5's 109-module pass at 8b1c0cf (107 OK plus 2 fixed by 018351f, re-run 20 OK), `docs/evidence/batch-2b-candidate-per-module-8b1c0cf/`, and the mutation battery on 018351f (6/6). 2b's own delta has no model, field or migration changes. |

## My mutants (12)
| Mutant | Result |
|---|---|
| R1: H-25's commit re-bump removed | killed (8 tests) |
| G1–G4: `cache.clear()`, `get_redis_connection().flushdb()`, raw FLUSHALL, `caches[...].clear()` | all killed by the no-wildcard guard |
| L1: aliased `from django.core.cache import cache as default_cache; default_cache.clear()` | **SURVIVED** (known limit, N1) |
| L2: `c = caches['default']; c.clear()` | **SURVIVED** (known limit, N1) |
| D1: an enrolment skips the course's `crs` | killed (classmate add and remove freshness, 5-scope pin) |
| D2: a student's keys skip `crs` | killed (6 tests) |
| D3: an enrolment skips `global` (my-courses) | killed |
| D5: the batched generation read returns nothing on a Redis error | killed: `test_an_unreachable_redis_reads_as_the_default_and_never_raises` |
| D6: the per-classmate fan-out restored | killed by the 5-scope cost pin |

All restores were sha-checked against 24d5ec0.

## Notes (not blocking)
**N1 (carried from 8063c44).** The no-wildcard guard matches a cache handle only by the name `cache`, a `.cache` attribute, `caches[...]` or `get_redis_connection()`. An aliased import or an assigned handle that wipes the whole cache is not caught (L1, L2). Nothing in production does this today. Extending `_is_cache_receiver` to follow simple aliases would close it.

**N2 (pre-existing, logged as H-64).** At login, `activate_pending_enrollments_on_login` and the Google path flip PENDING to ENROLLED with `QuerySet.update()`, which fires no signal, so the course's `crs` isn't bumped. A classmate's cached roster keeps showing the old `enrollment_status` until the TTL runs out. The student's own keys move, because their course set changes. This is not introduced by Design A: login did no invalidation before step 4 either.

**N3.** A student's course list costs one extra query per request (the enrolled course ids) plus one MGET. That's recorded and pinned in d5's evidence.
