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
