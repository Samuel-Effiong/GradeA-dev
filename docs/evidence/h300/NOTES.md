# H-300: one school overage purchase is fulfilled at most once (beta)

Owner approved the fix for beta on 2026-11-09 (relayed by the SM). Branch
task/h300-double-grant off origin/beta a3736b83c. The survey (roads, verdict,
characterization run on staging) is on scratch branch task/h300-survey,
docs/evidence/h300/NOTES.md there (114d67d3f, a71bc5dc3; not pushed).

## The bug, in one paragraph

`StripeWebhookHandler._handle_license_overage_checkout_completed` stops a
second run only when `intent.status == COMPLETED`. A purchase where a
teacher left before payment is saved FAILED with its grants made, so a
second run of the same checkout grants the remaining teachers again and
writes a second BillingTransaction. On beta the road is: the worker commits
and dies before SUCCEEDED, the sweeper marks the started claim FAILED, and
`replay_stripe_events --apply` re-runs it (or a redelivery claims the FAILED
row). The license-inactive branch, re-run, also writes a second
BillingTransaction.

## The fix (declared)

In the handler, right after the intent is locked:

1. Already fulfilled: return when `intent.completed_at is not None`
   (written only on the grant path; the only writer of this model's
   completed_at), or when a CreditLedger row carries
   `metadata.intent_id == str(intent.id)`, scoped to the wallets of the
   intent's teachers (the unscoped JSON lookup is a sequential scan of
   CreditLedger, see `_overage_already_granted`). The existing COMPLETED
   check stays.
2. Already recorded as not granted: return when the intent is FAILED and
   already holds this session's PaymentIntent (the unpaid and
   license-inactive branches write it), so a replay writes no second
   BillingTransaction.

Log lines only (logger.info); no user-facing text changes.

## Red 01 (declared before the run)

billing.tests.test_h300_reprocess, count from build_suite: **4**.

| Test | Predicted on beta today |
|---|---|
| b1 skipped teacher, road B replay | FAIL: `(10000, 10000, 0) != (5000, 5000, 0)`, the double grant |
| b4 control, full purchase, road B replay | PASS: the COMPLETED guard holds |
| c1 license inactive, road B replay | FAIL: `2 != 1` BillingTransactions |
| d1 belt: ledger row alone, completed_at cleared, replay | FAIL: `(10000, 10000) != (5000, 5000)` |

Predicted: **Ran 4, FAILED (failures=3)**. Log: `01_red.log`.

Red 01 result (`01_red.log`): **Ran 4, FAILED (failures=2)**: b1
`(10000, 10000, 0) != (5000, 5000, 0)` and d1 `(10000, 10000) != (5000,
5000)`; b4 and c1 pass. **Prediction wrong**: c1 was predicted to fail
(`2 != 1`) and passed.

Why: `BillingTransactionService.record` (billing/billing_transaction_service.py)
is an idempotent `get_or_create` keyed on the PaymentIntent, so a replay
updates the one row and never writes a second. The survey's claim
"each re-run also writes a second BillingTransaction" was wrong (for the
grant path and the license-inactive path alike). The license-inactive and
unpaid branches therefore duplicate nothing on a replay: the inactive one
re-saves the same intent fields and re-logs its ERROR; the unpaid one writes
no record at all. Decision, reported to the SM: fix step 2 (the "already
recorded as not granted" return) is NOT added, because no test can go red
for it and its plant could not be killed. c1 stays as a regression test
pinning the existing upsert.

## The fix

`_handle_license_overage_checkout_completed`: after the COMPLETED check,
return when `intent.completed_at is not None` or
`_license_overage_already_granted(intent)` (a CreditLedger row with
`metadata.intent_id == str(intent.id)` in a wallet of the intent's
teachers). One logger.info line; no user-facing text.

Green 02 (declared before the run): billing.tests.test_h300_reprocess, 4.
Predicted: **Ran 4, OK**. Log: `02_green.log`.

Plants (one per run, declared before each; labels: the same 4 tests; the
file restored from a saved copy, sha256 checked):

- P1, the completed_at check removed (the belt alone): predicted
  **Ran 4, OK, SURVIVES by design**: in b1 the belt finds the ledger rows;
  d1 already clears completed_at, so it is the belt's own test. Log
  `03_plant_p1.log`.
- P2, the belt removed (completed_at alone): predicted **Ran 4, FAILED
  (failures=1)**: d1 `(10000, 10000) != (5000, 5000)`; b1 passes on
  completed_at. Killed by d1. Log `04_plant_p2.log`.
- P3, both removed: predicted **Ran 4, FAILED (failures=2)**: b1
  `(10000, 10000, 0) != (5000, 5000, 0)` and d1 `(10000, 10000) != (5000,
  5000)`. Killed by b1 with the double-grant fragment. Log `05_plant_p3.log`.

Green 02 result (`02_green.log`): Ran 4, OK. **As predicted.**

Plant results (each restored, sha256 MATCHED 10dc88234f35cfb8):
- P1 (`03_plant_p1.log`): Ran 4, OK. Survives, **as predicted**: the belt alone holds.
- P2 (`04_plant_p2.log`): Ran 4, FAILED (failures=1), d1 `(10000, 10000) != (5000, 5000)`. Killed, **as predicted**.
- P3 (`05_plant_p3.log`): Ran 4, FAILED (failures=2), b1 `(10000, 10000, 0) != (5000, 5000, 0)` and d1. Killed, **as predicted**.
