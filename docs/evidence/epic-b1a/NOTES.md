# Epic B1a notes (Release Builder, a0)

## On task/epic-b0-r2 at 350e2682 — beta f5d2d577 plus B0 plus B1 plan

Plan: `docs/phase2/architecture/08_epic_b1_implementation_plan.md` at 350e2682.
Code facts checked against 350e2682 (code unchanged from b8f12291).

---

## 1. Survey: every file:line the plan cites, re-verified

### 1a. `execute_graded_task` structure (ai_processor/services.py)

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| Function start | :5480 | YES | :5480 `def execute_graded_task(` |
| B0 entry guard | :5502 | YES | :5502 `assert_not_zero_credit(MODEL_CALL)` |
| Unmetered SUPER_ADMIN branch | :5612 | YES | :5612 `response = self.__ai_model(` |
| Estimate | :5636–5639 | YES | :5635–5638 `estimated_cost = self.estimate_messages_cost(` then `:5638 call_record.note_estimate` |
| Balance check | :5640–5649 | YES | :5640 `balance = wallet.total_remaining_credits()` through :5649 raise |
| Metered provider call | :5654 | YES | :5654 `response = self.__ai_model(` |
| Charge (consume_credits inside atomic) | :5671–5684 | YES | :5671 `with transaction.atomic():` through :5685 |
| record_billing_task_id | :5700 | **:5699** | :5699 `record_billing_task_id(task_id)` — off by 1 from plan; the plan says :5700, actual :5699 |

**Note:** :5700 vs :5699 is a cosmetic off-by-one in the plan. The call is exactly where described.

### 1b. billing_refund_scope sites (§0.8, 5 production sites)

| Site | Plan cite | Verified | Actual |
|---|---|---|---|
| Grading: `grade_student_submission` | ai_processor/services.py:4635 | YES | :4635 `with billing_refund_scope(reason="grading run failed"):` |
| Single-paper grade engine | students/services.py:962 | YES | :962 `with billing_refund_scope(` |
| Answer upload | students/services.py:1365 | YES | :1365 `with billing_refund_scope(` |
| Raw-text edit | students/services.py:1709 | YES | :1709 `with billing_refund_scope(` |
| Assignment upload | assignments/file_uploads.py:132 | YES | :132 `with billing_refund_scope(reason="assignment upload failed"):` |

All 5 sites confirmed. Each wraps one or more `execute_graded_task` calls.

### 1c. CreditWallet and consume_credits (billing/models.py)

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| CreditWallet class | :596 | YES | :596 `class CreditWallet(models.Model):` |
| total_remaining_credits | :683 | YES | :683 `def total_remaining_credits(self):` |
| consume_credits | :875 | YES | :874–875 `@transaction.atomic` / `def consume_credits(` |
| assert_not_zero_credit(WALLET_CONSUME) | :925 | YES | :925 `assert_not_zero_credit(WALLET_CONSUME)` |
| select_for_update in consume_credits | :928 | YES | :928 `locked = CreditWallet.objects.select_for_update().get(pk=self.pk)` |

### 1d. lock_wallet_first (billing/locks.py)

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| Function | :25 | YES | :25 `def lock_wallet_first(wallet):` |
| select_for_update().get | :33 | YES | :33 `return CreditWallet.objects.select_for_update().get(pk=wallet.pk)` |

33 lines total, as expected.

### 1e. billing/refunds.py

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| ContextVar `_active_task_ids` | — | YES | :32 ContextVar("billing_refund_task_ids") |
| billing_refund_scope | :57 | YES | :57 `def billing_refund_scope(*, reason="task failed"):` |
| Nesting hands ids up | :82–83 | YES | :82–83 `outer.extend(ids)` |
| reset(token) in finally | :85 | YES | :85 `_active_task_ids.reset(token)` |

109 lines total.

### 1f. BackgroundProcessingTask (students/models.py)

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| Class | :540 | YES | :540 `class BackgroundProcessingTask(models.Model):` |
| reason_code | :590 | YES | :590 `reason_code = models.CharField(` |
| retry_count | :600 | YES | :600 `retry_count = models.PositiveIntegerField(` |
| item_index | :605 | YES | :605 `item_index = models.PositiveIntegerField(` |
| started_at | :619 | YES | :619 `started_at = models.DateTimeField(null=True, blank=True)` |
| cancel_requested_at | :618 | YES | :618 `cancel_requested_at = models.DateTimeField(null=True, blank=True)` |

**New columns needed for B1a:** `job` (FK → AIJob, null, SET_NULL, db_index=False), `hold_state` (HELD/RETURNED, null), `hold_credits` (int, db_default=0), `hold_drawn` (int, db_default=0), `released_credits` (int, db_default=0).

### 1g. Upload door (assignments/upload_door.py)

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| upload_refusal_if_unaffordable | :82 | YES | :82 `def upload_refusal_if_unaffordable(` |
| parked_files | :115 | YES | :115 `def parked_files(` |
| total_remaining_credits read | :92, :128 | YES | :92 and :128 both call `wallet.total_remaining_credits()` |

152 lines total. B1a converts the door's `total_remaining_credits()` check to `spendable_credits()` with a hold.

### 1h. Grant paths for FR-B-08 (§0.6, step 8)

| Path | Plan cite | Verified | Actual |
|---|---|---|---|
| Stripe checkout `_handle_license_overage_checkout_completed` | billing/stripe_service.py:3204 | YES | :3204 `def _handle_license_overage_checkout_completed(session, metadata):` |
| handle_checkout_completed outer atomic | :2907–2908 | **:2919** | The dispatch call is at :2919 inside `handle_checkout_completed` which has `@transaction.atomic` |
| Super admin grant `_grant_overage_offline` | billing/license_service.py:3169 | **:3170** | :3170 `def _grant_overage_offline(` (preceded by `@staticmethod` at :3169) |
| Offline approval `approve_overage_offline_request` | billing/license_service.py:3446–3447 | YES | :3446 `@transaction.atomic` / :3447 `def approve_overage_offline_request(` |
| Shared core `_grant_overage_blocks` | :3089 | YES | :3089 `def _grant_overage_blocks(` |
| Must be inside atomic | :3122 | YES | :3122 `Must be called from within an existing @transaction.atomic block` |

**Off-by-one on _grant_overage_offline:** plan says :3169, actual def at :3170 (decorator at :3169). Cosmetic.

### 1i. Timing and sweep constants

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| visibility_timeout | AutoGrader/settings.py:575 | YES | :575 `"visibility_timeout": 3600` (1 hour) |
| GRADING_TASK_TIME_LIMIT_SECONDS | students/services.py:297 | YES | :297 `GRADING_TASK_TIME_LIMIT_SECONDS = 25 * 60` (25 min) |
| GRADING_CLAIM_STALE_AFTER | students/services.py:306 | YES | :306 `GRADING_CLAIM_STALE_AFTER = timedelta(seconds=GRADING_TASK_TIME_LIMIT_SECONDS + 5 * 60)` (30 min) |
| CELERY_TASK_ACKS_LATE | AutoGrader/settings.py:559 | YES | :559 comment confirms `CELERY_TASK_ACKS_LATE=True` |
| Worker concurrency default | scripts/start-worker.sh:9 | YES | :9 `--concurrency="${CELERY_WORKER_CONCURRENCY:-4}"` |

### 1j. started_at write sites (R4-1 survey)

B1a's R4-1 conditional start claim needs to know what else writes `started_at` on `BackgroundProcessingTask`. Survey of all non-test, non-migration writes:

| Site | What | Safe? |
|---|---|---|
| students/task_tracking.py:178–180 | `update_processing_task(…, started=True)`: sets `started_at = now()` **only if not already set** (`if started and not task.started_at`). Uses `task.save(update_fields=…)` | YES — never overwrites an existing `started_at`. B1a's conditional UPDATE writes `started_at` first; this sees it set and skips |
| students/task_tracking.py:218 | `claim_processing_task_start`: conditional UPDATE `PENDING→STARTED` or `STARTED + started_at < cutoff → STARTED`. Resets `started_at = now` | **NEEDS COORDINATION** — on extraction tasks, this is the claim. B1a's start claim at unit entry runs instead; `claim_processing_task_start` is not used for grading (grading uses `_claim_submission_for_grading` at students/services.py:309, on StudentSubmission, not BackgroundProcessingTask). For extraction (file_uploads), B1a's conditional UPDATE replaces this call at unit entry |

**Conclusion:** `started_at` is safe for B1a's conditional start claim. The only production write that sets it regardless is `claim_processing_task_start` (line 218), which B1a's entry rule replaces on the held-item path. The `update_processing_task` (line 178) only sets it when null, so it will see B1a's value and skip.

### 1k. Entry points E-1 through E-7

| Id | Plan cite | Verified | Actual |
|---|---|---|---|
| E-1 | Single-paper grade: `grade_engine_async` from students/views.py:~964, assignments/views.py:~1785 | YES | students/views.py:964 dispatches `grade_engine_async`; assignments/views.py:1785 dispatches `grade_engine_async` |
| E-2 | Item retry: students/item_retry.py:~151 | YES | item_retry.py contains the retry-in-place path |
| E-3 | auto_grade_due_assignment: assignments/tasks.py:1668; _relaunch_failed_dispatches: :1616 | YES | :1668 `def auto_grade_due_assignment(` / :1616 `def _relaunch_failed_dispatches(` |
| E-4 | Sync student upload: students/views.py:~610 → upload_answers_engine | YES | :610 calls `upload_answers_engine`; fn at students/services.py:1334 |
| E-5 | Sync raw-text edit: students/views.py:~794 → update_submission_from_raw_text | YES | :794 calls `update_submission_from_raw_text`; fn at students/services.py:1669 |
| E-6 | Async raw-text edit: assignments/tasks.py:~450 → update_submission_from_raw_text | YES | :450 calls `update_submission_from_raw_text` |
| E-7 | Upload-async behind upload door | YES | assignments/upload_door.py provides the door; file_uploads.py:132 has the refund scope |

### 1l. Audit retention

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| RetentionClass | audit/enums.py:40 | YES | :40 `class RetentionClass(models.TextChoices):` — GENERAL and STUDENT_RECORD only; FINANCIAL does not exist yet |
| RETENTION_DAYS | audit/volume.py:55–57 | YES | :55–57 `{GENERAL: 365, STUDENT_RECORD: 365*3}` |
| retention_class_of | audit/volume.py:61 | YES | :61 `def retention_class_of(action) -> str:` |

B1a adds FINANCIAL (7 × 365) to all three.

### 1m. LicenseOveragePurchaseStatus and webhooks

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| Status choices PENDING/COMPLETED/FAILED | billing/models.py:1981 | YES | :1981–1984 exactly these three |
| checkout.session.completed wired | billing/webhooks.py:103 | YES | :103 `"checkout.session.completed": StripeWebhookHandler.handle_checkout_completed` |
| checkout.session.expired NOT wired | — | YES | No expired handler in webhooks.py |
| event_replay AUTO_REPLAYABLE | billing/event_replay.py:82 | YES | :82 `AUTO_REPLAYABLE = {` |

### 1n. Other plan cites

| What | Plan cite | Verified | Actual |
|---|---|---|---|
| _claim_submission_for_grading | students/services.py:309 | YES | :309 `def _claim_submission_for_grading(` |
| CreditLedger.record | billing/models.py:1505 | YES | :1505 `def record(cls, *, user=None, **kwargs):` |
| CreditLedger after_bulk_create | billing/models.py:~1514 | YES | :1514 `def after_bulk_create(cls, objs):` |
| refund_credits uses bulk_create | billing/services.py:1567 | YES | :1567 `CreditLedger.objects.bulk_create(ledger_rows)` |
| remove_teacher_from_license | billing/license_service.py:1949 | YES | :1949 `def remove_teacher_from_license(` |
| reject_overage_offline_request | billing/license_service.py:3659 | YES | :3659 `def reject_overage_offline_request(` |
| SubscriptionPlan class | billing/models.py:236 | YES | :236 `class SubscriptionPlan(models.Model)` |
| payment_refunds.py | billing/payment_refunds.py | YES | 423 lines, Stripe partial refund + offline refund path |
| env.bool pattern | AutoGrader/settings.py:598, :685, :695 | YES | Multiple env.bool flags confirmed |

---

## 2. Plan contradictions

**None found.** Every line reference checks out (two cosmetic off-by-ones noted: record_billing_task_id at :5699 not :5700, _grant_overage_offline def at :3170 not :3169). No structural discrepancy.

---

## 3. Near-run expectation

**Apps:** `billing ai_processor assignments students users`

**Baseline (B0 3-app near-run, from NOTES.md):** Ran 4614, OK (skipped=19).
B1a adds `users` to the near-run apps. The beta full-suite baseline (run2_skips.txt) has 30 skips total; the 3-app B0 baseline has 19 skips. Users tests are expected to have 0 additional skips relevant to our apps.

**Expected:** Ran ≥ 4614 + users test count, OK, with skip count and reasons matching the beta baseline for these 5 apps. The exact Ran count and skip analysis will be written after the break run establishes the test discovery count.

**Skip reasons expected (B0 3-app baseline, carried forward):**

```
 9 skipped 'Real AI call is opt-in and billed: set RUN_REAL_AI=1'
 8 skipped 'load tests are opt-in: set RUN_LOAD_TESTS=1'
 1 skipped 'set RUN_LOAD_TESTS=1 to build the 6,000-student school'
 1 skipped 'Live network check is opt-in: set CI_REQUIRE_NETWORK=1'
```

Plus whatever users adds. To be finalized after discovery.

---

## 4. B1a mutant battery: expectations

### Scope

B1a = Steps 1, 2, 3, 3b, 8 from §2.1. Mutants from §6.2 in B1a scope:

M-B1, M-B2, M-B3, M-B4, M-B5, M-B6, M-B7, M-B11, M-B13, M-B14, M-B15, M-B16, M-B18, M-B21, M-B22, M-B23, M-B25, M-B26, M-B27, M-B28, M-B30, M-B31.

Plus F-1, F-2, F-3 (step 8) and Z-1 (step 2).

**Total: 26 kill targets.**

### Mutant expectations

Each mutant is identified by function name + exact text. Line numbers are hints only.
Kill fragments are derived by tracing "what fires next" when the mutant is applied.

| # | Mutant | Function + exact text | Killing test | Expected fragment |
|---|---|---|---|---|
| M-B1 | Reserve's UPDATE without the `reserved_credits + n <= total` condition | `reserve()`: remove `AND reserved_credits + :n <= :total_remaining` from the conditional UPDATE | R-1: 200 threads reserve against wallet fitting exactly 7 | `reserved_credits` exceeds `total_remaining` — more than 7 succeed, assertion on exactly-7 fails |
| M-B2 | Reserve without `lock_wallet_first` | `reserve()`: remove `lock_wallet_first(wallet)` call | R-4 (or R-1 depending on EPQ finding) | Two reserves race without the lock; either the conditional UPDATE over-reserves (R-4) or the count exceeds the fit (R-1) |
| M-B3 | Settle takes `min(actual, total_remaining)` ignoring other jobs' holds | `settle()`: change `min(actual, item_hold_left + (total_remaining − reserved_credits))` to `min(actual, total_remaining)` | R-3: overrun while another job holds | The other job's hold is violated — assertion that the other job's hold is untouched fails |
| M-B4 | Single-call hold removed from execute_graded_task | `execute_graded_task`: remove the single-call reserve/settle block | Provider-call-without-hold + AST dominance test | Provider mock called with no hold; `assert` that every `__ai_model` call is dominated by a reserve fails |
| M-B5 | Metered unit settles on exception | `metered_unit.__exit__`: settle on exception instead of release | No-charge-on-failure test | `CreditUsageLog` or `CreditLedger` row exists after a failed unit; assertion on zero rows fails |
| M-B6 | Settlement-key claim dropped from settle | `settle()`: remove the settlement-row insert | D-1: same item redelivered | Two settlements for the same `(item_id, retry_count)`; assertion on exactly one ledger set fails |
| M-B7 | Release not idempotent (no status condition) | `release()`: remove the conditional status transition | Double-release test | `reserved_credits` goes below its pre-release value (or negative, firing the CHECK); assertion on idempotent value fails |
| M-B11 | Stale-hold sweep keyed on batch job's `updated_at` | Sweep: key on `job.updated_at` instead of each item's `started_at + visibility_timeout + time_limit` | S-1: items waiting in broker > GRADING_CLAIM_STALE_AFTER | Held items in the broker backlog are swept early; assertion that their holds are untouched fails |
| M-B13 | `reserve()`'s `assert_not_zero_credit(CREDIT_RESERVE)` removed | `reserve()`: remove `assert_not_zero_credit(CREDIT_RESERVE)` | Z-1: zero-credit scope inside a held unit | `ZeroCreditViolation` not raised by `reserve`; assertion on the exception fails |
| M-B14 | Overrun charged beyond available balance | `settle()`: charge `actual` instead of `min(actual, item_hold_left + free_balance)` | Overrun test | Either `InsufficientCreditsError` raised (consume_credits refuses) or negative bucket; assertion on recorded `unbilled_credits` and non-negative balance fails |
| M-B15 | Release computed as `reserved − consumed` instead of `reserved − hold_drawn − released` | `release()`: use `job.reserved_credits − job.consumed_credits` | H-1: overrun then release | `reserved_credits` does not return to its starting value (overrun charges free balance, so `consumed > hold_drawn`); invariant assertion fails |
| M-B16 | Entry rule removed: metered_unit without a job opens a tally with no reserve | `metered_unit.__enter__`: skip the entry rule, always open a tally | E-1…E-7: provider mock called with no hold | Provider mock called while the unit's job has `hold_left = 0` and no fit was granted; entry-point test assertion fails |
| M-B18 | Pre-call fit check removed from the unit | `metered_unit.pre_call_check()`: remove entirely | U-1: unit with hold used up makes 0 further calls | Provider mock called when `item_hold_avail ≤ 0` and no free balance; assertion on 0 further calls fails |
| M-B21 | Pre-call check compares without reserving the shortfall | `metered_unit.pre_call_check()`: compare `item_hold_avail ≥ estimate` and proceed, without reserving shortfall | U-2: two units on one free balance | Both units spend from free balance unreserved; total charged exceeds holds + free balance; assertion on sum fails |
| M-B22 | Sweep returns hold without failing and counting the item | Sweep: return hold but skip `item.status = FAILED` and skip `completed_items += 1` | S-3: item whose message never returns | Job never reaches QUEUED/SETTLED because `completed_items` is short; assertion on terminal state fails |
| M-B23 | Settlement keyed on job id for an E-1 paper | `settle()`: use `job.id` as `item_id` in settlement key instead of the task row id | D-3: redelivered single-paper grade | Two settlements share one key (both use job id); only one ledger set written, but second delivery's work is lost or the wrong item settles; assertion on per-paper settlement identity fails |
| M-B25 | Sweep does not add to item's `released_credits` | Sweep: skip `item.released_credits += amount` | S-3: wallet and per-item invariants after sweep | Per-item invariant `hold_credits − hold_drawn − released_credits ≥ 0` cannot be checked (released_credits wrong); or wallet invariant fails because the sum over items' released_credits ≠ job's released_credits |
| M-B26 | Sweep also takes launched-but-unstarted items | Sweep: remove `started_at IS NOT NULL` or `started_at < cutoff` condition | S-5: broker backlog > 90 minutes not swept | Items in the broker (launched, not started) are swept; assertion that their holds are untouched fails |
| M-B27 | Late grading message for RETURNED item runs instead of exiting | `metered_unit.__enter__`: skip the hold_state check, proceed with the call | S-6: late item for SETTLED job | Provider mock called after the job settled; charges recorded on a terminal job; invariant assertion fails |
| M-B28 | Settle does not return the item's leftover | `settle()`: skip leftover release (skip `reserved_credits −= leftover`) | P-5: freed credits used before job ends; QUEUED assert | Hold is not freed per item; the move to QUEUED finds non-zero hold left; the assert fires |
| M-B30 | Pre-call check uses `item_hold_left` (ignores the tally) | `metered_unit.pre_call_check()`: use `item_hold_left` instead of `item_hold_avail` (= `item_hold_left − tally`) | U-1: unit with hold used up, 0 further calls | After the tally consumes the hold, the check still sees `item_hold_left > 0` (before tally subtraction); provider mock called when it should be refused |
| M-B31 | Entry reads `hold_state` instead of conditional start claim; or sweep drops `started_at < cutoff` | Entry: read `hold_state` and exit if RETURNED, instead of conditional UPDATE `SET started_at WHERE hold_state = 'HELD'` | S-8: barrier, redelivery starts after long backlog | Race: both the run and the sweep see `hold_state = 'HELD'` (no atomic claim); either both win (double work) or the sweep wins and the run doesn't know; assertion on exactly-one-winner fails |

### Forced-failure proofs (step 8)

| # | Test | Function + exact text | Expected kill |
|---|---|---|---|
| F-1 | Stripe checkout grant path: force failure after first teacher's bucket | `_grant_overage_blocks` at billing/license_service.py:3089: patch `CreditLedger.record` to raise for 2nd teacher | No bucket, ledger, `overage_blocks_used` or `BillingTransaction` survives for anyone; intent unchanged. Assertion: all rolled back |
| F-2 | Super admin grant path: force failure after first teacher's bucket | `_grant_overage_offline` at billing/license_service.py:3170: same patch | Same assertion: all rolled back |
| F-3 | Offline approval path: force failure after first teacher's bucket | `approve_overage_offline_request` at billing/license_service.py:3447: same patch | Same assertion: all rolled back, offline request unchanged |

### Zero-credit proof (step 2)

| # | Test | Expected kill |
|---|---|---|
| Z-1 | `reserve()` inside a `zero_credit_scope`: both reserve and consume raise `ZeroCreditViolation` | `ZeroCreditViolation` raised with `attempted = 'CREDIT_RESERVE'`; no hold created, no credits moved |

---

## 5. Test ID expectations

### 5a. New test files (B1a)

All new tests go in:
- `billing/tests/test_holds.py` — reserve, settle, release, spendable_credits, per-item accounting, invariants
- `billing/tests/test_holds_sweep.py` — stale-hold sweep, backstop, late messages
- `billing/tests/test_holds_entry_points.py` — E-1 through E-7 entry rule tests
- `billing/tests/test_holds_concurrency.py` — R-1 through R-4 barrier tests
- `billing/tests/test_holds_overrun.py` — H-1, overrun, unbilled
- `billing/tests/test_grant_atomicity.py` — F-1, F-2, F-3
- `billing/tests/test_holds_zero_credit.py` — Z-1
- `billing/tests/test_holds_metered_unit.py` — U-1, U-2, U-3, pre-call check, settle/fail
- `billing/tests/test_holds_settlement.py` — D-1, D-2, D-3, D-4, settlement keys
- `billing/tests/test_holds_late_messages.py` — S-1 through S-9
- `billing/tests/test_departed_teacher_refund.py` — §9 item 3, step 3b

Exact test IDs and fragments will be written once tests are coded. The red-run expectations (which tests fail by call-site revert) and break-run expectations (which pre-existing tests break) follow the B0 pattern: committed before any run, then the run is compared.

### 5b. Red-run plan

Red run = B1a tests only, with the production code at each guard site reverted (call-site revert). Each new guard site's revert is tested:

1. **reserve()** reverted to no-op → all hold tests fail
2. **settle()** reverted to direct consume_credits → settlement tests fail
3. **metered_unit entry rule** reverted to no-op → entry-point tests fail
4. **pre-call fit check** reverted to no-op → U-tests fail
5. **BILLING_HOLDS_ENABLED** flag forced off → all held-path tests fail (flag-on tests)

Exact FAIL count and test IDs are committed after test code is written, before the red run.

### 5c. Break-run plan

Break run = all pre-existing tests in the 5 apps, after B1a code is committed. Expected: all pass. B1a adds columns and a flag (default off), so existing behaviour is unchanged. Any break is a STOP.

### 5d. Near-run plan

Near run = all tests in `billing ai_processor assignments students users`, flag off (default). Expected: same Ran and same skips as B0 3-app baseline plus users tests.

---

## 6. Migration checklist (step 1)

From §4, B1a migrations:

1. `billing`: create `AIJob` — new table, indexes inline
2. `billing`: create settlement-key table — new table, unique `(item_id, retry_count)`
3. `billing`: `CreditWallet.reserved_credits` (int, default 0, db_default=0) + `low_balance_warned_level` + `low_balance_warned_cycle_start` — with CHECK via RunSQL/SeparateDatabaseAndState
4. `students`: `BackgroundProcessingTask.job` FK (null, SET_NULL, db_index=False) + `hold_state` + `hold_credits` + `hold_drawn` + `released_credits` — FK via RunSQL NOT VALID + validate; index via AddIndexConcurrently
5. `billing`: `AIJob.overrun_charged` + `SubscriptionPlan.queue_expiry_days/queue_expiry_warning_hours` + `BillingSettings`
6. `audit`: `RetentionClass` gains FINANCIAL — choices only, plus sweep logic

`makemigrations --check` after each.

---

## 7. Feature flag

`BILLING_HOLDS_ENABLED = env.bool("BILLING_HOLDS_ENABLED", default=False)` in `AutoGrader/settings.py`, following the pattern at :598/:685/:695.

No `CREDIT_REQUESTS_ENABLED` in B1a (that's B1c).
