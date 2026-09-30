# H-23 / P1 — Stripe receipt lookups held webhook transactions open

**Owner:** fix-overage-lock (grade-automator-plus-e2)
**Branch:** `task/overage-lock`
**Commits:** Design A `f7db2f2`; P1c `22cc9cf` + `2a7d285`; harness shared with H-28 on `task/fake-stripe-harness` @ `5400759` (byte-identical copy here). Parent `b744c9f`.
**Status: NOT LANDED. Gate 2 PASS (56 of 56 killed). Gate 4 for P1c, Gate 8 and Gate 10 are NOT RUN.**

## 10-gate table

| Gate | Status | Evidence |
|---|---|---|
| 1 Baseline / Regression | PASS | 12 tests fail on `b744c9f`, pass on `f7db2f2`: `g1_repro_b744c9f.log` (sha256 `d015b27d…`), `g1_repro_perflow_b744c9f.log` (550 lines, sha256 `851533eb…`). Billing app 1,448 tests OK (`billing_regression` run, 638s). pre-commit clean on every changed file. |
| 2 Mutation | PASS | **56 of 56 killed**, one mutant per guard (41 Design A, 15 P1c), each restored from the commit blob with a verified sha256: `mutation/run_mutants.py`, `mutation/logs/`, every run in `mutation/results.tsv`. Two first survived (M25, M40) as weak tests, not defects; the tests were tightened in `acdd29d` and both were then killed. The three mutants whose targets include a 20-thread class (M01, M02, M18) ran one at a time on `91e1626` with at least 76 of 100 connections free before each; their errors are the mutant's own consequences — including `SSL connection has been closed unexpectedly`, the idle-in-transaction kill that putting the lookup back inside the transaction recreates — and none is connection starvation. |
| 3 Concurrency | PASS (LOCAL-REAL) | `ReceiptConcurrencyTests`: 20 threads × 10 rounds on real Postgres. Concurrent fills → exactly one FILLED per round; duplicate deliveries → exactly one grant, one BillingTransaction, no lookup inside a transaction. |
| 4 Adversarial | PARTIAL — independent red-team replay in progress | Per the Senior Manager's ruling (2026-09-19) Gate 4 is **not** recorded as NOT APPLICABLE for either part: it is being done independently by the red team, as H5.1 requires, and closes PASS only when their verdicts land clean. **Closed so far:** StripeEvent admin escalation onto the P1c replay path — NOT EXPLOITABLE (red-team-tenancy, `1f11dcd`): add/change/delete disabled, payload and status read-only, no DRF endpoint, webhook signature-verified. **Open:** (a) red-team-tenancy finalising the forge-path verdict on the landing SHA; (b) red-team-billing on the H-40 interaction — whether any real, signed event reaches P1c with `payment_intent` null, which would bypass the idempotency guard P1c relies on. |
| 5 Failure / Recovery | PASS | See the failure matrix below. Every case records the app DB **and** the Stripe side. |
| 6 Stress / Scale | PASS (LOCAL-REAL) | `g6_scale.log`, `billing/tests/test_receipt_replay_scale.py`. Both hourly tasks measured at 2,000 and 20,000 background rows (two years of history): **query count identical at both sizes** (sweep 51 = 1 selection + 2 per filled row; replay selection 1). Plans use an index at both sizes (sweep: bitmap scan on `occurred_at`; replay: index scan on `status`), asserted: no sequential scan. Sweep idle p50/p95 1.10/1.59 ms → 2.28/2.63 ms, growing with rows inside the 3-day window, not with the table. Replay p50/p95 ≈ 0.8/0.9 ms at both sizes. Peak Python memory under 2 MB. |
| 7 Real Infrastructure | PASS (LOCAL-REAL + real Stripe test mode) | Real Postgres and Redis throughout. `g7_real_stripe.py` / `.log`: one real test-mode PaymentIntent (500 usd, `livemode=False`), resolved through the bounded client (10 s, no retries) by payment intent and by charge; both return a `pay.stripe.com` receipt that opens with HTTP 200. **Found by the real service only:** Stripe re-mints the receipt URL's signed token on every retrieval, so the first run failed on my own assertion that the two URLs are identical (that log was overwritten by the corrected run; the probe that established it is described in commit `acdd29d`). This confirms fill-if-null as the correct write rule. |
| 8 Live / E2E | NOT RUN | Per the Senior Manager's re-prioritisation, QA (beta) is itself the deployed stage: the deployed check happens on QA after landing. |
| 9 Security / Isolation | PASS | `ReceiptIsolationTests`: two teachers' identical purchases; each link lands on its own row only, neither row carries the other's payment intent, each wallet gets exactly its own block. |
| 10 Final Production Gate | NOT RUN | Strict procedure on the committed tree, two consecutive clean runs (billing change). |

## The 8 completion answers

1. **What changed.** Every `checkout.session.completed` flow recorded its purchase and then called Stripe for the receipt link *inside* `handle_checkout_completed`'s `@transaction.atomic`, holding that flow's row locks. The link is now resolved after commit: handlers record with no link and queue `billing.receipts.fill_receipt_url` via `transaction.on_commit`; an hourly `sweep_missing_receipt_urls` beat task fills anything missed. Five `record()` calls now store `stripe_payment_intent_id`, which the deferred lookup needs. `resolve_stripe_receipt_url` is deleted.
2. **Why it was necessary.** Production and beta run Postgres with `idle_in_transaction_session_timeout = 60s` (confirmed live 2026-09-17), shorter than stripe-python's 80s default timeout. One slow receipt lookup let Postgres terminate the session and roll back a grant the customer had paid for. Dispatch is asynchronous, so Stripe had already been answered 200 and never redelivered; the event landed FAILED with `handler_started_at` set, which the sweeper deliberately never replays. The customer received nothing until a manual `replay_stripe_events`. Meanwhile the wallet — or every `SchoolCreditAllocation` on a school purchase — stayed locked and credit consumption failed at `lock_timeout`.
3. **What was tested.** 10 per-flow tests (one per call site) through the real dispatcher on real Postgres; 2 reproductions of the original failure at 2s-scaled guard rails; 40 tests for the new module (bounded lookup, fill-if-null, scheduling, sweep bounds, failure/recovery, isolation, concurrency); the billing app's 1,448 tests.
4. **Which gates passed.** 1, 2, 3, 5, 6, 7, 9.
5. **Which gates remain incomplete.** 4 (PARTIAL — independent red team: admin escalation closed NOT EXPLOITABLE; forge-path finalisation and the H-40 payment_intent=null interaction open), 8 (NOT RUN — on QA after landing), 10 (NOT RUN — booked after 93 lands and the batch integration gate).
6. **What risks remain.** Scale was measured to 20,000 rows locally, not at production size, and production's `billing_stripeevent` row count is unknown, which also decides migration 0070's lock choice. Receipt links now appear seconds later than before; no email or serializer depends on that timing (`receipt_url` is read only by `billing/serializers.py:2617`). Customers whose credits are already stuck from this bug are healed by P1c (`22cc9cf`), which automatically replays FAILED events for the one approved flow; P1c itself has not yet been red-teamed or mutation-tested.
7. **What exact commit contains the verified implementation.** Design A is `f7db2f2`; P1c is `22cc9cf` + `2a7d285`; evidence and test additions are committed on top. No commit on this branch has passed Gate 10 yet, so none is yet "the verified implementation".
8. **Is the verified commit the one intended for release.** Not yet. Nothing is released until Gate 10 passes on the exact commit, and the branch head is not yet gated.

## Reproduction (Gate 1)

Two independent proofs, both on `b744c9f`:

- **Per-flow invariant.** Stripe is faked at `HTTPClient.request_with_retries`, so every route to the API is seen (legacy global or `StripeClient`), and each call records whether the calling thread's connection was inside a transaction. All 10 sites failed with, e.g., `Stripe receipt lookup(s) ran INSIDE the webhook transaction: ['/v1/invoices/in_fresh']`.
- **The consequences.** With the guard rails scaled to 2s and Stripe sleeping 4s: the paid grant was lost (`0 != 500 … event status=FAILED, last_error="OperationalError('SSL connection has been closed unexpectedly')"`), and a concurrent `consume_credits` failed with `canceling statement due to lock timeout … while locking tuple (0,2) in relation "billing_creditwallet"`.

Prior state in these tests is built through production services (`activate_free_trial`, `activate_subscription`, the `license_create` webhook itself, `initiate_overage_purchase`, `handle_subscription_deleted`), never by setting fields production does not set.

## Call sites (all 10, audited with fix-flaky-test)

| Line (b744c9f) | Flow | Locks held across the lookup | Rollback cost |
|---|---|---|---|
| 2861 | individual_checkout, trial conversion | UserSubscription + wallet buckets | Paid trial→paid conversion |
| 2912 | individual_checkout, fresh activation | UserSubscription + wallet buckets (via `activate_subscription`) | Paid activation + credit grant |
| 3049 | overage block purchase | CreditWallet | **Paid overage grant** (reproduced) |
| 3187 | license overage, inactive licence | Intent + LicenseSubscription | Paid "needs manual refund" record |
| 3282 | license overage, fulfilled | Intent + LicenseSubscription + every SchoolCreditAllocation | Paid school-wide grant (widest lock) |
| 3380 | upgrade, subscription changed | rows written before it | Paid "needs review" record |
| 3442 | upgrade, applied | UserSubscription + buckets | **Stripe/DB divergence** — `Subscription.modify` already ran (P1b) |
| 3486 | individual_subscribe (retired) | UserSubscription + buckets | Paid activation |
| 3571 | license_create | licence rows + teacher invitations | Paid licence creation |
| 3697 | trial_to_paid (retired) | UserSubscription + buckets | Paid conversion |

## Failure matrix (Gate 5)

Stripe state is recorded for every case. The path **only ever reads** from Stripe (`assert_stripe_untouched` asserts no non-GET call), so Stripe state is unchanged in all of them.

| Injection | App DB after | Stripe after | Test |
|---|---|---|---|
| Stripe errors during the deferred lookup | Grant present, event SUCCEEDED, link NULL | Unchanged (GET only) | `test_stripe_down_during_receipt_task_grant_succeeds` |
| Malformed reply (unexpanded `latest_charge`) | Grant present, event SUCCEEDED, link NULL, then filled by the sweep | Unchanged | `test_malformed_stripe_reply_leaves_grant_intact_and_is_backfilled` |
| Crash after Stripe answers, before the local write | Grant present, event SUCCEEDED, link NULL, then filled by the sweep | Unchanged | `test_crash_after_stripe_answers_but_before_the_local_write` |
| Broker down when the task is queued (after commit) | Grant present, event SUCCEEDED | Unchanged | `test_broker_down_at_commit_grant_succeeds` |
| Unexpected error while queueing | Purchase committed, error logged | Unchanged | `test_unexpected_enqueue_error_after_commit_does_not_fail_the_caller` |
| Worker lost before the task runs | Grant present, link NULL, healed by the sweep | Unchanged | `test_worker_lost_before_receipt_task_is_healed_by_sweep` |
| Handler fails after `record`, before commit | Nothing committed, **no task queued**, no Stripe call | Unchanged | `test_handler_failure_after_record_queues_no_receipt_task` |
| Redelivery / duplicate deliveries | Exactly one grant, one row, one link | Unchanged | `test_duplicate_webhook_deliveries_with_slow_stripe_grant_once` |

The ordering that used to lose money is now inverted: Stripe is called only after the grant is durable, so a failure at any point can cost the link, never the credits.

## P1c — automatic replay, one flow only

Approved by the Senior Manager with exactly one allow-list entry: `("checkout.session.completed", "overage_block_purchase_checkout")`. The approval, the denied list with reasons, and the binding requirements are in `team/sessions/fix-overage-lock.md`.

How each requirement is met, and the test that proves it:

| Requirement | Implementation | Proof |
|---|---|---|
| Deny by default | Only keys of `AUTO_REPLAYABLE` are replayed; missing or unknown flow is skipped | `DenyByDefaultTests` |
| Cannot reach a refund or `Subscription.modify` even if the list is widened | The mapped flow handler is called directly, never the dispatcher; and a second gate checks its qualified name against `VETTED_HANDLERS` | `WidenedAllowListTests` add a refund handler and an upgrade handler and prove neither is called and Stripe sees no mutation; `test_runs_exactly_the_mapped_handler_not_the_dispatcher` |
| Pinned membership | — | `AllowListPinningTests` |
| Stored payload, never re-fetch Stripe | `event_flow` reads the recorded payload | `test_stripe_is_never_re_fetched_to_decide` asserts zero Stripe calls |
| Per-event skip reason, durable | `auto_replay_note` on the row, plus the log | every `DenyByDefaultTests` case asserts the note |
| Capped attempts | `auto_replay_attempts`, max 3, capped rows not selected, re-checked at claim time | `test_attempts_are_capped`, `test_exhausted_row_is_not_run_even_when_handed_over` |
| Exactly one grant under concurrency | One conditional UPDATE from FAILED, fenced on the attempts count seen | `ReplayConcurrencyTests` (20 threads × 10 rounds: concurrent sweeps; sweep vs live redelivery); `ReplayOneGuardTests` |
| Idempotency proof exercised, not asserted | — | `test_replaying_an_already_granted_purchase_grants_nothing_more` |
| A replay that fails again stays FAILED | settled by `_run_handler_inline` | `test_a_replay_that_fails_again_stays_failed` |

**Migration 0070** adds `auto_replay_attempts` and `auto_replay_note` to `billing_stripeevent`. `scripts/check_migration_safety.py --base b744c9f`: additive only, exit 0. Both columns are added with a constant default, which Postgres 11+ applies without rewriting the table. The one cost on a large table: `PositiveIntegerField` adds `CHECK (auto_replay_attempts >= 0)`, which Postgres validates with one sequential scan under `ACCESS EXCLUSIVE`, briefly blocking webhook claims. Migration 0067 (`recovery_attempts`) shipped the identical pattern to production on this table. A zero-lock variant (a `NOT VALID` constraint validated separately) is available if the Senior Manager asks for it.

## Environment finding (not a product defect)

Postgres `max_connections` is 100 on this machine (3 reserved). With ~12 concurrent `manage.py test` processes, 85 backends were in use and a 20-thread test failed with an `OperationalError` purely because the server was full. It passes alone and in its own module set. The test now fails with an explicit message naming the connection limit (`assert_not_db_capacity`) so it can never be read as a flake or a product failure. Standing rule agreed with the fixes-coordinator: only one 20-thread module runs at a time across all sessions.

## Follow-ups owned elsewhere

- **P1b** (separate item): irreversible Stripe calls still inside `@transaction.atomic` webhook handlers — `Subscription.modify` (3442, Stripe/DB divergence), `release_schedule`, the side-effect invoice void/refund, `sync_price`, `Customer.modify`.
- **P1c** (approved by d4, not yet written): a deny-by-default sweep that auto-replays only `("checkout.session.completed", "overage_block_purchase_checkout")`, to heal customers already stuck. Requirements recorded in `team/sessions/fix-overage-lock.md`.
