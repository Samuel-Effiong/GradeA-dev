# Verification by Verifier 2 (v2): H-178 part A, a cancellation Stripe schedules by DATE is mirrored onto the record

Tip verified: `26e16742` (code `9f6b1242`; later commits are docs only, read). Author: ed. Scope verified: **Part A only**
(show and respect a cancellation Stripe has scheduled for a date: the webhook mirror). Part B and three questions are OPEN (below).
Runs: Release Engineer's slots on 2026-10-08. First slot 18:53:51 to 18:53:56 (red baseline, my probe's fault); second and last
slot 19:03:04 to 19:03:38 (load 2.08 at start, 3.32 at end). Probe, runner and script written before any run
(runner `a125a462d9b073ef`, script `836f2d82eeb898e3`, probe as run first `2e6246e2b613a3e5`, probe as run second `bf00d7764b2b7642`).
The author's gates (Ran 483 OK, billing Ran 2140 OK, 10 of 10 mutants) were NOT repeated (rule 15).

## Word: VERIFIED-WITH-NOTES

## The first slot's red baseline, and its cause
Baseline Ran 6, all 6 FAILED at one line of my probe (`'PROCESSING' != 'SUCCEEDED'`). Cause in one sentence: **the route claims
and queues; the handler runs in the task** (`billing/webhooks.py` `_record_and_dispatch`, `billing/tasks.py` `process_stripe_event`),
and my probe stopped after the POST. It was a probe fault, not a finding about ed's code; no mutant ran; the red log and the probe as run
are kept (`first_slot_red/`). The Senior Manager allowed one more slot. It is my second route-not-read-to-the-end slip (H-158 before it).
Hand trace before the second slot (test e1, POST to last assertion): the signed POST creates the ledger row PROCESSING with the event's
data as payload and `claimed_at`; the task is queued (answer 200, row stays PROCESSING); the probe then runs the task with the row's
`claimed_at`; the task's only guards are "the row exists" and "a handler exists" (it does not skip a row that is not PROCESSING; a direct
`.apply` call needs no eager mode); the handler gets `row.payload["object"]` read back from the ledger; the mirror writes `auto_renew=False`
and Stripe's own `canceled_at`; the fenced finish sets SUCCEEDED. Expected baseline written: Ran 6, OK. Got: Ran 6 tests in 0.388s, OK.

## What I checked
1. **Reading:** the whole code change is 20 lines in `StripeWebhookHandler.handle_subscription_updated` / `_sync_cancellation_intent`
   (`billing/stripe_service.py`): "scheduled to end" is now `cancel_at_period_end` OR `cancel_at`; a payload with neither key still says nothing;
   back to renewing only when both are clear. No migration, no model, no new Stripe call. Licence subscriptions are not touched (their branch
   ignores cancellation fields). Trials are left alone (early return).
2. **Through the real route and the real task** (nothing patched, no call to Stripe): HMAC-signed `customer.subscription.updated` posted to the
   `stripe-webhook` endpoint, then the task run as the worker runs it. Six tests: a date with the flag off stops renewal and records Stripe's
   own `canceled_at` (e1); Stripe's real period-end shape (flag AND `cancel_at`) then a resume round trip (e2); a trial is untouched (e3);
   a repeat by event id and by content writes nothing (e4); a message without either key changes nothing (e5); the flag alone still mirrors (e6).
   Baseline on the tip: **Ran 6, OK**.
3. **Eight faults, each judged by my probe alone, failing set written beforehand; all 8 KILLED_AS_WRITTEN (set equals the written one),
   restores verified 8 of 8:** Q1 old code back -> e1, e4; Q2 a date does not count -> e1, e4; Q3 the flag does not count -> e6;
   Q4 Stripe's own date ignored -> e1; Q5 a trial is mirrored -> e3; Q6 a repeat writes again -> e4; Q7 never back to renewing -> e2;
   Q8 silence read as "not cancelling" -> e5.

## By-product for ed's tests
In production the handler receives the payload as a **plain dict read back from the ledger row** (`process_stripe_event` rebuilds the event from
the stored JSON), so the author's dict stand-in IS the real shape. What the signed route adds is the claim, the queueing and the dispatch table,
not a different payload.

## OPEN (listed, not answered by the code)
- Part B: store the date and show it (the page's sentence, the end date, who scheduled it). Not built; not decided.
- The three questions for the user: does anyone set cancel-by-date at all (if nobody does, MEDIUM is too high); may "Keep subscription" clear a
  date at Stripe; is Part B now or later.

## Notes
- N1 (the author's stated limits, confirmed by reading): the page will say "You cancelled this subscription on <date>" (`billing/serializers.py`
  `_cancellation_message`) for a date set by Stripe's dashboard or by support, and shows the period's END, wrong where the real date is earlier;
  "Keep subscription" is offered (`has_pending_cancellation`) and then refuses ("could not be undone here", contact support).
- N2: how a real Stripe forms `cancel_at` when the date equals the period end (whether it also flips `cancel_at_period_end`) is not shown by any
  run; the rule does not depend on it. A cancellation by date later than the next period end means Stripe keeps renewing until the date; I read
  no local code that grants or withholds renewal credits by `auto_renew` for individual subscriptions (grep of `auto_renew` outside licences,
  the cancel/resume views and the serializer: none), so that case did not show a fault by reading; not run.
- N3 (reverse thin payload, by reading, pre-existing): flag present and false with the `cancel_at` KEY ABSENT is read as "not scheduled" and puts a
  date-scheduled record back to renewing. Real Stripe objects always carry the key (null); not a fault today.

## NEW ROW H-197 (not part of this verdict; owner ed, inside Part B's design; severity proposed LOW until a live read)
**Stripe's `canceled_at` is read two opposite ways.** (1) `billing/stripe_service.py:4400-4404` (`handle_subscription_updated`, at 26e16742):
a payload with `cancel_at_period_end` false, `cancel_at` empty and only `canceled_at` set is NOT scheduled: the mirror sets `auto_renew=True`
and clears `cancelled_at`. (2) `billing/stripe_service.py:1041-1049` (`SubscriptionReactivationService`, H-174): the same payload IS a
cancellation recorded at the provider (`scheduled_at_provider = True`; `billing/views.py:1212`): "Keep" answers that it is scheduled with the
payment provider, could not be undone here, and changes nothing. **Worked example (by reading, not run):** a customer's record shows auto_renew
False; Stripe's object is {flag false, `cancel_at` null, `canceled_at` T}; the customer presses Keep and is told it cannot be undone here and to
contact support; the next webhook (1) then flips the record to renewing and the page shows no cancellation. **Can a LIVE subscription be in that
state? UNVERIFIED.** It depends on whether Stripe clears `canceled_at` when a period-end cancellation is undone. One search of Stripe's
documentation was inconclusive (the field's definition was not retrieved); a read of a test-mode subscription after a cancel and a resume would
settle it (not mine to run). If Stripe clears it, the state is unreachable and the row falls to a note; if not, MEDIUM.

## Files
`probe`, `runner`, `script`, driver logs, mutant logs (gzipped) in `logs/`; the first slot's red probe, status and logs in `first_slot_red/`.
