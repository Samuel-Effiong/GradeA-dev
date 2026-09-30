# H-60 + H-57: licence seats, Stripe text and silent PATCH no-ops

Branch `task/h60-h57-licence-seats` off `task/beta-batch-4` `8de3078`, not
`abeda10`, because H-28 rewrote `update_seats`. Author: ed (Security).
Verifier: 1a. Scope and rulings are the SM's, 2026-09-30.

## First: did H-28 already change either behaviour?

No, so neither row closes. At `8de3078`:
- **H-60:** `update_seats` still built its `ValueError` from Stripe's
  message in three places, and the view returns `str(e)` as the 400. The
  same shape is in `change_license_plan` (through
  `apply_licence_price_at_stripe`, four places), in
  `cancel_license_subscription`, and in `convert_license_to_offline`. The
  SM included all of them.
- **H-57:** `LicenseSubscriptionSerializer` accepts `max_seats` (write-only,
  validated), but `update()` applies only `auto_renew` and
  `custom_price_cents`. A PATCH changing `max_seats` answered 200 and
  changed nothing. So did a PATCH changing plan, school, admin_user,
  contract_months, billing_method, stripe_subscription_id or teacher_emails.

## H-60: fixed text, Stripe's detail kept server-side

| Site | Before (client saw) | After |
|---|---|---|
| update_seats, subscription read | "Stripe error while updating seats: {stripe text}" | "Stripe error while updating seats." + TRY_AGAIN |
| update_seats, card error | "Seat increase payment failed (card error: {stripe text}). …" | "Seat increase payment failed. Seats have not been increased." |
| update_seats, other Stripe error | "Stripe error while updating seats: {stripe text}" | "Stripe error while updating seats." + TRY_AGAIN |
| change_plan, subscription read | "Could not retrieve Stripe subscription: {stripe text}" | "Could not retrieve Stripe subscription." + TRY_AGAIN |
| change_plan, custom Price creation | "Custom price creation failed: Failed to create custom price: {stripe text}" | "Custom price creation failed." + TRY_AGAIN |
| change_plan, card error | "Card declined: {stripe text}" | "Card declined. The plan has not been changed; update the payment method and try again." |
| change_plan, other Stripe error | "Stripe error: {stripe text}" | "Stripe error while changing the plan." + TRY_AGAIN |
| cancel | "Failed to schedule Stripe cancellation: {stripe text}" | "Failed to schedule Stripe cancellation." + TRY_AGAIN |
| convert-to-offline | "Failed to cancel Stripe subscription: {stripe text}" | "Failed to cancel Stripe subscription." + TRY_AGAIN |

`TRY_AGAIN` is " Please try again; if it keeps failing, contact support."

- Each message keeps its old prefix, so the H-28 tests that pin prefixes
  ("Stripe error while updating", "Card declined", "Stripe price change
  failed", "Failed to schedule Stripe cancellation") stand unchanged.
- **Where Stripe's text still goes:**
  - The intent's `failure_reason`, which H-28's reconciliation reads. This
    is unchanged; `abandon`, `_set_status` and `undo_unpaid_change` already
    wrote it there.
  - A new `license_stripe_mutation.log_provider_error(intent, exc)`. It logs
    the licence id, intent id, exception class, Stripe error code and
    Stripe request id, and never the message.
- **Not changed:**
  - H-28's own reconciliation and lost-response log lines, which already
    carry Stripe's text server-side for a human reconciler.
  - "Plan {name} has no stripe_price_id", which is our own configuration
    text, not provider text.

## H-57: a changed non-patchable field is a 400 (FRONTEND CONTRACT CHANGE)

`LICENCE_NOT_PATCHABLE` in `billing/serializers.py` maps each field to the
message that names its route:

| Field | 400 message |
|---|---|
| max_seats | "Seats can't be changed here. Use the update_seats action." |
| plan | "The plan can't be changed here. Use the change_plan action." |
| billing_method | "… Use the convert-to-offline or convert-to-stripe action." |
| contract_months | "The contract length can't be changed after creation." |
| school | "A licence's school can't be changed after creation." |
| admin_user | "The licence admin can't be changed here." |
| stripe_subscription_id | "The Stripe subscription can't be changed here." |
| teacher_emails (non-empty) | "Teachers can't be added here. Use the add_teachers action." |

The SM's rules:
- **Refused only when changed.** A field is refused only when it is present
  AND differs from the stored value.
  - Foreign keys are compared by pk.
  - A blank and a null Stripe id count as the same "none".
  - A client that PATCHes back the whole object unchanged still gets 200,
    with `auto_renew` and `custom_price_cents` applied.
- **Nothing half-applies.** A refused PATCH applies nothing: the check is in
  `validate()`, before `update()`.
- **Teacher fields.** `teacher_emails` is refused when non-empty; an empty
  or absent list is ignored. `carry_forward_teachers` is ignored, because it
  defaults to True and only governs creating a replacement licence (SM
  ruling).

**Frontend contract change:** a PATCH that changed one of these fields used
to answer 200 (with nothing changed). It now answers 400, with the field
name as the error key. A frontend that relied on the silent 200 must call
the named action instead.

## Tests

`billing/tests/test_h60_licence_stripe_text.py` builds on H-28's
`LicencePhaseTestCase`, which puts a stateful Stripe fake on every call.
Each Stripe failure carries a SENTINEL. For every site, the raised
ValueError holds the fixed text and not the sentinel; no log line holds the
sentinel; and the log names the intent. Also:
- Where a refusal writes one, the intent's failure_reason still holds the
  sentinel.
- A view-level test shows the update_seats 400 body is free of the sentinel.

`billing/tests/test_h57_licence_patch.py` covers:
- a changed max_seats with auto_renew → 400 naming update_seats; nothing
  changes, and auto_renew is not applied;
- an unchanged max_seats with auto_renew → 200, auto_renew applied;
- every other field changed → 400 with its message, nothing changed;
- the whole object echoed back unchanged (with an empty teacher_emails and
  carry_forward_teachers true) → 200, patchable fields applied;
- a blank Stripe id echo against a null stored one → 200;
- a non-empty teacher_emails → 400 naming add_teachers;
- carry_forward_teachers alone → 200, no change.

## Merge touchpoint (expected)

The held add-teachers leak fix (`task/add-teachers-school-name-leak`, off
abeda10) edits `billing/license_service.py`: the "Skipped enrolling …" and
carry-forward log lines, which at 8de3078 still carry the teacher's email.
This branch edits the same file elsewhere (cancel, update_seats, convert).
0b handles it when bundle 4 and the fixes combine (SM).

## Gates

| Gate | Result | Log |
|---|---|---|
| Reproduce-first on 8de3078 (source reverted, tests kept) | see log | `prefix_8de3078_failing.txt` |
| Changed modules: the new tests, H-28's licence modules, the licence cancellation/admin tests and all repo-wide guards | see log | `changed_modules.txt` |
| Mutation (A1–A11 for H-60, B1–B6 for H-57) | see log | `mutation_log.txt`, `mutation_results.json` |
| ONE owning-app regression: billing | see log | `regression_billing.txt` |
| `pre-commit run mypy --all-files`, `makemigrations --check` | pass | n/a |

## Behaviour change: four H-28 tests pinned Stripe's text

Run 1 (at 230be2d) stopped at changed modules. 203 of 207 tests passed.
The 4 failures were H-28 tests whose expected client message contained
Stripe's own text, which is the disclosure H-60 removes. Each now asserts
the fixed message. The detail it used to find in the message is asserted
on the intent's `failure_reason` instead, where H-28's reconciliation reads
it and where Stripe's text still goes. Every other assertion is untouched:
the revert, the void, the guard and the intent state.

| Test | Old expected message | New expected message | Detail now asserted on the intent |
|---|---|---|---|
| test_h28_seat_phases `test_F5_a_card_error_is_not_taken_as_a_refusal` | "card error" (from `f"… ({why})"`) | "Seat increase payment failed" | `"card error" in failure_reason` |
| test_h28_seat_phases `test_the_licence_stays_guarded_while_a_card_error_is_undone` | "card error" | "Seat increase payment failed" | `"card error" in failure_reason` |
| test_h28_seat_phases `test_with_no_new_invoice_an_older_open_one_is_left_alone` | "card error" | "Seat increase payment failed" | `"card error" in failure_reason` |
| test_h28_cancel_phases `test_a_lost_response_that_did_not_land_is_read_back_and_failed` | "Request timed out" (Stripe's exception text) | "Failed to schedule Stripe cancellation" | `"Request timed out" in failure_reason` |

The refusals themselves are unchanged. The same exception type is raised
at the same point, and the intent ends in the same state. Only the client
wording changed. The stopped log is `changed_modules_run1_stopped_230be2d.txt`.
