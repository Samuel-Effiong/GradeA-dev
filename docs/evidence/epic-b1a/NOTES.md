# Epic B1a notes (Release Builder, a0)

## On task/epic-b0-r2 at 350e2682 — beta f5d2d577 plus B0 plus B1 plan

Plan: `docs/phase2/architecture/08_epic_b1_implementation_plan.md` at 350e2682.
Code facts checked against 350e2682 (code unchanged from b8f12291).

---

## 0. Re-survey on task/epic-b1a at staging 2f00c078 (2026-11-06, Lane 1)

Staging 2f00c078 is beta 40f514f6 plus B0, about 165 commits after the 350e2682 survey below.
Every file:line in plan 08 (89) and in this file (37) was compared, cited block by cited block, between d808a9ce and 2f00c078.
**None is broken.** The lines below moved; every other cite is unchanged. The tables in §1 keep their 350e2682 numbers. **This table overrides them.**

| File | Old (350e2682 / d808a9ce) | Now (2f00c078) | Moved by |
|---|---|---|---|
| ai_processor/services.py | SUPER_ADMIN call :5612 | :5615 | H-281 |
| | estimate :5636–5639 (actual :5635–5638) | :5638–5641 | |
| | balance check :5640–5649 | :5643–5657 (both raises now pass `individual=`) | |
| | metered call :5654 | :5661 | |
| | charge :5671–5684 | :5678–5691 | |
| | record_billing_task_id :5699/:5700 | :5706 | |
| | estimate_messages_cost :5721 / estimate_total_token :5774 / `+= 20000` :5790 | :5728 / :5781 / :5797 | |
| | unchanged: :4574, :4635, :5480, :5502 | | |
| students/services.py | refund scopes :962 / :1365 / :1709 | :1130 / :1538 / :1882 | H-279 |
| | GRADING_TASK_TIME_LIMIT_SECONDS :297, GRADING_CLAIM_STALE_AFTER :306, `_claim_submission_for_grading` :309 | :300, :309, :312 | |
| | upload_answers_engine :1334, update_submission_from_raw_text :1669 | :1507, :1842 | |
| assignments/tasks.py | grade_engine_async :646, :450, grade_batch_async :1436, `_relaunch_failed_dispatches` :1616, :1662, auto_grade_due_assignment :1668, :1733 | :648, :452, :1466, :1646, :1692, :1698, :1763 | H-279 |
| billing/models.py | CreditLedger.record :1505, after_bulk_create :1514, LicenseOveragePurchaseStatus :1981, stripe_checkout_session_id :2042, SchoolCreditAllocation :2315 | :1508, :1517, :1984, :2045, :2318 | H-281 |
| billing/stripe_service.py | handle_checkout_completed atomic :2907–2908, `_handle_license_overage_checkout_completed` :3204, :3248, §8 :~3250, m3-a :~3384–3387 | :3044–3045, :3341, :3385, :~3385, :~3521–3524 | H-274/H-275 |
| AutoGrader/settings.py | :559, :575, :598, :685, :695 | :558, :574, :597, :684, :694 | |
| students/views.py | :610, :794, :964 | :628, :814, :984 | |
| assignments/views.py | :1785 | :1796 | |
| users/models.py | notify_* :408–424 | :437–453 | |
| users/services.py | :202 | :205 | |
| billing/views.py | wallet :1463 | :1465 | |

**Drift that changes the build (SM rulings 2026-11-06):**

1. **H-281.** `InsufficientCreditsError` takes `individual=is_individual_teacher(user)`, and `mark_processing_task_failure` stores `meta["message_variant"]` (`credit_refusal_variant`).
   - Every refusal B1a adds passes `individual=`, with one test per entry point.
   - Existing messages only; no new wording.
2. **H-279 naming.** H-279 brought in a "held result" (`StudentSubmission.pending_result` / `pending_graded_at`). B1a code, fields and audit keys say "credit hold" / `hold_*`, never a bare "held".
3. **H-279 billing (2(b)): ON HOLD, waiting for an owner ruling.**
   - Charging the held re-grade itself is the same as today: one charge at the AI call; the hold is decided later, at the save.
   - Approval (promote_pending → refresh_student_summary) starts one billed summary call, charged to the approving actor.
   - There is no reject path. An overwriting re-grade charges again, with no refund.
   - Publish-all is per assignment, and a student has at most one submission per assignment. So publish-all of N held rows covers N different students: at most one summary per student (SM correction, 2026-11-06).
   - The pinning tests wait for the ruling.
4. **Billed paths added since the survey.** The student summary (student_summary_async → generate_student_summary → execute_graded_task at ai_processor/services.py:5999) is single-call, so the hold inside `execute_graded_task` covers it. It is not a unit site.
5. **Migration numbering.**
   - billing: after 0073_schoolcreditallocation_grant_anchor_at (unchanged).
   - students: after 0036_studentsubmission_pending_result (was 0035).
   - audit: after 0003_auditevent_target_time_ix (was 0002).
   - assignments, if needed: after 0041_topic_set_null (SM, H-297).
6. **Stash b1a-paused (cfd89cc2)** is applied onto 2f00c078 (apply only; the entry is kept). The 3-way merge resolved the `audit/metadata.py` AUDIT_RETENTION_SWEEP hunk, and every H-273/H-278/H-279 key is kept.

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

## 3. Near-run expectations

### 3a. Flag-off near run (default)

**Apps:** `billing ai_processor assignments students users`

**Discovery (DiscoverRunner on 350e2682 + B1a new tests):** 6378 total, 20 skipped.
B0 3-app baseline: 4633 discovered (4614 run, 19 skipped). Users adds 1745 tests and 1 extra skip.

**Expected:** Ran 6358 + (B1a new test count), OK (skipped=20 + B1a skips if any)

Exact Ran is the discovery count minus skips, updated after all B1a tests are coded.

**Skip reasons (5-app discovery at 350e2682, exact):**

```
10 skipped 'Real AI call is opt-in and billed: set RUN_REAL_AI=1'
 8 skipped 'load tests are opt-in: set RUN_LOAD_TESTS=1'
 1 skipped 'set RUN_LOAD_TESTS=1 to build the 6,000-student school'
 1 skipped 'Live network check is opt-in: set CI_REQUIRE_NETWORK=1'
```

### 3b. Flag-on near run (BILLING_HOLDS_ENABLED=True)

Same 5 apps, with `override_settings(BILLING_HOLDS_ENABLED=True)`.

**Not predictable in advance.** Procedure once code is complete:
1. Exploratory flag-on run, log committed. This is NOT the gate.
2. Classify every failure:
   - Real bug → STOP to SM.
   - Test that legitimately asserts old charge-then-refund behaviour → declared per exact test id.
3. Once all failures are classified and bugs fixed, declare the flag-on near run with exact Ran, skips, and declared test ids.
4. The declared flag-on near run is then the gate run.

Both flag-off and flag-on near runs are required for green.

### 3c. Concurrency evidence

R-1 to R-4 run locally on PostgreSQL 18.6. The PG 16 run (`.github/workflows/tests.yml:57`, NFR-TST-11) is pending CI; not claimed here.

---

## 4. B1a mutant battery: expectations

### Scope

B1a = Steps 1, 2, 3, 3b, 8 from §2.1. Mutants from §6.2 in B1a scope:

M-B1, M-B2, M-B3, M-B4, M-B5, M-B6, M-B7, M-B11, M-B13, M-B14, M-B15, M-B16, M-B18, M-B20 (door path), M-B21, M-B22, M-B23, M-B25, M-B26, M-B27, M-B28, M-B30, M-B31, M-B33 (new, step 3b).

Plus F-1, F-2, F-3 (step 8) and Z-1 (step 2).

**Total: 28 kill targets.**

### Mutant expectations

Each mutant is identified by function name + exact text. Line numbers are hints only.
Kill fragments below are derived by tracing "what fires next" when the mutant is applied.

**Fragment refinement (SM directive 5):** Before the first mutant runs, each fragment below will be replaced with ONE exact assertion-message fragment from the coded test. Descriptive fragments (e.g. M-B2 "either … or", M-B25, M-B31) will be settled once:
- M-B2's EPQ question is settled by R-4 before its fragment is written.
- All others are taken from the coded test's assertion message.
These refined fragments are committed before any mutant run.

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
| M-B20 | Door launch sent inline instead of in `on_commit` | Upload door: send Celery message inline instead of `transaction.on_commit(lambda: ...)` | P-3 (door path): no broker message before commit | A worker starts before the hold commits; assertion that no message exists before commit fires fails |
| M-B21 | Pre-call check compares without reserving the shortfall | `metered_unit.pre_call_check()`: compare `item_hold_avail ≥ estimate` and proceed, without reserving shortfall | U-2: two units on one free balance | Both units spend from free balance unreserved; total charged exceeds holds + free balance; assertion on sum fails |
| M-B22 | Sweep returns hold without failing and counting the item | Sweep: return hold but skip `item.status = FAILED` and skip `completed_items += 1` | S-3: item whose message never returns | Job never reaches QUEUED/SETTLED because `completed_items` is short; assertion on terminal state fails |
| M-B23 | Settlement keyed on job id for an E-1 paper | `settle()`: use `job.id` as `item_id` in settlement key instead of the task row id | D-3: redelivered single-paper grade | Two settlements share one key (both use job id); only one ledger set written, but second delivery's work is lost or the wrong item settles; assertion on per-paper settlement identity fails |
| M-B25 | Sweep does not add to item's `released_credits` | Sweep: skip `item.released_credits += amount` | S-3: wallet and per-item invariants after sweep | Per-item invariant `hold_credits − hold_drawn − released_credits ≥ 0` cannot be checked (released_credits wrong); or wallet invariant fails because the sum over items' released_credits ≠ job's released_credits |
| M-B26 | Sweep also takes launched-but-unstarted items | Sweep: remove `started_at IS NOT NULL` or `started_at < cutoff` condition | S-5: broker backlog > 90 minutes not swept | Items in the broker (launched, not started) are swept; assertion that their holds are untouched fails |
| M-B27 | Late grading message for RETURNED item runs instead of exiting | `metered_unit.__enter__`: skip the hold_state check, proceed with the call | S-6: late item for SETTLED job | Provider mock called after the job settled; charges recorded on a terminal job; invariant assertion fails |
| M-B28 | Settle does not return the item's leftover | `settle()`: skip leftover release (skip `reserved_credits −= leftover`) | P-5: freed credits used before job ends; QUEUED assert | Hold is not freed per item; the move to QUEUED finds non-zero hold left; the assert fires |
| M-B30 | Pre-call check uses `item_hold_left` (ignores the tally) | `metered_unit.pre_call_check()`: use `item_hold_left` instead of `item_hold_avail` (= `item_hold_left − tally`) | U-1: unit with hold used up, 0 further calls | After the tally consumes the hold, the check still sees `item_hold_left > 0` (before tally subtraction); provider mock called when it should be refused |
| M-B31 | Entry reads `hold_state` instead of conditional start claim; or sweep drops `started_at < cutoff` | Entry: read `hold_state` and exit if RETURNED, instead of conditional UPDATE `SET started_at WHERE hold_state = 'HELD'` | S-8: barrier, redelivery starts after long backlog | Race: both the run and the sweep see `hold_state = 'HELD'` (no atomic claim); either both win (double work) or the sweep wins and the run doesn't know; assertion on exactly-one-winner fails |
| M-B33 | Departed teacher's skipped share NOT refunded | `_handle_license_overage_checkout_completed` (step 3b): remove the auto-refund for the skipped teacher | Departure-between-checkout-and-webhook test | The skipped teacher's share is not refunded; assertion on Stripe partial refund (or offline refund record) being created fails |

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

Red run = B1a tests only, with the production code at each guard site reverted (call-site revert). Guard sites:

1. **reserve()** reverted to no-op → hold tests fail
2. **settle()** reverted to direct consume_credits → settlement tests fail
3. **metered_unit entry rule** reverted to no-op → entry-point tests fail
4. **pre-call fit check** reverted to no-op → U-tests fail

Exact FAIL/ERROR count, test IDs, and fragments are committed after tests are coded and before the red run.

**Supplementary check (not red evidence):** `BILLING_HOLDS_ENABLED` flag forced off → all held-path tests exercise the flag-off branch, confirming the flag gates the new code.

### 5c. Break-run plan

Break run = pre-existing tests that B1a EDITS, run at their OLD versions against the new code. It proves they fail only for the declared reason.

**Edited (SM ruling 2026-11-06, (a)/(b) approved):** `audit/tests_background_attribution.py` `SweepSelfRecordTests`.
- The old file is saved at `~/Documents/Projects/GAP-a0-runs/epic-b1a/old_tests_background_attribution.py`, sha256 `ec65d3e334463c1a069ddb438d22a5c67ba15ce0d95bcf2b448a82089788049f` (= HEAD ad0f8b3a).
- Run the two tests at their old text against the step-1 code. **Expected: 2 FAIL, 0 ERROR**:
  - `test_a_retention_sweep_that_deletes_nothing_still_records_itself`: the metadata dict differs only by the extra `'deleted_financial': 0`.
  - `test_a_retention_sweep_records_its_counts`: `{'deleted_general': 0, 'deleted_student_record': 0, 'deleted_financial': 0} != {'deleted_general': 2, 'deleted_student_record': 0}`. The two grant rows are now FINANCIAL, so the old GENERAL-only `update` ages nothing.
- Any other failure, or a different dict, is a STOP.

**Edited (SM ruling 2026-11-06, (c) approved):** `audit/tests_schema.py:251` `ClosedVocabulariesTest.test_the_role_outcome_class_and_retention_values_match_the_data_model`. Not foreseen in §9; found by the targeted audit-app check (459 tests, 1 FAIL).
- The old file is saved at `~/Documents/Projects/GAP-a0-runs/epic-b1a/old_tests_schema.py`, sha256 `4ef0fe7520ffcd533b25a77287e8547d30df8d398d0f59be490713cc4e6e3263` (= HEAD f65e53b4).
- Run the one test at its old text against the step-1 code. **Expected: Ran 1, 1 FAIL, 0 ERROR**, with fragment `Items in the first set but not the second:` followed by `'FINANCIAL'` and nothing else.
- Afterwards, the same targeted check (`audit billing.tests.test_credit_transaction_audit`, 459 tests) must give 0 failures.

**Edited (SM ruling 2026-11-06, step 2):** `AutoGrader/tests_beat_locks.py:48` `EPIC_ONLY_GUARDED_BEAT_TASKS` gains `billing.tasks.sweep_stale_credit_holds` and `billing.tasks.check_credit_hold_invariants`. That's the documented pattern (precedent 160271889); beta's pinned 21 stays. Found by the step-2 targeted check (113 tests).
- The old file is saved at `~/Documents/Projects/GAP-a0-runs/epic-b1a/old_tests_beat_locks.py`, sha256 `78e63aa52149a92a11b4c4a885a011d47d22997fd51ad7d473134c1bc441665f`.
- Run `RealTaskSkipTests.test_every_guarded_beat_task_skips_while_another_run_holds_it` at its old text against the step-2 code. **Expected: Ran 1, 1 FAIL, 0 ERROR**, fragment `AssertionError: 23 != 21`.
- Afterwards, the step-2 targeted check (113 tests) must give 0 failures.

### 5d. Near-run plan

See §3a (flag-off) and §3b (flag-on). Both are required for green.

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

---

## 8. Checkpoints (SM directive 8)

After each plan step, send SM the sha plus its own tests' result. A checkpoint is a progress report, not a gate run. Mismatches against declared expectations are still stops.

| Step | What | Checkpoint |
|---|---|---|
| 1 | Schema (AIJob, settlement key, wallet reserved_credits, BPT columns, audit FINANCIAL) | sha + makemigrations --check + model tests |
| 2 | billing/holds.py (reserve, settle, release, sweep, invariants) | sha + unit tests for holds |
| 3 | Wire holds behind BILLING_HOLDS_ENABLED (entry rule, metered unit, pre-call check) | sha + entry-point tests, metered-unit tests |
| 3b | Departed-teacher share refund | sha + departure-refund test |
| 8 | FR-B-08 forced-failure proofs (F-1, F-2, F-3) | sha + grant atomicity tests |

---

## 9. Step 1 (schema): expectations, committed before any run (2026-11-06)

### 9a. Departures from the stash draft (cfd89cc2), all SM-approved 2026-11-06

| Where | Stash draft | Step 1 | Why |
|---|---|---|---|
| `AIJob.wallet` | CASCADE | PROTECT | 03a §2.2 |
| `AIJob.school` | FK to `users.School` | `school_id` UUIDField, null, indexed | 03a §2.2, plan §3.1 "captured as 03a says" |
| `AIJob.course` | missing | FK `classrooms.Course`, SET_NULL | 03a |
| `AIJob.sufficiency_verdict`, `failed_items`, `trace_id` | missing | added (CharField(16) null; int default 0; UUID indexed) | 03a |
| `AIJob.estimated_p50/p90` | that name | `estimate_p50/p90` | 03a |
| `AIJob.total_items` | default 1 | default 0 | 03a |
| `AIJob.job_type` | 4 values (BATCH_GRADING, SINGLE_CALL, SINGLE_ITEM, UPLOAD) | 03a's four + SINGLE_CALL, SINGLE_ITEM, UPLOAD; max_length 32, indexed | 03a + plan §3.1 |
| `AIJob.status` | max_length 30 | max_length 32 | 03a |
| `AIJob.queued_at` … `cancel_requested_at` | `cancel_requested_at` present | kept (plan §3.1 state machine: "teacher cancels / cancel_requested_at") | plan |
| `AIJob` indexes | `(wallet, status)` plain | 03a's three: `aijob_wallet_open_ix` partial RESERVED/RUNNING, `aijob_queued_expiry_ix` partial QUEUED_PENDING_CREDITS, `aijob_requester_created_ix` `(requested_by, -created_at)` | 03a |
| `AIJob` CHECKs | none | `aijob_credits_nonneg` (reserved, consumed, released ≥ 0), `aijob_items_within_total` (completed + failed ≤ total) | 03a |
| `HoldState` labels | "Held" / "Returned" | "Credit held" / "Credit returned" (values unchanged); BPT uses the same labels | SM 2(a) |
| `CreditWallet` CHECK `reserved_credits >= 0` | missing | `creditwallet_reserved_credits_nonneg`, RunSQL NOT VALID, then VALIDATE in a separate migration; `Meta.constraints` is the state side | plan §3.2, §4 |
| Reverse of `reserved_credits` | plain | `refuse_reverse_with_open_holds`: refuses with the flag on or any `reserved_credits ≠ 0` | plan §4 |
| `BPT.job` | AddField FK | ADD COLUMN + FOREIGN KEY NOT VALID (`students_bpt_job_id_fk_billing_aijob`), VALIDATE separately, `AddIndexConcurrently` `students_bpt_job_id_ix` in an `atomic = False` migration | plan §4 |
| Emitter | unchanged (only GENERAL or STUDENT_RECORD) | CREDIT_TRANSACTION stored FINANCIAL; FINANCIAL wins over `touches_student_record` | plan §4, SM ruling 1 |
| `FINANCIAL_ACTIONS` | in `audit/volume.py` | in `audit/enums.py` beside `STUDENT_RECORD_ACTIONS`; volume and tasks import it | SM ruling 1 |
| Drift-summary table | absent | still absent (B1b) | plan §4 marks it B1b |
| `audit/metadata.py` `ALLOWED_KEYS` | only the AUDIT_RETENTION_SWEEP allowlist gained `deleted_financial` | the global `ALLOWED_KEYS` gains it too; without it the emitter drops the key | found by the first green attempt (§9e) |

`AIJob.requested_by` CASCADE (a student for student uploads) stays as 03a says: "an operational row, not history; the ledger keeps the money trail". SM agreed 2026-11-06. No per-student audit deletion or export walk exists (AuditEvent is append-only), so FINANCIAL tagging removes nothing from one. The SM is raising the wider NFR-CMP-04 gap with the owner separately.

**Migrations (each header names its class, per docs/MIGRATIONS.md §2):**
- billing: `0074_b1a_credit_hold_schema`, `0075_creditwallet_reserved_credits_check` (NOT VALID), `0076_validate_creditwallet_reserved_credits_check`
- students: `0037_backgroundprocessingtask_credit_hold` (column + FK NOT VALID + hold columns), `0038_validate_backgroundprocessingtask_job_fk`, `0039_backgroundprocessingtask_job_index` (concurrent, `atomic = False`)
- audit: `0004_alter_auditevent_retention_class` (choices only)

### 9b. Step-1 tests

New: `billing/tests/test_holds_schema.py` (23 tests; S-1…S-8) and `audit/tests_financial_retention.py` (8 tests; FR-1, FR-2). Edited: the two `SweepSelfRecordTests` tests (§5c).

### 9c. Red run: expected result

Red = the step-1 tests at commit T (tests and NOTES only) on code = staging 2f00c078, with no stash and no schema. Run in a disposable detached worktree with test DB `test_epic_b1a_red`.

Command: `python manage.py test billing.tests.test_holds_schema audit.tests_financial_retention audit.tests_background_attribution.SweepSelfRecordTests --settings=settings_worktree --noinput`

Expected: **Ran 34, FAILED (failures=13, errors=21)**.
- Ran 34 = 23 (test_holds_schema) + 8 (tests_financial_retention) + 3 (SweepSelfRecordTests).
- errors=21 counts `test_negative_job_credits_are_refused` as 3 subtest ERRORs; every other test gives one result.
- 2 PASS: `test_an_ordinary_event_is_still_general` (control) and `test_the_pii_sweep_records_what_it_scrubbed_including_zero` (untouched).
- The `test_holds_schema` ERRORs are 4 + 3 + 1 + 2 + 3 + 2 + 3 + 2 = 20, plus 1 in FR-2.

| Test | Expected | Fragment |
|---|---|---|
| S-1 test_aijob_has_the_03a_columns_and_the_plan_additions | ERROR | LookupError: App 'billing' doesn't have a 'AIJob' model |
| S-1 test_aijob_on_delete_rules_follow_03a | ERROR | LookupError … 'AIJob' |
| S-1 test_aijob_job_types_and_statuses | ERROR | LookupError … 'AIJob' |
| S-2 test_a_new_job_starts_estimated_with_every_counter_at_zero | ERROR | LookupError … 'AIJob' |
| S-2 test_aijob_counters_have_database_defaults | FAIL | 'NO COLUMN' != '0' |
| S-3 test_negative_job_credits_are_refused (×3 subtests) | ERROR ×3 | LookupError … 'AIJob' |
| S-3 test_items_finished_beyond_the_total_are_refused | ERROR | LookupError … 'AIJob' |
| S-3 test_aijob_indexes_match_03a | FAIL | '(wallet_id, status)' not found in '' |
| S-4 test_wallet_starts_with_nothing_reserved | ERROR | AttributeError: 'CreditWallet' object has no attribute 'reserved_credits' |
| S-4 test_a_negative_reservation_is_refused_by_the_database | ERROR | FieldDoesNotExist: CreditWallet has no field named 'reserved_credits' |
| S-4 test_the_reservation_check_is_validated | FAIL | None != ('c', True) |
| S-5 test_reverse_is_refused_while_a_wallet_holds_credits | ERROR | ModuleNotFoundError: No module named 'billing.migrations.0074_b1a_credit_hold_schema' |
| S-5 test_reverse_is_refused_with_the_flag_on | ERROR | ModuleNotFoundError (same) |
| S-5 test_reverse_is_allowed_with_the_flag_off_and_no_hold_open | ERROR | ModuleNotFoundError (same) |
| S-6 test_one_settlement_per_item_and_retry | ERROR | LookupError … 'SettlementRecord' |
| S-6 test_settlement_has_no_settled_flag_column | ERROR | LookupError … 'SettlementRecord' |
| S-7 test_a_task_starts_with_no_credit_hold | ERROR | TypeError: BackgroundProcessingTask() got unexpected keyword arguments: 'job' |
| S-7 test_deleting_the_job_keeps_the_task | ERROR | LookupError … 'AIJob' |
| S-7 test_the_job_foreign_key_is_validated_and_indexed | FAIL | None != ('f', True) |
| S-7 test_hold_state_reads_as_a_credit_hold | ERROR | AttributeError: module 'billing.models' has no attribute 'HoldState' |
| S-8 test_plan_queue_lifetime_defaults | ERROR | FieldDoesNotExist: SubscriptionPlan has no field named 'queue_expiry_days' |
| S-8 test_billing_settings_is_one_row_with_its_defaults | ERROR | LookupError … 'BillingSettings' |
| S-8 test_holds_flag_defaults_off | FAIL | None is not False |
| FR-1 test_financial_is_kept_seven_years | FAIL | 'FINANCIAL' not found in |
| FR-1 test_a_new_credit_event_is_stored_financial | FAIL | 'GENERAL' != 'FINANCIAL' |
| FR-1 test_a_student_linked_credit_event_is_stored_financial | FAIL | 'STUDENT_RECORD' != 'FINANCIAL' |
| FR-1 test_an_ordinary_event_is_still_general | PASS | control |
| FR-2 test_a_financial_row_survives_until_seven_years | FAIL | True is not false |
| FR-2 test_a_legacy_general_credit_row_survives_the_general_cutoff | FAIL | False is not true |
| FR-2 test_a_legacy_general_credit_row_goes_at_seven_years | ERROR | KeyError: 'deleted_financial' |
| FR-2 test_a_student_record_credit_row_survives_the_three_year_cutoff | FAIL | False is not true |
| SweepSelfRecord test_a_retention_sweep_that_deletes_nothing_still_records_itself (new text) | FAIL | 'deleted_financial' |
| SweepSelfRecord test_the_pii_sweep_records_what_it_scrubbed_including_zero | PASS | untouched |
| SweepSelfRecord test_a_retention_sweep_records_its_counts (new text) | FAIL | {'deleted_general': 3, 'deleted_student_record': 0} != {'deleted_general': 1, … 'deleted_financial': 1} |

Any mismatch with this table is a STOP, investigated and reported before green.

### 9d. Green: expected result

Same command on the step-1 code: all OK. Then `makemigrations --check --dry-run` reports no changes. Then the break run (§5c).

### 9e. Step 1 results (2026-11-06)

| Run | Code | Result | Matches expectation | Log (docs/evidence/epic-b1a/step1/) |
|---|---|---|---|---|
| Red | f65e53b4 (tests only, staging code), disposable worktree, DB test_epic_b1a_red | Ran 34, FAILED (failures=13, errors=21) | yes: every row of §9c, fragment for fragment | red-run.log |
| Green, first attempt | WIP | Ran 34, FAILED (failures=2, errors=2): the sweep metadata had no `deleted_financial` (the global `ALLOWED_KEYS` lacked it) | n/a: a build fault, fixed (§9a last row). **The log was overwritten by the next run**; the four failures were the two SweepSelfRecordTests dict mismatches and two `KeyError: 'deleted_financial'` in FR-2 | — |
| Green | WIP, then 55c19958 | Ran 34, OK, both times | yes | green-run.log (at 55c19958) |
| makemigrations --check | 55c19958 | No changes detected | yes | — |
| check_migration_safety --base origin/staging | 55c19958 | all 7 migrations "additive only" | yes | migration-safety.txt |
| Break run: SweepSelfRecordTests at old text (sha ec65d3e3) vs step-1 code | WIP | Ran 3, FAILED (failures=2): exactly the two §5c dict mismatches (dict key order is printed differently) | yes | break-run-sweep-tests.log |
| Targeted check: audit app + billing test_credit_transaction_audit | WIP | Ran 459, FAILED (failures=1): `audit/tests_schema.py:251`, not foreseen. STOP; SM ruling (c) | no: a STOP, ruled on | audit-app-check-1.log |
| Break run: tests_schema.py:251 at old text (sha 4ef0fe75) vs step-1 code | WIP | Ran 1, FAILED (failures=1): `Items in the first set but not the second: 'FINANCIAL'` | yes: §5c (committed at 4642207c before the run) | break-run-tests-schema.log |
| Targeted check, again | WIP (= 55c19958 code apart from formatting) | Ran 459, OK (skipped=2) | yes | audit-app-check-2.log |

The edited test files were restored by sha256 from saved copies after each break run: tests_background_attribution.py 87c626e9…, tests_schema.py b447e8f8…. Both equal the committed files.

Commits:
- f65e53b4: expectations and tests
- 4642207c: §5c addition
- 55c19958: step-1 code. The pre-commit black/isort reformatting (audit/tasks.py, 0074 import order) is cosmetic; green was re-run on 55c19958.
- 3db3871e: 03a amendment (SM ruling (d))

---

## 10. Slice summary items (collected for the B1a slice review)

- **Always on, not behind `BILLING_HOLDS_ENABLED` (step 1):** the emitter stores CREDIT_TRANSACTION as FINANCIAL, and the retention sweep keeps financial actions 7 years. New credit rows are tagged FINANCIAL as soon as step 1 deploys. Plan §4 intends this (SM note 2026-11-06).
- Every run has its own log file (attempt number or timestamp); nothing is overwritten (SM directive 2026-11-06).
- **Counting deviation (step 2, SM-approved as necessary):** settle → `completed_items`; failure or sweep → `failed_items`; done when `completed + failed = total`. The plan's "sweep increments completed_items" would break 03a's CHECK.
- **Always on, read-only (step 2):** the daily `check_credit_hold_invariants` beat task is not behind the flag. It is silent when nothing is held (tested). The sweep task is flag-gated.
- **Retry (step 2, SM ruling):** an item retry runs under a new SINGLE_ITEM job (plan §3.4 E-2 over §3.2's wording), linked by the shared `trace_id`.
- **Open:** the sweep writes no AuditEvent until B1's four audit actions are named (plan §9 item 12).

## 11. Step 2 (hold service, `billing/holds.py`): expectations, committed before any run (2026-11-06)

### 11a. Scope

In step 2 (plan §2 row 2):
- reserve, settle, fail and finish (release) on `hold_drawn`, with `overrun_charged`
- per-item settlement keyed on the task row
- the pre-call fit check against the item's own hold, reserving any shortfall
- `spendable_credits`
- the per-item stale-hold sweep (started items only)
- leftover returned in each settle or fail
- the start claim; the late door helpers (settlement-row check, new UPLOAD job)
- the invariant checks
- `CREDIT_RESERVE` zero-credit code
- two beat tasks in `billing/tasks.py`, flag-gated at the task: the sweep every 5 min under `single_instance`, and the daily invariant check (reports only, `credit_ledger_anomaly{kind="hold_drift"}`), with their beat entries in `AutoGrader/settings.py`

Not in step 2:
- `metered_unit` itself and every wiring (step 3)
- the late-door teacher email (step 3). **It needs new wording, which I'll bring to the SM before step 3 code.**
- the 36-hour unstarted-items backstop, last-item transition, promotion and expiry (step 5)
- R-2, R-4, U-2, P-1…P-5 (steps 5 and 9)
- the mutant battery (step 9 gate)

### 11b. API

```
spendable_credits(wallet) -> int
create_job(*, wallet, requested_by, job_type, total_items=1, held_per_item=0,
           assignment=None, course=None, school_id=None, trace_id=None, meta=None) -> AIJob
reserve(wallet, amount, job, item=None) -> bool
item_hold_left(job, item=None) -> int
ensure_call_fits(job, estimate, *, item=None, tally=0) -> int   # shortfall reserved; raises InsufficientCreditsError
settle(job, actual, *, item=None, key=None, feature=None, task_type=None, course=None) -> SettlementOutcome
fail_item(job, *, item=None) -> int
finish_job(job, to_status) -> int | None
claim_item_start(item) -> bool
already_settled(item) -> bool
open_late_door_job(old_job, item, amount) -> AIJob             # raises InsufficientCreditsError
item_sweep_window() / single_sweep_window() -> timedelta
sweep_stale_holds(now=None) -> {"items_swept", "credits_returned", "jobs_released"}
check_invariants(wallet=None) -> list[str]
```

### 11c. Design decisions (plan silent or ambiguous; flagged to the SM at the checkpoint)

1. **The holder.** A job with no task row is its own single item: E-4, E-5, SINGLE_CALL, and a late door job. Its hold is the job's `reserved − hold_drawn − released`. Plan §3.2: "the item's hold is the job's".
2. **Counting.** Settle adds 1 to `completed_items`; a failure (unit exception or sweep) adds 1 to `failed_items`. A job is finished when `completed + failed = total`. That fits 03a's `CHECK (completed_items + failed_items <= total_items)`. Plan §3.2 says the sweep "increment[s] completed_items"; counting the failure there as well would count it twice against the CHECK. A settle on an item already RETURNED by the sweep counts nothing (plan R4-1).
3. **After settle**, the item's `hold_state` becomes RETURNED (nothing is left to sweep), so a late redelivery's start claim fails and it exits.
4. **Ledger identity.** `consume_credits(task_id=str(SettlementRecord.id))`: each attempt has its own ledger identity (plan §3.2 step 4).
5. **Late door job.** A new UPLOAD job with `total_items=1`, accounted as its own holder. The settlement key is still `(task.id, task.retry_count)`, passed as `key=`. The task row and the old job are untouched (S-4). A refused reserve leaves the new job FAILED with 0 reserved and raises `InsufficientCreditsError(individual=…)`.
6. **Sweep window.**
   - Items: `visibility_timeout` (3600 s, from `CELERY_BROKER_TRANSPORT_OPTIONS`) + `GRADING_TASK_TIME_LIMIT_SECONDS` (1500 s) + `HOLD_SWEEP_MARGIN` (15 min).
   - Jobs with no task row: `GRADING_TASK_TIME_LIMIT_SECONDS` + margin, from `created_at`.
   - The plan says only "a margin"; 15 min is my choice.
7. **A swept item** gets status FAILURE and `reason_code = PROVIDER_FAILURE`. That code is retryable, and its existing text defaults to "No credits were charged.", which is true for a swept item. No new wording.
8. **Refusals** pass `individual=is_individual_teacher(wallet.user)` (SM ruling 1) and reuse the existing message "Refill your wallet to continue".
9. **Lock order:** wallet (`lock_wallet_first`), then job row, then item row, in every transaction (H-181 order: wallet before its buckets; `consume_credits` locks the wallet again, re-entrantly in the same transaction).
10. **Metrics:** `credit_overrun` and `credit_overrun_charged` (count, tags `job_type`) when non-zero (plan §3.5).

### 11d. Tests (46)

| File | Tests |
|---|---|
| `billing/tests/test_holds.py` | 23: spendable/reserve 6, settle/fail/finish 5, pre-call 4, start claim 2, late door 3, invariants 3 |
| `billing/tests/test_holds_settlement.py` | 5: D-1, D-2, D-3, D-4, no-task-row key |
| `billing/tests/test_holds_overrun.py` | 4: H-1a, H-1b, absorbed overrun + metrics, R-3 |
| `billing/tests/test_holds_sweep.py` | 9: S-1/S-5 sweep half, window kept, S-3, settled never swept, single job swept/kept, task flag off/on, invariant task metric |
| `billing/tests/test_holds_zero_credit.py` | 3: Z-1 (reserve, create_job, settle under a scope) |
| `billing/tests/test_holds_concurrency.py` | 2: R-1 (32 threads, not 200: one connection per thread; max_connections 300 is shared with other sessions), S-8 (10 rounds) |

Helpers: `billing/testing/holds.py`. `holds()` imports the module when a test runs.

### 11e. Red: expected

At the tests commit, with no `billing/holds.py` and no tasks:

`python manage.py test billing.tests.test_holds billing.tests.test_holds_settlement billing.tests.test_holds_overrun billing.tests.test_holds_sweep billing.tests.test_holds_zero_credit billing.tests.test_holds_concurrency --settings=settings_worktree --noinput`

**Expected: Ran 46, FAILED (failures=1, errors=45).**
- 42 ERRORs are `ModuleNotFoundError: No module named 'billing.holds'`, including setUp errors (PreCallCheckTests ×4 and LateDoorTests ×3, each reported per test).
- 3 ERRORs are `AttributeError: module 'billing.tasks' has no attribute …`: `sweep_stale_credit_holds` ×2 and `check_credit_hold_invariants` ×1. The sweep-task tests look the task up via importlib before building their fixture, which keeps mypy quiet about a not-yet-existing name.
- The 1 FAIL is R-1 `test_concurrent_reserves_grant_exactly_what_fits`. The worker threads record the ModuleNotFoundError, so `assertEqual(errors, [])` fails with a list of ModuleNotFoundError. Fragment: `No module named 'billing.holds'`.
- Run in a disposable detached worktree at the tests commit (step-1 code present), test DB `test_epic_b1a_red2`. Log: `step2_red_<sha>.log`.

Any other count or fragment is a STOP.

### 11f. Green: expected

The same command on the step-2 code: Ran 46 OK. Then the step-1 tests again (Ran 34 OK), and `makemigrations --check` (no changes; step 2 adds no model change). Each run gets its own log.

### 11g. SM rulings 2026-11-06 after green attempt 1, and the changed tests (committed before green attempt 2)

**Green attempt 1** (log `step2_green_attempt1.log`): Ran 46, errors=1. D-2 raised IntegrityError on `aijob_items_within_total`: a retry in place on the same job counts the item twice. This exposed a contradiction between plan §3.2 and §3.4 E-2.

Rulings:
- **Retry (approved).** E-2 wins (review r2 m-c is later than §3.2's wording).
  - §3.2's "re-reserves on the same task row (`hold_credits += n`, `hold_state → HELD`)" now reads: **"re-reserves under a new SINGLE_ITEM job, settled with key `(task.id, task.retry_count)`; the task row's hold columns and the old job's counts are untouched"**.
  - Service: `open_retry_job(old_job, item, amount)`, the same pattern as `open_late_door_job`.
  - **The link:** 03a's AIJob has no "originated from" column, but has `trace_id`, "shared with AuditEvent rows and every item". The retry job takes the old job's `trace_id`, and `meta` records `{"retry_of": <old job id>, "task_id": <task id>}` (ids only). No column is added; the task row is the key.
- **(2) Counting (approved as a necessary deviation; slice summary).** Settle → `completed_items`; failure or sweep → `failed_items`; done when `completed + failed = total`.
- **(6) Sweep margin (approved), moved into `BillingSettings.hold_sweep_margin_minutes`** (default 15), beside `unstarted_backstop_hours`. Migration `billing/0077_billingsettings_hold_sweep_margin` (additive: a new column with default and `db_default` on an unreleased table).
- **(7) Swept item → PROVIDER_FAILURE (approved)**, provided no ledger row is written. S-3 now asserts zero CreditUsageLog and zero CreditLedger rows.
- **Invariant task** ungated (approved), but it must stay silent with the flag off and no holds. New test.

Slice summary (§10) gains:
- the counting deviation (2)
- the invariant task: always on, read-only, silent when nothing is held

**Changed/new tests (48 in total now):**
- D-2 rewritten as `test_a_retry_runs_under_a_new_single_item_job_with_its_own_key`
- S-3 gains the zero-ledger asserts
- new `test_the_sweep_margin_is_an_editable_setting`
- new `test_the_invariant_task_is_silent_when_nothing_is_held`

**Red for these 4** (at the tests commit, step-1 code only, in a disposable worktree): **Ran 4, errors=4**.

| Test | Fragment |
|---|---|
| D-2 | `ModuleNotFoundError: No module named 'billing.holds'` |
| S-3 | `ModuleNotFoundError: No module named 'billing.holds'` |
| margin | `AttributeError: 'BillingSettings' object has no attribute 'hold_sweep_margin_minutes'` |
| silent | `AttributeError: module 'billing.tasks' has no attribute 'check_credit_hold_invariants'` |

**Green attempt 2** (own log): all 6 step-2 files, **Ran 48, OK**. Then the step-1 tests (Ran 34 OK) and `makemigrations --check` (no changes after 0077).

**Wallet saves** (SM: in scope for step 3; listed in NOTES before any fix). A first read-only grep over production code found every `CreditWallet` `.save(` already passing `update_fields`:
- `billing/services.py` ×6, `license_service.py` ×4, `payment_refunds.py` ×2, `disputes.py` ×3, `stripe_service.py` ×1
- `CreditWallet.consume_credits`' own `self.save(update_fields=[...])`

None of the bare `row.save()` sites is a wallet. Step 3 extends the check to `.update()` and `bulk_update` paths and adds the interleaving tests.

### 11h. Sweep overlap safety (SM condition 2026-11-06 on the 3-min/1-min lock)

**Relied on: overlap safety, not a capped batch.** A sweep run that outlives its lock can overlap the next run.
- Each item is released only after the conditional UPDATE `hold_state HELD → RETURNED WHERE hold_state = 'HELD' AND started_at < cutoff`, inside a transaction holding the wallet row lock (`_sweep_item`). A second run's UPDATE matches 0 rows and returns before touching any amount.
- Each job with no task row is released only through `finish_job`'s conditional status UPDATE (`status IN open`), under the same wallet lock.
- A double release is impossible.

**Test, new:** `billing/tests/test_holds_concurrency.py` `SweepOverlapTests.test_two_overlapping_sweeps_release_a_stale_hold_once`. Two `sweep_stale_holds()` run on a barrier over one stale item and one stale SINGLE_CALL job (5 rounds). Asserted:
- the items swept across both runs total exactly 1, and the jobs released exactly 1
- item `released_credits` and job `failed_items` are as for one release
- wallet `reserved_credits` is 0, never negative
- zero CreditUsageLog/CreditLedger rows
- invariants are clean

**Audit row:** the sweep writes **no AuditEvent** in step 2. B1's four audit actions are not named yet (plan §9 item 12: "B1 adds its four actions itself unless the Feature Lead rules for one shared task"). Flagged to the SM. The test pins "exactly once" on the books, not on an audit row.

**Red** for the new test: at its tests commit there is no committed `billing/holds.py`, so it is expected to give Ran 1, errors=1, `ModuleNotFoundError: No module named 'billing.holds'`. It is run in the same disposable worktree as the break run, but the break run needs the step-2 code; so the red runs at the tests commit, and the break run runs in the main worktree with the old file swapped in.

### 11i. Step 2 results (2026-11-06)

| Run | Code | Result | Matches | Log (docs/evidence/epic-b1a/step2/) |
|---|---|---|---|---|
| Red | 8df4ec54 (tests only) | Ran 46, failures=1, errors=45 | yes (§11e) | red-run.log |
| Green attempt 1 | WIP | Ran 46, errors=1: D-2 IntegrityError `aijob_items_within_total` | STOP, plan contradiction; SM ruling (§11g) | green-attempt1.log |
| Red, 4 changed tests | 7c7d1bc6 | Ran 4, errors=4 | yes (§11g) | red-run-changed-tests.log |
| Green attempt 2 | WIP | Ran 46+2 = 48, OK | yes | green-attempt2.log |
| Targeted check 1 (beat locks/health, zero-credit, h28 alerting, audit sweep lock) | WIP | Ran 113, failures=2: lock lifetime (code fix) and the guarded-task pin (STOP, SM ruling) | STOP | targeted-check-1.log |
| Beat locks after the TTL fix | WIP | Ran 27, failures=1 (the pin only) | as expected | beat-locks-after-ttl-fix.log |
| Red, overlap test | 36e0befa | Ran 1, errors=1, `No module named 'billing.holds'` | yes (§11h) | red-run-overlap-test.log |
| Break run, tests_beat_locks pin at old text (sha in §5c) vs step-2 code | WIP | Ran 1, failures=1, `23 != 21` | yes (§5c) | break-run-beat-locks.log |
| Green | WIP = 2f3c5c45 | Ran 49, OK | yes | green-run.log |
| Targeted check 2 | WIP = 2f3c5c45 | Ran 113, OK | yes | targeted-check-2.log |
| Step-1 tests | WIP = 2f3c5c45 | Ran 34, OK | yes | step1-tests-recheck.log |
| makemigrations --check | 2f3c5c45 | No changes detected | yes | — |
| check_migration_safety --base origin/staging | 2f3c5c45 | 8 migrations, all "additive only" | yes | migration-safety.txt |

The edited test file was restored from its saved copy (sha256 419f3287…), which equals the committed file. The pre-commit hooks left 2f3c5c45 unchanged, so the committed code is the code tested.

Commits:
- 8df4ec54: expectations and tests
- 7c7d1bc6: rulings and the changed tests
- 36e0befa: pin edit, overlap test, §5c/§11h
- 2f3c5c45: step-2 code
- this evidence commit

---

## 12. Step 3: split into 3a / 3b / 3c (SM approved 2026-11-06)

SM rulings for step 3 (2026-11-06):
- **Q1** No-job entry opens a SINGLE_ITEM job, or a job-level holder, with no up-front amount; the pre-call fit check reserves each call's `estimate_messages_cost` before that call. **Carried to B1b: B1b replaces the entry amount with p90.**
- **Q2** `assignments/views.py:878` `upload_assignment` becomes **E-8**: per-item rule, its own E-test.
- **Q3** AICallRecord task ids are repointed to the settlement id inside settle's transaction, on both paths. Tested by the FR-B-09 join.
- **Q4** `settle(school=…)`; AnalyticsService runs at settlement.
- **Q5** students/views.py, assignments/views.py and the on-commit launch in task_tracking.py are this lane's. Test: a rolled-back request launches nothing.
- **Q6** Late-door email: code behind the flag, texts in one constant marked PENDING OWNER APPROVAL, **never sent until the SM confirms**.
- **Q7** AST guard plus interleaving tests; planted bugs at the 3a gate.
- **Q8** Flag-off near run over billing, ai_processor, students and assignments at the end of 3c, compared with the same four apps at base 2f00c078. A billing-only flag-off run at each sub-step checkpoint.
- **Audit:** CREDIT_HOLD_RESERVED / _SETTLED / _RELEASED / _SWEPT, FINANCIAL, target AIJob. Labels are placeholders ("Credits held", "Credit hold settled", "Credit hold released", "Stale credit hold cleared") until the owner confirms. RELEASED only when the amount is > 0.

Sub-steps:
- **3a:** single-call hold; `metered_unit` core; repoint; audit actions; wallet guard
- **3b:** the unit at the five sites; E-1…E-8 entry rule (start claim, late exit, retry, settled exit)
- **3c:** door hold, on-commit launch, late door, email code

## 13. Step 3a: expectations, committed before any run (2026-11-06)

### 13a. Code

**`billing/holds.py`:**
- `holds_enabled()`
- `metered_unit(*, processing_task_id=None, reason=…)`: a ContextVar unit. With the flag off it is `billing_refund_scope`. In 3a it supports no-item units only; items come in 3b.
- `current_unit()`, `Unit.before_call(...)` (opens the SINGLE_ITEM job lazily at the first call, then `ensure_call_fits`) and `Unit.after_call(actual, task_id)` (the tally)
- settle once at exit; `fail_item` on an exception; nested units pass their tally up on success and drop it on failure
- `begin_single_call` / `finish_single_call` / `abandon_single_call` for the no-unit path
- `settle(school=…)`, plus the AICallRecord repoint for the unit's call ids
- audit emits in `reserve`/`ensure_call_fits` (RESERVED), `settle` (SETTLED), `fail_item`/`finish_job` when the amount is > 0 (RELEASED), and the sweep (SWEPT)
- `open_*_job(amount=0)` allowed (no up-front reserve, Q1)

**`ai_processor/services.py` `execute_graded_task`:** an `if holds_enabled():` branch after the estimate.
- **Unit open:** `unit.before_call` → provider → `unit.after_call`.
- **No unit:** `begin_single_call` → provider → settle inside the cancellation-checked transaction, with AnalyticsService as today → `call_record.note_task_id(<settlement id>)` → `record_billing_task_id(<settlement id>)`. On an exception, `abandon_single_call` (release, job FAILED).
- The flag-off branch is unchanged (`:5643–5657` and the charge block).

**`audit`:** the four actions, in `FINANCIAL_ACTIONS`; METADATA_ALLOWLIST entries; ALLOWED_KEYS gains `job_type`, `item_id`, `credits_released`; migration `audit/0005` (choices).

### 13b. Tests

| File | Tests |
|---|---|
| `billing/tests/test_holds_single_call.py` | 7: flag-off control, held+settled+join (SC-2), refused before provider (SC-3), provider failure (SC-4), refund-scope compatibility (SC-5), super admin unmetered (SC-6), AST dominance M-B4 (SC-7) |
| `billing/tests/test_holds_unit.py` | 6: settle once + join, raise → nothing charged, U-1 refusal (1 provider call), nested success, nested inner failure, flag-off = refund scope |
| `billing/tests/test_holds_audit.py` | 5: vocabulary (4 subtests), RESERVED, SETTLED, RELEASED only when > 0, SWEPT |
| `billing/tests/test_wallet_save_guard.py` | 4: AST save guard, AST reserved_credits guard, two interleavings. **Guards for already-correct behaviour; PASS at red; proven by planted bugs at the 3a gate** |
| `billing/tests/test_holds_concurrency.py` | SweepOverlapTests gains "exactly one CREDIT_HOLD_SWEPT row per round" |

Helpers added to `billing/testing/holds.py`: `ai_ready_teacher`, `ai_response`, `estimate_for`, `call_ai`.

### 13c. Red: expected

At the tests commit, code = step 2 (2f3c5c45), in a disposable worktree:

`python manage.py test billing.tests.test_holds_single_call billing.tests.test_holds_unit billing.tests.test_holds_audit billing.tests.test_wallet_save_guard billing.tests.test_holds_concurrency.SweepOverlapTests --settings=settings_worktree --noinput`

**Expected: Ran 23, FAILED (failures=7, errors=16); 7 PASS.**

| Test | Expected | Fragment |
|---|---|---|
| SC flag-off control | PASS | |
| SC-2 held+settled | ERROR | `AIJob.DoesNotExist` |
| SC-3 refused | ERROR | `AIJob.DoesNotExist` (today's gate refuses first; every assert before the job lookup passes) |
| SC-4 provider failure | ERROR | `AIJob.DoesNotExist` |
| SC-5 refund-scope compatibility | PASS | (today's behaviour; guards 3a) |
| SC-6 super admin | PASS | |
| SC-7 dominance | FAIL | ``no `if holds_enabled()` branch found`` |
| unit ×6 | ERROR ×6 | `AttributeError: module 'billing.holds' has no attribute 'metered_unit'` |
| audit vocabulary (4 subtests) | ERROR ×4 | `AttributeError: CREDIT_HOLD_` |
| audit RESERVED | ERROR | `AuditEvent.DoesNotExist` |
| audit SETTLED | ERROR | `AuditEvent.DoesNotExist` |
| audit RELEASED | FAIL | `0 != 1` |
| audit SWEPT | ERROR | `AuditEvent.DoesNotExist` |
| guard ×4 | PASS ×4 | |
| SweepOverlap (5 rounds) | FAIL ×5 | `0 != 1` |

Any other count or fragment is a STOP.

### 13d. Green: expected

- The same command on 3a code: Ran 23, OK.
- All step-2 hold tests again: 49 OK. Step-1 tests: 34 OK. `makemigrations --check`: clean after `audit/0005`.
- Targeted: the audit app (vocabulary/metadata/schema tests), `ai_processor.tests_grading_completeness`, `ai_processor.tests` and AutoGrader beat locks.
- **The billing app with the flag off (default): every existing test passes unchanged** (SM Q8). Compared with the same billing run at base 2f00c078.

### 13e. 3a gate, planted bugs (one per guard, restored by sha256)

| Plant | Expected kill |
|---|---|
| P-1: drop `update_fields` from one production `wallet.save(...)` (billing/services.py, the first `overage_blocks_used` save) | `test_every_wallet_save_names_its_fields` FAIL, listing that line |
| P-2: add `CreditWallet.objects.filter(pk=wallet.pk).update(reserved_credits=0)` in a production billing function | `test_only_the_hold_service_updates_reserved_credits` FAIL |
| P-3 (M-B4): remove the `begin_single_call` line from execute_graded_task | SC-7 FAIL `not preceded by a hold`, and SC-3 (provider called) |
| P-4: unit settles on exception (M-B5) | unit "raise → nothing charged" FAIL |
| P-5: drop the AICallRecord repoint | the join assertions (SC-2, unit settle-once) FAIL |
