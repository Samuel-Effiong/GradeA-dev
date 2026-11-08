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

### 13f. 3a STOP and rulings (2026-11-06), committed before the runs below

**Billing app, flag off:**
- BASE 2f00c078: Ran 2410, OK (log `step3a_billing_flagoff_BASE_2f00c078.log`).
- 3a WIP: Ran 2504, failures=1, errors=1 (log `step3a_billing_flagoff_WIP.log`).
1. **ERROR `column "job_id" … does not exist`** in SweepOverlapTests.
   - Cause: `billing/tests/test_audit_identity_migration.py` rewinds billing to 0058, and its tearDown restored only the billing and users leaves. That un-applied students 0037–0039 (they depend on billing/0076) for every later test in the worker.
   - **SM ruling (1):** `_restore_targets` restores every app's current leaf.
   - New test `test_the_restore_brings_back_every_apps_leaf`.
   - **Break run:** the new test with `_restore_targets` reverted to its old billing+users body (old file sha256 `c73db935…`, saved at `GAP-a0-runs/epic-b1a/old_test_audit_identity_migration.py`). **Expected: Ran 1, 1 FAIL, the list names `students.0039_backgroundprocessingtask_job_index`** (other apps' leaves that depend on billing may also be listed).
   - **Timing:** the old file ran 2 tests in 12.7 s (24.6 s wall). The new file is timed the same way; anything more than a few seconds is reported.
2. **FAIL `test_zero_credit_registry`**: my step-2 Z-1 test registered `b1a_z1_test` process-wide. **SM ruling (2):** use the exempt `_selftest`. **Rule: a test must not register a name in the process-wide zero-credit registry without unregistering it.**

Other 3a facts, for the evidence:
- **`item_id` slip:** in green attempt 1, `item_id` was missing from `audit/metadata.py` ALLOWED_KEYS. I had removed it as a "duplicate", but the line I saw was my own just-added one. Restored; green attempt 2 is OK.
- **No audit migration:** `AuditEvent.action` has no choices, so §13a's `audit/0005` does not exist.
- **Target label:** the scanner `audit/tests_h273_query_filters` needed a label for target_type "AIJob". Production `audit/serializers.py` `_TARGET_LABEL` gains `"AIJob": "Credit hold job"`, a **PLACEHOLDER** on the owner wording list with the four action labels.

After the break run: both billing runs again. Expected: WIP = base 2410 + only the added hold tests, 0 failures. Then the §13e plants.

### 13g. 3a gate plants: exact expectations (committed before any plant runs; corrects §13e)

The code is committed first (8796a8d4). Each plant is applied to a saved copy's file, run, then restored from the copy and checked by sha256.

| Plant | Change | Run | Expected |
|---|---|---|---|
| P-1 | `billing/services.py`: the first `wallet.save(update_fields=["overage_blocks_used"])` → `wallet.save()` | `test_wallet_save_guard.WalletWriteGuardTests.test_every_wallet_save_names_its_fields` | 1 FAIL, offender list names `billing/services.py:<that line> wallet.save()` |
| P-2 | `billing/services.py`: after the first `CreditWallet.objects.filter(pk=wallet.pk).update(…)`, add `CreditWallet.objects.filter(pk=wallet.pk).update(reserved_credits=0)` | `…test_only_the_hold_service_updates_reserved_credits` | 1 FAIL, offender list names `billing/services.py:<line>` |
| P-3 (M-B4) | `ai_processor/services.py`: `credit_holds.begin_single_call(…)` replaced by `credit_holds.create_job(wallet=wallet, requested_by=target_teacher, job_type="SINGLE_CALL")` (no hold) | SC-7 dominance | 1 FAIL `not preceded by a hold`. (SC-3 run alongside and recorded: it is expected to fail too, FAIL or ERROR, since the provider is reached unheld.) |
| P-4 (M-B5) | `billing/holds.py` `metered_unit`: on an exception the root calls `settle_root()` instead of `fail_root()` | `test_holds_unit.UnitTests.test_a_unit_that_raises_charges_nothing_and_returns_its_hold` | 1 FAIL `True is not false` (a CreditUsageLog exists) |
| P-5 | `billing/holds.py` `settle`: the AICallRecord repoint disabled (`if call_task_ids:` → `if False:`) | `test_holds_unit.UnitTests.test_two_calls_in_a_unit_settle_once` | 1 FAIL `0 != 2` |
| P-5b | `ai_processor/services.py`: drop `call_record.note_task_id(billing_task_id)` | SC-2 `test_a_call_holds_settles_and_returns_the_rest` | 1 FAIL on `call.task_id` vs the settlement id |

**Correction to §13e:** P-5 cannot kill SC-2. The single-call path sets the call row's id through `call_record.note_task_id`, not through the repoint, so P-5b is added for it.

Logs: `step3a_plant_P-<n>.log`.

### 13h. Step 3a results (2026-11-06)

| Run | Code | Result | Matches | Log (docs/evidence/epic-b1a/step3a/) |
|---|---|---|---|---|
| Red | 64367061 (tests only) | Ran 23, failures=7, errors=16 | yes (§13c) | red-run.log |
| Green attempt 1 | WIP | Ran 23, errors=1: `KeyError: 'item_id'` (the ALLOWED_KEYS slip, §13f) | build fault, fixed | green-attempt1.log |
| Green attempt 2 | WIP | Ran 23, OK | yes | green-attempt2.log |
| Steps 1+2 recheck | WIP | Ran 83, OK | yes | steps12-recheck.log |
| Targeted 1 (audit app, ai_processor billing-gate tests, beat locks, zero-credit) | WIP | Ran 575, failures=1: target-label scanner (AIJob) | fixed in production code (placeholder label) | targeted-check-1.log |
| Targeted 2 | WIP | Ran 575, OK (skipped=2) | yes | targeted-check-2.log |
| Billing app flag off, BASE | 2f00c078 | Ran 2410, OK | — | billing-flagoff-BASE-2f00c078.log.gz |
| Billing app flag off, WIP 1 | WIP | Ran 2504, failures=1, errors=1 | STOP (§13f), ruled | billing-flagoff-WIP-1.log.gz |
| Migration test timing before (old file, 2 tests) | — | 12.7 s (24.6 s wall) | — | migration-test-timing-before.log |
| Break run: proof test with the old billing+users restore | f7e90e6c | Ran 1, failures=1, `['students.0039_backgroundprocessingtask_job_index'] != []` | yes (§13f) | break-run-migration-restore.log |
| Migration test file after (3 tests) | f7e90e6c | 25.0 s (36.8 s wall) | — | migration-test-timing-after.log |
| Original 2 tests with the new restore | f7e90e6c | 19.6 s, i.e. **+6.9 s (~3.5 s per test)**: each teardown re-applies students 0037–0039, which the rewind really un-applies (not a no-op) | reported | migration-test-timing-after-original-two.log |
| Billing app flag off, WIP 2 | WIP | Ran 2505, OK = 2410 + 95 added (23 step 1 + 49 step 2 + 22 step 3a + 1 proof test) | yes | billing-flagoff-WIP-2.log.gz |
| Green on committed code | 8796a8d4 | Ran 105 hold tests, OK | yes | green-run.log |
| Billing app flag off, committed | 8796a8d4 | Ran 2505, OK | yes | billing-flagoff-8796a8d4.log.gz |
| P-1 save without update_fields | 8796a8d4 + plant | 1 FAIL `['billing/services.py:357 wallet.save()'] != []` | yes | plant-P-1.log |
| P-2 stray reserved_credits update | 8796a8d4 + plant | 1 FAIL `['billing/services.py:1631'] != []` | yes | plant-P-2.log |
| P-3 single call without a hold (M-B4) | 8796a8d4 + plant | SC-7 FAIL `provider call at line 214 is not preceded by a hold`; SC-3 ERROR `TypeError` (provider reached unheld) | yes | plant-P-3.log |
| P-4 unit settles on exception (M-B5) | 8796a8d4 + plant | 1 FAIL `True is not false` | yes | plant-P-4.log |
| P-5 repoint disabled | 8796a8d4 + plant | 1 FAIL `0 != 2` | yes | plant-P-5.log |
| P-5b single-call note_task_id dropped | 8796a8d4 + plant | 1 FAIL (call row id ≠ settlement id) | yes | plant-P-5b.log |

Every plant was restored from its saved copy; sha256 equalled HEAD after each.

Commits:
- 64367061: tests
- f7e90e6c: rulings (migration-test restore, Z-1 `_selftest`)
- 8796a8d4: 3a code (pre-commit mypy/isort fixes before it landed; green re-run on it)
- 04f416b9: plant expectations
- this evidence commit

**Still placeholders (owner wording, SM-held):** the four action labels and the `"AIJob": "Credit hold job"` target label.
The four billing-app logs are gzipped (each about 0.78 MB raw, over the large-file hook's limit), as B0's near-run.log.gz was.

---

## 14. Step 3b: expectations, committed before any run (2026-11-06)

### 14a. Code

- **The five sites:** `ai_processor/services.py` `grade_student_submission`, `students/services.py` ×3 (`_run_grading_pipeline`, `upload_answers_engine`, `update_submission_from_raw_text`) and `assignments/file_uploads.py` `upload_assignment_file`. Each replaces `billing_refund_scope(reason=…)` with `metered_unit(processing_task_id=processing_task_id, reason=…)`. With the flag off that is the refund scope, unchanged.
- **The entry rule** in `metered_unit` (root unit, flag on, a task row found for `processing_task_id`):
  1. row with no job: the first call opens a SINGLE_ITEM job and attaches the row (row = holder, key (task, retry)).
  2. a settlement row exists for `(task.id, task.retry_count)`: raise `CreditHoldReturnedError`.
  3. hold HELD: `claim_item_start`. On a lost claim, re-read and fall through.
  4. hold not held and the row is finished (`FAILURE`/`SUCCESS`/`CANCELLED`, which `update_processing_task` never moves back to STARTED):
     - an UPLOAD job → `open_late_door_job(amount=0)`, a job-level holder keyed (task, retry)
     - otherwise → raise `CreditHoldReturnedError`
  5. hold not held and the row is not finished (a retry, PENDING/STARTED): `open_retry_job(amount=0)`, keyed (task, retry).
- **`CreditHoldReturnedError(ProviderFailureError)`:** PROVIDER_FAILURE, retryable, existing text "No credits were charged.". This is the code the sweep already set, so the late run's failure mark writes the same code. No new wording.
- **A unit finishes only a job it opened** (SINGLE_ITEM, retry or late-door). On a shared batch/door job it settles or fails the row only; that job's terminal transition is step 5.
- `charges_in_open_scope()` (PROVIDER_FAILURE's credit_clause): with the flag on no refund scope is open, so it reads "No credits were charged.", which is true under units.

### 14b. Tests: `billing/tests/test_holds_entry.py` (17)

- **Entry rule** (direct `metered_unit` on real task rows): ER-1 no-job row attached; ER-2 HELD row claimed, shared job stays open; ER-3 late grading exits before any call; ER-4 settled row exits; ER-5 retry → new SINGLE_ITEM job (shared trace_id, old job untouched); ER-6 late door → new UPLOAD job; ER-8 failed unit on a shared job returns the row's hold only.
- **Entry points** (real entry functions; the AI step is a fake making real billed calls through `execute_graded_task`):
  - E-1 `grade_engine` (granted + refused)
  - E-4 `upload_answers_engine` sync (granted)
  - E-5 `update_submission_from_raw_text` sync (granted + refused)
  - E-6 `extract_answer_background_task.apply` (granted)
  - E-8 `upload_assignment_file`: a failure after two billed calls charges nothing
  - E-2 is ER-5's mechanism. **Carried:** E-3's BATCH_GRADING job to step 5; E-7's door to 3c.
- **Wiring** (AST): the five sites call `metered_unit` with `processing_task_id`; none calls `billing_refund_scope`.
- The refusal E-tests pass at red: today's single-call path also refuses before the provider. They are kept as guards; the granted tests discriminate.

### 14c. Red: expected

At the tests commit, code = 3a (8796a8d4), in a disposable worktree:

`python manage.py test billing.tests.test_holds_entry --settings=settings_worktree --noinput`

**Expected: Ran 17, FAILED (failures=12, errors=6); 2 PASS.**

| Test | Expected | Fragment |
|---|---|---|
| ER-1 | FAIL | `unexpectedly None` (row not attached) |
| ER-2 | FAIL | `unexpectedly None` (no start claim) |
| ER-3 | ERROR | `has no attribute 'CreditHoldReturnedError'` |
| ER-4 | ERROR | `has no attribute 'CreditHoldReturnedError'` |
| ER-5 | FAIL | trace_id `UUID(...) != UUID(...)` (the unit's own job, not a retry job) |
| ER-6 | FAIL | `('SINGLE_ITEM', 'SETTLED') != ('UPLOAD', 'SETTLED')` |
| ER-8 | FAIL | `('RUNNING', 0) != ('RUNNING', 1)` |
| E-1 granted | ERROR | `MultipleObjectsReturned` (two single-call settlements) |
| E-1 refused | PASS | |
| E-4 granted | ERROR | `MultipleObjectsReturned` |
| E-5 granted | ERROR | `MultipleObjectsReturned` |
| E-5 refused | PASS | |
| E-6 granted | ERROR | `MultipleObjectsReturned` |
| E-8 | FAIL | `True is not false` (refunded usage rows exist under the refund scope) |
| Wiring metered_unit (3 subtests) | FAIL ×3 | `0 != 1`, `0 != 3`, `0 != 1` |
| Wiring refund scope (3 subtests) | FAIL ×3 | `Lists differ` |

**A mismatch is a STOP.** If it comes from a test fixture (an entry function refusing my fixture for an unrelated reason), I fix the fixture, update this table and re-run red before any code.

### 14d. Green: expected

- `test_holds_entry`: Ran 17, OK.
- All hold tests (105 + 17): OK.
- Billing app flag off: 2505 + 17 = 2522, OK.
- Targeted: `ai_processor` grading/answer tests (`tests_grading_completeness`, `tests_answer_chunk_merge`, `tests_grading_pipeline`, `tests_reading_pipeline`), `students.tests_async_edit_path`, `students.tests_h242_submission_flags`, `assignments.tests_h219_task_error_text`, all flag off.
- Plants at the 3b gate: one per mechanism (entry rule branch 2/4/5, the shared-job finish guard, one site reverted to the refund scope), expectations written before they run.

### 14e. Red attempt 1: a STOP (fixture), corrected before re-running red

Red attempt 1 (log `step3b_red_824e7bcd.log`): **Ran 16, failures=12, errors=7.** It did not match §14c. Causes, both in my tests, not the code:
1. **Count:** the test file has 7 entry-point tests, not 8, so Ran is 16, not 17.
2. **Fixture:** both E-1 tests raised `RubricMissingError`. The classroom fixture's question had no marking guide (`students/grading_gates.py` needs a `rubric` list or a `model_answer`). Fixed: the question gains `"model_answer": "An answer."`.

Every other row matched §14c exactly.

**Corrected red expectation** (same command, at the fixture-fix commit): **Ran 16, FAILED (failures=12, errors=6); 2 PASS.**
- E-1 granted → ERROR `MultipleObjectsReturned`.
- E-1 refused → PASS.
- All other rows as in §14c.

Green: Ran 16 OK (not 17). Billing app flag off: 2505 + 16 = 2521.

### 14f. 3b gate plants: exact expectations (code committed first at c6d05df0; committed before any plant runs)

Each plant is applied to the committed file, run, restored from a saved copy and checked by sha256 against HEAD. The killing tests are in `billing.tests.test_holds_entry`.

| Plant | Change (`billing/holds.py` `Unit.enter` / `settle_root`, or a site) | Run | Expected |
|---|---|---|---|
| Q-1 settled exit removed | `if already_settled(row):` → `if False:` | ER-4 | 1 FAIL `CreditHoldReturnedError not raised` |
| Q-2 late grading exit removed | the `raise _late(f"task {row.pk}: its credit hold was returned")` → run as a retry instead | ER-3 | 1 FAIL `CreditHoldReturnedError not raised` |
| Q-3 retry on the OLD job | the final `self._run_as_successor(open_retry_job(old_job, row, 0), row)` → `self.job, self.item = old_job, row` | ER-5 | 1 ERROR `AIJob.DoesNotExist` (no retry job) |
| Q-4 shared-job finish guard removed | in `settle_root`, `if self.owns_job:` → `if True:` | ER-2 | 1 FAIL `'SETTLED' != 'RUNNING'` |
| Q-5 start claim skipped (M-B31 entry half) | `if claim_item_start(row):` → `if True:` | ER-2 | 1 FAIL `unexpectedly None` (started_at) |
| Q-6 a site without its task id | `students/services.py` grading site: drop `processing_task_id=processing_task_id,` from its `metered_unit(` | E-1 granted + wiring `test_the_five_sites…` | 2 FAIL: E-1 `Tuples differ` (key = the unit's job, not the row); wiring `'processing_task_id' not found` |

Logs: `step3b_plant_Q-<n>.log`.

### 14g. 3b plant results and SM rulings (2026-11-06)

| Plant | Observed (log `step3b_plant_Q-<n>.log`) | Ruling |
|---|---|---|
| Q-1 settled exit removed | killed, but ERROR `TypeError: '<' not supported between MagicMock and int`, not the declared FAIL | **Tighten** ER-4 |
| Q-2 late grading exit removed | killed, the same ERROR `TypeError` | **Tighten** ER-3 |
| Q-3 retry on the old job | killed by ERROR `IntegrityError … aijob_items_within_total`, not the declared `AIJob.DoesNotExist` | **Accepted as the kill.** Corrected fragment: `IntegrityError: … violates check constraint "aijob_items_within_total"`. Why: settling a retry on the old job counts the item a second time; 03a's CHECK is the next guard down, the same one D-2 hit in step 2. |
| Q-4 | FAIL `'SETTLED' != 'RUNNING'` | as declared |
| Q-5 | FAIL `unexpectedly None` | as declared |
| Q-6 | E-1 FAIL `Tuples differ`; wiring FAIL `'processing_task_id' not found in {'reason'}` | as declared |

**Why Q-1/Q-2 died the way they did:** ER-3/ER-4 patched the provider with no return value. With the exit removed, the run went on and crashed on a MagicMock usage in the hold arithmetic. That's a weak catch: it would pass if the arithmetic changed.

**Tightened** (`EntryRuleTests.assert_exits_before_any_call`):
- the provider returns `ai_response(100)`
- `provider.assert_not_called()` comes first, then `CreditHoldReturnedError` raised
- the error class is looked up before the call, so at red (no class) both tests still ERROR `has no attribute 'CreditHoldReturnedError'`

**Re-run expectations (before running):**
- Q-1 → ER-4: 1 FAIL `Expected 'mock' to not have been called. Called 1 times.`
- Q-2 → ER-3: the same 1 FAIL.
- ER-3/ER-4 unplanted on c6d05df0: 2 OK.
- **Red check, unchanged table:** ER-3/ER-4 with 3a's `billing/holds.py` (8796a8d4) checked out in a disposable worktree: 2 ERROR `has no attribute 'CreditHoldReturnedError'`.

### 14h. Step 3b results (2026-11-06)

| Run | Code | Result | Matches | Log (docs/evidence/epic-b1a/step3b/) |
|---|---|---|---|---|
| Red attempt 1 | 824e7bcd | Ran 16, failures=12, errors=7 | no: test count + E-1 fixture (§14e), fixed before re-running | red-attempt1.log |
| Red | dd7687f4 | Ran 16, failures=12, errors=6 | yes (§14e) | red-run.log |
| Green attempt 1 | WIP | Ran 16, OK | yes | green-attempt1.log |
| All hold tests | WIP / c6d05df0 / d203dd50 | Ran 121, OK each time | yes | all-holds-WIP.log, all-holds-c6d05df0.log, all-holds-d203dd50.log |
| Targeted (ai_processor grading/answer/reading pipelines; students async edit, h242 flags; assignments h219) | WIP / c6d05df0 | Ran 140, OK (skipped=1) | yes | targeted-WIP.log, targeted-c6d05df0.log |
| Billing app flag off | WIP / c6d05df0 | Ran 2521, OK = 2505 + 16 | yes | billing-flagoff-WIP.log.gz, billing-flagoff-c6d05df0.log.gz |
| makemigrations --check | c6d05df0 | No changes detected | yes | — |
| Plants Q-1…Q-6 | c6d05df0 + plant | all 6 killed; Q-4/Q-5/Q-6 as declared; Q-1/Q-2/Q-3 different fragments (§14g) | ruled | plant-Q-*.log |
| ER-3/ER-4 tightened, unplanted | d203dd50 | Ran 2, OK | yes | er34-unplanted.log |
| Q-1 re-run (ER-4) | d203dd50 + plant | 1 FAIL `Expected '_AIProcessor__ai_model' to not have been called. Called 1 times.` | yes, in substance. The mock prints its real name where §14g wrote `'mock'`; the fragment `to not have been called. Called 1 times.` matches | plant-Q-1-rerun.log |
| Q-2 re-run (ER-3) | d203dd50 + plant | the same 1 FAIL | yes, as Q-1 | plant-Q-2-rerun.log |
| Red check: ER-3/ER-4 with 3a's holds.py | d203dd50, holds.py at 8796a8d4 | Ran 2, errors=2, `has no attribute 'CreditHoldReturnedError'` | yes: the red table is unchanged | red-check-er34.log |

Every plant was restored from its saved copy; sha256 equalled HEAD after each.

Commits:
- 824e7bcd: tests
- dd7687f4: fixture fix + corrected red
- c6d05df0: 3b code
- 0c8f7d80: plant expectations
- d203dd50: tightened ER-3/ER-4 + §14g
- this evidence commit

**Carried:** E-3's BATCH_GRADING job → step 5; E-7 door → 3c; B1b replaces the entry amount with p90.

---

## 15. Step 3c: expectations, committed before any run (2026-11-06)

### 15a. Code

- **`assignments/upload_door.py`:** the balance the door compares with is `spendable_credits(wallet)` when the flag is on (plan §3.2: every "can this teacher spend?" reads spendable); `total_remaining_credits()` when it is off, unchanged. `parked_files` and `upload_refusal_if_unaffordable` also hand back the per-file estimates they computed, so the view reserves the same number without reading the file twice.
- **`billing/holds.py`:**
  - `open_door_job(*, billed_user, requested_by, total_items, assignment=None)`: an UPLOAD job, RESERVED.
  - `hold_door_item(job, item, estimate)`: attaches the row to the job and reserves the estimate on it. On refusal it detaches the row and returns False. An estimate of None (the door could not read the file) attaches nothing; the task's per-item rule applies.
- **The three routes** (`students/views.py` student upload_async and teacher batch; `assignments/views.py` teacher assignment files), flag on:
  - **Student route:** the hold is taken inside the transaction that creates the row. A refused hold raises inside it, so the row rolls back and the same 402 is returned with the existing student sentence. The launch stays after the transaction, as today.
  - **Batch routes:** one UPLOAD job per request, opened at the first queued file. Each queued file's estimate is held on its row before its launch. A hold that loses a race marks that row FAILED with the INSUFFICIENT_CREDITS refusal (existing message) and skips its launch.
- **On-commit launch (deviation from my Q5 proposal, recorded for the SM):**
  - Every door launch already runs after its transaction, in autocommit (no ATOMIC_REQUESTS). A refused hold rolls back before any launch.
  - The existing door tests patch `students.views.launch_processing_task` / `assignments.views.launch_processing_task` by name, so renaming the views' call to a new function would silently bypass those flag-off tests.
  - So the views keep `launch_processing_task`, and the rolled-back case is pinned by D-1: a refused hold rolls the row back and launches nothing. No unused launch variant is added.
- **Late-door notice:**
  - When a root unit that runs a late door row (`open_late_door_job`) ends in `InsufficientCreditsError`, `notify_late_door_refusal(row)` runs.
  - Texts: one constant, `LATE_DOOR_EMAIL_TEXTS` (subject, individual, license), marked **PENDING OWNER APPROVAL**.
  - Switches: `LATE_DOOR_EMAIL_APPROVED = False` and `LATE_DOOR_STUDENT_NOTICE_APPROVED = False` (code constants, not settings). While off, nothing is sent; the refusal is logged without the file name.
  - Student hook: built to reuse `STUDENT_UPLOAD_NOT_PROCESSED`, off until the owner answers.
  - Sending uses `send_email_task.delay(subject=…, message=…, from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[teacher.email])`.

### 15b. Tests: `billing/tests/test_holds_door.py` (8)

- D-1: refused hold rolls back, launches nothing
- D-2: granted hold on the row, then the task's unit settles on it and the shared job stays open
- D-4: flag off reads the total as before
- D-5: batch holds what fits and parks the rest
- D-6: assignment file held on its row
- D-7: no email while pending
- D-8: once approved, the teacher is emailed naming the file
- D-9: texts are one constant; both switches off

### 15c. Red: expected

At the tests commit, code = 3b (c6d05df0), in a disposable worktree:

`python manage.py test billing.tests.test_holds_door --settings=settings_worktree --noinput`

**Expected: Ran 8, FAILED (failures=1, errors=5); 2 PASS.**

| Test | Expected | Fragment |
|---|---|---|
| D-1 | FAIL | `200 != 402` (the door reads the total, 6000 ≥ 5000) |
| D-2 | ERROR | `AIJob.DoesNotExist` |
| D-4 | PASS | (flag-off guard) |
| D-5 | ERROR | `AIJob.DoesNotExist` |
| D-6 | ERROR | `AIJob.DoesNotExist` |
| D-7 | PASS | (guard: 3b already refuses, no email code exists) |
| D-8 | ERROR | `does not have the attribute 'LATE_DOOR_EMAIL_APPROVED'` |
| D-9 | ERROR | `has no attribute 'LATE_DOOR_EMAIL_TEXTS'` |

A mismatch is a STOP.

### 15d. Green and the end-of-3c near run

- `test_holds_door`: Ran 8, OK.
- All hold tests (121 + 8 = 129): OK.
- The existing door tests unchanged with the flag off: `students.tests_upload_credit_door`, `students.tests_batch_item_results`, `students.tests_h216_student_credit_sentence`, `students.tests_upload_bills_the_teacher_only`, `assignments.tests_edit_refusal_student_sentence`.
- **Four-app near run with the flag off (SM Q8):** `billing ai_processor students assignments`, on base 2f00c078 and on 3c, each with its own log. Expected: 3c = base + only the added B1a tests, 0 failures.
- Plants at the 3c gate (expectations written before they run): the door reads the total instead of spendable; the hold taken outside the transaction (after the row commits); the notice switch ignored.

### 15e. Red attempt 1: a STOP (my test's status code), corrected before re-running red

Red attempt 1 (log `step3c_red_5c76ce7f.log`): **Ran 8, failures=3, errors=3.** It did not match §15c: D-5 and D-6 failed `202 != 200`, because the batch and assignment-file routes answer **202 Accepted** and my tests expected 200. The test was wrong, not the code. Fixed: both assert 202.

Every other row matched.

**Corrected red expectation** (same command, at the fix commit): **Ran 8, FAILED (failures=1, errors=5); 2 PASS**, as in §15c, with D-5/D-6 → ERROR `AIJob.DoesNotExist`.

### 15f. Rulings received during 3c (SM, 2026-11-06)

- **Activity-log labels APPROVED as written:** "Credit hold job" (target AIJob), "Credits held", "Credit hold settled", "Credit hold released", "Stale credit hold cleared". The placeholder notes in `audit/enums.py` and `audit/serializers.py` are dropped. This supersedes the "Still placeholders" line in §13.
- **Late-door email NOT approved.** The owner wants the uploaded file kept temporarily, so the teacher resumes instead of re-uploading. That reopens F4/§0.9 and may change the 2026-10-30 late-door ruling. For 3c, the refusal path stays as built: refused before any provider call, FAILED/INSUFFICIENT_CREDITS, behind the flag, never sent. The texts stay a placeholder, and no test asserts "upload the file again".
- **The §15a on-commit deviation is NOT accepted.** The three door routes get a literal `transaction.on_commit`, flag on only. It goes in as a 3c amendment (§15g), with expectations written before it runs. The response `task_id` question is open with the SM.

### 15g. 3c amendment: door launches on commit (SM rulings 2026-11-06), expectations committed before any run

Replaces the §15a "On-commit launch (deviation…)" entry, which the SM did not accept and which is withdrawn. Plan M5 / §3.7: every door launch is `transaction.on_commit`. That must hold whatever transaction a future caller opens (ATOMIC_REQUESTS, a wrapping service, a test). It does not rest on today's autocommit.

**Code**

- **`assignments/upload_door.py` `launch_at_door(launch, task_callable, row, *args, **kwargs)`** returns the task id the response carries.
  - **Flag off:** `launch(task_callable, row, *args, **kwargs).id`. This is today's direct call, unchanged.
  - **Flag on:** generate a uuid4. Write it onto the row now (`attach_celery_task`). Register `transaction.on_commit(partial(launch, task_callable, row, *args, _celery_task_id=uuid, **kwargs))`, then return the uuid.
  - The views pass their own module's `launch_processing_task` as `launch`, so the existing tests that patch it by name still apply. `partial` binds each batch file's arguments per iteration, with no closure over loop variables.
- **Ordering (SM condition 1):** the row is created first, then carries the pre-assigned `celery_task_id`, and the message is sent only at commit.
  - The row id and its Celery id are in the same transaction as the hold (the student route) or committed before it (the batch routes, in autocommit).
  - No worker or poll can see a message without its row. Under ATOMIC_REQUESTS the response itself only leaves after commit.
- **`students/task_tracking.py` `launch_processing_task(task_callable, row, *args, _celery_task_id=None, **kwargs)`:**
  - `_celery_task_id` is keyword-only and is never put into the task's kwargs. When it is given, the dispatch is `task_callable.apply_async(args=args, kwargs=kwargs, task_id=_celery_task_id)`.
  - Without it, the dispatch is today's `task_callable.delay(*args, **kwargs)`, byte-identical. Only flag-on door callers pass it.
- **Broker failure (SM condition 3), chosen: release on the error path.**
  - The 36-hour unstarted backstop is step 5 and does not exist yet. The sweep never touches unstarted items (§11).
  - So when the `_celery_task_id` dispatch raises, `launch_processing_task` marks the row FAILURE (as today), returns that row's door hold with `fail_item`, then raises as today.
  - This also closes a leak in the committed 3c code: in autocommit a broker outage left the hold HELD.
  - The flag-off path does not change, because the release is only on the `_celery_task_id` path.
- **Default `on_commit` (robust=False)**, so a broker failure propagates as today: in autocommit at once, under an outer atomic at commit.
  - **Recorded limit:** under an outer atomic, Django stops running the remaining callbacks after the first one that raises. Later files in the same batch keep their rows HELD and unstarted.
  - Those holds are recovered by step 5's 36-hour backstop, which covers unstarted items of RESERVED UPLOAD jobs (plan §3.7). Until step 5 lands, the flag stays off in production, as it does for all of B1a.
  - This goes on the §10 slice-summary list.

**Tests: new file `billing/tests/test_holds_door_commit.py` (8)**

| # | Test | Red (wrapper not applied: committed 093e0f24 code) | Green |
|---|---|---|---|
| C-1 | P-3/M-B20: student upload inside the test transaction. No launch before commit; exactly one callback; running it launches once | FAIL (launched before commit) | PASS |
| C-2 | The launch gets `_celery_task_id`; response `task_id` equals it and equals the row's `celery_task_id` | FAIL | PASS |
| C-3 | Batch (2 files that both fit): 2 callbacks; each launch's `_celery_task_id` equals that file's response task_id; the bound args are each file's own | FAIL | PASS |
| C-4 | Assignment-file route: launch on commit with the pre-assigned id = response task_id | FAIL | PASS |
| C-5 | A refused student hold queues no callback at all | PASS (nothing is launched either way) | PASS |
| C-6 | `launch_processing_task` with `_celery_task_id`: `apply_async(task_id=…)`; the key is not in the task kwargs; `delay` not called | FAIL | PASS |
| C-7 | `launch_processing_task` without it: `delay(*args, **kwargs)` exactly as today; `apply_async` not called | PASS | PASS |
| C-8 | Broker error on the `_celery_task_id` dispatch: raises ProcessingTemporarilyUnavailable; row FAILURE and RETURNED; wallet reserved 0 | FAIL (hold left HELD) | PASS |

Red expected: **Ran 8, 2 pass (C-5, C-7), 6 fail or error.** The red is the committed code without the wrapper, which is the SM's red check ("revert the wrapper").

**My own 3c tests D-2, D-5, D-6** (step 3c, not yet accepted; not pre-existing) assert the launch inside the test transaction. Each request gets `captureOnCommitCallbacks(execute=True)`, with no other change. On the red code they pass either way, because execute=True only adds the run of the callbacks.

**Flag-off door tests: untouched**, and they must stay green (39 OK).

**Green (WIP):** test_holds_door_commit 8 OK; test_holds_door 8 OK; all holds tests OK (129 + 8 = 137); the flag-off door tests 39 OK.

**Then:** the four-app flag-off near run on WIP. Expected: base 5654 plus the added B1a tests only, with 0 failures. Then plants (§15h, written before they run).

**§15g results**

- **Red** at d0f983b0, in a disposable detached worktree (`step3c_amend_red_d0f983b0.log`): Ran 16, FAILED (failures=5, errors=1).
  - C-1, C-3, C-4 fail as declared: launched before commit.
  - C-2 is an ERROR, `KeyError: '_celery_task_id'` (the launch got no pre-assigned id).
  - C-6 fails: `delay` called.
  - C-8 fails: `('FAILURE', 'HELD')`.
  - C-5, C-7 and the 8 D-tests pass.
  - This matches §15g: 6 fail or error, C-5 and C-7 pass.
- **Green (WIP):**
  - All holds, as previously defined: Ran 137, OK (`step3c_amend_green_allholds_WIP.log`).
  - The flag-off door tests, untouched: Ran 39, OK (`step3c_amend_door_tests_flagoff_WIP.log`).
  - A first holds run, `step3c_amend_green_holds_WIP.log` (Ran 122, OK), used only the `billing/tests/test_holds*.py` glob. It left out `test_wallet_save_guard`, `audit.tests_financial_retention` and `SweepSelfRecordTests`, which the "all holds" set has carried since 3a. That was a command slip, not a result mismatch. It was re-run with the declared set.
- **Code commit 327ce23c:** isort reordered one import name in `students/views.py` on the first commit attempt, so it was re-staged. That change is import order only.
- **Four-app flag-off near run on 327ce23c:** expected **Ran 5781 = base 5654 + 127** B1a billing tests (no test added or removed in ai_processor, students or assignments since 2f00c078), OK (skipped=20). Log `step3c_near4_flagoff_327ce23c.log`.

### 15h. 3c plants: expectations, committed before any plant runs

**One test added first: D-10** in `billing/tests/test_holds_door.py`. The planned plant "the door reads the total instead of spendable" would survive every existing test. D-1 still gets its 402 because the hold itself refuses.

- **Setup:** a teacher batch of 2 files at 5,000 each, a wallet of 12,000, and another job holding 3,000 (spendable 9,000).
- **Assertion:** the door parks one file, so the UPLOAD job has `total_items == 1` and exactly one HELD row.
- **Under the plant:** both files pass the door, and the job has `total_items == 2`.
- D-10 has no red on this code, because the guard already exists. Its red is DP-1 below.

Each plant is applied to committed code, run against the listed tests, and restored by sha256 from a saved copy. One log per plant: `step3c_plant_DP-n.log`.

| Plant | Mutation | Killed by (expected) |
|---|---|---|
| DP-1 | `upload_door._balance`: always `wallet.total_remaining_credits()` | D-10 |
| DP-2 | Student route: the hold is taken after the `atomic()` block, not inside it | D-1 (the row survives the refusal) |
| DP-3 | `notify_late_door_refusal` ignores `LATE_DOOR_EMAIL_APPROVED` | D-7 |
| DP-4 | `launch_at_door`, flag on: calls `launch(..., _celery_task_id=…)` directly instead of `transaction.on_commit` | C-1, C-3, C-4 |
| DP-5 | `launch_processing_task`: `_celery_task_id` is also put into the task kwargs | C-6 |
| DP-6 | `launch_processing_task`: `_return_door_hold` is not called on a failure | C-8 |
| DP-7 | `launch_at_door`: the pre-assigned id is not attached to the row before commit | C-2, C-3 |

Test set for each plant: `billing.tests.test_holds_door billing.tests.test_holds_door_commit` (17 with D-10). A plant counts as killed when the named tests fail. Any other test failing, or a named test passing, is a STOP to the SM.

**Ruling received during 3c (SM, owner F4, 2026-11-06), added after §15h:**
- Uploaded files are to be kept in private Cloudinary storage for 30 days, and resume or retry uses the stored file. After 30 days the file is deleted and the teacher re-uploads. This reverses the §0.9 "no persistence" default and the 2026-10-30 "late door file is lost" outcome.
- **No 3c change:** the late-door refusal stays exactly as built, and the email stays a placeholder behind the flag. **No file storage in B1a.**
- Storage is B1b scope, with a plan amendment by the SM before B1b starts. Any changed user wording goes to the owner first.
- On the §10 slice-summary list.

### 15g addendum: a failing door callback under an outer atomic (SM rulings 2026-11-06), expectations before any run

This replaces the "Recorded limit" bullet in §15g, where one raising callback stopped the later ones.

**Why not `robust=True`** (checked on Django 5.2.17 / Python 3.12; the SM approved the guard instead):
1. In autocommit, robust=True swallows the exception, so the view would stop surfacing `ProcessingTemporarilyUnavailable` (503).
2. Django's handler formats `func.__qualname__`, which a `functools.partial` does not have. The handler raises AttributeError from inside its own `except`, and the later callbacks still do not run.
3. It logs the message with `exc_info=True`, which puts the chained broker error text and the traceback in the log. That breaks H-208.

**Code (`assignments/upload_door.py`):**
- **Only when `connection.in_atomic_block`**, `launch_at_door` registers a named guard (`_DoorLaunchOnCommit`) with default robust=False. The guard calls the launch.
- On any `Exception` the guard:
  - re-reads the row and, if it is still HELD with a job, calls `fail_item`. It does not assume `launch_processing_task` already accounted for it, since the error may come from before that point. `fail_item` is idempotent on hold_state.
  - logs one line, `Door launch for task %s failed on commit: %s`, with the row id and `describe_error_for_log(exc)`: the class only, with no message or traceback.
  - returns, so the later callbacks still run.
- **Autocommit** stays exactly as 327ce23c: the plain partial runs at once and raises to the view.

**Tests, added to `billing/tests/test_holds_door_commit.py`:**

| # | Test | Red (327ce23c) | Green |
|---|---|---|---|
| C-9 | Outer atomic (TestCase); batch of 2 files; the first file's real dispatch raises ConnectionError, the second launches. File 1 is FAILURE/RETURNED, file 2 HELD; wallet reserved = one estimate; the guard's log line carries no broker text | ERROR (ProcessingTemporarilyUnavailable escapes the first callback; the second never runs) | PASS |
| C-10 | Autocommit (TransactionTestCase); student upload whose dispatch raises ConnectionError: the response is 503, and the row is FAILURE/RETURNED | PASS (327ce23c already does this; it pins that the guard leaves autocommit alone) | PASS |
| C-11 | Outer atomic; student upload where the patched launch raises RuntimeError before any accounting: no exception escapes the commit, the row is RETURNED, wallet reserved 0 | ERROR (RuntimeError escapes) | PASS |

**Also added: D-10** (§15h) in `billing/tests/test_holds_door.py`. Red: PASS. Its red is plant DP-1.

**Red expected**, at the commit of these tests (code 327ce23c), for `test_holds_door` + `test_holds_door_commit`: **Ran 20, FAILED (errors=2): C-9, C-11.** All 18 others pass.

**Green expected:** the same 20 OK; all holds (declared set) 137 + 4 = 141 OK; flag-off door tests 39 OK.

**Near run:** the four-app flag-off near run on 327ce23c (expected 5781) stands for the amendment. The four-app near run is repeated once at the end of 3c, after the guard and the plants. Expected: **Ran 5785 = 5654 + 131** (127 + C-9, C-10, C-11, D-10), OK (skipped=20).

**Plants (§15h) gain one: DP-8.** The guard does not call `fail_item` (it relies on `launch_processing_task`). Expected kill: C-11. The test set for every plant becomes the 20 tests above.

(§15h plants are named DP-1…DP-8, so they do not collide with the 3a plants P-1…P-5b.)

## 15i. 3c finding: the 3a code broke three static guards; fix the code, not the guards (SM approved 2026-11-06). Expectations committed before any run

**Finding.** The four-app flag-off near run on 327ce23c (`step3c_near4_flagoff_327ce23c.log`) gave Ran 5781, FAILED (failures=3, skipped=67). The count matched; the result did not. STOP, reported to the SM.
1. `ai_processor.tests_metering_chokepoint` `test___ai_model_is_called_only_from_execute_graded_task_twice`: 4 sites, not 2. The 3a flag-on branch copied the provider call, once for the unit path and once for the single call.
2. `ai_processor.tests_call_record_static` `test_the_hooks_are_five_lines_in_all`: 10 != 5, because the copies also copied the call_record notes.
3. The same module's `test_no_other_code_names_the_model`: `billing/holds.py` named AICallRecord (the 3a repoint in `settle`).

These came from 3a and were missed in 3a and 3b: those targeted sets left both modules out. **Process rule (SM condition 4), from now on:** every targeted set includes `ai_processor.tests_metering_chokepoint`, `ai_processor.tests_call_record_static`, and any other `*_static` / `*chokepoint*` module in an app I touch. Today that is also `students.tests_reading_label_static` and `AutoGrader.tests_h271_static_files` (found with `find … -name '*static*' -o -name '*chokepoint*'`).

**Skips: the new environment baseline is 67** (SM). Playwright's Chromium no longer launches anywhere on this machine. `assignments.tests_pdf_renderer` skips 34 of 59 in both the base worktree and epic-b1a. Playwright is not touched.

### 15i-1. The fix

**(a) One provider call and one set of notes in `execute_graded_task`.** The guards stay unedited. `tests_call_record_static` pins every note as a top-level statement beside its line, so the flow stays flat:

    call_record.note_estimate(estimated_cost)
    if not credit_holds.holds_enabled():
        <today's two balance checks, unchanged>
    ensure_task_not_cancelled(processing_task_id)
    hold = credit_holds.hold_for_call(wallet=…, target_teacher=…, estimate=estimated_cost)   # None with the flag off
    task_id = str(uuid.uuid4()) if hold is None else hold.usage_id
    call_record.note_task_id(task_id)
    try:
        response = self.__ai_model(…)                       # the one metered call site
        note_if_reply_cut_off(response, task_type)
    except BaseException:
        credit_holds.abandon_call(hold)                     # no-op for None or a unit
        raise
    call_record.note_response(response)
    resolved_course = …
    if hold is not None:
        hold.finish(actual, feature=…, task_type=…, course=…, school=…, before_charge=<the cancellation check>)
        return response
    <today's charge block, unchanged>

- **Flag off:** the same statements in the same order. The differences are (i) the two balance checks are indented under `if not holds_enabled()`; (ii) the `task_id` line gains `if hold is None else hold.usage_id`, because the guard allows exactly one top-level `task_id` assignment; and (iii) the provider call sits in a `try` whose `except` re-raises after a no-op.
  - Evidence: `git diff -w` of `execute_graded_task` against 2f00c078, saved as `execute_graded_task_flagoff.diff`. It must show only those three points plus the added flag-on lines.
- **Flag on, order:** the fit check (unit `before_call` or `begin_single_call`) now runs just after the cancellation check, so a cancelled task never takes a hold. The single call's hold is returned on any exception from the provider call through to the charge.
- **`billing/holds.py`:** `hold_for_call` returns a unit hold (`before_call`, then `after_call` at finish) or a single-call hold (`begin_single_call`; finish = the cancellation check, `finish_single_call` and the analytics in one atomic block; on failure `abandon_single_call`; then `record_billing_task_id(usage_id)` as in 3a). `abandon_call(hold)` is the no-op-safe abandon.

**(b) No repoint: the usage id is fixed before the call** (SM: accepted).
- **The FENCE KEY is unchanged:** (item_id, retry_count). That is the task row id where a row exists, and (job.id, 0) on sync E-4/E-5/SINGLE_CALL. It is computed in `settle()` exactly as now, with the same IntegrityError → NOT_SETTLED, and a retry still gets its own successor job and key.
- **What is fixed early is only the USAGE ID,** `SettlementRecord.id`, the task_id on the usage rows. A root `Unit` picks a uuid4 when it is created; a single call picks one at `hold_for_call`. Every call notes it once through the existing `note_task_id`, and `settle(record_id=…)` creates the SettlementRecord with that id.
- `settle()` loses `call_task_ids` and its AICallRecord update; `Unit.after_call` loses its `task_id` argument; `call_task_ids` is gone. **`billing/holds.py` no longer names AICallRecord.**
- **Refused duplicate settle** (accepted as today): that run's call rows carry a uuid with no SettlementRecord, and no credits move. Today they keep per-call uuids that join nothing.
- **Nested drop** (recorded, SM framing): a nested unit's calls carry the ROOT's usage id. When a nested unit fails and is dropped while the root succeeds, the root settlement's call rows include the provider cost of those dropped calls. That is true provider spend the company absorbed, so the provider-cost view stays accurate. **Credits come only from the usage rows (the root tally):** a dropped nested call adds 0 credits.

### 15i-2. Tests

| # | Where | What | Red (327ce23c code) | Green |
|---|---|---|---|---|
| G-1 | `ai_processor.tests_metering_chokepoint` + `ai_processor.tests_call_record_static`, unedited | the three guards | Ran 34, failures=3 (the finding) | Ran 34 OK |
| SC-7 (rewritten, my 3a test) | `test_holds_single_call.DominanceTests` | in `execute_graded_task`'s top-level body, exactly one statement after `hold = credit_holds.hold_for_call(…)` contains the provider call, and none between the cancellation check and the hold | FAIL (no `hold = …hold_for_call` statement) | PASS |
| SC-7 break run | the OLD SC-7 text against the NEW code | shows the old test no longer describes the code | n/a | FAIL `no \`if holds_enabled()\` branch found` (expected) |
| N-1 (new) | `test_holds_unit` | root call (100 tokens), nested unit's call (200) then the nested unit raises and is caught, root succeeds. Both AICallRecord rows carry the root's settlement id; the usage rows' sum for that id = 100 = the root tally; the dropped call adds 0 credits | FAIL (the dropped call's row keeps its own uuid; count 1 != 2) | PASS |
| D-1 (+1 line, SM-approved edit) | `test_holds_settlement` | second settle passes `record_id=u2`: no SettlementRecord with pk u2, and no usage row for u2 | ERROR (`settle()` takes no `record_id`) | PASS |

Unchanged and still meaningful under (b):
- the unit settle-once join (`test_two_calls_in_a_unit_settle_once`: both call rows reach the usage rows);
- SC-2 (`test_a_call_holds_settles_and_returns_the_rest`: the call row's task_id = the settlement id).

**Red expected** at the tests commit (code 327ce23c), for G-1 + `test_holds_single_call` + `test_holds_unit` + `test_holds_settlement`:
- **G-1:** 3 FAIL;
- **SC-7:** 1 FAIL;
- **N-1:** 1 FAIL;
- **D-1:** 1 ERROR;
- **everything else in those modules:** PASS.

**Green expected (WIP):**
- G-1: 34 OK;
- the three hold modules: OK;
- all holds (declared set): 137 + 1 (N-1) = 138 OK;
- the static/chokepoint set (G-1 + `students.tests_reading_label_static` + `AutoGrader.tests_h271_static_files`): OK;
- flag-off evidence (SM condition 1), each its own log:
  - the 3a/3b targeted ai_processor sets, run as in §13/§14 (ai_processor grading/answer/reading pipelines, students async edit, h242 flags, assignments h219; and the audit app, ai_processor billing-gate tests, beat locks, zero-credit);
  - the billing app (flag off; expected 2410 base + B1a billing tests, OK);
  - and `execute_graded_task_flagoff.diff`.

### 15i-3. Plants after the refactor (SM condition 3)

Every 3a and 3b plant is re-run, each re-pointed at the same logic in its new place, plus two new plants. Each runs against the 3a/3b set it was declared against, plus G-1.

| Plant | Mutation after the refactor | Killed by (expected) |
|---|---|---|
| P-1, P-2 | as in §13 (billing/services.py) | as in §13 |
| P-3 (M-B4) | `hold_for_call`, single path: `begin_single_call(…)` → `create_job(…)` with no reserve | `test_a_call_that_cannot_be_held_is_refused_before_the_provider` (the provider is reached) |
| P-4 (M-B5) | as in §13 (unit settles on exception) | as in §13 |
| P-5 | `settle()` ignores `record_id` (the SettlementRecord gets a fresh uuid) | `test_two_calls_in_a_unit_settle_once` (join 0 != 2), SC-2 |
| P-5b | `execute_graded_task`: `task_id = str(uuid.uuid4())` even with a hold | SC-2 (call row id ≠ settlement id) |
| Q-1…Q-6 | as in §14 | as in §14 |
| G-P1 (new) | a second copy of the provider call inside `if hold is not None:` | the chokepoint guard (3 sites) |
| G-P2 (new) | `billing/holds.py` gains `from ai_processor.models import AICallRecord  # noqa` | `test_no_other_code_names_the_model` |

**Then**, in the agreed order: the §15g addendum (C-9…C-11 and the guard), D-10, plants DP-1…DP-8, and the four-app near run. Expected: **Ran 5786 = 5654 + 132** (127 + C-9, C-10, C-11, D-10, N-1), OK (skipped=67 or as recounted).

### 15i-4. §10 slice-summary additions

- **B1b item (SM):** actual-cost reporting reads CREDITS only from the usage rows (`CreditUsageLog` by task_id) and PROVIDER COST from `AICallRecord`. It tells failed calls apart by the record's own `AICallRecord.outcome` field (`CallOutcome`), and it must never derive credits from AICallRecord.
- The nested-drop nuance (15i-1).
- The guard-coverage process rule (above).

**Skip baseline restored (SM, 2026-11-06).** Headless Chromium was reinstalled with owner approval: the Ubuntu 24.04 build, because Playwright does not list 26.04. `assignments.tests_pdf_renderer` now runs 59/59 with 0 skips. The four-app near run is expected at **skipped=20** again. Any browser skip is reported to the SM.

### 15i-5. §15i results (2026-11-06)

| Run | Code | Result | Matches? | Log |
|---|---|---|---|---|
| Red: G-1 + test_holds_single_call + test_holds_unit + test_holds_settlement | fec07eec (code = 327ce23c) | Ran 53, FAILED (failures=5, errors=1): the 3 guards; SC-7 `no \`hold = ...hold_for_call(...)\` line`; N-1 `1 != 2`; D-1 `TypeError: settle() got an unexpected keyword argument 'record_id'` | yes | step3c_15i_red_fec07eec.log |
| Green, the same set | WIP | Ran 53, OK | yes | step3c_15i_green_WIP.log |
| All holds (declared set) | WIP | Ran 138, OK | yes | step3c_15i_allholds_WIP.log |
| Static/chokepoint set (G-1 + students.tests_reading_label_static + AutoGrader.tests_h271_static_files) | WIP | Ran 58, OK | yes | step3c_15i_static_WIP.log |
| Flag off: the 3a targeted set | WIP | Ran 575, OK (skipped=2), as in 3a | yes | step3c_15i_flagoff_3aset_WIP.log |
| Flag off: the 3b targeted set | WIP | Ran 140, OK (skipped=1), as in 3b | yes | step3c_15i_flagoff_3bset_WIP.log |
| Flag off: billing app | WIP | Ran 2538 (= 2410 + 128), OK | yes | step3c_15i_flagoff_billing_WIP.log |
| `git diff -w` of execute_graded_task vs 2f00c078 | WIP | only the three declared points plus the flag-on lines | yes | step3c/execute_graded_task_flagoff.diff |
| Code commit | 3abd7628 | black wrapped one line in holds.py on the first attempt; re-staged (format only) | — | — |
| Unplanted baseline: all holds + G-1 | 3abd7628 | Ran 172, OK | yes | step3c_15i_unplanted_3abd7628.log |

**Plants on 3abd7628.** Runner `plants15i.py`: each plant was applied to the committed file, run with its declared tests plus G-1, restored from a saved copy, and sha256-checked against HEAD. All were restored.

| Plant | Result | Matches? |
|---|---|---|
| P-1 | 1 FAIL `['billing/services.py:357 wallet.save()'] != []` | yes |
| P-2 | 1 FAIL `['billing/services.py:1634'] != []` | yes |
| P-3 | SC-3 ERROR `TypeError: '<' not supported between MagicMock and int`: the provider was reached unheld. §13g allowed FAIL or ERROR | yes |
| P-4 | 1 FAIL `True is not false` | yes |
| P-5 | 2 FAIL: unit join `0 != 2`; SC-2 call id ≠ settlement id | yes |
| P-5b | 1 FAIL SC-2 call id ≠ settlement id | yes |
| Q-1 | ER-4 FAIL `Expected '_AIProcessor__ai_model' to not have been called` (the tightened form, §14) | yes |
| Q-2 | ER-3 FAIL, the same | yes |
| Q-3 | ER-5 ERROR `IntegrityError … aijob_items_within_total` (the kill accepted in 3b) | yes |
| Q-4 | ER-2 FAIL `'SETTLED' != 'RUNNING'` | yes |
| Q-5 | ER-2 FAIL `unexpectedly None` | yes |
| Q-6 | 2 FAIL: E-1 `Tuples differ`; wiring `'processing_task_id' not found in {'reason'}` | yes |
| G-P1 | chokepoint FAIL (3 sites) | yes |
| G-P2 | `test_no_other_code_names_the_model` FAIL `['billing/holds.py']` | yes |

In every plant G-1 otherwise stayed green. The SC-7 break run (the old SC-7 text against the new code) runs next, then the §15g addendum.

| SC-7 break run (old SC-7 text vs 3abd7628) | Declared: FAIL `no \`if holds_enabled()\` branch found`. Actual: Ran 1, FAILED (failures=1) `0 not greater than or equal to 2 : expected the unit and the single-call calls` | **matched in substance, fragment mispredicted** (SM accepted). The old walk finds the new `if not holds_enabled():` branch around the flag-off balance checks, and no provider call inside it, which is what the refactor intends. | step3c_15i_sc7_break_run.log |

### 15g addendum, amended before any run: the guard also finishes the row

The C-11 case is a non-broker error raised before `launch_processing_task` reaches its own handling. There the guard's `fail_item` alone would return the hold but leave the row unfinished, with no message ever sent, so it would never resolve. **So the guard also marks the row FAILURE** (`mark_processing_task_failure(row id, exc)`, the existing failure path and text) when the row is not yet finished. It then returns the hold if it is still HELD, logs, and returns. C-11 asserts `("FAILURE", "RETURNED")`.

Tests as committed with this addendum: C-9 and C-11 (`FailingCallbackUnderAnOuterAtomicTests`), C-10 (`FailingLaunchInAutocommitTests`, a TransactionTestCase), and D-10 (`TeacherDoorTests`). The red set is `billing.tests.test_holds_door billing.tests.test_holds_door_commit` plus G-1 (SM process rule): **Ran 54 (20 + 34), FAILED (errors=2): C-9, C-11.** C-10 and D-10 pass.

### 15h, updated before any DP plant runs: the kill sets with C-9…C-11 and D-10 in place (code cf82437d)

Each plant runs `billing.tests.test_holds_door billing.tests.test_holds_door_commit` (20 tests) plus G-1 (34). Exactly the listed tests are expected to fail; anything else, or a listed test passing, is a STOP.

| Plant | Mutation | Expected kills |
|---|---|---|
| DP-1 | `_balance` always returns `wallet.total_remaining_credits()` | D-10 |
| DP-2 | student route: the hold is taken after the `atomic()` block, and a refusal returns the 402 there | D-1 |
| DP-3 | `notify_late_door_refusal` ignores `LATE_DOOR_EMAIL_APPROVED` | D-7 |
| DP-4 | `launch_at_door`, under an atomic: `send()` directly instead of `transaction.on_commit(_DoorLaunchOnCommit(…))` | C-1, C-3, C-4 (sent before commit); C-9 (the view answers 503 at the first file); C-11 (the RuntimeError reaches the view) |
| DP-5 | `launch_processing_task`: `_celery_task_id` also goes into the task kwargs | C-6 |
| DP-6 | `launch_processing_task`: neither branch calls `_return_door_hold` | C-8; C-10 (autocommit: no guard, so the hold stays HELD). C-9 still passes because its guard returns the hold. |
| DP-7 | `launch_at_door`: no `attach_celery_task` before commit | C-2, C-3 |
| DP-8 | `_DoorLaunchOnCommit`: no `fail_item` | C-11 |

### 15h results: DP plants on cf82437d (expectations 1a384334)

Every file was restored, sha256-checked against HEAD. G-1 stayed green in every plant.

| Plant | Result | Matches? | Log |
|---|---|---|---|
| DP-1 | D-10 FAIL `2 != 1` (total_items) | yes | step3c_plant_DP-1.log |
| DP-2 | **SURVIVED** (Ran 54, OK) | **no: STOP**, reported to the SM | step3c_plant_DP-2.log |
| DP-3 | D-7 FAIL `Expected 'delay' to not have been called` | yes | step3c_plant_DP-3.log |
| DP-4 | C-1, C-3, C-4 FAIL (launched before commit); C-9 FAIL; C-11 FAIL (no guard log, the RuntimeError reached the view) | yes | step3c_plant_DP-4.log |
| DP-5 | C-6 FAIL `expected call not found` | yes | step3c_plant_DP-5.log |
| DP-6 | C-8 and C-10 FAIL `('FAILURE', 'HELD') != ('FAILURE', 'RETURNED')` | yes | step3c_plant_DP-6.log |
| DP-7 | C-2 and C-3 FAIL `None != '<uuid>'` (no id on the row) | yes | step3c_plant_DP-7.log |
| DP-8 | C-11 FAIL `('FAILURE', 'HELD') != ('FAILURE', 'RETURNED')` | yes | step3c_plant_DP-8.log |

**Why DP-2 survived: a test gap.** D-1's setup is refused at the door's own balance check (`upload_refusal_if_unaffordable`, which reads spendable) before the atomic block creates the row. So D-1 never reaches the hold-refusal path. That path is reached only in a race, when the door's check passes and the hold then refuses, and no test covered it. D-1 in effect pins "the door reads spendable", as D-10 does for batches. Proposed to the SM: D-11, a student-door race test, with DP-2 as its red.

### 15h-2. The door race: D-11, D-12, DP-9 (SM approved 2026-11-06), expectations before any run

**D-1 re-described (SM):** D-1 pins **"the student door reads spendable"**. It is refused at the door's own balance check before any row exists. It does **not** cover the hold-refusal rollback; D-11 does.

**D-11 (student door race)**, `StudentDoorTests`:
- **Setup:** wallet 6,000; another job holds 2,000; estimate 5,000. `assignments.upload_door._balance` is patched to a large number, so the door's check passes, as when another request takes the balance in between. The real `reserve` refuses.
- **Expected, current code:**
  - 402 with `{"error": STUDENT_UPLOAD_NOT_PROCESSED, "code": "insufficient_credits"}`;
  - no BackgroundProcessingTask row;
  - no UPLOAD job;
  - no on-commit callback;
  - launch not called.
  - Result: PASS.
- **Under DP-2** (the hold taken after the atomic block): FAIL `True is not false` on the row check.

**The batch door (SM addition 1): it has the same check-then-hold window, but not inside a transaction.** The two batch routes run no atomic block: each row is committed on its own, and a hold that loses the race marks that file FAILED with the existing INSUFFICIENT_CREDITS refusal and skips its launch (§15a). So its plant is not "outside the atomic" but **"the refused file is launched anyway"**, and that path was untested too.

**D-12 (batch door race)**, `TeacherDoorTests`:
- **Setup:** wallet 12,000; another job holds 3,000 (spendable 9,000); `_balance` patched large, so nothing is parked; 2 files at 5,000 each. The first hold fits; the second is refused.
- **Expected, current code:**
  - 202;
  - the second file's entry has `task_id` None, and its row is FAILURE with the credit refusal's reason code;
  - launch called once;
  - the UPLOAD job holds 5,000.
  - Result: PASS.

**DP-9** (`students/views.py`, batch): `if not door.hold(…):` becomes `if not (door.hold(…) or True):`, so the hold is attempted, its refusal ignored, and the file launched. Expected kill: **D-12** (launch called twice).

**DP-2 re-run** with D-11 in place: expected kill **D-11** only, fragment `True is not false`.

**Final DP record:** DP-1…DP-9 on the full door set (`test_holds_door` 11 + `test_holds_door_commit` 11 = 22) plus G-1. The expected kills are as in the updated §15h table, plus DP-2 → D-11 and DP-9 → D-12. D-11 and D-12 must pass in every other plant.

**Four-app near run, expected:** **Ran 5788 = 5654 + 134** (127 + C-9, C-10, C-11, D-10, N-1, D-11, D-12), OK (skipped=20). It is followed by the static set as its own run.

### 15h-2 results (code cf82437d, tests 6f3e6dd5)

- **Green:** door set + G-1, Ran 56, OK (`step3c_d11_d12_green.log`). D-11 and D-12 pass on the current code.
- **Final DP record:** DP-1…DP-9, each on the 22 door tests + G-1 (Ran 56 each), logs `step3c_plant_DP-n_final.log`. Every file was restored and sha256-checked. **All 9 matched:**

| Plant | Killed by | Fragment |
|---|---|---|
| DP-1 | D-10 | `2 != 1` |
| DP-2 | **D-11** | `True is not false` (the row survives the refusal) |
| DP-3 | D-7 | `delay` called |
| DP-4 | C-1, C-3, C-4, C-9, C-11 | as in the first record |
| DP-5 | C-6 | `expected call not found` |
| DP-6 | C-8, C-10 | `('FAILURE', 'HELD')` |
| DP-7 | C-2, C-3 | `None != '<uuid>'` |
| DP-8 | C-11 | `('FAILURE', 'HELD')` |
| DP-9 | **D-12** | `2 != 1` (the refused file launched) |

- **Four-app near run (flag off, HEAD 6f3e6dd5):** `billing ai_processor students assignments --parallel 3` gave **Ran 5788, OK (skipped=20)**, as expected (`step3c_near4_flagoff_6f3e6dd5.log.gz`). The ten `[PDF] Chromium is no longer connected` warnings match the base run (`step3c_near4_flagoff_BASE_2f00c078.log`, also 10). They come from the crash-recovery tests, not from a missing browser; there are no browser skips.
- **Static set** (G-1 + `students.tests_reading_label_static` + `AutoGrader.tests_h271_static_files`): **Ran 58, OK** (`step3c_static_6f3e6dd5.log`).
- **Step 3c status:** every expectation matched. Checkpoint sent to the SM; nothing pushed.

---

## 16. 2(b) pinning: H-279 held re-grades and billing. Expectations committed before any run (2026-11-07)

**Ruling (owner, relayed by the SM 2026-11-06):** keep today's behavior and pin it with tests in B1a.
1. A held re-grade gets exactly one grading charge, at the AI call, under its hold.
2. Approval adds no further grading charge, plus exactly one student-summary call, held and settled to the approving (course) teacher.
3. A newer re-grade that replaces a held one: the replaced one is **not** refunded, and the new one is charged.

The SM added: "exactly one charge" is counted in real settlement rows (`SettlementRecord`, the usage rows), never in AICallRecord.

**Where:** a new file `billing/tests/test_holds_regrade_pinning.py`, class `HeldRegradeBillingTests` (TestCase, `BILLING_HOLDS_ENABLED=True`). No production code changes, and no pre-existing test is edited.

**Setup:**
- `classroom()` from test_holds_entry, with 10 × estimate credits.
- The submission is made a released grade: `is_published=True`, `graded_at` set. So `holds_a_released_grade` is true and the next AI grade is held (`_hold_if_released`).
- Grading goes through the real `grade_engine(teacher, submission, processing_task_id=row.id)`, on a GRADING task row. `extract_grade_with_retry` is the `billed(teacher, 1, GRADING)` fake: one real billed `execute_graded_task` call. The provider is patched to `ai_response(300)`.
- The follow-ups run with `captureOnCommitCallbacks(execute=True)`:
  - `students.services.launch_processing_task` is patched, so the formatted-grade task is recorded, not run;
  - `students.services.student_summary_async` is patched to record its `.delay` calls.
- **Approval** is the real `promote_pending(submission.id, actor=teacher)` under `captureOnCommitCallbacks(execute=True)`.
  - The summary's `.delay` is routed to `student_summary_async.apply(args=…)`, the real task. It makes the real `generate_student_summary` call through `execute_graded_task`, with the provider patched to `ai_response(120)`.

**R-1 `test_r1_a_held_regrade_gets_exactly_one_grading_charge`. Expected PASS on the current code:**
- the row is held: `pending_result` is not None, and the released columns are unchanged;
- exactly 1 SettlementRecord: `item_id == row.id`, retry 0, `credits_charged == 300`, its job SINGLE_ITEM on the teacher's wallet;
- the wallet's remaining credits drop by exactly 300, and `reserved_credits == 0`;
- the summary is not dispatched at the held save (`delay` not called);
- the formatted-grade follow-up is launched once (recorded, not run).

**R-2 `test_r2_approval_adds_one_summary_call_settled_to_the_teacher_and_no_grading_charge`. Expected PASS:**
- After R-1's grade and then the approval:
  - the row is promoted: `pending_result` is None, and the released score is the held one;
  - `student_summary_async.delay` is called exactly once, with the teacher's id;
  - exactly 2 SettlementRecords: the grading one unchanged (300), plus one new one with `credits_charged == 120`, whose job is SINGLE_CALL, `requested_by` the teacher, on the teacher's wallet;
  - the formatted-grade launch count is still 1 (approval starts no grading or formatting call);
  - the wallet drops by 420 in total; `reserved_credits == 0`.

**R-3 `test_r3_a_newer_regrade_replaces_the_held_one_without_a_refund`. Expected PASS:**
- Two held re-grades in a row, on two GRADING task rows. The fake returns score 8, then score 9.
  - Exactly 2 SettlementRecords, one per row, each `credits_charged == 300`.
  - The first row's record and its job's `consumed_credits` (300) are unchanged after the second grade.
  - There is no `CreditLedger` REFUND row for the teacher.
  - The wallet drops by 600.
  - The held result is the second one (score 9); the released score is unchanged.

**Plants (each restored by sha256; run on the new file + G-1):**
| Plant | Change | Expected kill |
|---|---|---|
| RP-1 | `students/services.py` `_after_promotion`: `refresh_student_summary(row, actor)` runs twice | R-2 (`2 != 1`, delay count) |
| RP-2 | `students/services.py` follow-ups: `if not held:` becomes `if True:` (summary dispatched at the held save) | R-1 (delay called) |
| RP-3 | `students/services.py` `refresh_student_summary`: `str(actor.id)` becomes `str(row.student_id)` (summary charged to someone else) | R-2 (summary not settled to the teacher's wallet) |
| RP-4 | `students/services.py` `_hold_if_released`: always `return False` (the re-grade overwrites) | R-1, R-3 (`pending_result` None) |

There is no plant for "the replaced one is refunded", because no refund path exists to switch on. R-3 pins its absence: no REFUND ledger row, and the first record is unchanged.

**Observation for the SM (outside the ruling):** a held re-grade also queues the formatted-grade follow-up (`formatted_grade_async`), which is a separate billed AI call (wording of the feedback, not grading). These tests pin that it is launched once per re-grade and not again at approval. They do not run it.

**Run, expected:** the new file (3) + G-1 + `students.tests_reading_label_static` + `AutoGrader.tests_h271_static_files`: Ran 3 + 58 = **61, OK**. Then the H-279 modules `students.tests_h279_regrade_pending_approval` and `students.tests_h279_regrade_pending_concurrency`, unchanged (count recorded at the run).

### 16 results (code = HEAD 81158b31; expectations fde0abe4). 2(b) RESOLVED

| Run | Code | Result | Matches? | Log |
|---|---|---|---|---|
| Green: new file + G-1 + static | 81158b31 working tree before the mypy-only rewrite of `grading()` | Ran 61, OK | yes | step16/step16_green.log |
| Green, the same set, re-run | 81158b31 | Ran 61, OK | yes | step16/step16_green_81158b31.log |
| H-279 modules (`students.tests_h279_regrade_pending_approval`, `students.tests_h279_regrade_pending_concurrency`) + G-1 + static | 81158b31 | Ran 126 (= 68 + 58), OK | yes (count recorded at the run, as declared) | step16/step16_h279_modules.log |

**Plants on 81158b31.** The first attempt ran all four plants in one command. It was killed (exit 137) with RP-1 still applied and no output. The SM held all work. With the owner's approval (8 Nov) the file was restored with `git checkout` and its sha256 checked: 8b8881ac…, which is HEAD. The plants were then re-run one per command with a new runner, `plant16.py`. For each plant it:
- checks the base sha256;
- requires the anchor text to occur exactly once;
- runs the new file + G-1 + static (61 tests);
- restores the file in a `finally` block and checks the sha256 against HEAD.

Every plant was restored to 8b8881ac…. In every plant G-1 and static stayed green.

| Plant | Result | Matches? | Log |
|---|---|---|---|
| RP-1 | 1 FAIL: R-2 `2 != 1` (delay count, line 122) | yes | step16/step16_plant_RP-1.log |
| RP-2 | 2 FAIL: R-1 `Expected 'delay' to not have been called. Called 1 times.`; R-2 `2 != 1` (line 122) | **RP-2 killed. Predicted R-1, which fired. Prediction incomplete:** R-2 also fired, because it counts every summary dispatch across the held save and the approval, so a dispatch at the held save makes it 2 != 1 (SM ruling (a), 2026-11-08). | step16/step16_plant_RP-2.log |
| RP-3 | 1 FAIL: R-2, line 123, `delay` args[1] is the student's id, not the teacher's | yes. R-2 fired as predicted. It failed on the dispatch-args assertion, which comes before the settlement assertions. | step16/step16_plant_RP-3.log |
| RP-4 | 3 FAIL: R-1 line 101 `unexpectedly None` (`pending_result`); R-2 line 93 `unexpectedly None` (`promote_pending` returns None, nothing pending); R-3 line 156 `Decimal('9.00') != Decimal('5')` (the re-grade overwrote the released score) | **RP-4 killed. Predicted R-1 and R-3, which fired. Prediction incomplete:** R-2 also fired, because with nothing held the approval finds nothing to promote. R-3 fired on the released-score assertion, not on `pending_result` None, because the score check comes first. | step16/step16_plant_RP-4.log |

No plant survived. The formatted-grade observation above stands for the SM. It is outside the ruling and not changed here.

## 17. Late-door student notice switched on. Expectations committed before any run (2026-11-08)

**Ruling (owner, relayed by the SM 2026-11-08):** yes, the student is told. `LATE_DOOR_STUDENT_NOTICE_APPROVED = True` (billing/holds.py) lands in B1a. It reuses `STUDENT_UPLOAD_NOT_PROCESSED` unchanged, so there is no new wording. The email subject is the owner-listed, unchanged `LATE_DOOR_EMAIL_TEXTS["subject"]` (team/approved_wording_2026-11-08.md §3; the SM confirmed it does not breach "no new wording").

What stays untouched:
- `LATE_DOOR_EMAIL_APPROVED` stays False, and `LATE_DOOR_EMAIL_TEXTS` is not changed. The three F4 wordings go in B1b.
- The comment at holds.py:1056-1057 is updated to cite the 2026-11-08 ruling. That is a code comment, not user-facing text.

**Pre-existing test edit (SM approved 2026-11-08):** in `billing/tests/test_holds_door.py:270`, `test_the_texts_are_one_pending_constant_and_both_switches_are_off`:
- the assertion `assertIs(holds().LATE_DOOR_STUDENT_NOTICE_APPROVED, False)` becomes `True`;
- the test is renamed `test_the_texts_are_one_pending_constant_the_email_switch_is_off_and_the_student_notice_is_on`;
- the texts-keys and `LATE_DOOR_EMAIL_APPROVED is False` assertions are kept.

**New file `billing/tests/test_holds_late_door_student.py`, `LateDoorStudentNoticeTests` (APITestCase, `BILLING_HOLDS_ENABLED=True`).** The setup is the one from `LateDoorNoticeTests.late_door_refusal`, with one difference: the swept UPLOAD job and its row have `requested_by` set to the enrolled student. `send_email_task.delay` is patched.

| Id | Test | Red (flag False) | Green |
|---|---|---|---|
| L-1 | a student's late-door refusal sends exactly one email, to the student, whose `message == STUDENT_UPLOAD_NOT_PROCESSED` | FAIL (`Expected 'delay' to have been called once. Called 0 times.`) | PASS |
| L-2 | exactly one message goes to the student (`0 != 1` on red), and it contains none of `credit`, `balance` or `wallet` (any case) and no digit | FAIL (`0 != 1`) | PASS |
| L-3 | control: no email goes to the teacher, and the teacher-side warning (`billing.holds`, "no email was sent") is still logged | PASS | PASS |
| L-4 | control: a teacher-requested late-door refusal sends no email at all | PASS | PASS |

**Runs.** The targeted set is the new file + `billing.tests.test_holds_door` + G-1 + `students.tests_reading_label_static` + `AutoGrader.tests_h271_static_files`. Counts are recorded at the run.
1. **Red** on HEAD + the new file, flag False: `FAILED (failures=2)`, which are L-1 and L-2. Everything else passes.
2. **Break run:** the flag switched on, `test_holds_door.py` not yet edited. `FAILED (failures=1)`: only `test_the_texts_are_one_pending_constant_and_both_switches_are_off`, `True is not False`. L-1..L-4 pass.
3. **Green:** the flag on, the test edited. OK.
4. **Plant LP-1:** `LATE_DOOR_STUDENT_NOTICE_APPROVED = True` back to `False`, restored and checked by sha256. Expected `FAILED (failures=3)`: L-1, L-2, and the renamed door test (`False is not True`).

Any mismatch is a STOP, reported to the SM.

### 17 results (expectations 16052e3b, tests 8fad134a, code c5bb2e19)

| Run | Code | Result | Matches? | Log |
|---|---|---|---|---|
| Red: new file + test_holds_door + G-1 + static, flag False | 8fad134a | Ran 73, FAILED (failures=2): L-1 `Expected 'delay' to have been called once. Called 0 times.`; L-2 `0 != 1` | yes | step17/step17_red_8fad134a.log |
| Break run: flag on, test_holds_door.py not edited | WIP | Ran 73, FAILED (failures=1): `test_the_texts_are_one_pending_constant_and_both_switches_are_off` `True is not False`. L-1..L-4 passed. | yes | step17/step17_break_run.log |
| Green: flag on, door test edited | WIP | Ran 73, OK | yes | step17/step17_green_WIP.log |
| Green, the same set | c5bb2e19 | Ran 73, OK | yes | step17/step17_green_c5bb2e19.log |
| LP-1: `LATE_DOOR_STUDENT_NOTICE_APPROVED` back to False, restored by sha256 (d19bbf1a…, HEAD) | c5bb2e19 + plant | Ran 73, FAILED (failures=3): L-1, L-2 (same messages as red); the renamed door test `False is not True` | yes | step17/step17_plant_LP-1.log |

`LATE_DOOR_EMAIL_APPROVED` (False) and `LATE_DOOR_EMAIL_TEXTS` are unchanged in c5bb2e19. The late-door student notice is on in B1a.

## 18. Plan step 3b: the departed-teacher share refund (§9 item 3). Expectations committed before any code or run (2026-11-08)

**Owner ruling 2026-10-29 (plan §9.3):** a teacher skipped at fulfillment after the school paid has that share refunded automatically to the school, and the admin is notified. Stripe payments get a partial refund; offline payments get an offline refund record for the super admin.

**Plan correction (SM Q1, 2026-11-08).** §9.3 says "through the existing payment_refunds.py path". That module only applies an inbound `charge.refunded`, so the phrase describes the inbound side. The refund is issued the way `stripe_service.py:1710` issues the interval-change refund, and the inbound `charge.refunded` then goes through `RefundService.apply` with `retain_credits=true`.

### 18a. Survey findings (sent to the SM, rulings Q1–Q5 on 2026-11-08)

- **F-1.** Nothing in the code issues a refund for this. The precedent is `stripe.Refund.create(payment_intent=…, idempotency_key=…, metadata={system_refund_reason: …})` (stripe_service.py:1710).
- **F-2 (the key risk).** Our partial refund comes back as `charge.refunded`. By default `RefundService` reclaims `granted × refunded // captured` from the teachers who WERE granted. The refund must carry `retain_credits=true`.
  - payment_refunds.py: the retain branch returns before `reverse_credits_for_payment` and `_restore_block_allowance`, so credits and the block allowance are left alone.
  - `system_refund_reason` only changes the no-matching-transaction branch of `handle_refund`. Ours matches the overage purchase's BillingTransaction, which becomes PARTIALLY_REFUNDED.
- **F-3.** The handler skips only COMPLETED intents, and a partial-skip intent ends FAILED, so a re-invocation re-grants. **Filed by the SM as H-300 and not fixed here.** Guard 2 makes a second refund impossible.
- **F-4.** Offline: `approve_overage_offline_request` skips the same way (`skipped_allocations`). There is no refund record today.
- **F-5.** The handler runs inside `handle_checkout_completed`'s atomic block, so the refund goes to `transaction.on_commit`, as a Celery task.

### 18b. Design (SM rulings Q1–Q5)

**One switch.** `DEPARTED_SHARE_REFUND_APPROVED = False`, in a new module `billing/departed_share_refund.py`.
- **While off:** today's behavior and today's texts, byte for byte. The switch exists because a refund running while W-1 still says "Needs manual review (partial refund…)" invites a second, manual refund of real money.
- **While on:** the refund, plus the PENDING texts below.

**PENDING texts.** `DEPARTED_SHARE_REFUND_TEXTS`, one constant marked PENDING OWNER APPROVAL, following the `LATE_DOOR_EMAIL_TEXTS` pattern. The SM is drafting the texts for the owner, so I write none.
- The keys are `failure_reason` (W-1), `transaction_suffix` (W-2), `email_subject` and `email_body` (W-3), and `offline_notes` (W-4 notes).
- Every value is a placeholder starting `PENDING OWNER TEXT`.

**Offline record.** A new `LicenseBillingRecordType.OFFLINE_OVERAGE_REFUND_DUE`, with the placeholder label `"PENDING OWNER TEXT: offline overage refund due"`.
- Migration `0078`: a choices-only `AlterField` on `LicenseBillingRecord.record_type`.
- The record is created only while the switch is on.

**W-5.** A failure is logged only, at ERROR, containing `MANUAL RECONCILIATION`. No message is sent.

**Stripe path, switch on.** In `_handle_license_overage_checkout_completed`, when `skipped` is non-empty, `transaction.on_commit(lambda: refund_departed_teacher_share.delay(str(intent.id)))`. Nothing is sent to Stripe inside the webhook transaction.

The task `billing.tasks.refund_departed_teacher_share(intent_id)` does this:
1. Reads the intent. The share is `sum(skipped blocks) × unit_price_cents_snapshot`. The skipped blocks are recomputed from `allocations` minus the granted ledger rows' `metadata.intent_id`.
   - **Code-time confirmation:** if that turns out too indirect, the skipped list is parsed from nowhere else. A non-empty share that cannot be computed is an ERROR `MANUAL RECONCILIATION`. Either way the tests pin the amount.
2. If there is no `stripe_payment_intent_id`: no Stripe call, ERROR `MANUAL RECONCILIATION`.
3. **Guard 2:** `stripe.Refund.list(payment_intent=pi, limit=100)`.
   - If any refund carries `metadata.system_refund_reason == "departed_teacher_share"` and `metadata.intent_id == intent.id`, the task returns and does nothing.
   - **Cap (SM Q2):** refundable = `amount_total` (the intent's paid amount: session `amount_total`, or else `amount_cents`) minus the sum of existing refunds on the PI whose status is not `failed` or `canceled`. amount = `min(share, refundable)`. If amount ≤ 0: no refund, ERROR `MANUAL RECONCILIATION`.
4. `stripe.Refund.create(payment_intent=pi, amount=amount, idempotency_key=f"departed-share-refund-{intent.id}", metadata={"system_refund_reason": "departed_teacher_share", "retain_credits": "true", "intent_id": str(intent.id)})`.
5. Failures:
   - On `stripe.error.APIConnectionError`, `RateLimitError` or `APIError`, the task retries: `max_retries=5`, exponential countdown, everything inside 24 hours so the idempotency key still holds.
   - On any other `StripeError`, or once the retries are used up, the task logs at ERROR with `MANUAL RECONCILIATION` and stops.
   - Grants, the intent and the BillingTransaction are never rolled back or edited by the task.
6. The W-3 email to `license_sub.admin_user` (falling back to `intent.initiated_by`) goes through `send_email_task.delay` with the PENDING subject and body, in the handler's on_commit, switch on only.

**W-1/W-2, switch on.** `failure_reason` and the BillingTransaction suffix use the PENDING texts, and the intent status stays FAILED (plan m3-a: FAILED handling unchanged).

**Offline path, switch on.** In `approve_overage_offline_request`, when `skipped` is non-empty, one `LicenseBillingRecord(record_type=OFFLINE_OVERAGE_REFUND_DUE, amount_paid_cents=min(share, amount_confirmed_cents), notes=TEXTS["offline_notes"], performed_by=…)` is written in the same transaction, and the W-2 suffix uses the PENDING text. No Stripe call. `amount_paid_cents` holds the amount owed back (open question R-Q1 below).

**Scope.** No change to `payment_refunds.py`, to H-300 (the re-grant), to the late-door or holds code, or to live_qa.

### 18c. Tests: new file `billing/tests/test_departed_teacher_refund.py`

**Fixture.** The school-license fixture from `test_overage_never_expires.LicenseOverageCheckoutWebhookNeverExpiresTestCase`, with 3 teachers on the license.
- The intent is 3 teachers × 1 block at `overage_block_price` 299. The session is paid, with `payment_intent="pi_dr"` and `amount_total=897`.
- **Departure between checkout and webhook:** after the intent is created, teacher C's `SchoolCreditAllocation` gets `is_active=False`, the condition the handler re-checks under lock.
- `stripe.Refund.create` and `stripe.Refund.list` are patched in `billing.departed_share_refund`, and `send_email_task.delay` is patched. `refund_departed_teacher_share.delay` is routed to `.apply`.
- The handler runs under `captureOnCommitCallbacks(execute=True)`.
- **No live Stripe call anywhere.** The switch is patched on per test with `patch.object(drs, "DEPARTED_SHARE_REFUND_APPROVED", True)`. The module is reached through a lazy helper, so on red each test fails on its own rather than the whole file failing to import.

| Id | Test | Red (no module) | Green |
|---|---|---|---|
| DR-1 | switch off, Stripe path: A and B granted, C not. Intent FAILED, with today's `failure_reason` (contains `Needs manual review (partial refund or manual grant).`) and today's suffix ` (1 skipped, needs review)`. No Refund.list or Refund.create, no task, no email | PASS (control) | PASS |
| DR-2 | switch off, offline path: today's approval (`skipped_allocations` holds C). No `OFFLINE_OVERAGE_REFUND_DUE` record, no Stripe call | PASS (control) | PASS |
| DR-3 | `DEPARTED_SHARE_REFUND_APPROVED is False`; the texts keys equal the 5 above and every value starts `PENDING OWNER TEXT`; the new choice's label is the placeholder | ERROR (ModuleNotFoundError) | PASS |
| DR-4 | switch on, the departure: A and B granted exactly as in DR-1. Exactly one Refund.list(payment_intent="pi_dr") and exactly one Refund.create with `amount=299`, `idempotency_key="departed-share-refund-<intent>"` and metadata `{system_refund_reason: departed_teacher_share, retain_credits: "true", intent_id}` | ERROR | PASS |
| DR-5 | on-commit: switch on, the handler under `captureOnCommitCallbacks(execute=False)`. Refund.create not called, ≥ 1 callback captured. A handler run inside an atomic block that then rolls back: no Refund.create, ever | ERROR | PASS |
| DR-6 | round trip (F-2): a `charge.refunded` fixture built from DR-4's Refund.create call (amount_refunded 299, captured 897, `refunds.data[0].metadata` = the call's metadata) goes through `RefundService.apply`. A's and B's OVERAGE bucket credits are unchanged, as is the license's overage block count. `PaymentRefund.retain_credits` is True, the purchase BillingTransaction is PARTIALLY_REFUNDED, and no standalone manual-review transaction is created | ERROR | PASS |
| DR-7 | cap: Refund.list returns an unrelated succeeded refund of 700, so the refundable amount is 197: Refund.create `amount=197`. A second case with a 897 refund already made: no Refund.create, ERROR `MANUAL RECONCILIATION`. A `failed` refund in the list is not subtracted | ERROR | PASS |
| DR-8 | guard 2: Refund.list returns our own refund (matching metadata), so a second task run makes no Refund.create. Re-invoking the handler for the same intent also makes no second Refund.create; the re-grant (H-300) is not asserted on | ERROR | PASS |
| DR-9 | Stripe failure: Refund.create raises `APIConnectionError`, so the task retries (`Retry` raised, retries ≤ 5). Raising `InvalidRequestError` instead: no retry, ERROR `MANUAL RECONCILIATION`. In both cases A's and B's grants and the intent are unchanged | ERROR | PASS |
| DR-10 | no `payment_intent` on the session: no Stripe call, ERROR `MANUAL RECONCILIATION` | ERROR | PASS |
| DR-11 | switch on, W-3: exactly one `send_email_task.delay` to the license admin, with the PENDING subject and body | ERROR | PASS |
| DR-12 | switch on, W-1/W-2: `failure_reason` is the PENDING text and the transaction suffix is the PENDING text. The intent stays FAILED | ERROR | PASS |
| DR-13 | switch on, offline: exactly one `OFFLINE_OVERAGE_REFUND_DUE` record (amount 299, the notes text). No Stripe call | ERROR | PASS |
| DR-14 | switch on, no teacher skipped: no Refund.list or Refund.create, no task, no email, no record | ERROR | PASS |
| DR-15 | migration 0078 is choices-only: forward then back with `MigrationExecutor` changes no column. `makemigrations --check` is clean (run separately) | ERROR (no 0078) | PASS |

DR-15 is the "rollback-defaults" test the SM asked for. I found no existing test by that name. `test_holds_schema.py` S-5 (reversal) is the nearest precedent, so DR-15 lives in the new file and leaves S-5 untouched.

No pre-existing test is edited. With the switch off, every text and behavior is today's, and `test_license_overage_offline.py`, the only file touching `skipped_allocations`, runs in the targeted set unchanged.

### 18d. Runs

The targeted set is the new file + `billing.tests.test_license_overage_offline` + `billing.tests.test_overage_never_expires` + `billing.tests.test_overage_refund_lifecycle` + `billing.tests.test_billing_transaction_refunds` + G-1 + `students.tests_reading_label_static` + `AutoGrader.tests_h271_static_files`. The count is recorded at the run.

1. **Red** (tests committed, no code): DR-1 and DR-2 PASS; DR-3 to DR-15 ERROR, 13 errors. Everything else passes.
2. **Green:** OK. `makemigrations --check` is clean after 0078.
3. **Plants**, each restored by sha256 and run on the targeted set:

| Plant | Change | Expected kill |
|---|---|---|
| RF-1 (M-B33) | the handler's on_commit enqueue removed | DR-4, DR-6, DR-11 |
| RF-2 | `retain_credits` dropped from the Refund.create metadata | DR-4 (metadata), DR-6 (A's/B's credits reclaimed) |
| RF-3 | guard 2 removed (no Refund.list match check) | DR-8 |
| RF-4 | the refund called inline in the handler instead of on commit | DR-5 |
| RF-5 | the cap is `min(share, amount_total)` (existing refunds ignored) | DR-7 |
| RF-6 | the handler's switch check becomes `if True` | DR-1 |
| RF-7 | `idempotency_key` dropped | DR-4 |

4. **Near run:** under `flock ~/.machine-fullsuite.lock` with h284_deps_env, `billing` + G-1/static, `--parallel 4`. Expected OK; the count is recorded at the run.

**Open question for the SM (R-Q1):** the offline record has no amount-owed field. I plan to put the amount in `amount_paid_cents`, since the record type says it is a refund due. The alternative is notes only. Answer before green, not before red.

Any mismatch is a STOP, reported to the SM.

### 18 addendum, amended before any run: where the share comes from

18b step 1 is replaced as follows. The intent stores no skipped list: on the Stripe path it exists only inside the `failure_reason` text. Rebuilding it from ledger metadata is indirect and fragile. Instead:
- The handler, which already holds `skipped`, enqueues `refund_departed_teacher_share.delay(intent_id, share_cents)`, where `share_cents = sum(skipped blocks) × unit_price_cents_snapshot`.
- The task never re-derives the share.
- Guard 2 and the cap still run in the task, against Stripe's current state, so a replay with the same arguments is still a no-op.

DR-4 also asserts the task was enqueued with `(str(intent.id), 299)`. No other expectation changes.

### 18 addendum 2, amended before any run (SM 2026-11-08)

1. **R-Q1: no.** `amount_paid_cents` goes out through the API (billing/serializers.py:2348 onward) and reads as money the school paid. On an `OFFLINE_OVERAGE_REFUND_DUE` record it stays **null**. The owed amount goes into `notes`, from `TEXTS["offline_record_notes"].format(amount=…)`.
   - DR-13 now asserts `amount_paid_cents is None` and that the notes contain the formatted amount.
2. **The text keys follow the owner draft** (team/draft_wording_departed_share_2026-11-08.md, awaiting approval). There are 7, all PENDING:
   - `purchase_note` (W-1), formatted with `{blocks}`, `{n}`, `{skipped}` and `{amount}`;
   - `history_suffix_card` and `history_suffix_offline` (W-2), formatted with `{n}`; the offline one replaces the suffix at license_service.py:~3633, switch on only;
   - `email_subject`, `email_body_card` and `email_body_offline` (W-3), the bodies formatted with `{n}` and `{amount}`;
   - `offline_record_notes` (W-4 notes), formatted with `{amount}`.
   - Each value is a placeholder starting `PENDING OWNER TEXT` that carries the draft's format fields, so formatting is exercised. The W-4 label stays the placeholder choice label.
   - **DR-3:** exactly these 7 keys; every value starts `PENDING OWNER TEXT`; the label is the placeholder.
   - **DR-11:** the card email uses `email_subject` and `email_body_card`.
   - **DR-12:** `purchase_note` and `history_suffix_card`.
   - **DR-13 (offline, switch on)**, in addition:
     - exactly one `send_email_task.delay` to the license admin, with `email_subject` and the formatted `email_body_offline`, in the approval's on_commit;
     - the offline BillingTransaction suffix is the formatted `history_suffix_offline`;
     - `amount_paid_cents is None`, and the notes contain `299` formatted as the amount.
   - **DR-2 (switch off, offline)** also asserts that no email is sent and that today's ` (1 skipped, needs review)` suffix stays.
   - How `{amount}` is shown is the placeholder's business for now; the tests check only that the formatted value contains the amount figure.
3. **The rollback test.** `AutoGrader.tests_migration_rollback_defaults` joins the targeted set and the near run.
   - 0078 is a choices-only `AlterField`, which the guard skips by its stated scope, so no edit to that module is expected.
   - DR-15 stays.

Red is otherwise as declared: DR-1 and DR-2 PASS, DR-3..DR-15 ERROR (13), every other test passes, with `tests_migration_rollback_defaults` passing on red too. The targeted-set count is recorded at the run.

### 18 addendum 3, amended before any run: test-level counts

DR-5 (after-commit; a rolled-back webhook), DR-7 (the cap; nothing left to refund) and DR-9 (a connection error retried; a refused refund reported) are each two tests. So the new file has **18 tests**. The red declaration "DR-3..DR-15 ERROR (13)" counted IDs. At test level, red is **DR-1 and DR-2 PASS, 16 ERROR** (ModuleNotFoundError or AttributeError for the missing module or task, and DR-15 a missing 0078). Nothing else changes.

### 18 red attempt 1: fixture STOP (not a valid red; recorded, not amended away)

On 035d0ffb: Ran 191, FAILED (errors=18). All 18 new tests errored in setUp, so this attempt says nothing about the code.
- Cause: in `make_school()`, `add_teacher_to_license` for the 2nd and 3rd teacher raised `ValueError: No seats remaining to add a new teacher.` (license_service.py:~1747). The fixture's LicenseSubscription did not set `max_seats`.
- The expected errors that did surface were ModuleNotFoundError `billing.departed_share_refund` and KeyError for 0078.
- Every other test in the set passed.
- Log: step18/step18_red_035d0ffb.log.
- SM approved the fix on 2026-11-08, following the dd7687f4f precedent: `max_seats=3` in `make_school()` only, committed separately, with no change to the expectations. Red is re-run against the declaration as it stands: DR-1 and DR-2 PASS, 16 ERROR. If that run does not match exactly, STOP again.

### 18 red attempt 2 (6fea0ae3, the seat fix only): recorded as it stands, not amended

Ran 191, FAILED (failures=1, errors=16).
- DR-1 PASS, and DR-3..DR-15 16 ERROR (15 ModuleNotFoundError `billing.departed_share_refund`, DR-15 KeyError 0078), as declared.
- **DR-2: declared PASS, observed FAIL. Prediction incomplete: the survey missed the existing approval email.** `send.assert_not_called()` → `Called 1 times`. `approve_overage_offline_request` already sends the requester, the school admin, "Your overage request has been approved — {school}" on commit. That is `_notify_school_admin_offline_overage_approved`, license_service.py:~3770, template `templates/email/license_overage_offline_request_approved.html`.
  - Its skipped section (template :81-92) says "<strong>Not credited</strong> — these teachers are no longer active under the license and were skipped. Contact support if this needs manual follow-up." and lists the skipped teachers.
  - The survey read the approval only up to ~:3697.
- **Accepted existing noise (SM):** "ValueError: Missing staticfiles manifest entry for 'admin/css/base.css'" is logged once from a template render. It also appears in attempt 1 and in step16_h279_modules.log, a run that came back OK. It is not a test result.
- Log: step18/step18_red_6fea0ae3.log.

### 18 addendum 4, committed before any further run (SM ruling (b), 2026-11-08)

**The offline admin gets ONE email: the existing approval email.** No separate W-3 offline email.

1. **The text keys (still 7).** `email_body_offline` is dropped and `approval_email_not_credited` (W-6) is added, formatted with `{amount}`. The keys are now:
   - `purchase_note`, `history_suffix_card`, `history_suffix_offline`, `email_subject`, `email_body_card`, `offline_record_notes`, `approval_email_not_credited`.
   - All are PENDING. `email_subject` and `email_body_card` are for the card path only.
   - DR-3 checks these 7.
2. **The template.** It switches between today's sentence and the PENDING one through one context value, `not_credited_text`.
   - Unset or empty: today's `<p><strong>Not credited</strong> — … Contact support if this needs manual follow-up.</p>`, byte for byte.
   - Set: `<p>{{ not_credited_text }}</p>` in its place.
   - The skipped-teacher list and every other part of the template stay as they are.
   - `_notify_school_admin_offline_overage_approved` sets `not_credited_text` only while the switch is on and teachers were skipped.
3. **DR-2 (switch off, offline): corrected, SM approved.**
   - Exactly one `send_email_task.delay`, to the requester (the admin): today's approval email, with today's subject and plain message.
   - Its `html_message` contains today's sentence "Contact support if this needs manual follow-up." and no `PENDING OWNER TEXT`.
   - The other DR-2 assertions stay: `skipped_allocations` holds C, no refund-due record, today's suffix, no Stripe call.
4. **DR-13 (switch on, offline): corrected.**
   - Still exactly one email to the admin, the approval email with today's subject.
   - Its `html_message` contains the formatted `approval_email_not_credited`: starts `PENDING OWNER TEXT`, contains the amount 299.
   - It does not contain "Contact support if this needs manual follow-up.".
   - It still lists C's skipped row.
   - No second email.
   - The record assertions stay: `amount_paid_cents is None`, the notes contain the amount. So do the PENDING offline history suffix and "no Stripe call".
5. **Red attempt 3**, against this addendum on the code as it stands: DR-1 and DR-2 PASS; DR-3..DR-15 16 ERROR; everything else passes. If it does not match: STOP.

### 18 addendum 5, declared before green (SM approved 2026-11-08): how amounts are shown, and currency

Red attempt 3 (f17c40b1) **matched** addendum 4: Ran 191, FAILED (errors=16). DR-1 and DR-2 passed. Of the 16 errors, 15 were ModuleNotFoundError and 1 (DR-15) a KeyError for 0078. The staticfiles noise appeared once. Log: step18/step18_red_f17c40b1.log.

1. **Amounts.** `{amount}` is money in the owner draft. `departed_share_refund.format_amount(cents)` returns `f"${cents / 100:,.2f}"`. DR-11 (card email body), DR-12 (`purchase_note`) and DR-13 (approval email html and record notes) assert the **literal** `"$2.99"`, not the helper, because a check against the helper would be circular.
2. **DR-16 (new):** `format_amount` maps 299 → `"$2.99"`, 0 → `"$0.00"` and 123456 → `"$1,234.56"`.
3. **Currency, DR-17 (new).** The license overage checkout charges `plan.stripe_overage_price_id` (license_service.py:~3331). Its currency is whatever that Stripe Price carries, so a non-usd purchase cannot be ruled out in code.
   - The handler therefore checks the session's `currency`. If it is not `"usd"`, there is no refund task, no Stripe call and no guessed symbol, and an ERROR `MANUAL RECONCILIATION` is logged.
   - DR-17: a session with `currency="eur"` and the switch on gives no enqueue, no Refund.list or Refund.create, and the ERROR line.
4. The new file is now **20 tests**. Green expects all 20 to pass.

### 18 green attempts 1 and 2 (code uncommitted, tests 78d9309a): recorded as they stand

- **Green attempt 1:** Ran 193, FAILED (failures=2). Log: step18/step18_green_WIP.log.
  - **DR-3 caught a real code bug:** the `history_suffix_card` and `history_suffix_offline` placeholders began with a space, so they did not start `PENDING OWNER TEXT`. Fixed in the code: the values start PENDING, and the callers add the leading space. SM accepted.
- **Green attempt 2:** Ran 193, FAILED (failures=1). Log: step18/step18_green_WIP2.log.
  - **DR-9a, a fixture defect in the test:** `Retry not raised`. The test called the task without fulfilling first, so the intent had no PaymentIntent and the code correctly returned at its no-PaymentIntent guard.
  - Fix, SM approved: `self.fulfill_off()` as DR-9a's first line, the same setup as DR-9b, plus the SM's added assertion that Refund.create was called exactly once before the Retry, so the retry can only come from the Stripe call.
- **Design points settled while coding (SM accepted):**
  - The cap's paid amount is the purchase BillingTransaction's `amount_cents` (the session's amount_total), falling back to the intent's `amount_cents`.
  - The automatic path needs the switch on, skipped teachers, a PaymentIntent and currency usd. Otherwise the handler keeps today's texts, logs ERROR `MANUAL RECONCILIATION`, and enqueues nothing.
  - The task and the admin email are enqueued in one on_commit.
  - The offline share is capped at `amount_confirmed_cents`.

### 18 results: green and plants (tests 60ce83fa, code 55c1ed41)

| Run | Code | Result | Matches? | Log |
|---|---|---|---|---|
| Green attempt 3 (after the DR-9a fix) | WIP | Ran 193 (20 new), OK | yes | step18/step18_green_WIP3.log |
| Code commit | 55c1ed41 | black reformatted license_service.py and stripe_service.py; flake8 B904 on the task's `raise self.retry(…)`, fixed with `from exc`; re-staged | — | — |
| Green on the commit | 55c1ed41 | Ran 193, OK. `makemigrations --check`: no changes | yes | step18/step18_green_55c1ed41.log |

**Plants on 55c1ed41.** Runner `plant18.py`, one plant per command. For each plant it:
- checks both base sha256s (stripe_service.py d2fa42ab…, departed_share_refund.py e284485f…);
- requires the anchor text to occur exactly once;
- runs the targeted set (193 tests);
- restores the file in a `finally` block and checks both hashes.

Every plant was restored OK. G-1, static and the rollback-defaults module stayed green in every plant.

| Plant | Exact change | Result | Matches? | Log |
|---|---|---|---|---|
| RF-1 (M-B33) | stripe_service: `transaction.on_commit(_after_commit)` → `pass` | FAILED (failures=4, errors=1): DR-4, DR-11 FAIL; DR-6 ERROR (no Refund.create call to read); DR-7 ×2 FAIL | **RF-1 killed. Predicted DR-4, DR-6 and DR-11, which fired. Prediction incomplete:** both DR-7 tests also fired, because with no task there is no Refund.create to cap and no MANUAL RECONCILIATION log | step18/step18_plant_RF-1.log |
| RF-2 | `"retain_credits": "true"` removed from the refund metadata | FAILED (failures=2): DR-4 (metadata), DR-6 (A's and B's credits reclaimed on the round trip) | yes | step18/step18_plant_RF-2.log |
| RF-3 | guard 2 disabled: `if meta.get(` → `if False and meta.get(` | FAILED (failures=1): DR-8 | yes | step18/step18_plant_RF-3.log |
| RF-4 | stripe_service: `transaction.on_commit(_after_commit)` → `_after_commit()` (inline) | FAILED (failures=2): both DR-5 tests | yes | step18/step18_plant_RF-4.log |
| RF-5 | `refundable = _paid_cents(intent) - already` → `_paid_cents(intent)` | FAILED (failures=2): both DR-7 tests | yes | step18/step18_plant_RF-5.log |
| RF-6 | the switch ignored: `if not (skipped and enabled()):` → `if not skipped:` (the handler's switch check lives in `can_refund_automatically`) | FAILED (failures=1): DR-1 | yes | step18/step18_plant_RF-6.log |
| RF-7 | the `idempotency_key=…` line removed | FAILED (errors=1): DR-4 ERROR (the key is missing from the call's kwargs) | yes. The declaration named no failure form (cf. HP-3) | step18/step18_plant_RF-7.log |

No plant survived.

**Next (SM, 2026-11-08):** the approved-texts sub-step, with its own expectations, then the near run.

## 19. 3b approved texts and the switch on. Expectations committed before any run (2026-11-08)

**Ruling:** the owner approved the departed-share wording verbatim on 2026-11-08 (team/approved_wording_departed_share_2026-11-08.md, main checkout). SM order: finish 3b with placeholders (done, c89e8c93), then this sub-step, the same pattern as the student notice (§17).

**SM rulings on two gaps in the approved file (2026-11-08):**
- **G-1 (b).** The file has no text for `offline_record_notes`. The key becomes `"{amount}"`, a bare value such as "$2.99" and no prose, so it is not new wording. The approved label "Overage Refund Owed (Offline)" says what the amount is.
- **G-2 (a).** W-6's `**Not credited** — ` is the file's markdown for today's `<strong>Not credited</strong> — `.
  - The key holds the approved sentence **without** that heading.
  - The template keeps `<strong>Not credited</strong> — ` shared by both branches. The `{% if not_credited_text %}` moves after it.
  - Switch off, today's sentence and HTML stay byte for byte.

### 19a. The change

**`billing/departed_share_refund.py`:** `DEPARTED_SHARE_REFUND_APPROVED = True`, and `DEPARTED_SHARE_REFUND_TEXTS` holds exactly these values. The PENDING comment becomes "approved by the owner 2026-11-08".

| Key | Value (verbatim) |
|---|---|
| purchase_note | `Paid for {blocks} block(s). {n} teacher(s) had left before the purchase went through, so they weren't given credits: {skipped}. Their share, {amount}, is being refunded automatically to the card used.` |
| history_suffix_card | `({n} teacher(s) had left; their share is being refunded)` (the caller adds the leading space, as now) |
| history_suffix_offline | `({n} teacher(s) had left; their share is owed back)` (likewise) |
| email_subject | `Part of your credit purchase is being refunded` |
| email_body_card | `{n} teacher(s) in your credit purchase had left your school before it went through, so they weren't given credits. The other teachers received theirs. We're refunding {amount} for the credits that weren't given to the card you paid with.` |
| offline_record_notes | `{amount}` |
| approval_email_not_credited | `these teachers had left your school before the purchase went through, so they weren't given credits. Our team will refund {amount} to you directly.` |

The approved file's W-2 values begin with a space (" (…)"). The key holds them without it, and the caller's `" " +` supplies it, so the rendered suffix is the approved text character for character. DR-12 and DR-13 check the rendered suffix.

**Other changes:**
- `billing/models.py`: the W-4 label `_("Overage Refund Owed (Offline)")`. Migration 0078 is regenerated (still choices-only, same name), and `makemigrations --check` must be clean.
- Template: `<p><strong>Not credited</strong> — {% if not_credited_text %}{{ not_credited_text }}{% else %}these teachers are no longer active under the license and were skipped. Contact support if this needs manual follow-up.{% endif %}</p>`. With the switch off this renders the same bytes as today.

### 19b. Test changes (new file only, `billing/tests/test_departed_teacher_refund.py`)

- **DR-1, DR-2:** each runs inside `patch.object(drs, "DEPARTED_SHARE_REFUND_APPROVED", False)`. They remain the switch-off controls; their assertions are unchanged.
- **DR-3:** renamed `test_dr3_the_switch_is_on_and_every_text_is_the_approved_wording`. It asserts `DEPARTED_SHARE_REFUND_APPROVED is True`, `DEPARTED_SHARE_REFUND_TEXTS == APPROVED` (the 7 values above, written out in the test), and the choice label `"Overage Refund Owed (Offline)"`.
- **DR-11:** the subject equals the approved subject. The message equals `email_body_card` formatted with n=1 and amount="$2.99": "1 teacher(s) in your credit purchase … We're refunding $2.99 for the credits …".
- **DR-12:** `failure_reason` equals the approved purchase_note formatted (blocks=3, n=1, skipped=the skipped list, amount="$2.99"); it contains "$2.99" and not "Needs manual review". The transaction description ends with `" (1 teacher(s) had left; their share is being refunded)"`.
- **DR-13:**
  - The record's `notes == "$2.99"` and `amount_paid_cents is None`.
  - The offline description ends with `" (1 teacher(s) had left; their share is owed back)"`.
  - The approval email html contains `<strong>Not credited</strong> — these teachers had left your school before the purchase went through, so they weren&#x27;t given credits. Our team will refund $2.99 to you directly.` Django autoescapes the apostrophe, so the test compares against `django.utils.html.escape` of the approved sentence.
  - It does not contain "Contact support if this needs manual follow-up.". C's row is still listed, and there is one email.
- The other tests (DR-4..DR-10, DR-14..DR-17) are unchanged. Their `switch_on()` patch is now redundant and harmless.
- **No pre-existing test outside this file is edited.** `test_license_overage_offline.test_approve_partial_fulfillment_when_teacher_removed` approves with a skipped teacher. With the switch on it also writes a refund-due record and the new texts. It asserts status, counts and buckets only, so it should still pass.

### 19c. Runs (targeted set as in §18)

1. **Break run:** the 3b test file as committed at c89e8c93 (the old DR-1/2/3/11/12/13) against the new code (switch on, approved texts). Expected `FAILED (failures=6)`:
   - DR-1, at `failure_reason.endswith(TODAY_REVIEW)`;
   - DR-2, at `assertFalse(self.refund_due().exists())`;
   - DR-3, `True is not False`;
   - DR-11, `message.startswith("PENDING OWNER TEXT")`;
   - DR-12, `failure_reason.startswith("PENDING OWNER TEXT")`;
   - DR-13, `notes.startswith("PENDING OWNER TEXT")`.
   - Everything else passes.
2. **Green:** the new code and the new tests: Ran 193, OK. `makemigrations --check` is clean.
3. **Plant AP-1:** `DEPARTED_SHARE_REFUND_APPROVED = True` back to `False`, restored by sha256. Expected `FAILED (failures=1)`, DR-3 only (`False is not True`).
   - Every other switch-dependent test sets the switch itself: DR-1 and DR-2 patch it off, and DR-4..DR-14 and DR-17 patch it on. So none of them notices the module default.
   - This plant proves that DR-3 pins the shipped default.
4. **Near run:** under `flock ~/.machine-fullsuite.lock` with h284_deps_env, `--parallel 4`: `billing audit` + `AutoGrader.tests_migration_rollback_defaults` + G-1/static. Expected OK; the count is recorded at the run.

Any mismatch is a STOP, reported to the SM.

### 19d. Results (2026-11-08)

Every run matched its declared expectation. Logs are in `docs/evidence/epic-b1a/step19/`.

1. **Break run** (tests as at c89e8c93, new code): `FAILED (failures=6)` on DR-1, DR-2, DR-3, DR-11, DR-12 and DR-13, each at its declared line; everything else passed. Matched (`step19_break_run.log`).
2. **Green:** Ran 193, OK, both on the working tree (`step19_green_WIP.log`) and on e879bbf2 (`step19_green_e879bbf2.log`). `makemigrations --check` is clean. A script compared `DEPARTED_SHARE_REFUND_TEXTS` with the approved file (with rulings G-1 b and G-2 a): verbatim.
3. **Plant AP-1** (switch back to `False`): `FAILED (failures=1)`, DR-3 only, `False is not True`. Matched. `billing/departed_share_refund.py` was restored by sha256 (10f409b2…), OK (`step19_plant_AP-1.log`).
4. **Near run** (HEAD e879bbf2, under `flock`, h284_deps_env, `--parallel 4`): `billing audit AutoGrader.tests_migration_rollback_defaults` + G-1/static gave **Ran 3095, OK (skipped=2)**. Matched: OK was declared and the count is recorded here (`step19_near_e879bbf2.log.gz`).

**Next (plan):** step 8, the FR-B-08 forced-failure proofs F-1..F-3, with expectations first. Then step 9, the gate.

## 20. Plan step 8: FR-B-08 forced-failure proofs. Expectations committed before any code or run (2026-11-08)

SM rulings on the survey (2026-11-08): M-1 yes, M-2 (b), M-3 notes only, M-4 yes, M-5 yes, with F-1c on path 1 only. No production code changes, and no pre-existing test is edited.

### 20a. Plan corrections (the plan doc is not edited)

- **Line refs in §0.6, at 302d6ae12:**
  - `handle_checkout_completed`'s `@transaction.atomic` is at `billing/stripe_service.py:3044–3045`, not `:2907–2908`.
  - `_handle_license_overage_checkout_completed` is at `:3341`, not `:3204`.
  - `_grant_overage_offline` is at `billing/license_service.py:3169–3170`; `approve_overage_offline_request` is at `:3446–3447`; `_grant_overage_blocks` is at `:3089`. These three are unchanged.
- **Ledger type.** §0.6 says the helper writes "the CreditLedger PURCHASE row". The helper writes the type its caller passes:
  - path 1 (webhook) writes **PURCHASE** (`stripe_service.py:3502`);
  - path 2 (super-admin grant) writes **GRANT** (`license_service.py:3230`);
  - path 3 (offline approval) writes **PURCHASE** (`:3545`).
  The tests match the type per path.
- **"The intent or offline request is unchanged"** does not fit path 2, which has neither. Its record is the `MANUAL_OVERAGE_GRANT` LicenseBillingRecord, written *before* the grants (`:3214`). F-2 asserts that it does not survive (M-1).
- **The BillingTransaction clause.** In all three paths the BillingTransaction is written *after* the grants (`stripe_service.py:3560`, `license_service.py:3244`, `:3640`). A failure on the second teacher's ledger row happens before it exists. **So in F-1..F-3 "no BillingTransaction survives" proves nothing on its own.** The pairing is proven by F-1b..F-3b (M-2 b).
- **For the Feature Lead (plan §9 item 2):** FR-B-08 and NFR-CRD-05 say "dispersal debits the admin and credits the teacher". There is no admin balance. The admin's side is the payment (Stripe or offline) plus the LicenseBillingRecord and BillingTransaction written in the same transaction as the teachers' grants. FR-B-08 is met by allocate-at-purchase; F-1..F-3b below prove its atomicity.
- **For the Feature Lead, a note for B1's R-tests (SM, 2026-11-08):** on paths 1 and 3 the grant order is set by jsonb key order, because `allocations` is read back from a JSONField and jsonb sorts its keys by length and then by bytes, and teacher ids are UUIDs. So concurrent grants lock teachers' wallets in an order the code does not control. That may matter for deadlock ordering. Only a note; nothing is changed for it.

### 20b. The tests: `billing/tests/test_overage_grant_atomicity.py` (new, 7 tests)

**Class.** `TransactionTestCase` (M-4), so each path's own `@transaction.atomic` is the only transaction, and on_commit callbacks run only on a real commit.

**`serialized_rollback` is not needed:**
- The repo's 10 `RunPython` migrations are all backfills or repairs over existing rows, or a reverse guard (`billing/0074`). None seeds a row the tests read. Content types and permissions are rebuilt by `post_migrate` after each flush.
- The fixture creates everything it uses.
- The repo already has many `TransactionTestCase` files, none with `serialized_rollback`.

It runs on the local PG (18.6). It is **not** quoted as the PG16 CI job.

**Fixture.** As in the 3b tests: a school, an admin, a super admin, a plan (block 5000, price 299), a license with max_seats=3, and teachers A, B and C added through `add_teacher_to_license`. Each has 1 block, in dict order a, b, c.
- Path 1: a PENDING intent, with session `cs_ga` and `pi_ga`, `amount_total` 897, usd. It is driven through `StripeWebhookHandler.handle_checkout_completed(session)`, with `metadata` in the session, so that path's real atomic is in play.
- Path 2: `LicenseSubscriptionService._grant_overage_offline(license_sub, super_admin, 3, allocations)`.
- Path 3: the request is created through `request_overage_offline` with its email patched, then `approve_overage_offline_request(request, super_admin, 897, ...)`.

**How the failure is forced (approved).**
- **F-1, F-2, F-3, F-1c:** `CreditLedger.record` is wrapped. On its first call it goes through to the real `record`. On its second call it checks that the first teacher's OVERAGE bucket exists in the open transaction, notes the result, and raises `RuntimeError("forced F-n")`.
- **The b-tests: hook points surveyed per path (M-2 b).** Each one is a real call that comes after the BillingTransaction and stays inside the path's atomic:
  - **F-1b:** `billing.stripe_service.schedule_receipt_url_fill`, called at `stripe_service.py:3583`, right after the BillingTransaction at `:3560`. It is patched to check that the BillingTransaction exists in the open transaction, then raise.
  - **F-3b:** `LicenseSubscriptionService._notify_school_admin_offline_overage_approved`, called at `license_service.py:3680`, after the BillingTransaction (`:3640`). It is patched the same way. It is called before its own on_commit is registered, so no email is queued.
  - **F-2b: path 2 has no call after its BillingTransaction except `logger.info` (`:3261`) and `return`.** No seam is invented. I use the only honest point instead: `BillingTransactionService.record` is wrapped to go through to the real method, check that its row exists in the open transaction, and then raise. This is the end of the BillingTransaction write itself, still inside `_grant_overage_offline`'s atomic. **Flagged for the SM.** The alternative is patching the module logger, which I do not propose.

**Each test asserts:**
1. The forced `RuntimeError` reaches the caller, with its message. A swallowed failure fails the test.
2. The in-transaction check passed: the first teacher's bucket (F-1/2/3/1c) or the BillingTransaction (b-tests) existed before the rollback. This shows the test is not vacuous.
3. Afterward, against the counts taken before the call:
   - no OVERAGE bucket for any teacher;
   - the CreditLedger count is unchanged;
   - every teacher's `overage_blocks_used` is unchanged (0);
   - the BillingTransaction count is unchanged;
   - the LicenseBillingRecord count is unchanged. On path 3 this covers both APPROVED and OFFLINE_OVERAGE_REFUND_DUE; on path 2, MANUAL_OVERAGE_GRANT;
   - the `CREDIT_TRANSACTION` AuditEvent count is unchanged (M-5).
4. Path 1: the intent is still PENDING, with no `failure_reason` and no `fulfilled_allocations`. Path 3: the request is still PENDING, with `reviewed_by` None.
5. With `send_email_task.delay`, `stripe.Refund.create`, `stripe.Refund.list` and the refund task's `delay` all patched, none of them is called (M-5).

| Id | Path | Failure | Extra |
|---|---|---|---|
| F-1 | 1 webhook | 2nd `CreditLedger.record` (teacher B) | — |
| F-1b | 1 webhook | `schedule_receipt_url_fill` after the BillingTransaction | — |
| F-1c | 1 webhook | 2nd `CreditLedger.record`, with teacher C departed first, so the failure is on B, an active teacher | `refund_share` is on (switch True, PI, usd); asserts no `Refund.create`/`Refund.list` and no refund task |
| F-2 | 2 super-admin | 2nd `CreditLedger.record` (teacher B) | no MANUAL_OVERAGE_GRANT record (M-1) |
| F-2b | 2 super-admin | `BillingTransactionService.record`, call-through then raise | — |
| F-3 | 3 offline approval | 2nd `CreditLedger.record` (teacher B) | — |
| F-3b | 3 offline approval | `_notify_school_admin_offline_overage_approved` | — |

### 20c. Runs (h284_deps_env, `settings_worktree`)

**There is no red run.** This step changes no code, and the plan says the property holds today. The plants below take its place.

1. **First run:** the new file alone. Expected **Ran 7, OK**. A failure here would be a real FR-B-08 finding: STOP, reported to the SM, with no fix.
2. **Targeted set:** §18's set (`test_departed_teacher_refund`, `test_license_overage_offline`, `test_overage_never_expires`, `test_overage_refund_lifecycle`, `test_billing_transaction_refunds`, `AutoGrader.tests_migration_rollback_defaults`, plus G-1/static) and the new file. Expected **Ran 200 = 193 + 7, OK**.
3. **Plants**, each restored by sha256. Each is run on the new file plus G-1/static.
   - **PF-1:** remove the `@transaction.atomic` line above `def handle_checkout_completed` (`stripe_service.py:3044`). Expected **FAILED (errors=3)**: F-1, F-1b and F-1c each fail with `TransactionManagementError` at the intent `select_for_update()` (`:3368`). Everything else passes.
   - **PF-2:** remove the `@transaction.atomic` above `def _grant_overage_offline` (`license_service.py:3169`). Expected **FAILED (errors=2)**: F-2 and F-2b, `TransactionManagementError` at `:3189`.
   - **PF-3:** remove the `@transaction.atomic` above `def approve_overage_offline_request` (`:3446`). Expected **FAILED (errors=2)**: F-3 and F-3b, `TransactionManagementError` at `:3479`.
   - **What the plants prove, and what they don't.** Without its atomic, each path stops at its first row lock, before any write. So each plant is killed by the error type, not by a row that survived. They prove that each F test runs its path under that path's own transaction, and notices when it is gone. A plant that let a partial grant *commit* would need a per-teacher commit seam that the code does not have, so none is invented.
4. **Near run:** under `flock ~/.machine-fullsuite.lock`, `--parallel 4`: `billing audit AutoGrader.tests_migration_rollback_defaults` plus G-1/static. Expected **Ran 3102 = 3095 + 7, OK (skipped=2)**.

Any mismatch is a STOP, reported to the SM.

### 20d. Results, part 1 (2026-11-08)

1. **Run 1** (new file alone, WIP): Ran 7, OK. Matched (`step20_run1_WIP.log`).
2. **Run 2** (targeted set, d86e02fa): Ran 200, OK. Matched (`step20_targeted_d86e02fa.log`).
3. **Run 3 (plants): did not match. Prediction incomplete (jsonb key order).** Each run was Ran 65.
   - **PF-1:** `FAILED (failures=1, errors=3)`. The declared 3 errors happened: F-1, F-1b and F-1c, with `TransactionManagementError: select_for_update cannot be used outside of a transaction.`. There was also one undeclared FAIL, on **F-3**.
   - **PF-2:** `FAILED (failures=1, errors=2)`. The declared F-2 and F-2b errors happened, with the same error. There was also one undeclared FAIL, on **F-3**.
   - **PF-3:** `FAILED (failures=1, errors=2)`. The declared F-3 and F-3b errors happened, with the same error. There was also one undeclared FAIL, on **F-1**.
   - **Every plant was restored by sha256:** stripe_service d2fa42ab…, license_service b350e0d1…, both OK.
   - **The extra FAILs all fail at one line:** `{'first_bucket': False, 'ledger_calls': 2} != {'first_bucket': True, 'ledger_calls': 2}`.
   - **Cause: a defect in the test's non-vacuity check, not an FR-B-08 finding.** The wrapper checked for *teacher A's* bucket at the second ledger call, which assumed the grants ran in the order a, b, c. But paths 1 and 3 iterate `allocations` read back from a JSONField. jsonb does not keep key order: it sorts keys by length and then by bytes. `CustomUser.id` is a UUID (`users/models.py:100`), so the first teacher granted is whichever UUID sorts lowest, and that changes from test to test. Path 2 iterates the in-memory dict, so it is stable.
   - **What this means for runs 1 and 2:** they passed by luck.
   - **The rollback assertions:** in the three failing tests they were not reached, because the inside-check comes first. Wherever they were reached, they passed.
   - **What the survey missed:** the jsonb key order.
   - **The SM ruled (2026-11-08):** fix the check in the tests only, so that it checks the bucket of the teacher from the first record call. Commit the fix separately. Then re-run from run 1: 5× Ran 7 OK, then the targeted Ran 200, then PF-1/2/3 at errors 3/2/2 with no failures, then the near run at Ran 3102 OK (skipped=2).

### 20e. Results, part 2: the re-run after the fix (2026-11-08)

The test fix was committed separately at fca151db; no production file changed. Every re-run matched.

1. **New file 5×** (fca151db): each run gave Ran 7, OK. Matched (`step20_rerun1_x1..x5.log`).
2. **Targeted set** (fca151db): Ran 200, OK. Matched (`step20_targeted_fca151db.log`).
3. **Plants**, each run on the new file + G-1/static (Ran 65), with no failures:
   - **PF-1:** `FAILED (errors=3)`, F-1, F-1b and F-1c, `TransactionManagementError` at the intent lock.
   - **PF-2:** `FAILED (errors=2)`, F-2 and F-2b, at `license_service.py:3188`.
   - **PF-3:** `FAILED (errors=2)`, F-3 and F-3b, at `:3478`.
   - **Matched** (`step20_replant_PF-*.log`).
   - **Where the errors point.** The lines are the declared ones, one lower because the plant deletes the decorator line above them. For PF-1 the traceback names the call at `stripe_service.py:3055` and the end line, 3371, of the statement starting at :3367 (declared :3368).
   - **Restored.** Every plant was restored by sha256 OK.
   - **The manifest warning.** The one `Missing staticfiles manifest entry for 'admin/css/base.css'` warning in each log is also in the §19 baseline logs (green, near): it is not new.
   - **What the plants prove (as accepted, and no more).** Each path depends on its own atomic: without it, the path stops at its first row lock before any write. F-1..F-3b prove the rollback.
4. **Near run** (fca151db, under `flock`, h284_deps_env, `--parallel 4`): `billing audit AutoGrader.tests_migration_rollback_defaults` + G-1/static gave **Ran 3102, OK (skipped=2)**, as declared (`step20_near_fca151db.log.gz`).

**FR-B-08 / NFR-CRD-05: proven on all three grant paths.** A failure forced after the first teacher's bucket, and a failure forced after the BillingTransaction, each leave nothing behind for anyone. That covers buckets, ledger rows, block counts, billing records, BillingTransactions, credit audit events, the intent or request, emails and refunds.

**Next (plan):** step 9, the gate, with expectations first.

## 21. Plan step 9: the B1a gate. Expectations committed before any code or run (2026-11-08)

The SM's rulings on the survey (2026-11-08) are listed below. PG 16 CI (G) is with the owner, and this section claims nothing for it.

### 21a. Scope rulings and plan deviations, recorded

- **R-1 runs 32 threads, not 200 (C-4).** Each thread holds its own connection, and `max_connections` (300) is shared with other sessions on this machine. Accepted as a plan deviation. **For the Feature Lead:** the plan's 200 threads are not proven; only 32 are.
- **R-2 moves to B1b (C-5).** It needs batches and the queue.
- **M-B28's kill test is swapped to `test_holds.py` `SettleAndFailTests.test_settle_charges_from_the_hold_and_returns_the_leftover` (C-3).** P-5 needs promotion and stays with B1b.
- **Kill tests are mapped by exact test name, never by label (C-6).** `test_holds_schema.py` uses its own S-1…S-7 for schema tests, and those are not plan §6.3's S-ids.
- **Adversarial rows (§6.3) are N/A for B1a (D).** Each is re-opened in the B1b/B1c gate once its endpoint exists:
  - "a teacher reserving against another teacher's job id (404)": B1a has no job or reserve endpoint. The job API is step 6 (B1b), and holds are server-side only.
  - "a forged `reservation_claimed_at`": it is written only at `billing/holds.py:200` (`_reserve_locked`), and no request input reaches it.
  - "approving a request from another license" and "withdrawing someone else's request": these are CreditRequest rows, which belong to B1c.
- **Failure rows (E).**
  - "Redis down during promotion" moves to B1b, with promotion.
  - "Worker killed between reserve and settle" is evidenced by `test_holds_sweep.ItemSweepTests.test_a_started_item_past_the_window_is_failed_counted_and_released`.
  - "Provider times out inside a unit" gets a new test, T-1 (§21b).
- **Gate 6 (H).** Reserve/settle latency is in B1a (§21d). The estimate-endpoint half moves to B1b.
- **U-3 (C-2) is not barrier-based.** It is `test_holds.py` `SettleAndFailTests.test_settle_draws_only_the_items_own_hold`, run single-threaded: two items, then one settle. The barrier case is U-2, which is new.

### 21b. New tests: `billing/tests/test_holds_gate.py` (4, `TransactionTestCase`, flag on via `override_settings`)

- **R-4a: the §0.3 EPQ question, as characterization.** The test runs a raw conditional UPDATE with a bucket-sum subquery and no wallet lock:
  ```sql
  UPDATE billing_creditwallet SET reserved_credits = reserved_credits + 70
  WHERE id = %s AND reserved_credits + 70 <= (SELECT sum(total_credits - used_credits) FROM billing_creditbucket WHERE wallet_id = %s)
  ```
  The race:
  1. The wallet holds 100 credits.
  2. Thread A, inside `transaction.atomic()`, runs `wallet.consume_credits(60)`. This takes the wallet row lock, `select_for_update`, at models.py:~1007.
  3. A sets an Event, then sleeps 1 s before committing.
  4. Thread B waits for the Event, then runs the UPDATE. B blocks on A's row lock, and PostgreSQL re-checks after A commits.

  **Prediction on PG 18.6 READ COMMITTED: it over-reserves.** The EPQ re-check reads the new wallet row, but the subquery keeps the statement's snapshot, which still shows 100. The asserts:
  - B's rowcount is 1;
  - `reserved_credits` is 70;
  - `total_remaining_credits()` is 40.

  This proves the §0.3 rationale for `lock_wallet_first`. If PG behaves otherwise, the test fails. That is a STOP: the finding is recorded and M-B2's kill test is re-ruled. On PG 16 this is pending, the same as G.
- **R-4b: the production `reserve()` in the same race.** Thread B calls `holds.reserve(wallet, 70, job)`. Expected:
  - B blocks on `lock_wallet_first` until A commits;
  - it then reads 40 and is refused, `assertFalse(granted)`;
  - `reserved_credits` is 0;
  - `check_invariants(wallet)` is `[]`.

  This is M-B2's kill test.
- **U-2: barrier, two units on one free balance.** E = `estimate_for()` = 20003 (measured on this tree).
  - One teacher holds 1.5E.
  - Two threads pass a barrier, and each runs `metered_unit()` → `call_ai(teacher)`, with the provider stubbed at `ai_response(E)`.
  - After the pre-call check, both threads wait at a second barrier (timeout 30 s), whether they were refused or granted. Then each unit exits.
  - The asserts:
    - refused count is 1 (`InsufficientCreditsError`);
    - the provider is called once;
    - the sum of `CreditUsageLog.amount` is E, which is at most the starting 1.5E;
    - the sum of `AIJob.unbilled_credits` is 0;
    - `reserved_credits` is 0;
    - the invariants are `[]`.
- **T-1: a provider timeout inside a unit, on a two-item batch.**
  - Setup:
    - The teacher holds exactly 2E, so there is no free balance.
    - One `BATCH_GRADING` job has 2 items, each reserved E.
  - Item 1 runs `metered_unit(processing_task_id=item1)` → `call_ai`. The provider raises `openai.APITimeoutError` (openai 1.107.1; `execute_graded_task` re-raises it unchanged at `ai_processor/services.py:5686–5688`).
  - Asserts after item 1:
    - `APITimeoutError` is raised;
    - there are no `CreditUsageLog` rows;
    - item 1 has `hold_state` `RETURNED` and `released_credits` E;
    - the wallet's `reserved_credits` is E;
    - `item_hold_left(item2)` is E;
    - `total_remaining_credits()` is 2E.
  - Item 2 runs `metered_unit(processing_task_id=item2)` → `call_ai`, with the provider returning `ai_response(E)`. Asserts:
    - item 2 has `hold_drawn` E;
    - the job has `overrun_charged` 0, `unbilled_credits` 0 and `consumed_credits` E;
    - `total_remaining_credits()` is E;
    - `reserved_credits` is 0;
    - the invariants are `[]`.
  - **Wording vs code.** §6.3 says "hold kept for the next item". In the code, the failed item's own hold is *returned* (`fail_item`) and is not passed on. The next item runs on its own hold, which the failure does not touch. Because the wallet holds exactly 2E, item 2 could not run if item 1's failure had consumed anything, or had taken item 2's hold. T-1 asserts exactly that.

**First run: all four are expected to PASS against today's code.** This step changes no code, so there is no red run. The plants in §21c stand in for red (M-B2 → R-4b, M-B21 → U-2, TP-1 → T-1). A failing R-4b, U-2 or T-1 is a real finding: STOP, with no fix.

### 21c. The mutant battery: 19.5 mutants plus TP-1, fragments committed here

**Run set for every plant:** `billing.tests.test_holds billing.tests.test_holds_concurrency billing.tests.test_holds_overrun billing.tests.test_holds_settlement billing.tests.test_holds_sweep billing.tests.test_holds_unit billing.tests.test_holds_zero_credit billing.tests.test_holds_entry billing.tests.test_holds_single_call billing.tests.test_holds_gate` plus G-1/static. The settings are `settings_worktree` on h284_deps_env, with no `--parallel`. Each plant is restored by sha256 of `billing/holds.py`, recorded at the run.

**Kill criterion (proposed for SM approval at this checkpoint):**
- A mutant is **killed** when its named kill test fails, or errors, with the declared fragment in that test's output.
- Other tests that also fail are listed as **collateral**, as observed. They are not a mismatch.
- A named kill test that passes is a **survivor**: STOP.
- A named kill test that fails without its fragment is a **mismatch**: STOP.

Where the anchor text is unique, each mutant is one text replacement in `billing/holds.py`. The line numbers are hints at 4f5f9a85.

| # | Function: exact change | Kill test (exact name) | Fragment |
|---|---|---|---|
| M-B1 | `_reserve_locked` (:192): `pk=locked_wallet.pk, reserved_credits__lte=total - amount` → `pk=locked_wallet.pk` | `test_holds_concurrency.ReserveRaceTests.test_concurrent_reserves_grant_exactly_what_fits` | `32 != 7` |
| M-B2 | `reserve` (:217): `locked = lock_wallet_first(wallet)` → `locked = CreditWallet.objects.get(pk=wallet.pk)` | `test_holds_gate.…test_r4b_…` (R-4b) | `True is not false` |
| M-B3 | `settle` (:296): `chargeable = min(actual, left + _free(locked))` → `chargeable = min(actual, locked.total_remaining_credits())` | `test_holds_overrun.OverrunTests.test_an_overrun_never_takes_another_jobs_hold` | `(200, 0) != (100, 100)` |
| M-B6 | `settle` (:282): `item_id=key[0],` → `item_id=uuid.uuid4(),` | `test_holds_settlement.SettlementKeyTests.test_a_redelivered_settlement_settles_once` | `True is not false` |
| M-B7 | `finish_job` (:380): `AIJob.objects.filter(pk=job.pk, status__in=OPEN_STATUSES).update(` → `AIJob.objects.filter(pk=job.pk).update(` | `test_holds.SettleAndFailTests.test_finish_job_releases_the_remainder_exactly_once` | `0 is not None` |
| M-B11 | `sweep_stale_holds` candidates: `started_at__isnull=False,` + `started_at__lt=cutoff,` → `job__updated_at__lt=cutoff,`; and `_sweep_item`: `hold_state=HoldState.HELD, started_at__lt=cutoff` → `hold_state=HoldState.HELD, job__updated_at__lt=cutoff` | `test_holds_sweep.ItemSweepTests.test_an_unstarted_item_keeps_its_hold_however_old` | `1 != 0` |
| M-B13 | `reserve` (:213): remove its `assert_not_zero_credit(CREDIT_RESERVE)` line (the one before `if amount <= 0`) | `test_holds_zero_credit.ZeroCreditHoldTests.test_reserve_is_refused_inside_a_zero_credit_scope` | `ZeroCreditViolation not raised` |
| M-B14 | `settle`: `chargeable = min(actual, left + _free(locked))` → `chargeable = actual` | `test_holds_overrun.OverrunTests.test_beyond_hold_and_free_balance_the_excess_is_absorbed` | `InsufficientCreditsError` (ERROR from `consume_credits`) |
| M-B15 | `finish_job` (:400): `returned = max(_left(job_row, None), 0)` → `returned = max(job_row.reserved_credits - job_row.consumed_credits, 0)` | `test_holds_unit.UnitTests.test_two_calls_in_a_unit_settle_once` | `creditwallet_reserved_credits_nonneg` (ERROR, IntegrityError) |
| M-B16 | `Unit.before_call`: a `return` added as the last line of the `if root.job is None:` block (after `root.item = …attach_item.pk)`), so the first call opens its job without reserving | `test_holds_unit.UnitTests.test_a_call_the_unit_cannot_hold_is_refused_before_the_provider` | `InsufficientCreditsError not raised` |
| M-B18 | `Unit.before_call`: the `ensure_call_fits(root.job, estimate, item=root.item, tally=self.open_tally())` line → `pass` | same U-1 test | `InsufficientCreditsError not raised` |
| M-B21 | `ensure_call_fits`: the three lines `assert_not_zero_credit(CREDIT_RESERVE)` / `if not _reserve_locked(locked, shortfall, job_row, item_row):` / `raise _refusal(locked)` + `return shortfall` → `if shortfall > _free(locked):` / `raise _refusal(locked)` / `return 0` | `test_holds_gate.…test_u2_…` (U-2) | `0 != 1` (refused count) |
| M-B22 | `_sweep_item`: remove `status=BackgroundTaskStatus.FAILURE,` and `failed_items=F("failed_items") + 1,` | `test_holds_sweep.ItemSweepTests.test_a_started_item_past_the_window_is_failed_counted_and_released` | `'PENDING' != 'FAILURE'` |
| M-B23 | `settle`: the `key = ((item_row.id, item_row.retry_count) if item_row is not None else (job_row.id, 0))` block → `key = (job_row.id, 0)` | `test_holds_settlement.SettlementKeyTests.test_a_single_paper_settles_on_its_task_row_not_its_job` | `Tuples differ` |
| M-B25 | `_sweep_item`, the task-row update: `released_credits=F("released_credits") + amount,` (the line above `status=BackgroundTaskStatus.FAILURE,`) removed | the S-3 test above | `0 != 80` |
| M-B26 | candidates: `started_at__isnull=False,` + `started_at__lt=cutoff,` removed; `_sweep_item`: `, started_at__lt=cutoff` removed | `test_holds_sweep.ItemSweepTests.test_an_unstarted_item_keeps_its_hold_however_old` | `1 != 0` |
| M-B27 | `Unit.enter`: `raise _late(f"task {row.pk}: its credit hold was returned")` → `self._run_as_successor(open_retry_job(old_job, row, 0), row)` / `return`. This is the 3b plant Q-2, re-run under its M-B name | `test_holds_entry.EntryRuleTests.test_er3_a_late_grading_message_exits_before_any_call` | `CreditHoldReturnedError not raised` |
| M-B28 | `settle` (:306): `leftover = left - drawn` → `leftover = 0` | `test_holds.SettleAndFailTests.test_settle_charges_from_the_hold_and_returns_the_leftover` | `(60, 60, 0) != (60, 60, 40)` |
| M-B30 | `ensure_call_fits` (:229): `available = _left(job_row, item_row) - tally` → `available = _left(job_row, item_row)` | the U-1 test above | `InsufficientCreditsError not raised` |
| M-B31 (sweep half) | `_sweep_item` only: `, started_at__lt=cutoff` removed (the candidate query is unchanged) | `test_holds_concurrency.StartClaimSweepRaceTests.test_a_start_claim_and_the_sweep_have_exactly_one_winner` | `True == True` |
| TP-1 (T-1's plant) | `fail_item`: `amount = max(_left(job_row, item_row), 0)` → `amount = max(_left(job_row, None), 0)` | `test_holds_gate.…test_t1_…` (T-1) | `0 != 20003` |

**Notes on the predictions:**
- **M-B2 against R-1: expected to SURVIVE R-1.** Every thread reads the same total, and the wallet-row UPDATE's EPQ re-check on `reserved_credits` still caps the reserves at 7. That is why R-4b is its kill test.
- **M-B15: plan mismatch.** The plan names H-1 as the kill. H-1(a) has `consumed` (80) ≥ `reserved` (50), so `reserved − consumed` floors at 0, the same as the correct value. **So H-1 is expected NOT to kill it.** The unit test kills it, because 2E reserved against 400 consumed returns too much, and the wallet's `CHECK` fires.
- **M-B31's sweep half is non-deterministic.** It is killed only in a round where the start claim commits before the sweep's conditional UPDATE. Over 10 rounds that is expected, but not certain. A run in which every round goes to the sweep first is reported as a survivor, with no re-run without approval.
- **Collateral is expected for most mutants** (U-1, D-4, the single-call tests, and the E-refused tests), and is recorded as observed.
- **Already evidenced, not re-run:**
  - M-B4 and M-B5 (§13h, P-3/P-4);
  - M-B20 (§15h, DP-4);
  - M-B31's entry half (§14g, Q-5);
  - M-B33 (§18, RF-1);
  - F-1…F-3b (§20).

### 21d. Gate 6: `billing/tests/test_holds_gate6_latency.py` (1 test, opt-in: `RUN_LOAD_TESTS=1`, the repo convention, skipped otherwise)

The method follows plan §9 item 13:
- **Calls.** M = 500 billed single calls through `call_ai` (the real `execute_graded_task`), with the provider stubbed at `ai_response(150)` and a warm-up of 20. They run with the flag off and then the flag on, in one test on one database, local PG 18.6.
- **Wall time.** `time.perf_counter()` is taken around each call.
- **Reserve and settle, separately.** In the flag-on block, `holds.begin_single_call` (reserve) and `holds.finish_single_call` (settle plus release) are timed through `patch.object(…, wraps=…)`.
- **Reported:**
  - p50 and p95 of each block;
  - **added** = flag-on minus flag-off, at p50 and at p95;
  - reserve p50/p95 and settle p50/p95;
  - bytes per `AIJob` row and per `SettlementRecord` row (`pg_total_relation_size(table) / count`);
  - storage per month from `audit/volume.py`, the formula declared here: 30 gradings per active teacher per busy day (`PER_TEACHER_DAY`). Each grading writes one AIJob row, one SettlementRecord row and two hold audit rows (RESERVED, SETTLED), the audit rows at the measured bytes. That is × 22 school days, stated per 100 active teachers.
- **Pass (plan):** added p95 ≤ 5 ms per call. This is asserted. A fail is a STOP: the plan's fallback, a wallet-counter hold for SINGLE_CALL, is a design decision for the SM and the owner.
- The numbers are recorded in NOTES at the run.

### 21e. Runs, in the approved order (each a STOP on mismatch)

1. **New tests, first run:** `billing.tests.test_holds_gate`, Ran 4, OK, 5× in a row (the step 8 lesson). Then the Gate 6 test with `RUN_LOAD_TESTS=1`: Ran 1, OK, with its numbers recorded.
2. **Hold targeted set** (the run set above): OK, with Ran equal to the log's `Found N test(s)`.
3. **The mutant battery** (§21c): 20 plants. Each is killed with its fragment, plus M-B2 surviving R-1.
4. **Flag-on exploratory run (§3b; not the gate):**
   - Command: `BILLING_HOLDS_ENABLED=True flock ~/.machine-fullsuite.lock timeout -k 60 5400 python manage.py test billing ai_processor assignments students users --settings=settings_worktree --parallel 4 --noinput`, on h284_deps_env, with free -m ≥ 4000.
   - The flag goes through the environment variable only (`settings.py:697` reads `env.bool`). No settings file is created or edited.
   - **No expected result is declared; the result is recorded as it comes.** Checkpoint with the SM, then classify every failure. A real bug is a STOP, with no fix. An old-behavior assertion is declared by exact test id.
   - Then the declared flag-on gate run.
5. **Flag-off near run:** the five apps, flag off, under `flock`, `--parallel 4`. Expected: OK, with the count recorded against §3a's discovery. The billing near alone is expected at **Ran 3107 = 3102 + 5, OK (skipped=3)**: the Gate 6 test adds one skip.
6. **Gate summary** in NOTES: every B1a kill target with its evidence, every deviation, and every row deferred to B1b/B1c or the owner (PG 16).

### 21f. SM rulings on §21 (2026-11-08), committed before any code or run

- **GO on §21. The kill criterion is approved, with two additions:**
  - (a) Every mutant's collateral is listed by test name in the results table, not as a count.
  - (b) A mutant that breaks import or collection of a module, with an ERROR before any test runs, is a **STOP**, not a kill, even when the named test is among the errors.
- **R-4a is accepted as a characterization test.** If PG 18.6 does not over-reserve, STOP. PG 16 is pending with G: the owner is choosing between a manual workflow trigger (needs main) and a throwaway PR into dev.
- **Plan-wording amendment, §6.3 "Failure":** "the provider times out inside a unit (nothing charged, hold kept for the next item)". It should read: "nothing charged for the failed item; its hold is returned (`fail_item`), and the next item runs on its own hold, untouched". The code is right, so T-1 asserts the code. The SM folds this into the B1 plan amendment. No code change.
- **M-B15 is recorded as "prediction incomplete" in the plan mapping, not as an amended plan.** The plan's H-1 does not kill it. Its kill is `test_holds_unit.UnitTests.test_two_calls_in_a_unit_settle_once`, by IntegrityError on `creditwallet_reserved_credits_nonneg`.
- **M-B31's sweep half is accepted as written.** The claim-first count over the 10 rounds is recorded. At 0/10 it is reported as a nondeterministic survivor, with no re-run and no seed tricks without the SM.
- **M-B2 surviving R-1 is the expected survivor.** R-4b kills it.
- **Gate 6 is accepted** (opt-in, asserted p95 ≤ 5 ms added, a fail is a STOP).
- **Run order §21e is accepted.** **Every run goes under `flock ~/.machine-fullsuite.lock` with h284_deps_env.** There is a checkpoint after the flag-on exploratory run, before the declared gate run. The flag-off near prediction, billing Ran 3107 OK (skipped=3), stands; any other count is "prediction incomplete".

### 21g. Run 1 results, and Gate 6 measurement 1: FAIL (2026-11-08)

- **`test_holds_gate` 5×** (WIP): Ran 4, OK each time. Matched (`step21_gate_x1..x5.log`).
  - **R-4a confirms on PG 18.6** that the unlocked bucket-subquery UPDATE over-reserves: rowcount 1, reserved 70, remaining 40. §0.3's [UNVERIFIED] claim holds on 18.6; PG 16 is pending with G.
  - R-4b, U-2 and T-1 pass on today's code.
- **Committed at cfd598b6:**
  - At commit, black reformatted both new files. mypy also required the race mixin to become a `TransactionTestCase` base (`ConsumeRaceBase`), a structural change only.
  - The committed files were re-run: `test_holds_gate` + `test_holds_gate6_latency` without the env var gave Ran 5, OK (skipped=1) (`step21_gate_cfd598b6.log`).
- **Gate 6 measurement 1: FAIL.** This is the recorded result. It is not amended and not replaced (SM).
  - **Setup:** `RUN_LOAD_TESTS=1`, under `flock`, `step21_gate6_WIP.log`. The logic is the same as the committed file, before black. 500 calls after a 20-call warm-up, with the provider stubbed (150 tokens), on local PG 18.6, all inside one `TestCase`.

    | | p50 | p95 |
    |---|---|---|
    | Flag off | 41.344 ms | 55.698 ms |
    | Flag on | 60.164 ms | 71.850 ms |
    | **Added** | **18.819 ms** | **16.153 ms**, against ≤ 5 ms: **FAIL** |
    | Reserve (`begin_single_call`) | 8.562 ms | 11.106 ms |
    | Settle (`finish_single_call`, including `consume_credits` and `finish_job`) | 31.734 ms | 39.076 ms |

  - **Bytes per row** (`pg_total_relation_size` / rows, on small tables, so the index and fixed pages inflate them): AIJob 2363.1, SettlementRecord 504.1, AuditEvent 712.5.
  - **Storage:** 283.3 MB per month per 100 active teachers (30 gradings × 22 days × (AIJob + Settlement + 2 audit rows)).
  - **Conditions:** the load average was about 5.5 on 8 cores (the machine is shared; `flock` was held). The test ran inside one `TestCase` transaction, so every `atomic()` is a savepoint.
- **The SM's ruling (2026-11-08):** the FAIL stands. The decision goes to the owner after measurement 2 and the profile (§21h): build the plan's wallet-counter fallback for SINGLE_CALL, or change the 5 ms budget. **The step 9 summary and any "B1a done" claim are held until the owner decides.**

### 21h. Gate 6 investigation (c) and measurement 2 (b): declared before either runs (SM, 2026-11-08)

**(c) Profile, investigation only.** File: `billing/tests/test_holds_gate6_profile.py`, opt-in with `RUN_LOAD_TESTS=1`. There is no production code change, and the test asserts nothing about speed.
- **Calls.** 200 billed single calls after a 20-call warm-up, with the provider stubbed (150 tokens), flag on and then flag off, in one `TestCase`.
- **Timing.** Every SQL statement is timed through `connection.execute_wrapper`. Inside the TestCase each `atomic()` is a savepoint, so statements are grouped into the call's top-level transactions by SAVEPOINT / RELEASE depth.
- **Printed per mode:**
  - per transaction, in call order: the mean query count, the mean wall ms from SAVEPOINT to RELEASE, the first statement (which identifies it), and the statement shape with the most total time;
  - the transactions per call;
  - the queries and ms outside any transaction;
  - the call's mean wall, SQL and non-SQL ms.
- **The flag-off block shows the consume path's own cost**, so the true added cost is visible.
- **Prediction: none beyond the run completing (Ran 1, OK).** The numbers are recorded as they come.

**(b) Gate 6 measurement 2**, recorded beside measurement 1 and never replacing it. File: `billing/tests/test_holds_gate6_m2.py`, opt-in.
- **Method changes, declared:**
  - M = 2000 calls per mode;
  - off and on interleaved in blocks of 100 (off first), after a 20-call warm-up in each mode;
  - `os.getloadavg()` logged at the start and the end.
- **Unchanged from measurement 1:** the threshold (added p95 ≤ 5 ms, asserted), the provider stub, the single `TestCase`, and reserve/settle timed by wrapping. It is run once, under `flock`.
- **Prediction: added p95 > 5 ms, so it remains a FAIL** (FAILED (failures=1) at the threshold assert). That would be the declared outcome, not a new STOP. Any other outcome is recorded as it is and reported.

**The billing near-run count is updated because of these two files.** Both are opt-in and skip without the environment variable. The billing near is therefore expected at **Ran 3109 = 3102 + 4 gate + 3 opt-in, OK (skipped=5)**. This replaces §21e/§21f's 3107/skipped=3, declared here before the run.

**Order (SM):** (c), then (b), then the mutant battery, the targeted set, the flag-on exploratory run (checkpoint), and the flag-off near. The step 9 summary and any "B1a done" claim are held until the owner decides.

### 21i. Results: the Gate 6 profile (c) and measurement 2 (b) (2026-11-08, at 055748bd)

**(c) Profile.** Ran 1, OK, as declared (`step21_gate6_profile_055748bd.log`). The load average at the start was 2.83/4.18/4.97. 200 calls per mode after a 20-call warm-up; the times are means per call.

| Mode | Transaction (first statement) | Queries | Wall ms | Statement shape with the most time (ms/call) |
|---|---|---|---|---|
| **On** | tx1, reserve (`begin_single_call`: wallet SELECT … FOR UPDATE) | 5.0 | 6.032 | wallet `reserved_credits` UPDATE (0.606) |
| On | tx2, settle (`finish_single_call`: settle + `consume_credits` + `finish_job`, one atomic) | 34.0 | 39.678 | wallet SELECT, the lock (2.605) |
| On | tx3, the AICallRecord INSERT | 1.0 | 1.069 | the same (0.434) |
| On | outside any transaction | 7.0 | 4.372 | — |
| **Off** | tx1, consume (`consume_credits` path) | 19.0 | 25.957 | BetaProfile SELECT (1.681) |
| Off | tx2, the AICallRecord INSERT | 1.0 | 1.027 | the same (0.416) |
| Off | outside any transaction | 5.0 | 3.701 | — |

| Per call | On | Off | Added |
|---|---|---|---|
| Transactions | 3 | 2 | +1 (the reserve) |
| Queries | 47 | 25 | **+22** |
| Wall ms | 58.514 | 36.578 | **+21.9** |
| SQL ms (statement time) | 23.489 | 16.526 | **+7.0** |
| Non-SQL ms | 35.025 | 20.052 | **+15.0** |

**What the profile shows:**
- **About a third of the added time is SQL statement time (~7 ms), spread over ~22 extra queries** at roughly 0.3 ms each. No single query dominates: the largest is the wallet lock read, at 2.6 ms per call.
- **About two thirds (~15 ms) is outside the statements:**
  - Python and ORM work for those queries;
  - the audit emits (`CREDIT_HOLD_RESERVED` / `SETTLED`, each in its own nested `atomic()`);
  - the SAVEPOINT/RELEASE round trips. These are not counted in "SQL ms" by this recorder; that is a limitation of the method, noted here.
- **The settle transaction alone adds 15 queries and ~13.7 ms** over the flag-off consume transaction (34 vs 19 queries, 39.7 vs 26.0 ms). The reserve transaction adds ~6.0 ms.

**(b) Gate 6 measurement 2: FAIL, as declared** (`FAILED (failures=1)` at the threshold assert; `step21_gate6_m2_055748bd.log`). It is recorded beside measurement 1 and does not replace it.
- **Setup:** 2000 calls per mode, interleaved in blocks of 100. The load average was 2.77/3.99/4.85 at the start and 2.80/4.24/4.80 at the end.

  | | p50 | p95 |
  |---|---|---|
  | Flag off | 69.285 ms | 106.923 ms |
  | Flag on | 110.913 ms | 165.599 ms |
  | **Added** | **41.628 ms** | **58.677 ms**, against ≤ 5 ms: **FAIL** |
  | Reserve | 15.561 ms | 23.389 ms |
  | Settle | 56.501 ms | 83.888 ms |

- **An observation, not a re-measurement and not an amendment:** both modes ran far slower than in measurement 1 (flag off p50 69.3 vs 41.3 ms), and the added cost grew too.
  - A likely cause is the method itself: 4,040 calls inside one `TestCase` transaction. The wallet row is updated several times per flag-on call and the bucket rows on every call, and none of their dead row versions can be pruned until the transaction ends, so every read walks a growing version chain. The flag-on path touches the wallet row more, so it degrades faster.
  - This is unverified. It would explain why a longer run inside one transaction measures worse, not better. That is the SM's to weigh: a run outside a single transaction (`TransactionTestCase`) would be a further measurement, and needs a ruling.
- **Both measurements FAIL** the plan's 5 ms. The decision goes to the owner (SM): the wallet-counter fallback for SINGLE_CALL, or a changed budget.

### 21j. Battery and targeted-set results; measurement 3, the PG 16 expectations and the near count, declared before either runs (2026-11-08, at e5e9d5ad)

**The mutant battery** (`plant21.py`, run set as in §21c, `settings_worktree`, no `--parallel`, each plant under `flock`). `billing/holds.py` was restored after every plant and its sha256 was checked against base `d19bbf1ae0343ef477d5f96182558dbdaf14d451eaaca93dc7db32511f0a8550`: all 21 matched. There were no loader errors. Logs: `step21/step21_plant_<name>.log`; summaries `step21/step21_battery_summary.jsonl` (the first run) and `step21/step21_battery_summary_resume.jsonl` (the resume).

| Plant | Result (Ran 141) | Kill test | Killed with fragment | Collateral (by test name) |
|---|---|---|---|---|
| M-B1 | FAILED (failures=8, errors=3) | `ReserveRaceTests.test_concurrent_reserves_grant_exactly_what_fits` | yes | `ERROR test_holds_entry.EntryPointTests.test_e1_refused_before_the_provider`; `ERROR test_holds_entry.EntryPointTests.test_e5_refused_before_the_provider`; `ERROR test_holds_single_call.FlagOnSingleCallTests.test_a_call_that_cannot_be_held_is_refused_before_the_provider`; `FAIL test_holds.LateDoorTests.test_a_late_door_item_is_refused_when_its_new_job_cannot_reserve`; `FAIL test_holds.PreCallCheckTests.test_a_call_that_cannot_be_covered_is_refused`; `FAIL test_holds.PreCallCheckTests.test_the_check_counts_what_the_unit_already_used`; `FAIL test_holds.SpendableAndReserveTests.test_reserve_beyond_spendable_is_refused_and_changes_nothing`; `FAIL test_holds_gate.EpqReserveRaceTests.test_r4b_the_production_reserve_is_refused_after_the_consume`; `FAIL test_holds_gate.TwoUnitsOneBalanceTests.test_u2_two_units_on_one_free_balance_one_is_refused`; `FAIL test_holds_unit.UnitTests.test_a_call_the_unit_cannot_hold_is_refused_before_the_provider` |
| M-B2 | FAILED (failures=1) | `EpqReserveRaceTests.test_r4b_the_production_reserve_is_refused_after_the_consume` | yes | none |
| M-B3 | FAILED (failures=2) | `OverrunTests.test_an_overrun_never_takes_another_jobs_hold` | yes | `FAIL test_holds.SettleAndFailTests.test_settle_draws_only_the_items_own_hold` |
| M-B6 | FAILED (failures=14) | `SettlementKeyTests.test_a_redelivered_settlement_settles_once` | yes | `FAIL test_holds.LateDoorTests.test_already_settled_reads_the_items_settlement_key`; `FAIL test_holds_entry.EntryPointTests.test_e1_a_single_paper_grade_settles_once_on_its_task_row`; `FAIL test_holds_entry.EntryPointTests.test_e4_a_sync_upload_settles_once_on_its_own_job`; `FAIL test_holds_entry.EntryPointTests.test_e6_an_async_edit_settles_once_on_its_task_row`; `FAIL test_holds_entry.EntryRuleTests.test_er1_a_row_with_no_job_is_attached_to_a_new_single_item_job`; `FAIL test_holds_entry.EntryRuleTests.test_er2_a_held_row_on_a_shared_job_is_claimed_and_the_job_stays_open`; `FAIL test_holds_entry.EntryRuleTests.test_er4_a_row_already_settled_exits_before_any_call`; `FAIL test_holds_entry.EntryRuleTests.test_er5_a_retried_row_runs_under_a_new_single_item_job`; `FAIL test_holds_entry.EntryRuleTests.test_er6_a_late_door_row_runs_under_a_new_upload_job`; `FAIL test_holds_settlement.SettlementKeyTests.test_a_job_with_no_task_row_settles_on_the_job_id`; `FAIL test_holds_settlement.SettlementKeyTests.test_a_retry_runs_under_a_new_single_item_job_with_its_own_key`; `FAIL test_holds_settlement.SettlementKeyTests.test_a_single_paper_settles_on_its_task_row_not_its_job`; `FAIL test_holds_settlement.SettlementKeyTests.test_two_files_under_one_door_job_each_settle_once` |
| M-B7 | FAILED (failures=7) | `SettleAndFailTests.test_finish_job_releases_the_remainder_exactly_once` | yes | `FAIL test_holds.SettleAndFailTests.test_finish_job_refuses_a_job_already_terminal`; `FAIL test_holds_concurrency.SweepOverlapTests.test_two_overlapping_sweeps_release_a_stale_hold_once` |
| M-B11 | FAILED (failures=8, errors=1) | `ItemSweepTests.test_an_unstarted_item_keeps_its_hold_however_old` | yes | `ERROR test_holds_entry.EntryRuleTests.test_er6_a_late_door_row_runs_under_a_new_upload_job`; `FAIL test_holds_concurrency.SweepOverlapTests.test_two_overlapping_sweeps_release_a_stale_hold_once`; `FAIL test_holds_sweep.ItemSweepTests.test_a_started_item_past_the_window_is_failed_counted_and_released`; `FAIL test_holds_sweep.SweepTaskTests.test_the_sweep_task_sweeps_with_the_flag_on` |
| M-B13 | FAILED (failures=1) | `ZeroCreditHoldTests.test_reserve_is_refused_inside_a_zero_credit_scope` | yes | none |
| M-B14 | FAILED (failures=2, errors=1) | `OverrunTests.test_beyond_hold_and_free_balance_the_excess_is_absorbed` | yes | `FAIL test_holds.SettleAndFailTests.test_settle_draws_only_the_items_own_hold`; `FAIL test_holds_overrun.OverrunTests.test_an_overrun_never_takes_another_jobs_hold` |
| M-B15 | FAILED (errors=15) | `UnitTests.test_two_calls_in_a_unit_settle_once` | yes | `ERROR test_holds.InvariantTests.test_invariants_hold_across_reserve_settle_fail_and_finish`; `ERROR test_holds_entry.EntryPointTests.test_e4_a_sync_upload_settles_once_on_its_own_job`; `ERROR test_holds_entry.EntryPointTests.test_e5_a_sync_edit_settles_once_on_its_own_job`; `ERROR test_holds_entry.EntryPointTests.test_e8_a_failed_assignment_upload_charges_nothing`; `ERROR test_holds_entry.EntryRuleTests.test_er5_a_retried_row_runs_under_a_new_single_item_job`; `ERROR test_holds_entry.EntryRuleTests.test_er6_a_late_door_row_runs_under_a_new_upload_job`; `ERROR test_holds_single_call.FlagOnSingleCallTests.test_a_call_holds_settles_and_returns_the_rest`; `ERROR test_holds_single_call.FlagOnSingleCallTests.test_a_provider_failure_charges_nothing_and_returns_the_hold`; `ERROR test_holds_single_call.FlagOnSingleCallTests.test_a_refund_scope_still_refunds_a_settled_single_call`; `ERROR test_holds_unit.UnitTests.test_a_call_the_unit_cannot_hold_is_refused_before_the_provider`; `ERROR test_holds_unit.UnitTests.test_a_failed_inner_unit_is_not_charged_when_the_outer_goes_on`; `ERROR test_holds_unit.UnitTests.test_a_unit_that_raises_charges_nothing_and_returns_its_hold`; `ERROR test_holds_unit.UnitTests.test_an_inner_unit_passes_its_tally_to_the_outer_on_success`; `ERROR test_holds_unit.UnitTests.test_n1_a_dropped_nested_call_carries_the_root_id_and_adds_no_credits` |
| M-B16 | FAILED (failures=2, errors=2) | `UnitTests.test_a_call_the_unit_cannot_hold_is_refused_before_the_provider` | yes | `ERROR test_holds_entry.EntryPointTests.test_e1_refused_before_the_provider`; `ERROR test_holds_entry.EntryPointTests.test_e5_refused_before_the_provider`; `FAIL test_holds_gate.TwoUnitsOneBalanceTests.test_u2_two_units_on_one_free_balance_one_is_refused` |
| M-B18 | FAILED (failures=2, errors=2) | `UnitTests.test_a_call_the_unit_cannot_hold_is_refused_before_the_provider` | yes | `ERROR test_holds_entry.EntryPointTests.test_e1_refused_before_the_provider`; `ERROR test_holds_entry.EntryPointTests.test_e5_refused_before_the_provider`; `FAIL test_holds_gate.TwoUnitsOneBalanceTests.test_u2_two_units_on_one_free_balance_one_is_refused` |
| M-B21 | FAILED (failures=3) | `TwoUnitsOneBalanceTests.test_u2_two_units_on_one_free_balance_one_is_refused` | yes | `FAIL test_holds.PreCallCheckTests.test_a_call_beyond_the_hold_reserves_the_shortfall`; `FAIL test_holds_unit.UnitTests.test_a_call_the_unit_cannot_hold_is_refused_before_the_provider` |
| M-B22 | FAILED (failures=7) | `ItemSweepTests.test_a_started_item_past_the_window_is_failed_counted_and_released` | yes | `FAIL test_holds_concurrency.SweepOverlapTests.test_two_overlapping_sweeps_release_a_stale_hold_once`; `FAIL test_holds_entry.EntryRuleTests.test_er6_a_late_door_row_runs_under_a_new_upload_job` |
| M-B23 | FAILED (failures=9) | `SettlementKeyTests.test_a_single_paper_settles_on_its_task_row_not_its_job` | yes | `FAIL test_holds.LateDoorTests.test_already_settled_reads_the_items_settlement_key`; `FAIL test_holds_entry.EntryPointTests.test_e1_a_single_paper_grade_settles_once_on_its_task_row`; `FAIL test_holds_entry.EntryPointTests.test_e6_an_async_edit_settles_once_on_its_task_row`; `FAIL test_holds_entry.EntryRuleTests.test_er1_a_row_with_no_job_is_attached_to_a_new_single_item_job`; `FAIL test_holds_entry.EntryRuleTests.test_er2_a_held_row_on_a_shared_job_is_claimed_and_the_job_stays_open`; `FAIL test_holds_entry.EntryRuleTests.test_er4_a_row_already_settled_exits_before_any_call`; `FAIL test_holds_settlement.SettlementKeyTests.test_a_redelivered_settlement_settles_once`; `FAIL test_holds_settlement.SettlementKeyTests.test_two_files_under_one_door_job_each_settle_once` |
| M-B25 | FAILED (failures=7) | `ItemSweepTests.test_a_started_item_past_the_window_is_failed_counted_and_released` | yes | `FAIL test_holds_concurrency.SweepOverlapTests.test_two_overlapping_sweeps_release_a_stale_hold_once` |
| M-B26 | FAILED (failures=12) | `ItemSweepTests.test_an_unstarted_item_keeps_its_hold_however_old` | yes | `FAIL test_holds_concurrency.StartClaimSweepRaceTests.test_a_start_claim_and_the_sweep_have_exactly_one_winner`; `FAIL test_holds_sweep.ItemSweepTests.test_a_started_item_inside_the_window_keeps_its_hold` |
| M-B27 | FAILED (failures=1) | `EntryRuleTests.test_er3_a_late_grading_message_exits_before_any_call` | killed by its named test; **prediction incomplete** (fragment absent) | none |
| M-B28 | FAILED (failures=7) | `SettleAndFailTests.test_settle_charges_from_the_hold_and_returns_the_leftover` | yes | `FAIL test_holds.InvariantTests.test_invariants_hold_across_reserve_settle_fail_and_finish`; `FAIL test_holds.LateDoorTests.test_a_late_door_item_runs_under_a_new_upload_job`; `FAIL test_holds_entry.EntryPointTests.test_e1_a_single_paper_grade_settles_once_on_its_task_row`; `FAIL test_holds_entry.EntryRuleTests.test_er1_a_row_with_no_job_is_attached_to_a_new_single_item_job`; `FAIL test_holds_overrun.OverrunTests.test_with_no_free_balance_the_leftover_returns_in_the_settle`; `FAIL test_holds_settlement.SettlementKeyTests.test_a_retry_runs_under_a_new_single_item_job_with_its_own_key` |
| M-B30 | FAILED (failures=4) | `UnitTests.test_a_call_the_unit_cannot_hold_is_refused_before_the_provider` | yes | `FAIL test_holds.PreCallCheckTests.test_a_call_beyond_the_hold_reserves_the_shortfall`; `FAIL test_holds.PreCallCheckTests.test_a_call_that_cannot_be_covered_is_refused`; `FAIL test_holds.PreCallCheckTests.test_the_check_counts_what_the_unit_already_used` |
| M-B31s | FAILED (failures=1) | `StartClaimSweepRaceTests.test_a_start_claim_and_the_sweep_have_exactly_one_winner` | yes | none |
| TP-1 | FAILED (failures=1) | `ProviderTimeoutTests.test_t1_a_provider_timeout_charges_nothing_and_the_next_item_runs_on_its_hold` | yes | none |

**Recorded as they stand, not amended:**
- **M-B27: STOP, then SM ruling.** The run halted at M-B27. Its named test was the only failure, but it failed at `provider.assert_not_called()` ("Expected '_AIProcessor__ai_model' to not have been called. Called 1 times."), which comes before the declared fragment "CreditHoldReturnedError not raised". **SM ruling (2026-11-08):** "killed by its named test; prediction incomplete (fragment: the provider-not-called assertion fires first)". The fragment is not amended. The SM gave the go to resume with M-B28, M-B30, M-B31s and TP-1, fragments unchanged.
- **M-B2** was killed by R-4b, as predicted.
- **M-B15** was killed by the `creditwallet_reserved_credits_nonneg` IntegrityError (errors=15), as predicted. That is prediction incomplete against the plan's mapping (H-1).
- **M-B31s: 1 claim-first round out of 10** (round=2: `{'claim': True, 'sweep': 1}`, both won). The summary's `claim_first_rounds: 0` is a defect in the counting script: it searched the test-name part of the failure header, not the trailing `(round=N)`. The count above is read from the log. The mutant is killed, so the "nondeterministic survivor" branch does not apply.

**The hold targeted set at base** (run set above, e5e9d5ad): Ran 141, OK, equal to `Found 141 test(s)`. Matched (`step21/step21_targeted_e5e9d5ad.log`).

**Gate 6 measurement 3** (SM-approved), recorded beside measurements 1 and 2 and never replacing either. File: `billing/tests/test_holds_gate6_m3.py`, opt-in with `RUN_LOAD_TESTS=1`.
- **The one declared method change from measurement 2:** a `TransactionTestCase`, so every call commits its own transactions and no single test transaction holds dead row versions.
- **Unchanged from measurement 2:**
  - 2000 calls per mode, interleaved in blocks of 100 (off first), after a 20-call warm-up in each mode;
  - the load average logged at start and end;
  - the provider stub (150 tokens), and reserve/settle timed by wrapping;
  - the threshold, added p95 ≤ 5 ms, asserted.
- It is run once, under `flock`.
- **Prediction: added p95 > 5 ms, so it remains a FAIL** (FAILED (failures=1) at the threshold assert). This is the declared outcome, not a new STOP. The basis is the profile (§21i): +22 queries per call and ~7 ms of SQL statement time alone, which committing each call should not remove. **Also predicted:** both modes are faster than in measurement 2 (flag off p50 below 69.3 ms). Any other outcome is recorded as it is and reported.

**PG 16 CI expectations** (G, owner: a draft PR into dev, which the SM pushes after the local runs and the near are reviewed). `.github/workflows/tests.yml` runs the full suite on `postgres:16` with `--parallel 4`. The tests expected to carry the PG 16 result:
- `billing.tests.test_holds_gate`: R-4a, R-4b, U-2 (and T-1, which is not a barrier test);
- `billing.tests.test_holds_concurrency`: R-1, S-8, the sweep overlap;
- `billing.tests.test_overage_grant_atomicity` (step 8, single-threaded `TransactionTestCase`).

**R-4a's over-reserve is declared for PG 16 too** (rowcount 1, reserved 70, remaining 40). A different outcome there is "prediction incomplete" and a STOP. The `RUN_LOAD_TESTS` Gate 6 files (latency, profile, m2, m3) skip in CI.

**The billing near count, updated for the m3 file** (one more opt-in skip; SM accepted): **Ran 3110 = 3109 + 1, OK (skipped=6).** This replaces §21h's 3109/skipped=5.

### 21k. Results: Gate 6 measurement 3 (2026-11-08, at 2cab9872)

**FAIL, as declared** (`FAILED (failures=1)` at the threshold assert: 32.593 not ≤ 5.0; `step21/step21_gate6_m3_2cab9872.log`). Matched. It is recorded beside measurements 1 and 2 and does not replace either.
- **Setup:** `TransactionTestCase`, 2000 calls per mode, interleaved in blocks of 100. Ran 1 test in 185.8 s. The load average was 3.56/4.00/3.97 at the start and 3.04/3.62/3.83 at the end.

  | | p50 | p95 |
  |---|---|---|
  | Flag off | 32.945 ms | 44.078 ms |
  | Flag on | 55.658 ms | 76.670 ms |
  | **Added** | **22.713 ms** | **32.593 ms**, against ≤ 5 ms: **FAIL** |
  | Reserve | 9.511 ms | 13.480 ms |
  | Settle | 29.577 ms | 41.662 ms |

- **The second prediction matched too:** both modes ran faster than in measurement 2 (flag off p50 32.9 vs 69.3 ms; flag on p50 55.7 vs 110.9 ms).

| Gate 6 measurement | Method | Added p50 | Added p95 | Result |
|---|---|---|---|---|
| 1 (§21g) | `TestCase`, 500 calls per mode, off then on | 18.8 ms | 16.2 ms | FAIL |
| 2 (§21i) | `TestCase`, 2000 calls per mode, interleaved | 41.6 ms | 58.7 ms | FAIL |
| 3 (here) | `TransactionTestCase`, 2000 calls per mode, interleaved | 22.7 ms | 32.6 ms | FAIL |

- **All three measurements FAIL** the plan's 5 ms. Committing each call removed measurement 2's slowdown in both modes, but the added cost is still above 20 ms at p50. This is consistent with the profile's +22 queries per call. The decision stays with the owner (SM): the wallet-counter fallback for SINGLE_CALL, or a changed budget.

### 21l. Gate 6: the owner's decision (2026-11-08, relayed by the SM)

**ACCEPT.**
- The added-latency budget is raised from 5 ms to **40 ms at p95**.
- The wallet-counter fallback is **not** built.
- Trimming the ~22 extra queries per call (§21i) moves to **B1b**. It goes on the B1b plan list; nothing is built now.

**How it is recorded:**
- Measurements 1, 2 and 3 stay recorded as **FAIL against 5 ms** (§21g, §21i, §21k). Nothing is amended.
- **Gate 6 against the new budget is judged on measurement 3**, the declared realistic method (`TransactionTestCase`): added p95 32.593 ms ≤ 40 ms, **PASS**.
- The opt-in assertion in `test_holds_gate6_latency.py` and `test_holds_gate6_m3.py` changes from 5 to 40 ms in a separate commit that cites this decision. `test_holds_gate6_m2.py` keeps its 5 ms, as the record of measurement 2.
- The load tests are **not** re-run for this; they are opt-in and the measurement 3 numbers stand.

### 21m. The flag-on exploratory run: results and classification (2026-11-08, at b66a6d19)

**Run:** `BILLING_HOLDS_ENABLED=True` from the environment only, under `flock`, `--parallel 4`. Labels: `billing ai_processor assignments students users audit.tests_h273_query_filters audit.tests_route_coverage`. The students guards are inside `students`. There was no expectation (exploratory).

**Result:** Found 6866, Ran 6866, **FAILED (failures=37, errors=9, skipped=28)** (`step21/step21_flagon_explore_b66a6d19.log.gz`). Nothing was fixed.

**Classification** of all 46, by reading the failing assertion and the flag-on code path. Groups B2 to B4 were classified from representative tests and the shared code path; no test was re-run.

**A. Hold-path defect candidate (1).**
- `ai_processor.tests_call_record.TheRowsOutliveTheirRecords.test_deleting_the_teacher_or_the_assignment_leaves_the_rows`: ProtectedError at `teacher.delete()`.
  - `CreditWallet.user` is CASCADE and `AIJob.wallet` is PROTECT (models.py, "a wallet with open reservations must not vanish").
  - With the flag on, every billed call leaves an AIJob (SINGLE_CALL, or the unit's job). So a teacher who has ever made an AI call cannot be deleted. PROTECT applies to finished jobs, not only to open reservations.
  - This bears on teacher deletion (H-298) and on NFR-CMP-04 erasure.

**B. Tests that assume the flag is off (45).**
- **B1. The flag and migration guards, by design (6):**
  - `billing.tests.test_holds_schema`: `PlanAndSettingsTests.test_holds_flag_defaults_off` (reads the env-set value); `ReverseGuardTests.test_reverse_is_allowed_with_the_flag_off_and_no_hold_open` and `test_reverse_is_refused_while_a_wallet_holds_credits`.
  - `billing.tests.test_audit_identity_migration.AuditIdentityMigrationTests`: `test_backfill_is_idempotent`, `test_the_restore_brings_back_every_apps_leaf`, `test_user_id_survives_and_email_is_backfilled`.
  - All six hit 0074's "Refusing to reverse billing 0074: BILLING_HOLDS_ENABLED is on."
- **B2. The door launch is deferred to `on_commit` (plan M5, NOTES §15g), and under `TestCase` the callbacks never run, so the patched `launch_processing_task` is never called (26):**
  - `students.tests_h223_batch_runs_what_fits`: t1, t2, t3 and t5 on both `TheAnswerBatchRunsWhatFits` and `TheAssignmentUploadRunsWhatFits` (8).
  - `students.tests_h274_empty_student_answer_doors`: `TheAsyncStudentUploadKeepsS6` ×2, `AnEmptyFileInABatchFailsAsItsItem` ×2 (4).
  - `assignments.tests_h274_empty_upload_doors.TheAsyncTeacherUploadIsNeverHalfQueued.test_one_empty_file_fails_as_its_item_and_the_rest_are_queued` (1).
  - `students.tests_batch_item_results`: `TheSessionResultsShape` ×3 (the ValueError unpacks), `ATooLargeFileFailsAsItsItem` ×2, `ThirtyFilesTwelveFailures` ×1 (6).
  - `students.tests_upload_credit_door`: `StudentUploadDoorTest` ×3 (the task id is the door's uuid, not the mock's), `TeacherBatchDoorTest` ×1, `AssignmentUploadDoorTest` ×1 (5).
  - `students.tests_credits_mid_batch.AnUploadBatchThatRunsOutOfCredits.test_the_rest_stops_uncharged_and_resumes_by_re_upload` (1).
  - `billing.tests.test_h38_part2_removed_teacher_routes.ActiveTeacherCourseGuardRouteTests.test_batch_upload_answers_succeeds_for_active_teacher` (1).
- **B3. The charge-then-refund ledger shape.** With the flag on, `metered_unit` charges at `settle_root`, a failed call is never charged (decision 2), and `record_billing_task_id` is a no-op (no refund scope is open). So fakes that call `wallet.consume_credits` directly are never refunded, and tests that expect a charged-then-refunded usage row find none (12):
  - `billing.tests.test_grading_refund_scope.GradingRefundScopeTest`: `test_failure_during_persistence_refunds_the_ai_charge`, `test_malformed_summary_also_refunds` (2);
  - `students.tests_answers_unreadable.TheWritersRefuseWhatTheBuilderRefusedForThem.test_the_edits_refusal_refunds_the_charge`; `students.tests_async_edit_path.ExtractionTaskTest.test_failure_after_the_charge_refunds_it`; `students.tests_s6d_extraction_refund.ExtractionRefundTests.test_a_mid_chunk_failure_nets_the_ledger_to_zero` (3);
  - `ai_processor.tests_grading_pipeline`: `PerAttemptRefundSemanticsTest.test_failed_attempt_is_refunded_successful_attempt_is_kept`, and `FailedGradingRunRefundsEveryChargeTest.test_successful_run_keeps_every_charge` (its fake picks the batch by the usage-log count, which stays 0 until settle, hence GradingCompletenessError) (2);
  - `ai_processor.tests_h256_provider_timeout.TimedOutRunRefundsTest.test_a_run_that_times_out_refunds_what_it_charged` (1);
  - `assignments.tests_upload_batch_billing`: `FailedFilesAreRefundedTest` ×2, `LeftoverClaimsTest` ×1 (3);
  - `assignments.tests_upload_task_retry_policy.AssignmentUploadTaskBillingTest.test_an_unusable_response_is_refunded_and_the_file_released` (1).
- **B4. The patched balance (1):** `billing.tests_h281_individual_credit_wording.TheRaiseSitesTagTheRefusal.test_r8_execute_graded_task_nothing_left_individual`. It patches `total_remaining_credits` to 0 and the estimate to 0. The flag-on path skips the flag-off balance check, and `begin_single_call` reserves only when the estimate is above 0, so the call reaches the provider. A production estimate carries the flat 20,000, so an estimate of 0 is not reachable there. Noted for the SM's view.

### 21m addendum. SM rulings on §21m (2026-11-08), and the flag-on-day list

**SM rulings:**
- **A is fixed in B1a** (§21n), because it is a defect B1a introduces. The gate run comes after the fix, so `ai_processor.tests_call_record.TheRowsOutliveTheirRecords.test_deleting_the_teacher_or_the_assignment_leaves_the_rows` must **PASS** in it. It is not on the expected-failure list.
- **The 45 in B are not fixed in B1a.**
  - The declared flag-on gate run uses the same labels as the exploratory run.
  - The 45 are declared by full name as expected failures, each with its class, committed before the run.
  - A failure not on the list, or a listed test that passes, is a STOP.
- **B4 is accepted** as not reachable in production, because a real estimate carries the flat 20,000.

**Flag-on-day list**, for B1b or whatever turns the flag on. These 45 tests must be brought to the flag-on behavior before the flag goes on anywhere:
- **B1 (6):** decide the flag-off-only intent of `test_holds_schema`'s default and reverse tests, and run `test_audit_identity_migration` with the flag off (`override_settings`).
- **B2 (26):** make the tests `on_commit`-aware (`captureOnCommitCallbacks(execute=True)`, or `TransactionTestCase`), so the deferred door launch runs.
- **B3 (12):** move the tests to the hold ledger shape: a failed call has no usage row, a unit charges once at settle, and there is no refund row.
- **B4 (1):** `test_r8` reaches the provider with a mocked estimate of 0. Give it a flag-on refusal that is reachable, or mark it flag-off.

### 21n. Design note: AIJob.wallet PROTECT stops teacher deletion (A). For SM review before any code (2026-11-08)

**The defect:**
- `CreditWallet.user` is CASCADE and `AIJob.wallet` is PROTECT.
- With the flag on, every billed call leaves an AIJob, so deleting any teacher with AI history raises ProtectedError. That undoes H-298.
- `AIJob.requested_by` is CASCADE (03a: "an operational row, not history; the ledger keeps the money trail"). `BackgroundProcessingTask.job` and `AICallRecord` are untouched by this note.

**The design: SET_NULL, and open holds are released first.**
1. **The field:** `AIJob.wallet` becomes `on_delete=SET_NULL, null=True`. A finished job survives its wallet being deleted (job id, status, amounts), as the SM's shape asks.
   - For a teacher's own deletion, the jobs that teacher requested still go with `requested_by` (CASCADE, 03a, unchanged).
   - Jobs requested by someone else but billed to the wallet, such as a student's upload, survive with `wallet = NULL`.
   - The money trail stays where it is today: the ledger and the usage log.
2. **Open holds are released first, not refused.**
   - A `pre_delete` receiver on `CreditWallet` (billing, registered in `ready()`, the pattern of `billing/immutable.py`) moves every open job of that wallet (`OPEN_STATUSES`) to **CANCELLED** through the existing `finish_job`. That takes the wallet lock first, returns each held item's remainder, and emits `CREDIT_HOLD_RELEASED` with `reason_label="cancelled"`.
   - It runs inside the delete's own transaction, before the collector's SET_NULL updates and deletes. So no open job is ever left with `wallet = NULL`, and the sweep, `check_invariants` and settle never see an open job without a wallet.
   - It runs whatever the flag says, because jobs made while the flag was on can outlive a later flag-off. With no open job it is one query.
   - **Why release rather than block:**
     - The wallet and its buckets are deleted in the same transaction, so releasing a hold changes no balance anyone can spend.
     - Blocking would need a refusal sentence on the delete paths (admin and account deletion). That is new user-facing text for the owner, and it would make teacher deletion fail again while any upload is in flight.
     - With the flag off today, deleting a teacher mid-call is already allowed; the worker's later charge meets a missing wallet. Releasing keeps that behavior.
3. **An in-flight worker.** A unit or single call whose job was cancelled this way finds it terminal: its conditional settle and finish UPDATEs move nothing. This is the existing "late message" and idempotence path. The fix adds a test of that path and no new branch, unless the test shows one is needed. If it does, that is a STOP back to the SM.

**User-facing text: none needed.** Nothing is refused. The release is silent to users, and the audit row is internal (ids and amounts only).

**Migration (its own commit):** `billing/0079`, an AlterField making `wallet` nullable with SET_NULL.
- **Forward** only drops NOT NULL. `on_delete` is Django-side; the FK constraint is unchanged. `check_migration_safety.py` treats it as additive (a loosening).
- **Reverse:** a RunPython guard, in the style of 0074's, refuses the reverse while any `AIJob.wallet IS NULL` row exists ("Refusing to reverse billing 0079: N AIJob rows have no wallet."). That message is operator-facing, like 0074's. Otherwise the reverse restores NOT NULL.
- The reverse of 0079 does not pass through 0074, so 0074's guard is not involved unless a rollback goes on past 0079. In that case it applies as today.
- **Checks:** `python scripts/check_migration_safety.py --base origin/dev` and `AutoGrader.tests_migration_rollback_defaults` must pass, plus a migrate forward/back/forward on the worktree DB with the flag off.

**Tests first** (new file `billing/tests/test_holds_wallet_deletion.py`, flag on via `override_settings`):
- **D-1 (the red characterization):** a teacher with finished AI jobs (a SINGLE_CALL that was settled), and `teacher.delete()` succeeds. Red today: ProtectedError.
- **D-2:** a job requested by a student but billed to the teacher's wallet survives the teacher's deletion with `wallet_id = None`, and its status and amounts are unchanged.
- **D-3:** a teacher with an open hold (an UPLOAD job, one item HELD). The delete succeeds; the job is CANCELLED first, the item RETURNED, and one `CREDIT_HOLD_RELEASED` emitted.
- **D-4:** an in-flight unit whose job was cancelled by the delete; its settle moves nothing and raises nothing new.
- **D-5:** the 0079 reverse guard refuses while a NULL-wallet row exists, and allows the reverse when there is none.
- **Red:** D-1, D-2 and D-3 ERROR with ProtectedError. D-4 and D-5 are red as well, because there is no receiver and no 0079 (exact counts are declared at the red commit).
- **The plant, declared with its kill before the fix:** **PD-1** removes the receiver's call to `finish_job`. Kill: D-3 fails with the fragment `'CANCELLED' != ` (the job is left open).
- `tests_call_record`'s deletion test then passes flag on, and stays passing flag off.

### 21n addendum. SM approval with conditions C1 and C2, and one finding before the red commit (2026-11-08)

**The SM approved §21n (f09c4ee77):** SET_NULL, open holds released first, no new text, on conditions C1 and C2.

**C1. Rows written inside `pre_delete`.**

Django's Collector picks what it deletes or sets NULL before it sends `pre_delete`, so a row the receiver creates that points at the wallet or a bucket would fail at COMMIT. What `finish_job` (holds.py:372) writes, traced through `lock_wallet_first`, `_move_wallet`, `_left` and `_audit` → `audit.emitter.emit`:

| Write | Table | FK to the wallet or a bucket |
|---|---|---|
| `SELECT … FOR UPDATE`, then UPDATE `reserved_credits` | `CreditWallet` (the row being deleted) | the row itself: an update before its own delete, nothing new |
| UPDATE `status`, `released_credits`, ×2 | `AIJob` (existing rows, already collected: CASCADE via `requested_by`, or SET_NULL via `wallet`) | `wallet` → SET_NULL (0079); nothing new |
| UPDATE `released_credits`, `hold_state` | `BackgroundProcessingTask` (existing rows) | none (`job` → SET_NULL) |
| **INSERT** (`CREDIT_HOLD_RELEASED`, in emit's own nested `atomic()`) | `AuditEvent` | **none**: `actor_id`, `target_id` and `school_id` are plain UUID columns with no FK (audit/models.py:36–45) |

- **No row is created with an FK to the wallet, a bucket, or the user.** `finish_job` writes no CreditLedger, CreditUsageLog or CreditBucket row; the only insert is the FK-free AuditEvent. So there is **no C1 STOP**.
- **D-1 to D-4 run as `TransactionTestCase`**, so the delete really commits and a deferred FK violation would surface. D-5 is also in that file; it calls the guard function and needs no commit.
- **The H-299 trap.** H-299's fix (in-scope events are stored) is on staging, not on this branch.
  - User and wallet deletion is not a zero-credit operation, and `finish_job` calls no `assert_not_zero_credit`.
  - Even so, emit swallows its own failures (FR-A-11), so D-3 asserts after the commit that exactly one `CREDIT_HOLD_RELEASED` AuditEvent exists with `target_id = job.pk` and `reason_label = "cancelled"`.

**C2. QuerySet deletion and registration.**
- D-1 and D-3 each delete one teacher with `teacher.delete()` and one with `CustomUser.objects.filter(pk=…).delete()`.
- The receiver is connected in `BillingConfig.ready()` with `pre_delete.connect(…, sender=CreditWallet, dispatch_uid=…)`, not at import time.

**A finding: §21n's own STOP condition (point 3) is met before any code.**
- `settle` (269), `fail_item` (347), `finish_job` (379), `ensure_call_fits` (227) and `_sweep_item` (511) all call `lock_wallet_first(job.wallet)` before their conditional UPDATE.
- On a job the receiver cancelled whose wallet is then NULL (a job billed to the wallet but requested by someone else, such as a student's upload), each would raise `AttributeError: 'NoneType' object has no attribute 'pk'`. They would not move nothing.
- The successor paths (`_open_successor_job`, 436–459) read `old_job.wallet` too.
- So **D-4, as declared, needs a new branch.** Per §21n point 3, this goes to the SM before the red commit.
- **The proposal (no new text):**
  - In `settle`, `fail_item`, `finish_job` and `_sweep_item`: when `job.wallet_id is None`, return their existing "not open / nothing moved" result before locking. That is None, or 0 for `finish_job`'s return; the exact values are fixed in the test.
  - A NULL-wallet job is terminal by construction (the receiver cancels it first), so this is the same answer the conditional UPDATE would give.
  - In `ensure_call_fits`, and for a late door or retry on a NULL-wallet job: raise the existing refusal (`InsufficientCreditsError` with the existing `REFUSAL_MESSAGE`, "Refill your wallet to continue"). That stops the provider call, using an existing sentence.
  - D-4 then asserts each of these.
