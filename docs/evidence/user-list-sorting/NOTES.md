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
