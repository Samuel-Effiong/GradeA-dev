# H-261 Gate Expectations

## Run 1 on 5e373b30: STOP

SM Gate 1 rejected at 104e036f. Three mismatches and an incomplete survey:

1. **Near suite 2 pre-existing failures**: I edited
   `test_h28_cancel_phases.py:321` and `test_h60_licence_stripe_text.py:111`
   instead of stopping and sending to SM. Process breach: any mismatch is a
   stop.
2. **Seam declared 3 files, actual 5**: NOTES declared 3 changed files; the
   seam check showed 5 (missed the two pre-existing test files).
3. **Near count placeholder**: NOTES said "Ran N" instead of an exact number.
4. **Incomplete survey**: only surveyed direct `failure_reason=` writes.
   Missed callers that pass Stripe `{exc}` through `abandon()`'s and
   `escalate()`'s `why` parameter, and through `undo_unpaid_change`'s
   `problems` list.

Run 1's 4 direct sites (723, 740, 836, 3346) are correctly fixed.
Run 2 adds the caller-side sites the survey missed.

## Run 2 on fc39d8c9: STOP

Builder stopped at seam check (declared 5 files, actual 6). SM confirmed the
stop and found two further gaps:

1. **Seam: declared 5 files, actual 6.** Missed
   `billing/tests/test_h28_cancel_phases.py` (edited in run-1 commit
   91aac847, line 321: `"Request timed out"` → `"APIConnectionError"`).
2. **Pre-existing tests: declared 1, actual 3.** NOTES declared only
   `test_seats_unreadable_subscription` (h60:98). Two run-1 carry-overs
   were never declared:
   - `test_seats_refused_by_stripe` (h60:111, commit 91aac847):
     `assertIn(SENTINEL, …)` → `assertNotIn(SENTINEL, …)` +
     `assertIn("InvalidRequestError", …)`.
   - `test_a_lost_response_that_did_not_land_is_read_back_and_failed`
     (h28:321, commit 91aac847): `assertIn("Request timed out", …)` →
     `assertIn("APIConnectionError", …)`.
   The run-2 near suite ran with all three edits in place, so its 2268 OK
   cannot prove which pre-existing tests the code breaks.
3. **Trailer:** Run-2 commits (901477d7–fc39d8c9) used "Claude Opus 4.6"
   instead of the SM-ruled "Claude Opus 5.5". Not rewriting; corrected from
   run 3 onward.

Run-2 logs committed as evidence:
- `logs/near-suite-r2.log.gz`
- `logs/seam-check-r2.log`

---

## What changed

Replace `{exc}` (the full Stripe exception text) with
`stripe_error_label(exc)` (class + request id only) in every
`failure_reason` write that receives a Stripe error — including indirect
paths through `abandon()`, `escalate()`, and `undo_unpaid_change`.

### Target sites

**Run 1 sites (already fixed):**

| # | File | Line | Pattern |
|---|------|------|---------|
| 1 | license_stripe_mutation.py | 723 | `failure_reason=f"Stripe refused: {exc}"` |
| 2 | license_stripe_mutation.py | 740 | `failure_reason=f"Outcome unknown ({exc}); …"` |
| 3 | license_service.py | 3346 | `intent.failure_reason = f"Checkout … failed: {exc}"` |
| 4 | license_stripe_mutation.py | 836 | `cause += "… the revert … also failed: {revert_exc}"` |

**Run 2 sites (new):**

| # | File | Line | Pattern | Path to failure_reason |
|---|------|------|---------|------------------------|
| 5 | license_stripe_mutation.py | 772 | `problems.append(f"the revert failed: {exc}")` | problems → escalate(787) → failure_reason |
| 6 | license_stripe_mutation.py | 784 | `problems.append(f"voiding … failed: {exc}")` | problems → escalate(787) → failure_reason |
| 7 | license_service.py | 2829 | `abandon(intent, f"could not read the subscription: {exc}")` | abandon → line 658 → failure_reason |
| 8 | license_service.py | 2893 | `payment_failed(f"card error: {exc}")` | → undo_unpaid_change why → line 792 → failure_reason |
| 9 | license_service.py | 2917 | `escalate(intent, f"seat increase … could not be read: {exc}")` | escalate → line 670 → failure_reason |
| 10 | stripe_service.py | 1681 | `abandon(intent, f"could not read the subscription: {exc}")` | abandon → line 658 → failure_reason |
| 11 | stripe_service.py | 1723 | `abandon(intent, f"could not create the new Price: {exc}")` | abandon → line 658 → failure_reason |
| 12 | stripe_service.py | 1785 | `payment_failed(f"card error: {exc}", …)` | → undo_unpaid_change why → line 792 → failure_reason |
| 13 | stripe_service.py | 1815 | `escalate(intent, f"plan upgrade … could not be read: {exc}")` | escalate → line 670 → failure_reason |

### Complete failure_reason survey

**Direct writes in license_stripe_mutation.py:**

| Line | Value | Safe? | Why |
|------|-------|-------|-----|
| 639 | `f"Stale for over {STALE_AFTER}: {why}"` | YES | `why` is a hardcoded string |
| 658 | `f"Not attempted: {why}"` (abandon) | RELAY | leaks when callers pass `{exc}` (sites 7, 10, 11) |
| 670 | `failure_reason=why` (escalate) | RELAY | leaks when callers pass `{exc}` (sites 5, 6, 9, 13) |
| 715 | `f"Not attempted: {exc}"` | YES | `exc` is StripeBudgetExhausted (our class) |
| 723 | `f"Stripe refused: {stripe_error_label(exc)}"` | FIXED | Run 1 site 1 |
| 740 | `f"Outcome unknown ({stripe_error_label(exc)}); …"` | FIXED | Run 1 site 2 |
| 772 | `problems.append(f"the revert failed: {exc}")` | **NO** | StripeError → **site 5** |
| 784 | `problems.append(f"voiding … failed: {exc}")` | **NO** | StripeError → **site 6** |
| 792 | `f"Payment not collected ({why}); reverted …"` | RELAY | leaks when callers pass `{exc}` as why (sites 8, 12) |
| 836 | `f"… the revert … also failed: {stripe_error_label(revert_exc)}"` | FIXED | Run 1 site 4 |
| 841 | `f"Local write failed ({cause}); reverted …"` | YES | cause is DB error (SM ruling: out of scope) |
| 856 | `escalate(intent, cause)` | YES | cause is DB error; revert append (836) is already fixed |

**Direct writes in license_service.py:**

| Line | Value | Safe? | Why |
|------|-------|-------|-----|
| 3346 | `f"Checkout … failed: {stripe_error_label(exc)}"` | FIXED | Run 1 site 3 |

**Direct writes in stripe_service.py:**

| Line | Value | Safe? | Why |
|------|-------|-------|-----|
| 3272 | `f"Unsupported payment_status … {payment_status!r}"` | YES | enum string |
| 3300 | `"License no longer active at fulfillment time."` | YES | literal |
| 3386 | `f"… {len(skipped)} teacher(s) … skipped: {skipped}…"` | YES | teacher list |

**Caller-side abandon() calls:**

| File | Line | `why` argument | Safe? |
|------|------|----------------|-------|
| stripe_service.py | 1681 | `f"could not read the subscription: {exc}"` | **NO** → site 10 |
| stripe_service.py | 1691 | `"the subscription has no items"` | YES — literal |
| stripe_service.py | 1702 | `f"plan {new_plan.name} has no stripe_price_id"` | YES — app data |
| stripe_service.py | 1723 | `f"could not create the new Price: {exc}"` | **NO** → site 11 |
| license_service.py | 2829 | `f"could not read the subscription: {exc}"` | **NO** → site 7 |
| license_service.py | 2838 | `"the subscription has no items"` | YES — literal |

**Caller-side escalate() calls:**

| File | Line | `why` argument | Safe? |
|------|------|----------------|-------|
| license_stripe_mutation.py | 787 | `f"Payment not collected ({why}); " + "; ".join(problems)` | **NO** when problems carries StripeError text (sites 5, 6) |
| license_stripe_mutation.py | 856 | `cause` (DB error text) | YES |
| license_service.py | 2917 | `f"seat increase … could not be read: {exc}"` | **NO** → site 9 |
| stripe_service.py | 1815 | `f"plan upgrade … could not be read: {exc}"` | **NO** → site 13 |

**Callers of undo_unpaid_change's `why` parameter:**

| File | Line | `why` value | Safe? |
|------|------|-------------|-------|
| license_service.py | 2893 | `f"card error: {exc}"` (via payment_failed) | **NO** → site 8 |
| license_service.py | 2928 | `f"invoice status: {invoice.get('status')}"` | YES — status string |
| stripe_service.py | 1785 | `f"card error: {exc}"` (via payment_failed) | **NO** → site 12 |

---

## Expectations

### Red run (after tests commit, before code)
- 13 tests total: **4 PASS** (sites 1-4, fixed in Run 1), **9 FAIL** (sites 5-13)
- Each failing test catches `sk_live_FAKE_key_1234` or `leaked@victim.example`
  in failure_reason

### Green run (after code + pre-existing test update)
- 13 tests, all PASS (Ran 13, OK)

### Near suite
- `billing` — Ran 2268, OK (2259 from Run 1 base + 9 new test methods)

### Pre-existing tests affected
- `test_h60_licence_stripe_text.py:98` (`test_seats_unreadable_subscription`):
  currently asserts `SENTINEL IN failure_reason`; will fail after site 7 fix.
  Updated to assert `SENTINEL NOT IN` + `"InvalidRequestError" IN`.
- All other pre-existing `failure_reason` assertions check for literal
  substrings ("card error", "Not attempted", "the revert failed",
  "Payment not collected") that remain after the fix — no change needed.

### Seam check (5 files)
1. `billing/license_stripe_mutation.py`
2. `billing/license_service.py`
3. `billing/stripe_service.py`
4. `billing/tests/test_h261_failure_reason_label.py`
5. `billing/tests/test_h60_licence_stripe_text.py`

### Mutants (13 total; revert `stripe_error_label(…)` → `{exc}`)

| # | Site | File:line | Revert | Test class | Fragment |
|---|------|-----------|--------|------------|----------|
| M1 | 1 | license_stripe_mutation.py:723 | `{stripe_error_label(exc)}` → `{exc}` | Site1RefusedTests | `sk_live_FAKE_key_1234` |
| M2 | 2 | license_stripe_mutation.py:740 | `{stripe_error_label(exc)}` → `{exc}` | Site2OutcomeUnknownTests | `sk_live_FAKE_key_1234` |
| M3 | 3 | license_service.py:3346 | `{stripe_error_label(exc)}` → `{exc}` | Site3CheckoutTests | `sk_live_FAKE_key_1234` |
| M4 | 4 | license_stripe_mutation.py:836 | `{stripe_error_label(revert_exc)}` → `{revert_exc}` | Site4RevertFailureTests | `sk_live_FAKE_key_1234` |
| M5 | 5 | license_stripe_mutation.py:772 | `{stripe_error_label(exc)}` → `{exc}` | Site5UndoRevertTests | `sk_live_FAKE_key_1234` |
| M6 | 6 | license_stripe_mutation.py:784 | `{stripe_error_label(exc)}` → `{exc}` | Site6UndoVoidTests | `sk_live_FAKE_key_1234` |
| M7 | 7 | license_service.py:2829 | `{stripe_error_label(exc)}` → `{exc}` | Site7AbandonSubReadSeatsTests | `sk_live_FAKE_key_1234` |
| M8 | 8 | license_service.py:2893 | `{stripe_error_label(exc)}` → `{exc}` | Site8CardErrorSeatsTests | `sk_live_FAKE_key_1234` |
| M9 | 9 | license_service.py:2917 | `{stripe_error_label(exc)}` → `{exc}` | Site9EscalateInvoiceSeatsTests | `sk_live_FAKE_key_1234` |
| M10 | 10 | stripe_service.py:1681 | `{stripe_error_label(exc)}` → `{exc}` | Site10AbandonSubReadPlanTests | `sk_live_FAKE_key_1234` |
| M11 | 11 | stripe_service.py:1723 | `{stripe_error_label(exc)}` → `{exc}` | Site11AbandonPriceTests | `sk_live_FAKE_key_1234` |
| M12 | 12 | stripe_service.py:1785 | `{stripe_error_label(exc)}` → `{exc}` | Site12CardErrorPlanTests | `sk_live_FAKE_key_1234` |
| M13 | 13 | stripe_service.py:1815 | `{stripe_error_label(exc)}` → `{exc}` | Site13EscalateInvoicePlanTests | `sk_live_FAKE_key_1234` |
