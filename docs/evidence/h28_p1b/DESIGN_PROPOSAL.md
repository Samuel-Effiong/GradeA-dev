# H-28 (P1b) — per-flow design proposal

**Status: PROPOSAL. NO CODE WRITTEN. Requires the Senior Manager's approval before any
implementation.** Routed fix-p1b -> fixes-coordinator (95) -> Senior Manager (d4).
Tree: `b744c9f`. Gate-8 class: environment-sensitive / billing.

---

## 1. The finding that shapes everything: the asymmetry

**The seven confirmed sites are NOT equally dangerous, and must NOT get one remedy.**
Five are view-layer, two are webhook.

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
| **P0** | `license_service.py:3465` `convert_license_to_offline` | view | `Subscription.delete` is **unrecoverable** — no un-delete. Rollback leaves the school's subscription gone while the app still bills it as STRIPE | **Pattern 2a**, plus a **state-machine intent row** (`PENDING` -> `STRIPE_DELETED` -> `COMPLETE`, terminal `FAILED`; see §8b.3) committed **before** the Stripe call, with `STRIPE_DELETED` committed **immediately after** it returns. Compensation is impossible here, so the intent row and the loud log ARE the safety net. Stripe `idempotency_key` from the intent row id |
| **P1** | `license_service.py:2062` -> `stripe_service.py:1680` `change_license_plan` | view | school charged (`always_invoice`); custom-price path can orphan a `stripe.Price` (:2257) | **Pattern 2a.** Move `Price.create` out too — an orphan Price is harmless but should be recorded |
| **P1** | `license_service.py:2224`/`:2249` `update_seats` | view | school charged; **revert at :2249 is inside the doomed transaction** | **Pattern 2a**, and move the revert OUT so it can actually run |
| **P1** | `license_service.py:1969` `cancel_license_subscription` | view | Stripe stops renewing; app says it will renew | **Pattern 2a** |
| **P2** | `views.py:754`/`:766` `cancel` | view | two irreversible calls under one `atomic` + `select_for_update`; lock held across both | **Pattern 2a**; release the lock before the Stripe calls. Individual population is larger, value per incident smaller |
| **P1** *(was P3 — re-ranked in review, see §8)* | `stripe_service.py:3397`/`:3399`/`:3406` via `handle_checkout_completed` | webhook | upgrade applied at Stripe, DB rolled back | **Pattern 2b** — establish handler-level idempotency **by test**, so redelivery provably converges. Ranked alongside the P1s **until that is proven**; recovery machinery only heals an idempotent handler |
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
3. **Scope discipline.** Seven sites is already large. Proposed landing order, each its own
   bounded change rather than one sweeping diff:
   - **Change 1 — licence layer:** P0 + the three licence P1s, all in `license_service.py`.
   - **Change 2 — `handle_checkout_completed`** (the re-ranked webhook site, now P1 per §8a).
     Same priority as change 1 but a **different file and a different remedy class**
     (idempotency-by-test, not compensation), so it does not join the licence batch. It can
     proceed in parallel with change 1.
   - **Change 3 — P2** `views.py cancel`.
   - **Change 4 — P4** `sync_price`: a regression test pinning its idempotency; no behaviour change.
4. **The detector is the safety net, not the fix** — and it is blocked on prod read access.

## 8. Review round 1 — fixes-coordinator (95), 2026-09-18

### 8a. P3 re-ranked to P1 — conclusion ACCEPTED, stated mechanism CORRECTED

**Accepted:** my original P3 ranking said "lower priority because recovery machinery already
exists." That does not follow. **Redelivery only heals a handler that is idempotent**, and I
had not established that for this handler. e2, reviewing the same code independently for the
P1c sweep, **DENIED** auto-replay of `individual_upgrade_checkout` for exactly this reason
(`team/sessions/fix-overage-lock.md`, "Denied, with reasons"). An independent reviewer's
caution on the same path is evidence, and I should not rank below it on an assumption.

**Corrected, because d4 should decide on the right mechanism:** the review predicted that on
redelivery the void/refund step would error at Stripe, drive the claim to `FAILED`, and retry
without converging for ~3 days. **Reading `_void_or_refund_side_effect_invoice`
(`stripe_service.py:1490-1580`) shows that step does NOT behave that way:**
- it **re-reads state first** — retrieves the subscription, its `latest_invoice`, and that
  invoice's status — before acting;
- `open` -> void; once voided, a re-run sees a non-`open` status and **does nothing**;
- `paid` -> `Refund.create` **with `idempotency_key=f"interval-change-refund-{pi_id}"`**, so
  Stripe itself refuses a second refund;
- `StripeError` is **caught and logged, not raised** (it logs MANUAL RECONCILIATION NEEDED),
  so it **cannot** drive the claim to `FAILED`.

So the void/refund step is idempotent, verified by reading. **What is NOT established is the
main `Subscription.modify` (:3399) plus the interval-crossing anchor reset** converging on a
re-run. My expectation is that re-applying the same price is a no-op at Stripe and the second
run finds the same side-effect invoice already neutralised — but that rests on Stripe API
semantics I am **inferring, not testing**. Inferred is not proven, which is exactly why the
ranking moves up until a test settles it.

**P4 note, same logic:** e2 also denied `invoice.payment_succeeded` ("reaches `sync_price`, a
Stripe mutation"). My claim is narrower than "the handler is safe": the **Stripe call** in
`sync_price` is idempotent (early return, :1806-1810), which is what matters for Stripe's own
redelivery after a *full* rollback. e2's sweep replays events **long after the fact**, when
local state may have moved on — a different risk. Both positions hold; they answer different
questions. P4 stays verify-only for H-28.

### 8b. Three design conditions — ALL ACCEPTED

1. **Stripe `idempotency_key` on every view-layer mutation, derived from the intent row id.**
   Accepted. My injection point 4 promised "no duplicate mutation" with nothing enforcing it;
   a double-clicked Cancel, or a retry after a gunicorn timeout, issues a second
   `Subscription.modify`. **This also has in-tree precedent** — the very helper above already
   uses `interval-change-refund-{pi_id}` — so it stays consistent with house style.
2. **P2's lock release needs a named replacement guard.** Accepted. Removing
   `select_for_update` without a replacement trades divergence for a concurrent-cancel race.
   Named: **(a) locally**, the intent row carries a **unique constraint on (subscription,
   operation) while in a non-terminal state**, so a second concurrent cancel fails to insert
   and never reaches Stripe; **(b) at Stripe**, the idempotency key from condition 1. Both, not
   either — (a) stops the local race, (b) stops a duplicate that slips past it.
3. **P0's intent row needs explicit states.** Accepted, and this is the sharpest point in the
   review. Proposed: **`PENDING` -> `STRIPE_DELETED` -> `COMPLETE`**, plus terminal `FAILED`.
   **"Never attempted" and "deleted at Stripe but never finalised" need opposite responses** —
   the first is safe to retry, the second must NOT be retried and needs a human — and a single
   existence flag cannot tell them apart. On P0, where no compensation exists, the intent row
   **is** the safety net, so its precision is the whole design. The `STRIPE_DELETED` write must
   commit **immediately after** the Stripe call returns, in its own short transaction, before
   any other local work.

### 8c. Typo fixed

§1 said "Six of the seven". Leftover from before site 7. Now: five view-layer, two webhook.

## 7. What is NOT claimed here

No code written. No test run. No gate run. The seven sites are **confirmed by code reading
against `b744c9f`, not reproduced**. No remedy has been demonstrated to work. Per H11.3
nothing here is "fixed", "secure" or "done".
