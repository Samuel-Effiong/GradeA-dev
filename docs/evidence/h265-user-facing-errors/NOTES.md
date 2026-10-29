# H-265 notes (SM, 2026-10-29)

Design: DESIGN.md (8296f11b). Tests a7b6dc95. Code 6de113f9. Base a3f5a6a1 (batch 30).
Private venv with batch-30 pins: `h266_list_errors_dict_env`.

## Classification of the reachable `raise ValueError(` (123, name-based call graph at the base)
- **Converted to `BillingRequestError` (98) / `QueryParamError` (4):** every sentence written for
  the user: refusals ("License already has this many seats."), caller input ("blocks must be a
  positive integer."), already-scrubbed Stripe text (`describe_stripe_error(...)`, `TRY_AGAIN`),
  and the QA time-travel guidance (QA staff are its users).
- **Kept a bare `ValueError` (19), now shown as the site's fallback:**
  - setup faults: license_service 268, 270 (plan has no overage pricing / block size);
    stripe_service 605, 734, 829, 1350, 1705, 2001, 2005 (plan has no stripe_price_id);
    license_service 2618 (same, licence plan change).
  - Stripe-state invariants: license_service 2612, 2835; stripe_service 1692;
    license_stripe_mutation 403.
  - programming invariants: models 159 (tier ranking); services 528, 535, 1041;
    qa_time_travel 194, 219, 692.
  (Line numbers at the base.)

## Expected red run (written before the gate)
Tests at the tip; the 7 site files taken back to a3f5a6a1 (license_views, views,
license_overage_offline_views, dashboard/views, serializers, qa_time_travel, views_admin_credits);
the error classes, registry and converted raises stay at the tip (call-site revert).

"Ran 23", `FAILED (failures=20)`, 20 FAIL blocks, 0 ERROR. Every `*_hides_library_error_text`
test fails on the message assertion; fragment `'sk_live_LEAK internal detail' !=`
(site 18: `'Admin user: sk_live_LEAK internal detail' !=`).
Green: the 3 `*_shows_a_written_refusal` guards.

## Expected green, near suite (written before the gate)
- own: `billing.tests_h265_user_facing_errors` Ran 23, OK.
- near: `billing dashboard AutoGrader.tests_error_messages` **Ran 2730**, OK (2278 + 430 + 22,
  from a DiscoverRunner id list at the tip, 0 load failures; the 23 H-265 tests are inside billing).
  Skips: any skip must be an opt-in (`RUN_*`) skip; any other skip is a stop.
- makemigrations --check: no changes.
- seam: production files changed vs a3f5a6a1 are exactly these 13: AutoGrader/error_messages.py,
  billing/errors.py, billing/license_overage_offline_views.py, billing/license_service.py,
  billing/license_views.py, billing/qa_time_travel.py, billing/serializers.py, billing/services.py,
  billing/stripe_service.py, billing/views.py, billing/views_admin_credits.py,
  dashboard/query_params.py (new), dashboard/views.py.

## Expected mutants (written before the gate). Each runs the named module(s).
| ID | What | Expected | Killed by (fragment) |
|---|---|---|---|
| M1 | license_views select_plan site back to `str(e)` | KILLED | test_change_plan_hides_library_error_text (`!=`) |
| M2 | `BillingRequestError` removed from the user-facing registry | KILLED | the 3 `*_shows_a_written_refusal` guards (`!=`) |
| M3 | `QueryParamError` removed from the registry | KILLED | dashboard.tests_dashboard_audit_fixes ... test_non_numeric_limit_is_a_400_not_a_500 (`not found in`) |
| M4 | "License already has this many seats." raised as a bare ValueError again | KILLED | test_update_seats_shows_a_written_refusal (`!=`) |
| M5 | dashboard unit-performance site back to `str(exc)` | KILLED | test_unit_performance_hides_library_error_text (`!=`) |
| M6 | views_admin_credits site back to `str(e)` | KILLED | test_top_up_credits_hides_library_error_text (`!=`) |
Other failing tests under a mutant are recorded, not hidden.

## Behaviour notes
- `describe_user_error` and `is_user_facing_error` now pass the converted sentences through
  wherever else they are used (background-task error fields; students/views.py:173). Those are
  sentences written for users, so this is the intended direction; the batch full suite covers it.
- No status code changes. qa_time_travel.py 906 (Stripe `user_message` in a QA-only tool) is out
  of scope.

## Run 1 on 326eb793 (SM, 2026-10-29): STOP at the near suite
Red run as written (Ran 23, 20 FAIL); makemigrations clean; seam the 13 files as written.
Near: Ran 2730 (as written) but FAILED (failures=2, skipped=2; both skips RUN_REAL_AI):
- billing.tests.test_h255_stripe_error_text ... test_a_price_mismatch_on_overage_purchase_answers_without_the_price_id:
  shown "We couldn't start the overage checkout. Please try again." where H-255's fixed sentence
  ("The price of this purchase has changed. ...") is expected.
- billing.tests.test_h60_licence_stripe_text ... test_the_seats_route_answers_with_the_fixed_text:
  shown "We couldn't update the seats. ..." where "Seat increase payment failed. ..." is expected.
Cause (survey defect): the survey only looked for `raise ValueError(`. It missed
(1) ValueErrors built and raised later: license_service.py 2868 (`return ValueError("Seat increase
payment failed. ...")`) and stripe_service.py 1770 (`return ValueError(message)`, fixed texts at
1787/1843/1848), and (2) existing ValueError subclasses whose text is written to be shown:
OveragePriceUnavailable, OveragePriceMismatch (overage_pricing.py), LicenceBillingChangeInProgress
(license_stripe_mutation.py), SubscriptionExpiredDuringRequest (stripe_service.py),
PlanAssignmentRefused (plan_policy.py; its one wrap of another ValueError's text, services.py 432,
wraps _assert_not_on_the_license_track, which raises only converted user sentences).
The two failing tests are right; the code is wrong.

SM ruling (as SM; this row is the SM's own): fix in H-265, not in the tests.
- the 2 built-later ValueErrors become BillingRequestError;
- the 5 subclasses derive from BillingRequestError (still ValueErrors);
- a new test pins every user-written ValueError subclass in billing as user-facing
  (is_user_facing_error and describe_user_error pass its text), red before the fix for the 5;
- widened survey: `grep -rnE "(return|=|\() *ValueError\("` and every `class X(ValueError)` in
  billing/ and dashboard/: the 2 above and the 5 above are the complete list.
Then the whole gate reruns with expectations revised below before the run.

## Revised expectations for run 2 (written before the run; tests dd657d3f, fix f0c52c97)
- red (same call-site revert of the 7 site files): "Ran 28", `FAILED (failures=20)`, the same 20
  `*_hides_library_error_text` FAIL blocks; green: the 3 guards and the 5 new
  `UserWrittenValueErrorClassesTests` (the classes are at the tip).
  (The 5 new tests were red at 7bf39c55, before the fix: Ran 5, FAILED (failures=5), "False is not true".)
- own: Ran 28, OK.
- near `billing dashboard AutoGrader.tests_error_messages`: **Ran 2735**, OK (2730 + 5; DiscoverRunner
  id list, 0 load failures). Skips: opt-in (`RUN_*`) only; run 1 had 2, both RUN_REAL_AI.
- seam: the 13 files plus billing/license_stripe_mutation.py, billing/overage_pricing.py,
  billing/plan_policy.py = 16.
- mutants M1-M6 as before, plus
  | M7 | `OveragePriceMismatch` back to a bare `ValueError` | KILLED | test_overage_price_mismatch_is_user_facing (`False is not true`) |

## Run 2 on 333ed425 (SM, 2026-10-29): GREEN, matches the revised expectations
- red: Ran 28, FAILED (failures=20), the 20 `*_hides_library_error_text` blocks (red_run.txt).
- own: Ran 28, OK (own.txt).
- near: Ran 2735, OK (skipped=2), both skips RUN_REAL_AI opt-in (dashboard.tests_real_ai_chat) (near_summary.txt; full log archived, sha256 in the summary).
- makemigrations: no changes. seam: 16 files, as declared (seam.txt).
- mutants M1-M7: all KILLED by their declared tests (mutation_results.json, mutant_logs/).
  M2 (BillingRequestError out of the registry) also failed the 5 `UserWrittenValueErrorClassesTests`.
  That follows from the design (those classes derive from BillingRequestError). The declaration named the killing
  tests, not an exclusive set, so this is recorded, not a mismatch.
- Runner output: gate_out_run2.txt (gate exit=0).
