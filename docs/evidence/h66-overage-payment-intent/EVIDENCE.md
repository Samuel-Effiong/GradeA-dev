# H-66: a paid overage session without a payment_intent grants nothing

**Author:** d5. **Branch:** `task/h66-overage-no-payment-intent`, base-updated
onto bundle 4's final tip `67a0681` (clean merge, `c23e71f4`).

## The change (4 points)
1. **Before:** `StripeWebhookHandler._handle_overage_checkout_completed`
   granted a "paid" overage session whether or not it carried a
   `payment_intent`. The duplicate-delivery check keys on the
   PaymentIntent, so a keyless session had no idempotency key (two event
   ids for it both granted), and nothing to match a refund or dispute on.
2. **After:** a session with no (or an empty) `payment_intent` is refused
   with an ERROR (session id, wallet id, payment_status; no address),
   after the unpaid check and **before** the cap check, and nothing is
   granted or recorded.
3. **Reach:** H-62's Gate 4 (1a) found no real signed event reaches this
   today; this is the second layer against a future flow change (coupons,
   $0 sessions, a wider AUTO_REPLAYABLE). Both callers (the webhook and the
   failed-event replay) go through the handler.
4. **Tests:** `billing/tests/test_overage_requires_payment_intent.py`
   (handler and replay paths, empty key, missing payment_status, double
   delivery, controls, and 1a's Q1: a keyless session at the cap is refused
   as keyless, not by the cap path). `test_overage_never_expires.py`'s
   fixture now carries a payment_intent.

| Commit | What |
|---|---|
| `552a3bf8` | the tests (red) |
| `3f2295cb` | the fix |
| `c23e71f4` | base update onto 67a0681 |
| `a1832485` | repro, module and battery logs |
| `58d5efb3` | 1a's Q1 as a test (SM ruling, test only); runner mutant K4 |

## Gates
| Gate | Result | Log |
|---|---|---|
| Repro at 552a3bf8 (h66-repro worktree, own DB) | 15 tests, 5 failures, all in the new module | `repro_552a3bf8.log` |
| (a) 2 modules at c23e71f4 | 15 OK | `a_modules_c23e71f4.log` |
| (b) battery at c23e71f4 (`test_h66_mut`) | 3/3 killed (K1–K3), sha-verified | `b_mutation_battery_c23e71f4.log` |
| (c) billing + 9 guards at a1832485 (docs-only over c23e71f4) | 2036 OK, wall 264 s | `c_app_billing_guards_a1832485.log.gz` |
| Q1 (rule 15.4: touched module + mutants, no regression re-run) at 58d5efb3 | module 8 OK; K1–K4 4/4 killed; 1a's Y1 (the H-66 block moved below the cap block) killed by exactly the Q1 test, restore sha-verified | `a_module_58d5efb3.log`, `b_mutation_battery_58d5efb3.log`, `mutant_Y1_1a_58d5efb3.log` |

All runs were under 0b's grants with `--settings=settings_worktree` and
`EXEMPT_EMAIL_DOMAINS=` empty, each wrapped as
`systemd-inhibit --what=idle:sleep … systemd-run --user --scope
MemoryMax=6G MemorySwapMax=0 nice -n 10 timeout -k 60 1800`.
`run_mutants.py` starts `manage.py` as a subprocess without wrappers of its
own; the runner itself ran inside that wrapper, so every mutant's test run
was in the same 6G scope and under the same 1800 s timeout (one cap and one
timeout for the battery, not per mutant).
