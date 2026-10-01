# Gate 4 (adversarial) for H-62 at the landing SHA f3002bc: items (a) and (b)

**Reviewer:** Verification Engineer (1a, grade-automator-plus-c2), assigned by the SM on 2026-09-30. **Author:** Hardening (d5).
**Scope:** the two items still open in `EVIDENCE.md`'s Gate 4 row. This is code reading only, with no production queries, per the SM.
**Prior verdicts carried:**
- red-team-tenancy `1f11dcd` (NOT EXPLOITABLE at `97ae0e6`);
- red-team-billing, recorded in `981d353` / `docs/SECURITY_FINDINGS_REGISTER.md` ("H-40 is not a P1c landing blocker").

**Verdict: Gate 4 PASS for H-62.** (a) is NOT EXPLOITABLE at `f3002bc`. (b) is NOT A BLOCKER at `f3002bc`: every condition the billing verdict rested on still holds.

## The code under review is the code the red team reviewed
| File or function | `97ae0e6` → `f3002bc` |
|---|---|
| `billing/admin.py`, `billing/webhooks.py`, `billing/event_replay.py`, `billing/tasks.py` | **byte-identical** (same blob) |
| `_handle_overage_checkout_completed`, `_overage_already_granted`, `handle_checkout_completed`, `create_overage_checkout_session` | **AST-identical** |
| `billing/stripe_service.py`, whole file | one hunk only: beta's hotfix `324164f`, a licence seat check in `StripeCheckoutService`, unrelated to P1c |

## (a) Forge path: can anyone make the hourly replay act on a payload that Stripe did not send?
The replay trusts the **stored** `StripeEvent.payload`: its `metadata.wallet_id`, `plan_id` and `quantity`. The questions are who can write that row, and who sets that metadata.

| Path to a payload | Finding |
|---|---|
| `stripe_webhook` | Signature-verified (`construct_event` against `STRIPE_WEBHOOK_SECRET`) before `_record_and_dispatch`. |
| `thin_webhook` | Signature-verified, and the full event is then **fetched from Stripe** (`stripe.Event.retrieve`) with our key, so no body content is trusted. |
| Re-claim of an existing row | `_claim_stripe_event`'s UPDATE rewrites `payload` from `event["data"]`, and it is reachable only through the two paths above and the live-QA path below. |
| Live QA (`stripe_live_qa.py`, `live_qa/events.py`, and the HTTP `qa_console`) | *Not covered by the earlier verdict.* It calls `_record_and_dispatch` without a signature, but its events come from `stripe.Event.list` using **our own key**, filtered to the harness's own QA customer. So they are genuine Stripe events, not user input. Every entry point requires `ENABLE_STRIPE_LIVE_QA` (default False) **and** an `sk_test_` key (both the runtime and the configured key). The console also requires an authenticated superadmin, who can grant credits by hand anyway. With live keys it refuses to run at all. **Not an escalation.** |
| Django admin | `StripeEventAdmin` returns False for add, change and delete, and its payload and status are read-only. Byte-identical to what the red team proved with a live superadmin over HTTP (all 403, rows unchanged). **Their recommended regression test now exists**: `test_event_replay` A01–A04, all killed by mutation (`8288cc6`). |
| API | No serializer, view or URL exposes `StripeEvent`. |
| The metadata itself | Set server-side in `create_overage_checkout_session`:<br>• `wallet_id` is the authenticated user's own wallet;<br>• `plan_id` comes from their active subscription;<br>• `quantity` is client-supplied but validated (`min_value=1`) and cap-checked, and is the **same quantity Stripe bills** on the line item.<br>A customer cannot edit a Checkout Session's metadata without our secret key. |

**Result:** the only way to forge a replayable payload is direct database write access. The red team's blast-radius proof still stands: a forged FAILED row would mint credits. So the admin lockdown and the signature checks carry real weight, and both are unchanged and now pinned by tests.

## (b) Can a real, signed event reach P1c with a null `payment_intent`?
This matters because the handler runs `_overage_already_granted` only `if payment_intent_id` (my H-62 record, N7).

| Condition from the billing verdict | At `f3002bc` |
|---|---|
| The overage flow is `mode="payment"` | Yes. |
| No coupons or promotion codes on the overage session | Yes. `Session.create` passes no `allow_promotion_codes`, `discounts` or `payment_intent_data`. Coupons appear only in a live-QA **subscription** scenario (test mode). |
| No $0 purchase | `plan.overage_block_price <= 0` is refused, `assert_overage_price_in_sync(plan)` pins the Stripe price to it, and `quantity >= 1`. |
| Async payment methods don't reach a grant | `checkout.session.completed` with `payment_status != "paid"` is refused and logged, and `async_payment_succeeded` is not on the allow-list. |
| The allow-list has not widened | One entry, pinned by `AllowListPinningTests.test_exact_membership` and by mutants P01–P03. |

A paid, payment-mode session with a positive amount carries a PaymentIntent, and no code path offers a $0 or discounted overage session. So a null `payment_intent` with `payment_status == "paid"` could only come from a forged payload, and (a) closes that route. **The guard rail stands:** re-open this if the overage flow ever gains coupons, discounts or $0 amounts, or if `AUTO_REPLAYABLE` widens.

## Suggestions (not required)
- **S1 (defence in depth).** Make the second layer unconditional. For a `"paid"` session without a `payment_intent`, the handler could log at ERROR and not grant, the same way it already treats unpaid sessions. That would turn (b)'s reasoning into code, so no future flow change can silently remove the idempotency key. Backlog item for d5.
- **S2 (deploy check, for 0b or the founder).** Confirm that `ENABLE_STRIPE_LIVE_QA` is off wherever live Stripe keys are used. The `sk_test_` check already makes it refuse there, so this is belt and braces. It's a configuration read, not a production query.
