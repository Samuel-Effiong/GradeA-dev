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
