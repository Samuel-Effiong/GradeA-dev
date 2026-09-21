# Suite-wide sweep: unchecked joins, live network in tests, sleep synchronisation

Ordered by the Senior Manager after the `ConcurrentOverageDeliveryTests`
flake was root-caused: find every other test with the same shape.

**Swept at** `bab3d09` (only `billing/tests/test_overage_purchase_integrity.py`
differs from `b744c9f`; every other line number is identical in both).
**Method:** static audit (reading the code), delegated to a read-only agent and
partially re-verified by hand. **No test was run to confirm a flake.**

## Verification status — read this before acting on a row

| Rows | Status |
|---|---|
| `test_overage_refund_lifecycle.py:1160`, `test_price_reconciliation.py:875-905`, `billing/tests/tests.py:206-220`, `users/tests_google_auth.py:765` (+ its gating) | **RE-VERIFIED BY HAND** (this session read the code) |
| `billing/tests/test_overage_purchase_integrity.py` (all classes) | **PROVEN BY RUN**: 42/42 pass with 0 live Stripe calls after the fix |
| Every other row | **AGENT-REPORTED, NOT RE-VERIFIED.** Line numbers must be re-checked before editing |

Two areas the audit could not fully determine, recorded as unknown rather than clean:
1. `RealSignatureVerificationTests` (`test_webhook_signature_verification.py:85`) — the no-local-subscription branch was confirmed to make no Stripe call, but not every dispatch branch was stepped through.
2. `billing/qa_time_travel.py` / `billing/qa_console.py` action paths — the tests patch `LiveQAHarness` wholesale, so the stub was verified but not each action path.

## Systemic finding 1 — nothing stops a test calling the internet

There is **no global network block**: no `TEST_RUNNER`, no `conftest.py`, no
pytest, no socket guard (`grep` for `TEST_RUNNER|pytest_socket|disable_socket|responses|respx|vcr|httpretty` finds nothing outside the venv).
`AutoGrader/settings.py:1239-1252` is the only test-time branch and it only swaps
the cache backend and Celery key prefix. `billing/imports.py:4` sets
`stripe.api_key` from a real `sk_test_` key, so an unstubbed `stripe.*` call in
any test is a real round trip, with stripe-python's ~80 s default timeout —
longer than most `join(timeout=60)` in this suite. That is the mechanism behind
the flake this branch fixes.

A socket/test-runner guard is the highest-leverage fix, but it must allow the
~90 tests deliberately gated on `RUN_REAL_AI`, `CI_REQUIRE_NETWORK`,
`ENABLE_STRIPE_LIVE_QA` and live-key checks. **The Senior Manager reviews that
design before anyone builds it.**

## Systemic finding 2 — `with patch(...)` entered inside worker threads

`unittest.mock.patch` rebinds a module attribute; it is not thread-safe. When
each worker enters its own `with patch(...)`, the first worker to exit restores
the real function while the others are still running — so a "stubbed" test makes
a **real** call, and a stub can be left installed for later tests.
`assignments/tests_load.py:282-290` already documents an incident from this.
Sites: `test_price_reconciliation.py:879-889`, `test_abandoned_claim_recovery.py:371/403`,
`assignments/tests_security.py:1264`, `test_webhook_idempotency.py:397`.

## A. `join(timeout=…)` without a liveness check

`FLAW` = asserts on state without proving the workers finished.
`FLAW-MITIGATED` = a following count assertion incidentally catches it, but
reports it as something else and still leaks a live thread into teardown.

| file:line | class :: method | verdict | note |
|---|---|---|---|
| billing/tests/test_overage_purchase_integrity.py:699 | ConcurrentOverageDeliveryTests :: _run | **FIXED (this branch)** | the original defect |
| billing/tests/test_overage_refund_lifecycle.py:1160 | ConcurrentRefundTests :: _run | **FLAW** | structural twin: same barrier, same 60 s join, asserts committed ledger state |
| billing/tests/test_concurrent_credit_operations.py:83 | module `run_in_threads` | **FLAW** | asserts wallet arithmetic; a timed-out worker reads as a lost update, i.e. a false money-bug accusation |
| billing/tests/test_price_reconciliation.py:900 | ConcurrentRunTests | **FLAW** | + per-thread patch (systemic finding 2): the winner's sweep can reach real Stripe |
| billing/tests/test_abandoned_claim_recovery.py:383, :412 | ConcurrentRecoveryTests | **FLAW** | + per-thread patch of `process_stripe_event.delay` |
| billing/tests/test_webhook_idempotency.py:367 | WebhookRaceRegressionTests | **FLAW-MITIGATED** | the same file does it correctly at :432 |
| billing/tests/test_live_qa_invariants.py:763 | CheckpointTests | **FLAW** | a thread that never ran yields `None`, a confusing failure |
| billing/tests/tests.py:218-219 | ConcurrentRegistrationTest | **FLAW** | `join()` with NO timeout (can wedge the suite) and the worker never closes its connection |
| assignments/tests_security.py:1289 | ConcurrentAccessRevocationTest | **FLAW** | passes VACUOUSLY if the revoke thread never runs; per-thread patch can expose the real renderer |
| assignments/tests_upload_batch_billing.py:488 | ConcurrentRetriesTest | **FLAW** | unpacking `ValueError` on a partial result, then billing assertions |
| assignments/tests_upload_batch_billing.py:523 | ConcurrentRetriesTest | **FLAW-MITIGATED** | |
| assignments/tests_upload_batch_scale.py:151 | LargeBatchUploadTest | **FLAW-MITIGATED** | |
| assignments/tests_prerender.py:441 | PrerenderConcurrentDispatchTest | **FLAW** | |
| assignments/tests_load.py:564 | AssignmentReadPathLoadTest | **FLAW (minor)** | a live writer keeps writing into teardown |
| assignments/tests_pdf_cache.py:436, :541 | SingleFlightTest | **FLAW-MITIGATED** | |
| assignments/tests_pdf_cache.py:578 | SingleFlightTest | **FLAW (minor)** | a stuck leader leaks silently |
| assignments/tests_pdf_renderer.py:330, :540, :673, :961 | renderer concurrency tests | **FLAW-MITIGATED** | :540's timing assertion silently includes a timed-out join |
| assignments/tests_pdf_renderer.py:1076, :1115 | LoadSheddingUnderRealContentionTest | **FLAW** | asserts reclaimed capacity while up to 12 workers may still hold slots |
| students/tests_submission_concurrency.py:126 | BatchUploadSessionResultsConcurrencyTest | **FLAW** | patch block exits with a worker possibly inside it |
| students/tests_submission_concurrency.py:193 | SubmissionAttemptLimitConcurrencyTest | **FLAW** | an overrunning worker would reach the REAL AI client |
| students/tests_post_grading_submission_lock.py:782, :819 | PostGradingLockConcurrencyTest | **FLAW-MITIGATED** | |
| students/tests_post_grading_submission_lock.py:882-883 | …grades-own-commit race | **FLAW** | asserts grading_state/score before the grader may have committed |
| students/tests_grading_idempotency.py:177 | ClaimSubmissionForGradingConcurrencyTest | **FLAW-MITIGATED** | |
| classrooms/tests_concurrency_and_resilience.py:94 | EnrollmentConcurrencyTests :: _run_concurrently | **FLAW** | unfinished entries stay `None`; callers assert row counts |
| AutoGrader/tests_cache_generation.py:319, :488 | GenerationConcurrencyTests, HighConcurrencyCounterTests | **FLAW** | 100 threads; a missed worker reads as a lost Redis INCR |
| AutoGrader/tests_cache_user_fanout.py:638 | UserRowConcurrencyTests | **FLAW** | asserts generation deltas |
| ai_processor/tests_answer_benchmark_concurrency.py:105 | module `run_concurrently` | **FLAW (no timeout)** | hangs rather than fails |
| ai_processor/tests_grading_pipeline.py:235 | WalletRowNotLockedBetweenCallsTest | **FLAW-MITIGATED** | reads as a lock failure rather than a timeout |
| ai_processor/tests_pdf_service_concurrency.py:125 | PdfServiceIsNotSharedBetweenThreadsTest | **FLAW-MITIGATED** | |
| assignments/tests_load.py:250, :656, :762 | load tests | **OK** | explicit `is_alive` assertions at 251-254, 657-659, 763 |
| assignments/tests_renderer_crash.py:244, :430 | crash recovery | **OK** | `is_alive` assertions at 246, 432-433 |
| billing/tests/test_webhook_idempotency.py:431 | WebhookRaceRegressionTests | **OK** | `assertFalse(thread_a.is_alive())` at 432 |
| assignments/tests_pdf_renderer.py:1025 | …exactly-the-limit test | **OK-by-construction** | semaphore drains prove all workers arrived |
| students/tests_post_grading_submission_lock.py:935; students/tests_async_edit_path.py:479; users/tests_login_lockout.py; users/tests_activity_middleware_load.py | executor-based tests | **OK** | `ThreadPoolExecutor`/`pool.map` joins every worker; HTTP calls bounded by explicit timeouts |

**Missing per-thread `connection.close()`:** only `billing/tests/tests.py:207-212`.
Every other DB-touching worker closes in `finally`.

## B. Live network in tests that are not gated live tests

Chain for the dominant one: `handle_checkout_completed` (`billing/stripe_service.py:2784`)
→ `_handle_overage_checkout_completed:2928` → `resolve_stripe_receipt_url:3049`
→ `stripe.PaymentIntent.retrieve` (`:331`). Errors are swallowed at `:336-344`,
so the tests pass while making a real round trip; it shows only as time.

| file / class | verdict | note |
|---|---|---|
| billing/tests/test_overage_purchase_integrity.py — all 5 classes | **FIXED (this branch)** | was ~42 live calls; now 0 |
| billing/tests/test_overage_refund_lifecycle.py — 10 classes (233, 326, 374, 455, 564, 655, 702, 805, 1051, 1128) | **LIVE-UNSTUBBED** | all via `RefundFixture.purchase:149`; in `ConcurrentRefundTests` the call is on the main thread, not the workers |
| billing/tests/test_price_reconciliation.py:857 ConcurrentRunTests | **STUBBED-RACY → LIVE under the race** | see systemic finding 2 |
| users/tests_google_auth.py:765 LiveGoogleEndpointContractTests | **REAL GOOGLE CALLS, weakly gated** | no `skipUnless`; gated only by an import-time socket probe, so it runs on any networked machine. **CI-failure risk — fixed by the integrator in 301d915** |
| ai_processor real-AI suites; tests_ssrf_guard.py:344; billing live-QA suites | **EXEMPT-LIVE** | correctly gated on `RUN_REAL_AI` / `CI_REQUIRE_NETWORK` / `ENABLE_STRIPE_LIVE_QA` |
| ~30 other billing suites (cap, never-expires, cycle integrity, renewal, cancel, upgrade, payment methods, webhooks, …) | **STUBBED** | each patches the resource it touches |
| Cloudinary default storage under `ENVIRONMENT=local` | **UNVERIFIED RISK** | `settings.py:1016-1022`; a test saving through default storage would go out with real credentials. Being checked by the integrator; push-blocking if real |

## C. `time.sleep` as synchronisation

| file:line | verdict | note |
|---|---|---|
| assignments/tests_pdf_cache.py:568 | **FLAW** | `sleep(0.2)` "let the leader claim the flight" — the whole test depends on it |
| assignments/tests_renderer_crash.py:242, :426 | **FLAW** | `sleep(0.8)`/`sleep(1.0)` before killing the browser; degrades silently into a different scenario |
| assignments/tests_load.py:760 | **FLAW** | `sleep(1.5)` to hold the queue full; the sibling test does this properly with a signal at tests_pdf_renderer.py:1018-1022 |
| assignments/tests_upload_task_retry_policy.py:434 | **WEAK** | `sleep(8)` to prove a retry never appears: proof of absence by clock |
| sleeps inside stubs simulating latency; bounded polls ending in a hard assertion | **OK** | e.g. tests_pdf_cache.py:446/484/524, tests_load.py:272/545, tests_prerender.py:421, `_wait_until` helpers |

Adjacent: `Event.wait(timeout=…)` whose return value is ignored
(`students/tests_post_grading_submission_lock.py:842`, `assignments/tests_upload_task_retry_policy.py:451`,
`assignments/tests_load.py:741`, `assignments/tests_pdf_cache.py:560`,
`assignments/tests_pdf_renderer.py:993/1053/1100`, `billing/tests/test_webhook_idempotency.py:392`).
All are inside stubs where a timeout degrades the scenario rather than failing it.

## Ranked by flake risk

1. `test_price_reconciliation.py:900` — unchecked join **+** per-thread patch: real Stripe from a worker, plus stub leakage into later tests.
2. `assignments/tests_security.py:1289` — 13 threads, vacuous pass, per-thread patch of the renderer.
3. `test_abandoned_claim_recovery.py:383/:412` — unchecked joins + per-thread patch; `len(queued) == 1` is genuinely flaky.
4. `test_overage_refund_lifecycle.py:1160` — structural twin of the fixed bug. Highest-value copy-the-fix target.
5. `test_concurrent_credit_operations.py:83` — a timed-out worker reads as a money bug.
6. `assignments/tests_upload_batch_billing.py:488` — billing assertions on partial state.
7. `students/tests_submission_concurrency.py:193` — an overrunning worker reaches the real AI client.
8. `students/tests_post_grading_submission_lock.py:882-883` — asserts before the grader commits.
9. `assignments/tests_pdf_renderer.py:1115/:1076` — capacity asserted under live workers.
10. `AutoGrader/tests_cache_generation.py:488/:319` — 100 threads, a missed worker reads as a lost increment.
11. `billing/tests/tests.py:218-219` — no timeout (can wedge the suite) + an undroppable test DB.
12. `ai_processor/tests_answer_benchmark_concurrency.py:105` — no timeout.
13. `classrooms/…:94`, `AutoGrader/tests_cache_user_fanout.py:638`, `assignments/tests_prerender.py:441`, `test_live_qa_invariants.py:763`, `assignments/tests_load.py:564`, `assignments/tests_pdf_cache.py:578` — confusing failures rather than false passes.
14. Sleep-synchronised: `tests_renderer_crash.py:242/:426`, `tests_load.py:760`, `tests_pdf_cache.py:568`.
15. All `FLAW-MITIGATED` rows — no false passes, but misleading messages and leaked threads/connections into teardown.

## Ownership (Senior Manager ruling)

**This branch (`task/fix-overage-concurrency-flake`): billing files + the shared helper only.**

- DONE: `AutoGrader/testing/concurrency.py` (+ `AutoGrader/tests_concurrency_harness.py`, 7 tests) and `billing/tests/test_overage_purchase_integrity.py`.
- REMAINING here: `test_overage_refund_lifecycle.py`, `test_concurrent_credit_operations.py`, `test_price_reconciliation.py`, `test_abandoned_claim_recovery.py`, `test_webhook_idempotency.py:367`, `test_live_qa_invariants.py:763`, `billing/tests/tests.py:218`.
- EVERYTHING ELSE → the new **"test-suite reliability hardening"** item, to run after the in-flight fixes land (files owned by live sessions), using this helper. Owner TBD.
