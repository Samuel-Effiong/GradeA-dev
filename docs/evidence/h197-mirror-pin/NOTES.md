# H-197 notes

- **The row:** Stripe's `canceled_at` is read two opposite ways.
  - The webhook mirror (`StripeWebhookHandler.handle_subscription_updated` / `_sync_cancellation_intent`,
    `billing/stripe_service.py` 4242-4418) counts a subscription as scheduled to end only when `cancel_at_period_end`
    is true or `cancel_at` is set. It uses `canceled_at` only as the date it stamps.
  - "Keep subscription" (`SubscriptionReactivationService.reactivate_if_cancelling`, 1043-1051, H-174) also counts
    `canceled_at` alone as scheduled.
  - For the payload *status active, the flag false, `cancel_at` empty, `canceled_at` set*, the mirror says
    "renewing" and Keep says "scheduled".
  - The read-only design note is `GAP-a0-runs/h197/DESIGN_NOTE.md` (Builder a0, 25 October 2026).
- **The Stripe test-mode probe (approved by the user, 25 October 2026, about 15:05):**
  - Run 15:13 WAT on API **2026-02-25.clover** (stripe-python 14.4.1's default, which the app also uses), test key
    only, throwaway objects named "H-197 probe". The script was read and approved by the Senior Manager before the
    run, and its output was checked by the Senior Manager for keys and full ids (none).
  - Undoing a cancellation clears `canceled_at`, `cancel_at` and `cancellation_details`, whether it is undone by the
    flag, by the date, or in the app's own order (cancel, then retrieve and resume).
  - So an active subscription never carries `canceled_at` alone. **H-197 cannot reach a customer on this API version.
    No fix.**
  - The only payload of this shape seen was a fully cancelled subscription (status `canceled`), which Keep refuses
    before the disputed branch.
  - Leftover: the test price could not be archived (it is its product's default price); the product was archived,
    the subscription cancelled and the customer deleted.
- **The user's ruling (about 15:20, passed on by the Senior Manager):** exactly one test, pinning the mirror's
  reading, so that a later change cannot flip it silently. No production change. It ships in a later batch, not
  batch 26.
- **The test:** `billing.tests.test_date_scheduled_cancellation.DateScheduledCancellationMirrorTests.
  test_a_recorded_cancel_time_alone_is_not_scheduled`.
  - It sits beside H-178's mirror tests and uses their fixture and `deliver()`.
  - The local record starts cancelling (`auto_renew` False, a cancellation date).
  - It delivers status active, `cancel_at_period_end` False, `cancel_at` None and `canceled_at` five minutes ago.
  - It checks the record is back to renewing: `auto_renew` True, `cancelled_at` None.
  - Its docstring cites the probe result.
- **Production files:** none. The gate's changed-file check proves it: only the test module and `docs/` differ from
  the base.
- **Docs:** the H-197 row in `docs/HARDENING_BACKLOG.md` (status only; the owner is unchanged); this file.

## No red run: why, and rule 19

The test pins existing behaviour, so it is green on today's code by design and there is nothing to reproduce first.
Rule 19 (a new test must be shown able to fail) is met by one mutant, written before any run:

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| T1 | the mirror counts `canceled_at` alone as scheduled (Keep's reading): `bool(cancel_at_period_end) or bool(cancel_at) or bool(stripe_subscription.get("canceled_at"))` at `stripe_service.py:4407` | the pin test (`False is not True`) |

- **T1 traced by hand.**
  1. The flag is present (False), so the guard above the changed line still lets the payload through.
  2. The sync is told "scheduled". `_sync_cancellation_intent` finds the record already not renewing with a date, and
     returns without writing.
  3. The first check reads `auto_renew` False: "False is not True".
- **The test traced by hand on today's code.**
  1. The sync is told "not scheduled" (False or None).
  2. The record is not already renewing, so it sets `auto_renew` True, clears `cancelled_at` and saves: green.
- **Why nothing else fails under T1:**
  - Every other payload in the four battery modules that carries `canceled_at` also carries
    `cancel_at_period_end=True`, which is already "scheduled", so T1 changes nothing there
    (`test_cancellation_visibility` 595-660; H-178's tests pair it with `cancel_at` or clear both).
  - `test_subscription_updated_webhook` and `test_event_replay` send no `canceled_at`.
- **The battery** is every module that drives the mirror: `billing.tests.test_date_scheduled_cancellation`,
  `test_cancellation_visibility`, `test_subscription_updated_webhook` and `test_event_replay`.

## Gate (proposed; the Senior Manager reads the pack before any GRANT)

- **Script:** `run_h197_gate.sh.txt`, the H-210 gate's form without the red run.
- **Steps:**
  1. makemigrations --check;
  2. the changed-file check (only the test module and `docs/`);
  3. own + near + the 21 guards;
  4. mutant T1 (own database, rules 17, 18 and 22; a 690 s limit and the 300 s stall watchdog).
- **Own:** `billing.tests.test_date_scheduled_cancellation`.
- **Near:**
  - the other three mirror modules;
  - `billing.tests.test_converted_trial_is_not_cancelling` (H-174's Keep tests, which read the same payload the
    other way and must not change).
- **The exit code** is captured by the start command (`; echo "gate exit=$?"` into the output file).
- **The regression** is the later batch's one full run.
- **Base:** `7286717e`.

## The gate on 9e38391d (granted by the Senior Manager, 25 October 2026)

Run 15:38:06 to about 15:41 WAT inside one `systemd-inhibit`. Logs are in `gate_9e38391d/` (gzipped), including the
gate's own output, `gate_9e38391d.out.gz`.
- **Exit code: 0**, captured by the start command (`; echo "gate exit=$?"`), whose line ends the output.
- Available memory at the start was 9G, so the cap was 7G. Load was 2.83 5.50 7.66 at the start and 4.37 4.72 6.97 at
  the end.

- **makemigrations --check:** exit 0, no changes.
- **Changed files:** "changed files as packed: the test module and docs/ only".
- **Step 1** (own, the four near modules, the 21 guards), 15:38:11 to 15:40:25:
  - "Ran 422 tests in 115.479s", "OK".
  - 422 test lines were counted in the log, the pin test's among them.
- **Step 2, mutant T1** (own database, rules 17, 18 and 22), 15:40:25 to 15:40:55: **KILLED**, "Ran 96 tests in
  10.865s".
  - It failed exactly the pin test, whose block holds "False is not True".
  - Nothing was missing, nothing extra failed, and there was no wrong reason.
- The source was clean after the mutant, and the mutant database was dropped. No watchdog fired.
- The Senior Manager read the logs and ruled the gate GREEN.
