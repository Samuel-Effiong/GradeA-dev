# H-178 (part A): a cancellation scheduled BY DATE at Stripe is mirrored onto the local record

Branch `task/h178-date-scheduled-cancellation`, base beta 035e0a07. Written by ed (Security Engineer), 2026-10-08. Severity MEDIUM until the user says whether anyone sets cancellations by date (then LOW if nobody does).
Design and reading: DESIGN.md (same folder). **Nothing has been run on this branch yet.** Marks: READ = read in the code; NOT RUN = reasoning.

## The change (READ; NOT RUN)

`StripeWebhookHandler.handle_subscription_updated` (billing/stripe_service.py): "scheduled to end on Stripe" is now `cancel_at_period_end` OR `cancel_at`. The mirror runs when the flag is present or a date is set,
and the local record goes back to renewing only when both are clear. A payload with neither key still says nothing and changes nothing. No migration, no model change, no new Stripe call; `cancel_at` is read from the event
Stripe already sends. Before: with `cancel_at` set and the flag false the mirror set `auto_renew = True` and cleared `cancelled_at`, so the local record said "renews" while Stripe would end the subscription on the date.

## KNOWN LIMITS, in plain words (the package carries them too; the Senior Manager's condition)

1. **The page's sentence will say the customer cancelled.** With the record now reading as scheduled to end, the page shows "You cancelled this subscription on <date>" for a cancellation that Stripe's dashboard or support scheduled: untrue for those. It also shows the END of the paid period as the end date, which is wrong when the scheduled date is EARLIER than the period end. Showing the real date and who scheduled it is part B (a stored date and the page), not built.
2. **"Keep subscription" will be offered and then refuse.** The record reads as scheduled to cancel, so the page offers "Keep subscription" (`has_pending_cancellation`); pressed, the answer says the cancellation is scheduled with our payment provider, could not be undone here, nothing was changed, and to contact support. It does not say the subscription is active, and it does not clear the date at Stripe (a decision, not a defect; the working default). Pinned by a test.
3. Stripe is a stand-in everywhere here: how Stripe forms `cancel_at` when the date equals the period's end (whether it also flips `cancel_at_period_end`) is NOT shown; the rule does not depend on it. The proration or refund of an early end is not read.
4. Licences: the licence branch of this webhook ignores cancellation fields entirely; not in this row.

## Tests (written first, 682d8b31; NOT RUN)

`billing/tests/test_date_scheduled_cancellation.py`: nine mirror tests with a stand-in payload (a date with the flag off stops the record renewing; Stripe's own `canceled_at` is recorded; a repeat writes nothing; a date without the flag counts;
clearing the flag does not undo a date still set; clearing both restores renewing; silence changes nothing; the flag alone still mirrors; a trial is left alone) and one pin of the "Keep subscription" answer.

## Written expectations, before any run

- **Step 0 (reproduce-first)**: `billing/stripe_service.py` as at 035e0a07 under the new module: **Ran 10, FIVE red**: `test_a_date_with_the_period_end_flag_off_stops_the_record_renewing`, `test_the_recorded_date_is_stripes_own_when_it_gives_one`, `test_a_repeat_of_the_same_message_writes_nothing`,
  `test_a_message_with_a_date_but_without_the_flag_is_scheduled`, `test_clearing_the_flag_does_not_undo_a_date_that_is_still_set`. **Green on the old code, by design (they hold existing behaviour or the page's answer):** `test_clearing_both_puts_the_record_back_to_renewing`,
  `test_a_message_that_says_nothing_about_it_changes_nothing`, `test_the_period_end_flag_alone_still_mirrors`, `test_a_trial_is_left_alone` and the Keep-answer pin; each is seen red by a mutant below (P8, P9, P3, P6, P10).
- **Step 1**: `makemigrations --check` no changes; the new module (Ran 10, OK), `test_subscription_updated_webhook`, `test_cancellation_visibility`, `test_event_replay`, `test_subscription_reactivation`, `test_subscription_cancel`, `test_converted_trial_is_not_cancelling` and the repo-wide guard modules: OK, no FAIL or ERROR line. Rule 20 (the cache payload test, `AutoGrader.tests_cache_bespoke_1114`) does NOT apply, and is not in the list. Reason (the Release Engineer asked whether the page's answer changes): the change is in the webhook handler only; no serializer, view or route code is touched. The page's answer for such a record DOES differ (it now reads as scheduled to end instead of renewing), but because the stored row differs, not because any code that builds the answer changed. The subscription routes in `billing/views.py` keep no cache of their answer (no `cache.get/set`, `cache_page` or generation use found by grep), and `users/serializers.py` (the `users/me` payload the cache test covers) does not nest the subscription (no `subscription` field found); a cached nested copy would be invalidated by the existing receivers on save, not by this row. Read by grep, not run.
- **Step 2, mutants (10)**, each fails exactly the tests named: P1 (the handler does not run the mirror for a date alone): the date-without-the-flag test; P2 (a date does not count as scheduled): the first five tests (date with the flag off, Stripe's own date, repeat, date without the flag, flag cleared with the date still set);
  P3 (the flag does not count): the flag-alone test; P4 (scheduled needs both): those five plus the flag-alone test; P5 (Stripe's own date ignored): the Stripe-own-date test; P6 (trials mirrored): the trial test; P7 (a repeat writes again): the repeat test;
  P8 (never back to renewing): the clearing-both test; P9 (silence read as "not cancelling"): the silence test; P10 (the Keep answer no longer says to contact support): the Keep-answer pin. SURVIVED, BROKEN, KILLED_NOT_AS_EXPECTED each expected empty. A mutant that fails more than written is reported with the extra tests named and why.
- **Step 3**: the billing app, one serial run: OK.
- Nothing is re-run without the Release Engineer's word; a difference is reported, not repaired in place.

## Not shown by any run so far

Everything: nothing has been run. And, even when run: how a real Stripe forms `cancel_at`, the order of Stripe's messages, what the customer sees on the real page.
