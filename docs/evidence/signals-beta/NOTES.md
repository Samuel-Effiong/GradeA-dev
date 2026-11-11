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

R1 result (R1_red.log): Ran 103, FAILED (failures=65, errors=29; the totals
count subTests). By distinct test, 40 fail and 63 pass.
**Prediction wrong**: declared 38 and 65.
- LeadQueryCountTests, both tests, failed (declared to pass): "7 != 9" and
  "6 != 8". This was a test defect. The first request of a test pays 2
  one-time queries, and the base count was taken on that first request.
  Fixed in the test only: count() now makes one warm-up request first.
- LeadOpenApiTests, both tests, failed as declared but **for the wrong
  reason**: the path lookup matched nothing ([]). The schema path has no
  trailing slash, the same trap as brief 2's R1. Fixed in the test only
  (match with rstrip("/")).
Every other failure was in the declared set, for the declared reason.

### R1b (declared): the 4 corrected tests on the base

Labels: billing.tests.test_intent_signals.LeadQueryCountTests and
billing.tests.test_intent_signals.LeadOpenApiTests (4 tests).
Expected:
- LeadQueryCountTests 2 pass;
- LeadOpenApiTests 2 fail for the right reasons: "'ordering' not in params"
  (KeyError) and the example's 3-key flags (a set mismatch, no
  frequent_user).

R1b result (R1b_red.log): Ran 4, FAILED (errors=2).
- LeadQueryCountTests: 2 pass, as declared.
- test_the_ordering_and_filters_are_documented: KeyError 'ordering', as
  declared.
- **Prediction wrong** for test_the_example_shows_all_four_flags: TypeError.
  drf-spectacular wraps a paginated list's example in {count, next,
  previous, results}, so the test iterated the dict's keys. This was the
  risk named in the R1 declaration. Test defect, fixed in the test only
  (read "results").

### R1c (declared)

Label: billing.tests.test_intent_signals.LeadOpenApiTests.test_the_example_shows_all_four_flags.
Expected: Ran 1, FAILED (failures=1, 2 subTests), a set mismatch: the 3
example flags without frequent_user.

R1c result (R1c_red.log): Ran 1, FAILED (errors=1), TypeError again.
**Prediction wrong.** Each example value is wrapped as ONE item of results,
so the decorator's list of two rows renders as `results: [[row, row]]`, a
list inside the list. That is a real docs defect beside the 3-key flags.
The test now asserts each documented row is an object with the 4 flags.
The product fix (the SM's "fix the example", docs only) writes each example
as a single row, so it renders as `results: [row]`.

### R1d (declared)

The same single test. Expected: Ran 1, FAILED (failures=1): AssertionError,
the row is a list, not a dict (the nesting).

R1d result (R1d_red.log): Ran 1, FAILED (failures=1), AssertionError: the row is a list (the nesting), as declared. Red is now right for all 103 tests: 40 fail, each for its declared reason (after the R1 corrections above).

Correction to the line above: after the R1 test fixes the 2 LeadQueryCountTests pass on the base (R1b), so red stands at **38 fail and 65 pass**, the originally declared sets. R1's 40 is the uncorrected run.

## Build (product)

- New billing/intent_signals.py: flag_q, expressions, annotate_signals,
  ordering_terms, filter_signals, SignalFilterSet, clock and request_now.
- billing/views.py: the distribution, summary, intent_signals and
  intent_signal_detail read it, and every hand-written copy of the rules is
  gone. intent-signals gets the optional ordering and the filters (a bad
  value raises translate_validation, a 400). OpenAPI: the ordering (enum and
  ORDERING_DOC) and the 5 filters are documented; the example is one row
  per example with all 4 flags.
- users/user_list.py: ensure_signals (beta_profile__, teachers only), the
   key in apply_ordering and ordering_values, list_queryset(now).
- users/filters.py: the 5 filters behind the super admin gate.
- users/list_serializers.py: 5 read-only keys.
- users/views.py: ordering_fields, list_queryset(now=request_now), and
  ORDERING_DOC in the list's ordering description.

### R2 green (declared)

Labels: billing.tests.test_intent_signals users.tests_user_list_signals billing.tests.test_intent_signals_pins users.tests_user_list_sorting users.tests_user_list_payload_pin billing.tests.test_conversion_probability_task billing.tests.test_custom_ai_prompt_beta classrooms.tests_teacher_access_sweep assignments.tests_schema_extension.
Count from build_suite: 138. Expected: Ran 138, OK.

(Shell quoting note: in the Build list above, "the  key in apply_ordering"
should read "the `signals` key in apply_ordering". The backticks were run by
the shell when the heredoc was written.)

R2 result (R2_green.log): Ran 138, FAILED (failures=2). **Prediction wrong.**
Both are test_a_bad_value_is_a_400 (LeadFilterTests, SignalFilterTests), on
the flag subtest ({"is_power_grader" / "at_80_percent": "sometimes"}): 200,
not 400. **Product defect**: django-filter's BooleanFilter treats an unknown
value as "not given" and ignores it, but the approved design says a bad value
is a 400. Fix (product): the 4 flag filters on both endpoints are
ChoiceFilters accepting only "true" and "false" (billing.intent_signals.flag_filter;
OpenAPI enum ["true", "false"]). The brief 2 has_plan filter keeps its old
lenient BooleanFilter, unchanged.

### R2b green (declared)

The same 9 labels, Ran 138. Expected: OK.

R2b result (R2b_green.log): Ran 138, OK, as predicted.

### Before the plants: a gap found while declaring plant 4 (R3, declared)

Tracing plant 4 ("intent-signals always applies the signals order") showed it
would SURVIVE. The pin fixture's score order equals its count order, so
neither the pin nor test_no_param_keeps_todays_order can tell the default
from -signals. Added
LeadOrderingTests.test_no_param_keeps_the_score_order_when_counts_disagree
(three teachers whose scores disagree with their counts; no param and three
ignored values must keep the score order).
R3 label: that test. Expected: Ran 1, OK on the product.

R3 result (R3_gap_test.log): Ran 1, OK, as predicted.
