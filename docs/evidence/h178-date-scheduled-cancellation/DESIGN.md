# H-178: a cancellation scheduled BY DATE at Stripe (`cancel_at`) is not mirrored, not shown and cannot be undone

Design note, 2026-10-08, before any test or code. Base: beta 035e0a07. Reading only; nothing run, no Stripe call, no live data. Owner: ed. Severity MEDIUM (as logged).
Marks: READ = read in the code; NOT READ = not read; UNVERIFIED = a Stripe behaviour I am told or assume but have not seen.

## What Stripe can say (READ in our code; UNVERIFIED at Stripe)

A subscription can be set to end in two different ways: `cancel_at_period_end = true` (end when the paid period ends) or `cancel_at = <a date>` (end on that date, which can be
earlier than the period end; Stripe's dashboard can set it; `canceled_at` records when the cancellation was requested). Our code reads only the first kind in the webhook; H-174's
"keep" change (98c75afa) now recognises the second kind and refuses to correct it away. I have not seen, and have not tried, what Stripe sends when a date equals the period end
(whether it also flips `cancel_at_period_end`): UNVERIFIED. The rule below treats EITHER as "scheduled to end" so it does not depend on that.

## What our code does with it today (READ; NOT RUN)

1. **The webhook mirror** `StripeWebhookHandler._sync_cancellation_intent` (stripe_service.py:4238, called from the `customer.subscription.updated` handler at :4392) is driven only by
   `cancel_at_period_end`. With `cancel_at` set and `cancel_at_period_end` false the event reads as "NOT cancelling" and the mirror sets `auto_renew = True` and `cancelled_at = None`
   (the "no longer scheduled to cancel" branch, :4286-4296). So the local record SAYS "renews" while Stripe will end the subscription on the date. This is the worst of the three.
2. **The page** (serializers.py `_has_pending_cancellation`, `_cancellation_message`) is built from `auto_renew` and `billing_cycle_end` only: it can say "scheduled to end on <period end>"
   never on the real `cancel_at`; when the mirror is wrong (1) it shows nothing at all. If the mirror were fixed to set `auto_renew = False`, the message would say "You cancelled this subscription
   on <date>" (wrong wording for a dashboard or support action) and "You keep your plan and credits until <period end>" (wrong if `cancel_at` is earlier), and `has_pending_cancellation`
   (documented as "Resume will work") would be true while H-174's resume answer now refuses such a record ("could not be undone here").
3. **At the date** Stripe deletes the subscription; `handle_subscription_deleted` (:4442) deactivates the local record and syncs the mail list. So the customer is told "renews" (1) until, on
   the date, the plan ends. If the date is before the period end they also lose access before the end of the period they paid for. Whether Stripe refunds the unused part depends on
   the proration setting of whoever set the date: NOT READ.
4. **"Keep subscription"** (H-174, 98c75afa): recognises a record scheduled by date and refuses ("a cancellation is scheduled with our payment provider and could not be undone here"). It never
   clears `cancel_at`. NOT a defect; a decision (see questions).

## The cure, in two parts (proposal)

**Part A, no migration (the mirror; proposed for this row).** In `_sync_cancellation_intent`, "scheduled to end on Stripe" means `cancel_at_period_end` true OR `cancel_at` set (a date).
Then `auto_renew = False` and `cancelled_at` = Stripe's `canceled_at` or now (as today), and the un-cancel direction (back to `auto_renew = True`) needs BOTH false/empty. The handler's guard
`if cancel_at_period_end is not None` stays (a thin payload that omits the flag must not un-cancel); a payload that carries `cancel_at` but omits the flag is treated by the new rule as scheduled.
Effect: the record stops saying "renews" the moment Stripe says it will end. Tests: the replay of a Stripe `customer.subscription.updated` payload with `cancel_at` set and the flag false, on a paid
record: `auto_renew` False, a repeat event writes nothing; the payload that clears both: back to True; a trial untouched; the period-end-only payload as before. All with a stand-in for Stripe; no Stripe call.

**Part B, a migration and the page (NOT proposed until decided).** Store the date (`UserSubscription.scheduled_cancel_at`, nullable) and show it: the message says the real end date and
who scheduled it (no "You cancelled" unless our own cancel action did). Needs a migration (nullable column, no default needed), a serializer field and the frontend to render it. Without B, Part A makes the
page say the period end (possibly wrong if `cancel_at` is earlier) and "you cancelled" (wrong for a support action).

## Questions for the Senior Manager / the user

1. **May "Keep subscription" clear the date at Stripe** (`stripe.Subscription.modify(sub, cancel_at="")`, a call that changes a customer's subscription at their own request, like the existing
   `cancel_at_period_end=False` call)? Or must a date set in the dashboard (possibly by support, possibly a fixed-term deal) stay out of the customer's reach? My default: NO, keep the H-174 behaviour
   (refuse, contact support); undoing a business decision on a button is not mine to make.
2. **Part B?** Does the page need the real end date and the wording "scheduled by our team", or is "will not renew; ends <date>" with the period end acceptable until it is built?
3. Where do cancellations by date actually come from today (support, the dashboard, a promotion)? If nobody sets them, MEDIUM is too high; I do not know.

## Not shown by any run

Everything about Stripe's real behaviour (the fields' exact forms, the order of events, the date equal to the period end, proration/refund of an early end); nothing here was run. Licences: the same fields on a
LicenseSubscription (`license_service.py:2114-2260` sets and clears `cancel_at_period_end`; the licence webhook branch ignores cancellation fields entirely): NOT READ beyond that; not in this row.
