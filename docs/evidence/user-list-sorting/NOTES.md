# User list sorting and filters: run notes

Brief: SM -16, 2026-11-11, owner-approved. Base: origin/staging aeb8b157e.

Checkpoint 1 is e2a0a146a (DESIGN.md). Rulings:
- D1: A at first. The owner then changed it to B: fix the existing per-row
  queries in the same job. A page must cost a fixed number of queries.
- D2: A, plus a cache-key test.
- D3: confirmed.
- D4: null sorts last.
- D5: count by course.teacher.
- D6: confirmed.
- D7: grep the existing pins, then add the absence pin.

Environment for every run:
- venv ~/Documents/Virtualenvs/h284_deps_env;
- `--settings=settings_worktree` (test DB test_user_list_sorting);
- `--keepdb --noinput -v 2`;
- under `flock ~/.machine-fullsuite.lock`.

## M0: measurement on the base (before any change)

A scratch test on aeb8b157e (not kept) listed a 25-row page with 30 teachers
with wallets: **309 queries**, cold cache. The per-row queries were:

| table | queries |
|---|---|
| billing_creditbucket | 150 |
| billing_usersubscription | 50 |
| billing_creditwallet | 26 |
| users_settings | 25 |
| billing_licensesubscription | 25 |
| billing_schoolcreditallocation | 25 |

That is about 12 a row. A fixed part of 9 covers users (2), audit (2) and so
on.

## D7: the existing whole-key pins

I grepped every test that hits user-list, user-detail, user-me, user-lock,
user-unlock or /api/v1/users/: 30 files. I looked for `.keys()`, `*_KEYS`,
`FIELDS =`, `assertCountEqual` and set/sorted of data. **None pins a whole
key set on these responses.** The nearest are H-34's field-presence tests,
users.tests_admin_sign_in_lock.ReadOnlyFieldsTests (assertIn/assertNotIn of
sign_in_locked and login_locked_until); they must pass untouched. The new
absence pin is ListOnlyTests.

## P0: the payload pin captured on the base

users/test_data/user_list_payload_pin.json was written by running
ListPayloadPinTests.test_the_list_payload_is_what_it_was with
USER_LIST_PIN_WRITE=1 on aeb8b157e plus the two new test files only, with no
product change: Ran 1, OK (skipped=1). Log P0_pin_capture.log.

The fixture:
- an individual PRO teacher with all 5 bucket types, an expired bucket, a
  never-expiring bucket, a hold, and usage in and before the cycle,
  including a refunded row;
- a license teacher with a 15,000 allocation;
- a school admin who manages the license and has an admin allocation;
- a student;
- a user with no wallet;
- a user with no settings;
- an empty teacher.

## Counts (build_suite().countTestCases())

- users.tests_user_list_sorting: 48
- users.tests_user_list_payload_pin: 3
- together: 51

## R1 red, declared (tests only, base product code)

Expected: Ran 51, FAILED (failures + errors = 41). 10 pass:

- ListOnlyTests:
  - test_a_teacher_still_cannot_list (403 today)
  - test_super_admin_non_list_responses (unknown params are ignored; the
    keys are absent)
  - test_teacher_non_list_responses
  - test_the_sign_in_payload
- OrderingTests:
  - test_an_unknown_key_is_ignored
  - test_the_old_keys_still_work
- FilterTests.test_the_date_bounds_are_inclusive (vacuous on the base: the
  filter is ignored, so the user is listed either way)
- the 3 payload pin tests (the pin was captured on this code)

The 41 failures:
- KeyError on the new fields:
  - DateValue 2, PlanValue 7, CountValue 3, CreditValue 3, Parity 2;
  - ListOnlyTests.test_the_list_carries_all_nine.
- Ignored ordering keys, so the default first_name order applies: OrderingTests
  last_login, activated_at, plan_tier, plan_category, plan_interval,
  assignments_created, papers_graded, credits_left, credits_left raw,
  credits_used, id tiebreak, and two keys combined.
- Ignored filters: FilterTests
  - last_login after/before;
  - activated after/before;
  - plan filters;
  - the bad value (200, not 400);
  - combine with ordering.
- QueryCountTests: both (the page cost grows with its size).
- CacheKeyTests: both.
- OpenApiTests: both.

Caveat, declared now: two of the failures depend on tie order, which is
arbitrary on the base.
- test_id_breaks_every_tie_in_both_directions passes on the base only if the
  random UUIDs happen to sort like W, X, Y, Z (1 in 24).
- test_two_keys_combine depends on the order of two tied "A" rows.
I predict both fail.

## R1 red, result (log R1_red.log)

Ran 51, FAILED (failures=52, errors=19). The totals count subTests. By
distinct test: **41 fail and 10 pass, the same sets as declared.** The two
caveat tests (id tiebreak, two keys) both failed, as predicted.

Prediction incomplete: OpenApiTests (both) failed for the wrong reason, a
KeyError on "/api/v1/users/". The schema path has no trailing slash
("/api/v1/users"). This is a test defect, fixed in the test only. The rerun
R1b (log R1b_openapi_red.log) gives Ran 2, FAILED (failures=2), now for the
right reasons: "'last_login_after' not found in {...}" and "'last_login' not
found in set()" (the ordering parameter has no enum today).

## Build (product code, after R1)

New files:
- users/user_list.py: the SQL values, ensure(), list_queryset() and
  apply_ordering().
- users/list_serializers.py: ListCreditWalletSerializer and
  UserListSerializer.

Changed:
- users/filters.py: the 8 filters, plus UserListOrderingFilter.
- users/views.py:
  - get_serializer_class (list only);
  - list_queryset for the super admin list;
  - ordering_fields;
  - the OpenAPI ordering enum.
- **billing/serializers.py: not changed.** CreditWalletSerializer is
  untouched.

Added after R1: ListOnlyTests.test_the_list_filters_do_nothing_off_the_list_for_others,
making 52 tests. filter_queryset runs under get_object(), so for a teacher
or school admin the new filters would be a 200-vs-404 oracle on another
account's plan or dates. They are no-ops for anyone but a super admin. Its
red comes from plant Q-P-gate below.

**Process slip, recorded:** I ran two green attempts without writing
expectations first.
- R2a (log not kept; overwritten by R2b): Ran 52, FAILED (failures=2,
  errors=2). Both were test defects, fixed in the tests only:
  1. license() reused the School name "School N" because the counter never
     moved on: IntegrityError in two PlanValueTests.
  2. QueryCountTests' filter combination (last_login_before + has_plan)
     matched no rows. An empty page skips its prefetches: 8 != 13. Changed to
     filters that keep every row (plan_tier=PRO, has_plan, plan_category).
- R2b (log R2b_green_undeclared.log): Ran 52, OK.

## R2c green, declared before running

users.tests_user_list_sorting + users.tests_user_list_payload_pin: Ran 52,
OK. That includes the payload pin unchanged against the P0 snapshot, and 5
rows == 25 rows on query count.

## M1 measurement, declared

The same scratch test as M0, on the build (30 teachers with wallets, a
25-row page, cold cache). Expected: a fixed count well under 20, with no
table appearing about 25 times.

R2c result: as declared. Ran 52, OK. Log R2c_green.log.

M1 result: as declared. **13 queries** for the 25-row page (it was 309):
users, the count, wallet, bucket, subscription, license and allocation
prefetches, and audit/activity writes. No table appears per row.

Product commit 6ff7fba6f: hooks reformatted the code (black, isort) and two
mypy fixes went in:
- functools.partial in place of a default-arg lambda;
- a type-ignore on the SerializerMethodField override.

R2d green on 6ff7fba6f, declared: Ran 52, OK.

R2d result: as declared. Ran 52, OK. Log R2d_green.log.

## Plants, declared before any plant runs

Harness: plant_uls.py (scratch, copied into the evidence after the runs).
One plant per run. Each run saves a copy, checks that the file's sha256
matches HEAD, plants, runs the 2 labels (52 tests), then restores and checks
the sha256 again. "Killed" means named tests fail or error.

| plant | file | change | killed (declared) |
|---|---|---|---|
| Q-dates | users/user_list.py | activated_at reads date_joined | 5: DateValueTests both; OrderingTests.test_activated_at; FilterTests.test_activated_after_and_before; OrderingTests.test_id_breaks_every_tie_in_both_directions (activated_at subtests: date_joined differs, so the id order loses) |
| Q-plan | users/user_list.py | the individual subscription first in the precedence | 1: PlanValueTests.test_a_license_teacher_allocation_wins_over_an_individual_one |
| Q-counts | users/user_list.py | papers_graded drops ai_graded_at not null | 1: CountValueTests.test_papers_graded_counts_ai_graded_submissions_only |
| Q-credits | users/user_list.py | credits_left drops the holds | 2: CreditsLeftParityTests both. Possibly a 3rd: OrderingTests.test_credits_left, where held and rich tie at 9000 and the id order decides (about 1 in 2) |
| Q-cache | users/mixins.py | the list key ignores the query params | 14: CacheKeyTests 2; OrderingTests' 9 assert_order tests (asc then desc); FilterTests last_login, activated and plan_filters (3) |
| Q-listonly | users/views.py | UserListSerializer for every action | 3: ListOnlyTests super_admin_non_list and teacher_non_list (last_login appears; the annotated keys are skipped); SingleRetrieveParityTests (retrieve gains last_login) |
| Q-prefetch | users/user_list.py | drop the credit_wallet__buckets prefetch | 2: QueryCountTests both. The payload pin still passes, through the fallbacks |
| Q-gate | users/filters.py | the new filters also apply for non-super admins | 1: ListOnlyTests.test_the_list_filters_do_nothing_off_the_list_for_others |

## Plant results (logs plants/<id>.log; every restore sha256 OK)

| plant | result |
|---|---|
| Q-dates | as declared: 5 killed (failures=6 counts subTests) |
| Q-plan | as declared: 1 |
| Q-counts | as declared: 1 |
| Q-credits | 3 killed: the 2 declared, plus the possible 3rd (OrderingTests.test_credits_left; the id order put rich before held) |
| Q-cache | as declared: the 14 named |
| Q-listonly | as declared: 3 |
| Q-prefetch | as declared: 2 |
| Q-gate | **Prediction wrong: SURVIVED** (Ran 52, OK) |

Q-gate: the test was defective. UserCacheMixin keys a retrieve by instance id
alone, not by query params. The test's first, unfiltered retrieve was cached,
so every filtered retrieve after it was answered from the cache with 200,
filter or not. In production a cold cache still exposes the probe, so the
gate is needed. Test-only fix: cache.clear() before each filtered request.

Declared for the rerun:
- Q-gate-2 (the same plant on the fixed test): kills 1,
  ListOnlyTests.test_the_list_filters_do_nothing_off_the_list_for_others.
- R2e green with the fixed test: Ran 52, OK.

Side finding, not in scope: the retrieve cache ignores query params, so a
filtered retrieve inside the TTL returns the unfiltered answer. That is
existing behavior, unchanged here.

Q-gate-2 result: as declared. 1 killed (4 subTests). Restore sha256 OK. Log
plants/Q-gate-2.log.

R2e result: as declared. Ran 52, OK. Log R2e_green.log.

## EXPLAIN ANALYZE (scratch seed, not kept)

The seed was 2000 teachers with wallets and 3 buckets each, then ANALYZE,
then a 25-row page. Files: explain_*.txt.

| sort | execution time |
|---|---|
| default | 5.3 ms |
| -credits_left | 16.6 ms |
| -papers_graded | 9.3 ms |
| plan_tier | **235.8 ms** |

The seq scans are on tables that are empty or tiny in the seed, plus the
users scan that any computed sort needs. No index is needed.

The plan_tier sort was slow because the rank Case repeated the
current-plan subquery once per tier, 6 times. The fix computes the rank
inside the single plan lookup. Behavior is unchanged; OrderingTests
.test_plan_tier_follows_the_ruled_rank covers it.

Declared for this change:
- R2f green: Ran 52, OK.
- EXPLAIN plan_tier is re-run, and should land near the other sorts
  (under 30 ms).

R2f result: as declared. Ran 52, OK. Log R2f_green.log.

EXPLAIN re-run: as declared. plan_tier 14.2 ms (it was 235.8). The other
sorts: default 6.3, -credits_left 20.2, -papers_graded 10.4 ms. The
explain_*.txt files hold the re-run plans.

## N1 near, declared

The build at bb093291d. Labels: users, billing, and the 67 standing guard
labels (guards67.txt).

Environment:
- venv h284_deps_env;
- settings_worktree (test_user_list_sorting);
- --keepdb --noinput -v 2 --parallel 1;
- under flock.

Count from build_suite: 4631 (users 1091, billing 2619, plus the guards).

Expected: Ran 4631, OK, failures 0, errors 0. Skip count not predicted. The
opt-in network, Redis and real-AI guards skip, as in earlier runs.

### N1 near: result (2026-11-11)

Environment: venv h284_deps_env, settings_worktree, --keepdb, --parallel 1,
under flock ~/.machine-fullsuite.lock, test DB test_user_list_sorting.
Labels users, billing and the 67 guards.

Result: Ran 4631 (count as predicted), FAILED (failures=2, errors=1, skipped=9).
**Prediction wrong**: declared OK.

1. assignments.tests_schema_extension, 1 FAIL + 1 ERROR. Caused by my test.
   OpenApiTests generates a schema, and drf-spectacular's _load_class then
   replaces every registered extension's target_class (an import path)
   with the class itself, in place, for the rest of the process. The guard
   pins the path. Isolation (N1_diag.log): the guard alone is OK. Running
   OpenApiTests first reproduces 1 FAIL + 1 ERROR. Running OrderingTests
   first is OK. Fix, test only: OpenApiTests snapshots every registered
   extension's target_class and restores it on cleanup. After the fix,
   OpenApiTests followed by the guard gives 11 OK.
2. classrooms.tests_teacher_access_sweep.test_no_unlisted_direct_owner_scoping,
   1 FAIL. The H-38 sweep flags the two D5 lines in users/user_list.py
   (assignments_created and papers_graded scope on course__teacher). This is
   the same kind of line as the existing ALLOWED entry in classrooms/views.py
   (`Assignment.objects.filter(course__teacher=OuterRef("id"))`, "admin
   reporting aggregate per teacher; not teacher access"). Changing a guard
   is not mine to decide, so it is held for an SM ruling. Not fixed.

### N1 ruling applied (SM -16, 2026-11-11), R3 check declared

Ruling (a) applied: the two users/user_list.py lines are in the sweep's
ALLOWED. Each reason names its call site, and a comment points to the
proving test, ListOnlyTests.test_only_the_super_admin_list_builds_the_list_values
(new). That test checks that a teacher, a school admin, a student and a
createsuperuser account (is_superuser, not SUPER_ADMIN) all get 403, with
list_queryset never called. The super admin control gets 200, with exactly
one call.

Cause 1 recorded as a **cross-test pollution finding**. Generating a
drf-spectacular schema mutates OpenApiGeneratorExtension.target_class
process-wide (an import path becomes the class), so any test that generates
a schema breaks later tests that read the path. Fixed inside the test that
generates the schema (snapshot, then restore on cleanup).

R3 (declared before running). Labels: classrooms.tests_teacher_access_sweep,
assignments.tests_schema_extension, users.tests_user_list_sorting.ListOnlyTests.
Same environment as N1. Count from build_suite: see R3 line below.
Expected: OK, 0 failures, 0 errors. The sweep must pass on its own (SM
condition 3), so it is also run alone.
Count: R3a combined 25; R3b sweep alone 9. Expected: Ran 25 OK; Ran 9 OK.

R3 result: R3b (sweep alone) Ran 9 OK, as predicted. R3a Ran 25 FAILED (failures=1).
**Prediction wrong**: the new test's super admin control expected exactly one
list_queryset() call and got 2, because UserCacheMixin.get_cache_key also calls
get_queryset() (it only reads the model name, so that queryset is never
evaluated). This was a test defect. The control now asserts at least one call.
The real check, 0 calls for every non-super-admin, is unchanged.

R3c declared: same 3 labels, Ran 25, expected OK.
R3c result: Ran 25 OK, as predicted.

Retrieve cache side finding written up as a backlog item in DESIGN section 11.
The key includes the requester's id and generation, so there is no
cross-caller or cross-role leak and no STOP.

### N2 near declared (2026-11-11)

Environment: venv ~/Documents/Virtualenvs/h284_deps_env, --settings=settings_worktree,
--keepdb, --noinput, -v 2, --parallel 1, under flock ~/.machine-fullsuite.lock,
test DB test_user_list_sorting, worktree Grade-Automator-Plus-user-list-sorting.
Labels: users, billing, and the same 67 standing guard labels as N1.
Count from build_suite: 4632 (N1's 4631 plus
test_only_the_super_admin_list_builds_the_list_values).
Expected: Ran 4632, OK, failures 0, errors 0. Skip count not predicted.

### N2 near: result (2026-11-11)

Ran 4632 tests in 461.679s, OK (skipped=9). Matches the prediction (Ran
4632, OK). The skip count was not predicted; it is the same 9 as N1.
Log: N2_near.log.gz.
