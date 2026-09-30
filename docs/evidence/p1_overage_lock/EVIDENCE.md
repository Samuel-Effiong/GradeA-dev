# H-23 / P1 — Stripe receipt lookups held webhook transactions open

**Owner:** fix-overage-lock (grade-automator-plus-e2)
**Branch:** `task/overage-lock`
**Implementation commit:** `f7db2f2` (parent `b744c9f`)
**Status: NOT LANDED. Gates 2, 6, 7 (real Stripe), 8 and 10 are NOT RUN.**

## 10-gate table

| Gate | Status | Evidence |
|---|---|---|
| 1 Baseline / Regression | PASS | 12 tests fail on `b744c9f`, pass on `f7db2f2`: `g1_repro_b744c9f.log` (sha256 `d015b27d…`), `g1_repro_perflow_b744c9f.log` (550 lines, sha256 `851533eb…`). Billing app 1,448 tests OK (`billing_regression` run, 638s). pre-commit clean on every changed file. |
| 2 Mutation | NOT RUN | 41 mutants ready, one per guard the diff adds: `mutation/run_mutants.py`. Held by the fixes-coordinator for host contention; slots at 09:00. |
| 3 Concurrency | PASS (LOCAL-REAL) | `ReceiptConcurrencyTests`: 20 threads × 10 rounds on real Postgres. Concurrent fills → exactly one FILLED per round; duplicate deliveries → exactly one grant, one BillingTransaction, no lookup inside a transaction. |
| 4 Adversarial | NOT APPLICABLE (accepted by fixes-coordinator, pending d4 confirmation) | Design A adds no endpoint, no authorization decision and no user-controllable input. `metadata.flow` is server-set when the Checkout Session is created (`billing/stripe_service.py:2173`). **Becomes APPLICABLE for P1c**, which adds automatic replay of money-moving events. |
| 5 Failure / Recovery | PASS | See the failure matrix below. Every case records the app DB **and** the Stripe side. |
| 6 Stress / Scale | NOT RUN | Planned: sweep query cost at two table sizes ≥10× apart, constant query count, p50/p95, peak memory. |
| 7 Real Infrastructure | PARTIAL (LOCAL-REAL) | Real Postgres and real Redis throughout. Stripe is faked at stripe-python's HTTP layer. **Real Stripe test-mode receipt resolution still to run.** |
| 8 Live / E2E | NOT RUN | Per the Senior Manager's re-prioritisation, QA (beta) is itself the deployed stage: the deployed check happens on QA after landing. |
| 9 Security / Isolation | PASS | `ReceiptIsolationTests`: two teachers' identical purchases; each link lands on its own row only, neither row carries the other's payment intent, each wallet gets exactly its own block. |
| 10 Final Production Gate | NOT RUN | Strict procedure on the committed tree, two consecutive clean runs (billing change). |

## The 8 completion answers

1. **What changed.** Every `checkout.session.completed` flow recorded its purchase and then called Stripe for the receipt link *inside* `handle_checkout_completed`'s `@transaction.atomic`, holding that flow's row locks. The link is now resolved after commit: handlers record with no link and queue `billing.receipts.fill_receipt_url` via `transaction.on_commit`; an hourly `sweep_missing_receipt_urls` beat task fills anything missed. Five `record()` calls now store `stripe_payment_intent_id`, which the deferred lookup needs. `resolve_stripe_receipt_url` is deleted.
2. **Why it was necessary.** Production and beta run Postgres with `idle_in_transaction_session_timeout = 60s` (confirmed live 2026-09-17), shorter than stripe-python's 80s default timeout. One slow receipt lookup let Postgres terminate the session and roll back a grant the customer had paid for. Dispatch is asynchronous, so Stripe had already been answered 200 and never redelivered; the event landed FAILED with `handler_started_at` set, which the sweeper deliberately never replays. The customer received nothing until a manual `replay_stripe_events`. Meanwhile the wallet — or every `SchoolCreditAllocation` on a school purchase — stayed locked and credit consumption failed at `lock_timeout`.
3. **What was tested.** 10 per-flow tests (one per call site) through the real dispatcher on real Postgres; 2 reproductions of the original failure at 2s-scaled guard rails; 40 tests for the new module (bounded lookup, fill-if-null, scheduling, sweep bounds, failure/recovery, isolation, concurrency); the billing app's 1,448 tests.
4. **Which gates passed.** 1, 3, 5, 9 (and 4 as NOT APPLICABLE, pending d4's confirmation).
5. **Which gates remain incomplete.** 2 (NOT RUN), 6 (NOT RUN), 7 (PARTIAL — no real Stripe call yet), 8 (NOT RUN), 10 (NOT RUN).
6. **What risks remain.** The guards are unproven until the mutation battery runs. The sweep's cost at scale is unmeasured. No real Stripe call has been made through the new bounded client, so only the fake proves the request shape. Receipt links now appear seconds later than before; no email or serializer depends on that timing (`receipt_url` is read only by `billing/serializers.py:2617`). Customers whose credits are already stuck from this bug are **not** healed by this change — that is P1c, approved and not yet written.
7. **What exact commit contains the verified implementation.** `f7db2f2` for the code. Gate-5 and Gate-9 additions are committed on top; see the branch head recorded below.
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

## Environment finding (not a product defect)

Postgres `max_connections` is 100 on this machine (3 reserved). With ~12 concurrent `manage.py test` processes, 85 backends were in use and a 20-thread test failed with an `OperationalError` purely because the server was full. It passes alone and in its own module set. The test now fails with an explicit message naming the connection limit (`assert_not_db_capacity`) so it can never be read as a flake or a product failure. Standing rule agreed with the fixes-coordinator: only one 20-thread module runs at a time across all sessions.

## Follow-ups owned elsewhere

- **P1b** (separate item): irreversible Stripe calls still inside `@transaction.atomic` webhook handlers — `Subscription.modify` (3442, Stripe/DB divergence), `release_schedule`, the side-effect invoice void/refund, `sync_price`, `Customer.modify`.
- **P1c** (approved by d4, not yet written): a deny-by-default sweep that auto-replays only `("checkout.session.completed", "overage_block_purchase_checkout")`, to heal customers already stuck. Requirements recorded in `team/sessions/fix-overage-lock.md`.
