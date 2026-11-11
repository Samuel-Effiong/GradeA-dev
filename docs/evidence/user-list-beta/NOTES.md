# User list sorting, beta port (brief 3, SM -16, 2026-11-11): run notes

Owner approved for beta as a bug fix (2026-11-11). Base origin/beta
2c8165b69, branch task/user-list-beta, worktree
Grade-Automator-Plus-user-list-beta, test DB test_user_list_beta.
Environment for every run: venv ~/Documents/Virtualenvs/h284_deps_env,
--settings=settings_worktree, --keepdb, --noinput, under flock
~/.machine-fullsuite.lock.

### M0 (survey, declared): the list's query count on beta's base

The scratch test (scratchpad qcount_test.py, copied into users/ for the run
and deleted afterwards, never committed) runs the super admin list with 30
teachers that have wallets, at page_size 5 and 25. users/views.py,
users/serializers.py and billing/serializers.py are byte-identical to the
staging base aeb8b157e, where 25 rows cost 309 queries.
Expected: 25 rows = 309. 5 rows not predicted. The test DB is created on
this first run.

M0 result: 5 rows = 69 queries, 25 rows = 309 (as predicted for 25). Log
M0_before_count.log. The scratch test was deleted and not committed.

## Rulings (SM -16 on 64cbbf845): GO

Q-B1 = FLOOR. credits_left equals beta's display_balance for every value,
negatives included, with no clamp, and sorting stays on raw credits. Plan
approved: re-apply, P0 pin first, 69/309 -> fixed 13, Q-credits plant = drop
the expiry filter, near = users + billing + guards60.

### P0 (declared): payload pin captured on beta's base, before any product change

The tests are staging's final versions, adapted for beta (no holds:
reserved removed from the wallet helper, parity pinned to display_balance
with negative fixtures, the pin fixture loses reserved=1_500). No product
file is changed. Command: USER_LIST_PIN_WRITE=1, label
users.tests_user_list_payload_pin.ListPayloadPinTests.test_the_list_payload_is_what_it_was.
Expected: Ran 1, OK (skipped=1, "pin written"). Then
test_the_pin_is_not_vacuous, expected Ran 1 OK.

P0 result: pin written (Ran 1, OK skipped=1). Not-vacuous check Ran 1, OK.
Log P0_pin_capture.log. **Beta's pin is byte-identical to staging's**
(diff against f90bb9b23:users/test_data/user_list_payload_pin.json is empty).

### R1 red (declared): tests only, beta's base product code

Labels: users.tests_user_list_sorting, users.tests_user_list_payload_pin.
Count from build_suite: 54. Expected: Ran 54, FAILED. By distinct test,
**43 fail and 11 pass**.

The 11 that pass:
- ListOnlyTests:
  - test_a_teacher_still_cannot_list
  - test_super_admin_non_list_responses
  - test_teacher_non_list_responses
  - test_the_sign_in_payload
  - test_the_list_filters_do_nothing_off_the_list_for_others (the filters
    are ignored on the base)
- OrderingTests:
  - test_an_unknown_key_is_ignored
  - test_the_old_keys_still_work
- FilterTests.test_the_date_bounds_are_inclusive (vacuous on the base)
- the 3 payload pin tests

The 43 that fail:
- KeyError on the new fields:
  - DateValue 2, PlanValue 7, CountValue 3, CreditValue 3;
  - Parity 3 (including the new negative-floor test);
  - ListOnlyTests.test_the_list_carries_all_nine.
- test_only_the_super_admin_list_builds_the_list_values: AttributeError,
  because users.views has no list_queryset.
- OrderingTests 12 (the ignored keys fall back to first_name order).
- FilterTests 5.
- QueryCountTests 2.
- CacheKeyTests 2.
- OpenApiTests 2.

Caveat, as on staging: id tiebreak and two keys combined depend on random
tie order. Predicted to fail.

R1 result (R1_red.log): Ran 54, FAILED (failures=54, errors=19). The totals
count subTests. By distinct test: **43 fail and 11 pass, the same sets as
declared.** Both caveat tests failed, as predicted. Each failure is for the
declared reason: KeyError on the new keys; AttributeError (no
users.views.list_queryset); OpenApi "'last_login_after' not found" and
"'last_login' not found in set()".

## Build (product)

Staging's final users/user_list.py, list_serializers.py, filters.py and
views.py, plus the sweep's 2 ALLOWED entries. Beta's copies of those files
were byte-identical to the staging base, so this equals applying the diff.
One beta change: credits_left_raw is the unclamped live sum with no
reserved_credits, and credits_left is Cast(Floor(Decimal(raw) / 1000)),
matching display_balance (Q-B1). billing/serializers.py is not changed.

### R2 green (declared)

Labels: users.tests_user_list_sorting users.tests_user_list_payload_pin classrooms.tests_teacher_access_sweep assignments.tests_schema_extension.
Count from build_suite: 72. Expected: Ran 72, OK.

R2 result (R2_green.log): Ran 72, OK, as predicted.

## Plants (declared before any plant runs)

The harness plant_ulb.py is staging's plant_uls.py with Q-credits changed
(it is copied into plants/ after the runs). One plant per run: copy, sha256
equals HEAD, plant, run the 2 labels (54 tests) under the flock, restore,
sha256 again, then delete the copy. Logs go to plants/<id>.log, restore
sha lines to plants/shas.txt.

| plant | file | change | killed (declared) |
|---|---|---|---|
| Q-dates | users/user_list.py | activated_at reads date_joined | 5: DateValueTests both; OrderingTests.test_activated_at; FilterTests.test_activated_after_and_before; OrderingTests.test_id_breaks_every_tie_in_both_directions |
| Q-plan | users/user_list.py | the individual subscription first in the precedence | 1: PlanValueTests.test_a_license_teacher_allocation_wins_over_an_individual_one |
| Q-counts | users/user_list.py | papers_graded drops ai_graded_at not null | 1: CountValueTests.test_papers_graded_counts_ai_graded_submissions_only |
| Q-credits (beta) | users/user_list.py | the live-bucket sum drops its expiry filter | 1: CreditsLeftParityTests.test_each_fixture_matches_display_balance (subtests "expired bucket", "live, expired and used"). The pin and retrieve parity survive: they drop the nine keys, and the wallet serializer has its own expiry check |
| Q-cache | users/mixins.py | the list key ignores the query params | 14: CacheKeyTests 2; OrderingTests' 9 assert_order tests; FilterTests last_login, activated and plan_filters (3) |
| Q-listonly | users/views.py | UserListSerializer for every action | 3: ListOnlyTests super_admin_non_list and teacher_non_list; SingleRetrieveParityTests |
| Q-prefetch | users/user_list.py | drop the credit_wallet__buckets prefetch | 2: QueryCountTests both |
| Q-gate | users/filters.py | the new filters also apply for non-super admins | 1: ListOnlyTests.test_the_list_filters_do_nothing_off_the_list_for_others |

## Plant results (logs plants/<id>.log, sha lines plants/shas.txt; every restore sha256 OK)

**All 8 as declared**, by distinct test (the failure totals count subTests):
Q-dates 5, Q-plan 1, Q-counts 1, Q-credits 1 (2 subTests), Q-cache 14,
Q-listonly 3, Q-prefetch 2, Q-gate 1. No .orig copy is kept; the harness
deletes it after a matching restore.

### M1 (declared): the fixed query count on beta's head

The scratch measure test, the same one as M0 (copied in, run, deleted, never
committed). Expected: 5 rows = 13 queries and 25 rows = 13, the same fixed
count as staging.

M1 result: 5 rows = 13, 25 rows = 13, as predicted (69/309 -> 13). Log M1_fixed_count.log.

### N1 near (declared)

Environment: venv ~/Documents/Virtualenvs/h284_deps_env, --settings=settings_worktree,
--keepdb, --noinput, -v 2, --parallel 1, under flock ~/.machine-fullsuite.lock,
test DB test_user_list_beta, worktree Grade-Automator-Plus-user-list-beta,
HEAD = this commit's parent tree, plus this declaration.
Labels: users, billing, and the 60 standing guard labels that exist on beta
(guards60.txt: the 67 minus the 7 named in DESIGN section 5).
Count from build_suite at HEAD: 4356 (base 4302 + the 54 new tests).
Expected: Ran 4356, OK, failures 0, errors 0. Skip count not predicted.
