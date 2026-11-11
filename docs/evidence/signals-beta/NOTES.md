# Signals sort and filter, beta (brief 4, SM -31 (was -16), 2026-11-11): run notes

Owner approved for beta (2026-11-11), the same bug-fix path as brief 3. Base
origin/beta 44904774a (includes the user-list port), branch
task/signals-beta, worktree Grade-Automator-Plus-signals-beta, test DB
test_signals_beta. Environment for every run: venv
~/Documents/Virtualenvs/h284_deps_env, --settings=settings_worktree,
--keepdb, --noinput, under flock ~/.machine-fullsuite.lock.

### M0 (survey, declared): query counts on beta's base

A scratch test (copied into billing/ for the run, deleted after, never
committed) with N teacher BetaProfiles (N = 5 and 30), a super admin client,
cold cache:
- GET analytics beta/intent-signals at page_size 5 and 25;
- GET intent-signal-distribution with 5 and with 30 profiles;
- GET /api/v1/users/ at page_size 5 and 25.
Expected:
- intent-signals is fixed (count + page select_related("user"); the flags
  are computed in Python from columns), 5 = 25;
- distribution is fixed (one select_related query), 5 = 30;
- the user list is fixed at 13 (M1 of brief 3).
Exact numbers are not predicted beyond the user list's 13.

M0 result: intent-signals 9 = 9 (page 5/25), distribution 8 = 8 (5/30 profiles), user list 13 = 13, as predicted (all already fixed). Log M0_before_count.log; the scratch test was deleted.

## Rulings (SM -31 on bc5eafb7c): GO. Recorded in DESIGN sections 9-11.

### P0 (declared): signal payload pins captured on beta's base, before any product change

billing/tests/test_intent_signals_pins.py, frozen clock 2026-06-15 12:00 UTC.
Fixture: 5 teachers (signal counts 4, 3, 1, 0, 2, with distinct scores) and
a school admin's profile. Pinned: intent-signals (no params), the
distribution, summary, and the detail for a teacher and for the non-teacher.
Command: INTENT_SIGNALS_PIN_WRITE=1, label
billing.tests.test_intent_signals_pins.IntentSignalsPinTests.test_the_signal_payloads_are_what_they_were.
Expected: Ran 1, OK (skipped=1, "pin written"). Then the whole file without
the variable: Ran 2, OK.

P0 result: pin written (Ran 1, OK skipped=1), then the file Ran 2, OK. Log
P0_pin_capture.log. Checked: leads in score order; the boundary on both
sides; the non-teacher in summary only (total 6); distribution
{1:1, 2:1, 3:1, 4:1}.

### R1 red (declared): tests only, beta's base product code

Labels: billing.tests.test_intent_signals (21),
users.tests_user_list_signals (26), billing.tests.test_intent_signals_pins
(2), users.tests_user_list_sorting + users.tests_user_list_payload_pin (54;
the pin now also drops SIGNAL_KEYS). Count from build_suite: 103.
Expected: Ran 103, FAILED. By distinct test, **38 fail and 65 pass**.

billing.tests.test_intent_signals: 6 pass and 15 fail.
- Pass:
  - LeadOrderingTests.test_no_param_keeps_todays_order (params are ignored
    today);
  - BoundaryTests 3 (they pin today's inclusive rule);
  - LeadQueryCountTests 2 (both endpoints are already fixed, and the
    ignored params change nothing).
- Fail:
  - LeadOrderingTests 5 (signals/-signals fall back to score order; the
    fixtures make that differ);
  - LeadFilterTests 5 (filters ignored; the bad value gives 200);
  - ClockTests 2 (error: no module billing.intent_signals);
  - AgreementTests 1 (KeyError: the user list has no flags);
  - LeadOpenApiTests 2 (no ordering or filter params; the example shows 3
    flags).

users.tests_user_list_signals: 3 pass and 23 fail.
- Pass:
  - SignalFilterTests.test_the_signal_filters_do_nothing_off_the_list_for_others;
  - SignalListOnlyTests.test_non_list_responses_carry_none_of_the_five;
  - SignalQueryCountTests 1.
- Fail:
  - SignalValueTests 6 (KeyError);
  - SignalOrderingTests 6 (users are named so first_name, id order cannot
    pass);
  - SignalFilterTests 4;
  - SignalListOnlyTests.test_the_list_carries_all_five;
  - SignalCacheKeyTests 2;
  - SignalClockTests 2 (error);
  - SignalOpenApiTests 2.

The 2 signal pins and the 54 user-list tests pass.
Possible wrong-reason failures to watch: the OpenAPI example's structure (a
paginated wrapper) could make test_the_example_shows_all_four_flags fail on
a KeyError rather than on the 3 keys.
