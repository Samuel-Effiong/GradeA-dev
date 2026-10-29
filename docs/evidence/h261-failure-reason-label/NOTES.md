# H-261 Gate Expectations

## What changed
Replace `{exc}` (the full Stripe exception text) with
`stripe_error_label(exc)` (class + request id only) in every
`failure_reason` write that receives a Stripe error.

### Target sites
| # | File | Line (approx) | Pattern |
|---|------|---------------|---------|
| 1 | billing/license_stripe_mutation.py | 723 | `failure_reason=f"Stripe refused: {exc}"` |
| 2 | billing/license_stripe_mutation.py | 740 | `failure_reason=f"Outcome unknown ({exc}); read-back shows not applied"` |
| 3 | billing/license_service.py | 3342 | `intent.failure_reason = f"Checkout session creation failed: {exc}"` |
| 4 | billing/license_stripe_mutation.py | 836 | `cause = f"{cause}; the revert at Stripe also failed: {revert_exc}"` |

### All failure_reason writes in billing/ — safety assessment
| File | Line (approx) | Value written | Safe? | Why |
|------|--------------|---------------|-------|-----|
| license_stripe_mutation.py | 658 | `f"Not attempted: {why}"` | YES | `why` is a string param from internal code (`abandon`) |
| license_stripe_mutation.py | 670 | `why` | YES* | `why` is a string param from `escalate`; *except when Site 4 flows into it — see Site 4 |
| license_stripe_mutation.py | 715 | `f"Not attempted: {exc}"` | YES | `exc` is `StripeBudgetExhausted`, our own class |
| license_stripe_mutation.py | 723 | `f"Stripe refused: {exc}"` | **NO** | `exc` is `stripe.error.StripeError` — TARGET Site 1 |
| license_stripe_mutation.py | 740 | `f"Outcome unknown ({exc}); …"` | **NO** | `exc` is `stripe.error.StripeError` — TARGET Site 2 |
| license_stripe_mutation.py | 789 | `f"Stripe refused: {exc}"` | YES | Line 787 checks `isinstance(exc, payment_errors)` — `payment_errors` are exceptions the caller handles; this path re-raises for the caller, not stored |
| license_stripe_mutation.py | 836 | `f"{cause}; the revert at Stripe also failed: {revert_exc}"` | **NO** | `revert_exc` is `stripe.error.StripeError` — TARGET Site 4 |
| license_stripe_mutation.py | 841 | `f"Local write failed ({cause}); reverted at Stripe"` | YES | `cause` is `f"{type(exc).__name__}: {exc}"` where `exc` is a DB error, not a Stripe error |
| license_service.py | 3342 | `f"Checkout session creation failed: {exc}"` | **NO** | `exc` is `stripe.error.StripeError` — TARGET Site 3 |
| stripe_service.py | 3272-3273 | enum string (payment_status) | YES | no Stripe text |
| stripe_service.py | 3300 | literal string | YES | no Stripe text |
| stripe_service.py | 3386-3390 | teacher list | YES | no Stripe text |

## Expectations

### Red run
- 4 tests, all FAIL
- Site 1: `assertNotIn` catches `sk_live` or `victim.example` in failure_reason
- Site 2: same — `APIConnectionError` message leaks into failure_reason
- Site 3: same — `AuthenticationError` message leaks into failure_reason
- Site 4: same — revert error message leaks into escalated failure_reason

### Green run
- 4 tests, all PASS (Ran 4, OK)

### Near suite
- `billing` — Ran N, OK (no regressions; N from discovery diff)

### Seam check
- Changed files outside `docs/`: billing/license_stripe_mutation.py,
  billing/license_service.py, billing/tests/test_h261_failure_reason_label.py

### Mutants (one per target site, revert to `{exc}`)
- M1 (Site 1): revert line 723 to `f"Stripe refused: {exc}"` → FAIL
- M2 (Site 2): revert line 740 to `f"Outcome unknown ({exc}); …"` → FAIL
- M3 (Site 3): revert line 3342 to `f"Checkout session creation failed: {exc}"` → FAIL
- M4 (Site 4): revert line 836 to `f"{cause}; the revert at Stripe also failed: {revert_exc}"` → FAIL
