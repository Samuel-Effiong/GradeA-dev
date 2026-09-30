# H-28 (P1b) — per-flow design proposal

**Status: PROPOSAL. NO CODE WRITTEN. Requires the Senior Manager's approval before any
implementation.** Routed fix-p1b -> fixes-coordinator (95) -> Senior Manager (d4).
Tree: `b744c9f`. Gate-8 class: environment-sensitive / billing.

---

## 1. The finding that shapes everything: the asymmetry

**Six of the seven confirmed sites are NOT equally dangerous, and must NOT get one remedy.**

| | Webhook layer | View layer |
|---|---|---|
| Sites | 2 of 7 | **5 of 7** |
| How it dies | Postgres `idle_in_transaction_session_timeout` = **60 s**, shorter than stripe-python's **80 s** default, so the transaction can be terminated *mid-call* | gunicorn request timeout, client disconnect, or any later exception |
| Is there a second chance? | **YES** | **NO** |
| Recovery machinery | Verified in-tree: the claim in `webhooks.py:150-200` is **deliberately not atomic**, so it survives the rollback; `FAILED` and stale `PROCESSING` are re-claimable; Stripe redelivers for ~3 days (`STRIPE_RETRY_WINDOW`) | **None. Nothing retries a dead web request.** |
| Therefore | divergence is **transient and self-healing**, given idempotent handlers | divergence is **silent and permanent** |

**This is the whole argument.** A remedy adequate at the webhook layer is inadequate at the
view layer, because the webhook layer already has a designed recovery path and the view
layer has nothing at all.

## 2. Both remedies already exist in this codebase

Neither half of this proposal invents an architecture. Both cite working in-tree precedent.

### 2a. View-layer remedy — `stripe_service.py:975-1070` (`reactivate_if_cancelling`)

Originally flagged by my own tool as a defect; reading it showed it is the **reference
pattern**, and I reclassified it:

1. the irreversible Stripe mutation runs **OUTSIDE** the transaction (~:998);
2. the `atomic` block (:1010) is **short and purely local**;
3. on local-save failure, a **compensating revert** (:1054);
4. when the compensation *itself* fails, a loud
   **`MANUAL RECONCILIATION NEEDED — Stripe and local state now disagree`** log.

### 2b. Webhook-layer remedy — idempotency + redelivery, already partly present

`sync_price` (`stripe_service.py:1795-1822`) is **already idempotent by construction**: it
retrieves the subscription, returns early if the price already matches, and only then
modifies. Re-running it after a redelivery converges on the correct state. That is the
webhook remedy working today.

## 3. Ordering, not speed — and the revert must be able to fail

Two principles, the first from e2's P1 mechanism work:

1. **Ordering is what saves you.** Any call inside the transaction can have that transaction
   terminated mid-call at 60 s while the call itself is still allowed 80 s. **Shortening the
   Stripe timeout narrows the window; it does not close it. Only moving the call out does.**
   No proposal here relies on a timeout tweak.
2. **A revert that fails silently is worse than no revert**, because it converts a loud
   divergence into a quiet one. The loud log in 2a is not decoration — it is the half that
   makes the pattern safe. Note `update_seats` (:2249) has a revert that is **itself inside
   the doomed transaction**, so it is skipped entirely by a mid-call kill: actively
   misleading to a reader who sees a revert and assumes coverage.

## 4. Per-flow proposal

Priority is blast radius per incident × irreversibility × absence of recovery.

| P | Site | Layer | What goes wrong | Proposed remedy |
|---|---|---|---|---|
| **P0** | `license_service.py:3465` `convert_license_to_offline` | view | `Subscription.delete` is **unrecoverable** — no un-delete. Rollback leaves the school's subscription gone while the app still bills it as STRIPE | **Pattern 2a**, plus: write an intent row and COMMIT it **before** the Stripe call, so a death leaves a durable trace instead of nothing. Delete, then a short local transaction, then compensation is impossible — so the loud log and the intent row ARE the safety net |
| **P1** | `license_service.py:2062` -> `stripe_service.py:1680` `change_license_plan` | view | school charged (`always_invoice`); custom-price path can orphan a `stripe.Price` (:2257) | **Pattern 2a.** Move `Price.create` out too — an orphan Price is harmless but should be recorded |
| **P1** | `license_service.py:2224`/`:2249` `update_seats` | view | school charged; **revert at :2249 is inside the doomed transaction** | **Pattern 2a**, and move the revert OUT so it can actually run |
| **P1** | `license_service.py:1969` `cancel_license_subscription` | view | Stripe stops renewing; app says it will renew | **Pattern 2a** |
| **P2** | `views.py:754`/`:766` `cancel` | view | two irreversible calls under one `atomic` + `select_for_update`; lock held across both | **Pattern 2a**; release the lock before the Stripe calls. Individual population is larger, value per incident smaller |
| **P3** | `stripe_service.py:3397`/`:3399`/`:3406` via `handle_checkout_completed` | webhook | upgrade applied at Stripe, DB rolled back | **Pattern 2b** — verify/complete idempotency so redelivery converges. Lower priority **because recovery machinery already exists**, not because the bug is less real |
| **P4** | `sync_price` `:1813` via `handle_invoice_payment_succeeded` | webhook | next cycle billed at new price, app on old plan | **Verify only.** Already idempotent (:1806-1810 early return). Add a regression test pinning that property so a future edit cannot remove it |

**Explicitly NOT changed:** `stripe_service.py:1054` (the reference pattern — changing it
would remove the model answer) and `:4691` `handle_setup_intent_succeeded` (nothing follows
it but logging; redelivery re-sets the same default payment method idempotently).

## 5. Gate-5 injection matrix (H6)

Per site, inject at each point and record **both** app-DB state and Stripe state:

| # | Injection point | Expected after fix |
|---|---|---|
| 1 | before the Stripe call | no Stripe change; local unchanged; clean error |
| 2 | during the call (timeout / 60 s transaction kill) | outcome intentional and recorded; no silent divergence |
| 3 | **after Stripe succeeds, before the local commit** | the P1b case. Compensation runs, or the loud log + intent row fires |
| 4 | on retry / redelivery | webhook: converges idempotently. View: no duplicate mutation |

**Runtime proof, not code-reading:** `assert_no_call_inside_transaction(test, fake,
only_receipt_lookups=False)` from e2's harness (`task/fake-stripe-harness` @ `5400759`)
records `in_transaction=connection.in_atomic_block` per call. That assertion is what turns
this audit into evidence and is the Gate-2 mutant target for every site.

## 6. Risks and open questions for d4

1. **P0 has no true compensation.** A deleted Stripe subscription cannot be restored, only
   re-created — a *new* subscription with a new id, new billing anchor, possibly a new
   charge. I am **not** proposing auto-recreation; that would move real money. The proposal
   is durable-intent + loud log + the reconciliation detector, and a **human** decides.
2. **Should the P0 flow be gated behind a confirmation or made async?** Out of scope for
   H-28 as a bug fix; flagging as a product question.
3. **Scope discipline.** Seven sites is already large. I propose landing **P0 + the three
   P1s (all licence-layer, all `license_service.py`) as one change**, then P2, then P3/P4
   separately — rather than one sweeping diff. Reviewability, and it matches the board's
   preference for batched-but-bounded changes.
4. **The detector is the safety net, not the fix** — and it is blocked on prod read access.

## 7. What is NOT claimed here

No code written. No test run. No gate run. The seven sites are **confirmed by code reading
against `b744c9f`, not reproduced**. No remedy has been demonstrated to work. Per H11.3
nothing here is "fixed", "secure" or "done".
