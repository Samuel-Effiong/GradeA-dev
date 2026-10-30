# 08 — Epic B1 Implementation Plan (credit holds, estimate band, queued work, credit requests)

**Status:** revised after independent reviews r1–r3 and the r4 confirmation (session grade-automator-plus-79, 2026-10-30); all findings accepted by the SM. Nothing here is implemented.
**Companion to:** [05_epics_b_to_i_roadmap.md](05_epics_b_to_i_roadmap.md) (§2.4 B0/B1 split, §3.1 Epic B, §7 plan contents),
[01a_requirements_specification.md](01a_requirements_specification.md) (FR-B-02…09, FR-B-11; NFR-CRD-01…05; NFR-REL-01/02; NFR-TST-06/07/11; D-01, D-02, D-04, D-A8),
[03_architecture.md](03_architecture.md) (§2.2, Stage 2, X-3, X-8),
[03a_data_model.md](03a_data_model.md) (§2.2 `AIJob`, §3.4 `CreditRequest`, §4.5 `BackgroundProcessingTask`),
[06_epic_b0_implementation_plan.md](06_epic_b0_implementation_plan.md) (style; the zero-credit guards B1 sits beside).
**Size:** L. About 39 working days for one engineer after reviews r1–r4, including each slice's gate evidence, in three slices (§2.1). SM review, the staging proof and the Epic B strict run are not included.
**Code facts are from `task/epic-b0-r2` at d808a9ce** (beta f5d2d577 plus B0); the review r1–r4 revisions were checked at b8f12291 / 98c064b1 / 610f3252 / b42d47e7 (the same code). Anything I did not
confirm is marked **[UNVERIFIED]** and collected in §9.

**Owner decisions (2026-10-29), stated here as decided:**

1. **Out of credits partway through a batch:** the remainder is queued. It is a durable row, not a waiting task. The teacher is notified, and the queue expires after a warning. Papers already graded stay graded.
2. **A failed AI call is not charged.** A hold is taken when the job starts. Only successful papers are charged; the rest of the hold is released. There is no charge-then-refund.
3. **No admin pool and no dispersal step.** Overage is bought and allocated to named teachers at purchase. FR-B-08 counts as met by that; B1 only proves it is atomic.
4. **Credit requests are tracked.**
   - The teacher can see the status of their request.
   - The admin can see the request list and the counts of pending and answered requests.
   - Answering means approving (which feeds the existing overage purchase, allocated to that teacher) or declining.
5. **The estimate is advisory (D-A8).** It is a p50/p90 band, and the verdict is computed against p90 (X-3).

The project rule is that no full suite runs until all of Epic B is built. Slices exist for gating, not landing: Epic B (B0 + B1) lands as one batch with one strict full run.

### Review r1 (grade-automator-plus-79) dispositions

The plan at b8f12291 was reviewed independently against the code. Every finding was accepted by the SM (2026-10-30).

| Finding | Severity | Change | Sections |
|---|---|---|---|
| B1 Release leaks the hold after an overrun | Blocker | `AIJob.hold_drawn`; release = reserved − hold_drawn − released; invariant restated; `CHECK reserved_credits >= 0` | §3.1, §3.2, §4, §6 (H-1, M-B15) |
| B2 Billed paths with no job call the provider unheld | Blocker | One rule at `metered_unit` entry: no job supplied → per-item job, reserve p90 before the first call, or refuse; every named entry point listed with a test; AST dominance moved to `metered_unit` entry | §3.3, §3.4, §6 (E-1…E-7, M-B4, M-B16) |
| B3 Abandoned checkout leaves the request `APPROVED` forever | Blocker | `checkout.session.expired` handler plus a sweep of stale `APPROVED` requests; partial unique index widened to `PENDING`/`APPROVED` | §3.8, §4, §6 (C-1…C-3, M-B17) |
| M1 Stale-hold sweep frees live batch holds | Major | Sweep per item on item age; never on a batch job's `updated_at` | §3.2, §6 (S-1, M-B11) |
| M2 Job-level fencing token cannot fence duplicate items | Major | (`settled_at` superseded by review r2 M-a) Per-item settlement idempotency: `BackgroundProcessingTask.settled_at` and a unique settlement key `(item_id, retry_count)`; no job-level token rewrite on promotion | §3.2, §3.4, §6 (D-1, D-2, M-B6) |
| M3 No check before each call inside a unit | Major | Pre-call fit check under the wallet lock before every provider call in a unit | §3.3, §3.4, §6 (U-1, M-B18) |
| M4 Two promoters launch twice; expiry vs promotion leaks | Major | One transaction: conditional `QUEUED→RESERVED`, reserve, per-item `PENDING→LAUNCHED` claims, launch on commit | §3.7, §6 (P-1, P-2, M-B19) |
| M5 Celery launched before the hold commits | Major | Every launch (batch, promotion, door) is `transaction.on_commit` | §3.7, §6 (P-3, M-B20) |
| M6 Approve flow is not ordered | Major | Conditional `PENDING→APPROVED`; any purchase exception reverts to `PENDING`; intent linked afterwards | §3.8, §6 (C-4) |
| M7 Teacher departure orphans requests | Major | Terminal `CLOSED` state on departure; a FAILED-with-skipped-teacher intent closes the request | §3.8, §6 (C-5, C-6) |
| m1 Migration details | Minor | FK fields `db_index=False` plus a separate `AddIndexConcurrently`; missing migrations listed; reversal rule for `reserved_credits` | §4 |
| m2 Overrun charges credits that were never reserved | Minor | Kept, and recorded as a documented deviation from NFR-CRD-03 for the owner, bounded by M3's pre-call check | §3.5, §9 item 15 |
| m3 Grant hooks miss bucket-creating paths | Minor | Promotion hooked on `CreditLedger.record` credit entries via an on-commit Celery task; 5-minute sweep bound documented | §3.7 |
| m4 Retry in place shares the settlement `task_id` | Minor | Follows from the `(item_id, retry_count)` settlement key | §3.4 |
| ALSO Last-item transition race | — | Atomic `completed_items` counter UPDATE decides `QUEUED`/`SETTLED` exactly once | §3.7, §6 (P-4) |

### Review r2 (grade-automator-plus-79, re-review of 98c064b1) dispositions

0 blockers, 7 majors, 5 minors. All accepted by the SM (2026-10-30); the SM spot-checked M-d, M-f and M-g in code.

| Finding | Severity | Change | Sections |
|---|---|---|---|
| M-a Settlement key does not fence `SINGLE_ITEM` paths; `settled_at` blocks retries; one door job's files share a key | Major | Key on the `BackgroundProcessingTask` id whenever one exists; the job id only on synchronous E-4/E-5; `settled_at` dropped, the unique `(item_id, retry_count)` row is the fence | §3.2, §3.4, §4, §6 (D-1, D-3, D-4) |
| M-b Pre-call check reserves nothing; batch items share a job-wide hold | Major | A shortfall is reserved by extending the item's hold through `reserve`; the check runs against the item's own hold, not job `hold_left` | §3.4, §3.5, §3.7, §6 (U-2, U-3, M-B21) |
| M-c Per-item sweep frees holds before Celery redelivers; swept items never count; door files lost | Major | (re-reserve at entry superseded by review r3 R3-3) `BackgroundProcessingTask.hold_state` `HELD`/`RETURNED`; sweep only after `visibility_timeout` + time limit; the sweep fails the item (retryable) and counts it in the same transaction; a `RETURNED` item re-reserves at entry; door jobs re-reserve rather than refuse | §3.2, §3.4, §3.7, §4, §6 (S-2, S-3, S-4, M-B22) |
| M-d Offline rejection has no transition | Major | Hook in `reject_overage_offline_request` (`billing/license_service.py:3659`) moves `APPROVED → PENDING` | §3.8, §6 (C-7) |
| M-e Crash during approve leaves `APPROVED` with no link | Major | `credit_request_id` in the checkout metadata and on the offline request; the `GRANTED` hook matches on it; unlinked `APPROVED` requests swept after N minutes | §3.8, §6 (C-8) |
| M-f Automatic grading gets no `AIJob` or queue | Major | `auto_grade_due_assignment` (`assignments/tasks.py:1668`) and `_relaunch_failed_dispatches` (`:1616`) create a `BATCH_GRADING` job as in §3.7; E-3 citations corrected | §3.4, §3.7, §6 (B-1) |
| M-g FINANCIAL retag would rewrite append-only audit rows | Major | No retag migration; `sweep_audit_retention` excludes FINANCIAL actions from the `GENERAL` cutoff by action | §4, §9 item 11 |
| §9.15 inaccurate | — | `AIJob.overrun_charged` and metric `credit_overrun_charged`; deviation text rewritten truthfully | §3.1, §3.2, §3.5, §9 item 15 |
| TTL | — | Store `session.expires_at` on the intent; the stale sweep uses it | §3.8, §4 |
| m-a `refund_credits` uses `bulk_create`, not `record` | Minor | Promotion also hooked on `after_bulk_create` for credit entries | §3.7 |
| m-b Replay allowlist and endpoint subscription | Minor | `checkout.session.expired` considered for `billing/event_replay.py` with its idempotency proof; Stripe endpoint subscription recorded as an ops step in the staging proof | §3.8, §5 |
| m-c `SINGLE_ITEM` missing from §3.1; §0.5 stale; E-2 job | Minor | Fixed | §0.5, §3.1, §3.4 |
| m-d `NOT VALID` needs raw SQL | Minor | `CHECK` and FK via `RunSQL` inside `SeparateDatabaseAndState` | §4 |
| m-e License cancellation and expiry also end access | Minor | Close requests on those too; added to the B1c survey list | §3.8 |

### Review r3 (grade-automator-plus-79, final pass on 610f3252) dispositions

4 majors (all in the per-item hold accounting), 3 minors, and caveats on M-g. All accepted by the SM (2026-10-30).

| Finding | Severity | Change | Sections |
|---|---|---|---|
| R3-1 One name for two quantities | Major | `item_hold_avail` (with the tally, for the pre-call check) and `item_hold_left` (without, for settle); H-1 gains the free-balance-0 case | §3.2, §3.4, §6 (H-1, M-B30) |
| R3-2 Swept holds not recorded per item | Major | `BackgroundProcessingTask.released_credits`; the sweep, re-reserve and per-item invariant defined; S-3 asserts the wallet invariant | §3.2, §4, §6 (S-3, M-B25) |
| R3-3 Sweep and late messages | Major | Sweep only started items past start + visibility timeout + time limit; a job-level backstop fails unstarted items after X = 36 h; a late grading message for a `RETURNED`/`FAILED` item exits; a late door item runs under a new `UPLOAD` job | §3.2, §3.4, §3.7, §6 (S-5, S-6, S-7, M-B26, M-B27) |
| R3-4 Leftovers held until the job ends | Major | Each item's leftover (a failed item's whole hold) is returned in its own settle or fail transaction; the move to `QUEUED` asserts the job's hold left is 0 | §3.2, §3.4, §3.7, §6 (P-5, M-B28) |
| r3-a Intent has no request id before `Session.create` | Minor | `LicenseOveragePurchaseIntent.credit_request_id` written at intent creation; `initiate_overage_purchase` gains a `credit_request_id` parameter; the `GRANTED` hook accepts `PENDING` or `APPROVED` and fails any other intent linked to the request | §3.8, §4, §6 (C-9, M-B29) |
| r3-b A re-entered door item that cannot reserve | Minor | Explicit exemption: it runs (the file exists only in the message); cost to `overrun_charged`, then `unbilled_credits`; second deviation in §9 item 15, bounded to one file's extraction | §3.2, §3.4, §9 item 15, §6 (S-7) |
| r3-c S-1 clause | Minor | Fixed | §6 |
| M-g caveats | — | §9 item 11 states that history older than 365 days at deploy is already gone; FINANCIAL actions also excluded from the `STUDENT_RECORD` delete; rollback effect recorded; `deleted_financial` in the sweep metadata | §4, §9 item 11, §6 (R-5) |

### Review r4 (grade-automator-plus-79, confirmation of b42d47e7) dispositions

1 major and 6 minors; everything else verified fixed. The reviewer agreed with both judgement calls (r3-b exemption; re-reserve only through item retry). All accepted by the SM (2026-10-30), minors included.

| Finding | Severity | Change | Sections |
|---|---|---|---|
| R4-1 Read-then-exit at entry races the sweep | Major | One conditional UPDATE `SET started_at = now() WHERE id = :item AND hold_state = 'HELD'` at every unit entry; zero rows → exit as a late message. The sweep's UPDATE is conditional on `hold_state = 'HELD' AND started_at < cutoff`. A run that finds its item `RETURNED` at settlement does not increment `completed_items` | §3.2, §3.4, §6 (S-8, M-B31) |
| m-1 Pre-call refusal would hit the late door job | Minor | §3.4 step 3 exempts the late door job; S-7 asserts it | §3.4, §6 (S-7) |
| m-2 §3.5 bound wording | Minor | "at settlement" | §3.5 |
| m-3 A failed duplicate intent can still be paid | Minor (money) | `stripe.checkout.Session.expire` on commit when failing the other intent; `_handle_license_overage_checkout_completed` refuses to grant on a `FAILED` intent and refunds through the §9 item 3 refund path | §3.8, §6 (C-9, M-B32) |
| m-4 Backstop scope | Minor | `RESERVED` `UPLOAD` jobs included | §3.2 |
| m-5 Late door item may duplicate a settled file | Minor | Checks the settlement row `(item_id, retry_count)` first and exits if it exists | §3.2, §6 (S-9) |
| m-6 `QUEUED` check raises | Minor | Emits `hold_drift` and releases the remainder through the backstop instead of raising | §3.7 |
| Backstop note | — | X derived from the deployed `CELERY_WORKER_CONCURRENCY` (`scripts/start-worker.sh:9`, default 4) × worker services, confirmed in the B1b survey | §3.2 |

---

## 0. Corrections to the prior documents

Checked against d808a9ce while drafting.

### 0.1 Line references

`execute_graded_task` is at `ai_processor/services.py:5480`. Inside it:

| What | Where |
|---|---|
| B0's entry guard | `:5502` |
| The unmetered `SUPER_ADMIN` provider call | `:5612` |
| The estimate | `:5636–5639` |
| The check-then-execute balance test | `:5640–5649` |
| The metered provider call | `:5654` |
| The charge (`wallet.consume_credits`, inside `transaction.atomic`) | `:5671–5684` |
| `record_billing_task_id` | `:5700` |

The estimator is `estimate_messages_cost` (`:5721`) over `estimate_total_token` (`:5774`); the flat `+= 20000` is at `:5790`. 03 §2.2 and X-3 quote older numbers. The structure they describe is unchanged.

### 0.2 FR-B-07's "drive the balance negative" does not happen today. The real fault is different, and B1 still has to fix it

`CreditWallet.consume_credits` (`billing/models.py:875`) works like this:
- it locks the wallet row with `select_for_update`;
- it re-reads `total_remaining_credits()` under that lock;
- it raises `InsufficientCreditsError` if the amount does not fit, and it also raises if the locked bucket scan comes up short.

The balance therefore cannot go below zero. What does happen with two concurrent jobs on a near-zero balance:
1. Both pass the unlocked pre-check at `services.py:5640`.
2. Both call the provider, which is paid.
3. The second charge is refused after the provider has already been paid.

The result is unbilled provider cost, and the paper's work is thrown away by the raise. **Consequence for the acceptance test (QA-B-06):** "the balance is never negative" passes today and proves nothing. B1's concurrency test asserts three things instead:
- **no provider call without a hold;**
- the number of provider calls equals the number of holds granted;
- every held paper is charged, or absorbed under §3.5's recorded overrun.

### 0.3 "One conditional UPDATE" cannot compare against the balance as stored

03a §2.2 says the reservation is one conditional UPDATE "the same claim idiom as `_claim_submission_for_grading`". That idiom works because its WHERE clause reads only the row being updated (`students/services.py:309`). The spendable balance is not on one row: it is a sum over `CreditBucket` rows (`total_remaining_credits`, `billing/models.py:683`).

A conditional UPDATE whose WHERE contains a subquery over buckets is not safe under PostgreSQL READ COMMITTED. When the row lock is released, the re-check re-evaluates the qual against the updated target row, but not a fresh bucket sum **[UNVERIFIED; §6.3 test R-4 proves or refutes it on PG 16]**.

**Design (§3.2):** a short transaction does the reservation.
1. It takes `lock_wallet_first(wallet)` (`billing/locks.py:25`). This is the lock every bucket writer already takes first (H-222; for example `expire_bucket`, `billing/services.py:1151`, and `consume_credits`).
2. Under that lock it runs **one conditional UPDATE** on a new `CreditWallet.reserved_credits` column, and the row count is the result.
3. No network I/O happens inside the transaction.

This keeps the owner's "one conditional UPDATE, never `select_for_update` across a network call". The wallet lock is what makes it correct, because buckets cannot change while it is held. The conditional UPDATE is the fencing check.

### 0.4 Epic A already delivers half of FR-B-06

Epic A S7c and S7b, which are on beta, already provide:

| Feature | Where |
|---|---|
| `BatchUploadSession.credits_exhausted_at` | `students/models.py:~464` |
| Items not yet run refuse before any provider call, uncharged, with `INSUFFICIENT_CREDITS_MID_BATCH` | `students/task_tracking.py`, `ensure_batch_has_credits`, `:~564` |
| `stopped_at_item` derived from the failed items | `students/item_results.py`, `failure_summary`, `:~71` |
| Retry in place after a top-up, without re-upload | `students/item_retry.py` (S7b; `clear_batch_credit_stop`) |

**B1 builds on this; it does not duplicate it.**

03 §9.2's `GradingBatch` and `GradingBatchItem` tables are superseded:
- the job is 03a's `AIJob`;
- the per-item row is the existing `BackgroundProcessingTask`;
- `CreditReservation` is not a separate table: the hold is an `AIJob` row plus the wallet counter (§3.2).

What S7c does not do, and B1 adds:
- the remainder waits and **resumes on its own** when credits arrive;
- the waiting has a lifetime and notifications;
- a hold is taken before any work starts.

### 0.5 `BackgroundProcessingTask` already has three of 03a §4.5's four columns

`reason_code`, `retry_count` and `item_index` exist (`students/models.py:~590–606`), and so does `trace_id`, all from Epic A. **New in B1: `job` (FK → `AIJob`) and `hold_state` (`HELD`/`RETURNED`, null when the item has no hold)** (review r2 m-c, M-c).

### 0.6 FR-B-08: allocate-at-purchase is atomic. Its wording assumes an admin debit that does not exist

I checked all three paths that put overage credits into teachers' wallets:

| Path | Transaction boundary |
|---|---|
| Stripe checkout fulfilment `_handle_license_overage_checkout_completed` (`billing/stripe_service.py:3204`) | Runs inside `handle_checkout_completed` (`@transaction.atomic`, `:2907–2908`), as its docstring says |
| Super admin grant `_grant_overage_offline` (`billing/license_service.py:3169`) | `@transaction.atomic`, all-or-nothing |
| Offline-payment approval `approve_overage_offline_request` (`:3446–3447`) | `@transaction.atomic` |

All three call `_grant_overage_blocks` (`:3089`). That helper requires an enclosing transaction (`:3122`). For each teacher it creates the OVERAGE bucket, increments `overage_blocks_used` and writes the `CreditLedger` PURCHASE row. A failure anywhere rolls back every teacher's grant.

**So within the database there is no partial application.** NFR-CRD-01 holds: each grant lands in that teacher's own wallet, and no pooled balance exists.

**Where the requirement's wording does not fit:**
- FR-B-08 and NFR-CRD-05 say "dispersal debits the admin and credits the teacher". There is no admin balance to debit. The admin's side is the Stripe payment plus the `BillingTransaction` record written in the same transaction. B1 proves that pairing (§6.3 F-1 to F-3) and records the wording mismatch for the Feature Lead.
- **One case for the owner to see.** If a teacher leaves the license between checkout and payment, the webhook does the following:
  - grants to the teachers who are still active;
  - skips the teacher who left;
  - marks the intent `FAILED` with "Needs manual review (partial refund or manual grant)".

  This is deliberate in the code: the school has paid, and holding everyone else's credits back would be worse. It is not partial within a transaction, but the school paid for credits that one teacher did not receive, and the remedy is manual. **Flagged in §9; B1 changes nothing here.**

### 0.7 03a's `CreditRequest` is keyed to the wrong thing

03a §3.4 gives `CreditRequest` a `school` FK and says granting "creates the BE-B-08 dispersal". Under decision 3, an approval feeds `LicenseSubscriptionService.initiate_overage_purchase` (`license_service.py:2975`), and that is per **license**: allocations are keyed by teacher, and eligibility comes from `SchoolCreditAllocation` (`billing/models.py:2315`). The approval can also be asynchronous, because the Stripe route grants only on the webhook.

§3.8 therefore:
- keys the request to `license_subscription`;
- adds an `APPROVED` state (purchase started, credits not yet landed) between `PENDING` and `GRANTED`;
- links the request to the purchase intent.

### 0.8 Owner decision 2 replaces `billing_refund_scope` on the grading path

Today multi-call pipelines charge each call as it completes and refund the lot if the run fails (`billing/refunds.py`). The five sites are:
- `ai_processor/services.py:4635` (grading);
- `students/services.py:962`, `:1365`, `:1709`;
- `assignments/file_uploads.py:132`.

Decision 2 means no charge-then-refund, so on these five sites B1 replaces the refund scope with a **metered unit**: calls draw on the hold, and one settlement is written when the unit succeeds (§3.4).

Single-call paths already never charge a failed call, because the charge at `:5671` comes only after a successful response. They keep that behaviour, now with a hold in front.

The provider cost of failed calls is absorbed by the company. It is already visible in `AICallRecord.cost_usd` (`ai_processor/models.py:254`).

### 0.9 Answer to 05 §3.1's open question: where do uploaded files live while work is queued?

- **Grading items** read a stored `StudentSubmission`: the extracted answers are in the database. The item-retry module says so (`students/item_retry.py`: "grading reads its submission, which is stored"). **No file needs keeping**, so a queued grading item is just a `BackgroundProcessingTask` row that has not been launched.
- **Upload-extraction items** (answer files, assignment files) carry the file only inside the Celery message, as a base64 payload rebuilt by `AssignmentProcessingService.rebuild_uploaded_file` (`assignments/services.py:714`). Item retry never retries an upload in place, because "its file isn't kept" (F4).

**Recommendation:** B1 does not queue uploads. The upload door (`assignments/upload_door.py`, H-180/H-223) already refuses, before accepting, any file the balance certainly cannot cover. B1 turns the door's estimate into a **hold taken at the door**. That closes the window the door's docstring names: "another job can spend the balance after the door and before the gate (Epic B's hold closes it)".

A file that does not fit is refused at the door, as today (402, or "parked" for a batch). It is never accepted and then lost. Queuing uploads would mean storing student files while they wait, which is a new kind of retention of student data. If the owner wants it, it needs its own decision (§9).

Decision 1 ("a batch of papers") is about grading, which this covers.

---

## 1. Scope

### In scope

1. **Holds** (FR-B-07, NFR-CRD-03): every billed provider call runs under a hold. This covers batch jobs, single calls and the upload door.
2. **No charge for failures** (NFR-CRD-04, decision 2): a metered unit settles on success and releases on failure. It replaces the refund scope at the five sites in §0.8.
3. **`AIJob`** (03a §2.2) with its state machine, and `BackgroundProcessingTask.job`.
4. **Estimate band and verdict** (FR-B-03/04, X-3, D-A8), derived from `AICallRecord`, with an endpoint and a backtest.
5. **Queued work** (FR-B-05/06, X-8, decision 1): the remainder is durable and not launched. A sweep promotes it and expires it. There is a warning before expiry, and notifications.
6. **Threshold warnings** (FR-B-05).
7. **Actual cost and the estimate gap** (FR-B-09, NFR-OBS-03).
8. **`CreditRequest`** (FR-B-11, decision 4): the model, its states, API, audit and role rows.
9. **FR-B-08 / NFR-CRD-05 proof:** forced-failure tests on the three existing grant paths. No new code.
10. The mutation matrix, and concurrency evidence re-confirmed on PG 16.

### Not in scope (deliberately)

- Any admin wallet, pool or dispersal step (decision 3).
- Queuing upload-extraction items (§0.9).
- Holds for the `SUPER_ADMIN` branch (`:5612`). It is unmetered by design and stays so.
- Wrapping the AI-generation features of D, E and H. They create their own `AIJob` (`job_type` `LESSON_GENERATION` and so on) in their own epics. B1 supplies the API and covers today's callers through the single-call hold.
- Changing the price of anything, or how bucket consumption is ordered.
- (Moved into scope by owner ruling 2026-10-29: the departed-teacher share is refunded automatically; see §9 item 3.)

---

## 2. Build order

| # | Step | Days |
|---|---|---|
| 1 | Schema (additive):<br>• `AIJob` (with `hold_drawn`, `completed_items`)<br>• `CreditWallet.reserved_credits` (`CHECK >= 0`) and the two warning fields<br>• `BackgroundProcessingTask.job` (`db_index=False`, separate concurrent index), `hold_state`, `hold_credits`, `hold_drawn`, `released_credits`, and the settlement-key table<br>• `AIJob.overrun_charged`<br>• `SubscriptionPlan.queue_expiry_days` / `queue_expiry_warning_hours`<br>• the FINANCIAL retention class (§9 item 11)<br>Model tests, `makemigrations --check`, migration reversal test | 3 |
| 2 | Hold service `billing/holds.py`:<br>• reserve / settle / release on `hold_drawn`, with `overrun_charged`<br>• per-item settlement keyed on the task row<br>• the pre-call fit check against the item's own hold, reserving any shortfall<br>• `spendable_credits()`<br>• the per-item stale-hold sweep (started items only) with `hold_state` and `released_credits`, failing and counting the item<br>• leftover returned in each settle or fail transaction; late messages exit, late door items get a new job<br>• the per-item and wallet invariant checks (§3.2–3.5)<br>• the conditional start claim at entry and the conditional sweep; the late door item's settlement-row check<br>Unit tests | 7.5 |
| 3 | Wire the holds in, behind `BILLING_HOLDS_ENABLED`:<br>• single-call hold in `execute_graded_task`, replacing `:5640–5649`<br>• the metered unit at the five refund-scope sites, with the entry rule (§3.4) covering every entry point in §3.4's table<br>• the pre-call check on every call in a unit<br>• late messages: a `RETURNED`/`FAILED` grading item exits; a late door item runs under a new `UPLOAD` job<br>• hold at the upload door; every launch on commit | 3.5 |
| 3b | Departed-teacher share refund (§9 item 3) | 1.5 |
| 4 | Estimate band from `AICallRecord`, verdict, the `estimate` endpoint, the backtest command, the cold-start setting and the monthly drift task (§3.6, §9 item 6) | 3 |
| 5 | Batch jobs:<br>• `grade_batch_async` / grade-all create an `AIJob`<br>• reserve per paper<br>• queue the remainder<br>• atomic promotion and per-item launch claims, launched on commit<br>• atomic last-item transition<br>• expiry sweep with the editable lifetime<br>• promotion triggered from `CreditLedger.record` and `after_bulk_create` credit entries<br>• `auto_grade_due_assignment` and `_relaunch_failed_dispatches` create batch jobs<br>• the 36-hour unstarted-items backstop (including `RESERVED` `UPLOAD` jobs); the zero-hold check on the move to `QUEUED`, releasing any remainder<br>• notifications<br>• fit with S7c (§3.7) | 7 |
| 6 | Threshold warnings, actual cost in the job API, the gap metric (§3.9–3.10) | 1 |
| 7 | `CreditRequest`:<br>• API<br>• admin list with counts<br>• ordered approve (→ `initiate_overage_purchase`) and decline<br>• `checkout.session.expired` handler; `session_expires_at` stored on the intent and the stale-`APPROVED` sweep on it; the unlinked-`APPROVED` sweep<br>• `credit_request_id` on the intent (before `Session.create`), in checkout metadata and on the offline request; `GRANTED` matched on it, accepting `PENDING`/`APPROVED` and failing duplicate intents<br>• the offline-rejection hook<br>• `CLOSED` on teacher departure, license cancellation and expiry<br>• audit<br>• `Session.expire` for failed duplicate intents; the completed handler refuses `FAILED` intents and refunds<br>• role matrix rows (§3.8) | 7.5 |
| 8 | FR-B-08 forced-failure proofs on the three grant paths (§6.3 F-1 to F-3) | 0.5 |
| 9 | Gate evidence: mutation matrix, barrier concurrency (local, then the PG 16 CI job), adversarial and failure runs, NOTES | B1a 2, B1b 1.5, B1c 1, so 4.5 |
| | **Total** | **about 39 days** |

The order is a hard sequence from 1 to 3. Step 4 can start in parallel with step 3. Step 7 depends only on step 1 and the existing purchase service. Steps 3b and 8 are independent.

### 2.1 Slices (for gating; nothing lands before all of Epic B)

| Slice | Steps | Days (gate included) | What it proves |
|---|---|---|---|
| **B1a — holds** | 1 (all schema except `CreditRequest`), 2, 3, 3b, 8 | about **18** | FR-B-07, NFR-CRD-03/04, FR-B-08 atomicity, every billed entry point held |
| **B1b — jobs, estimate, queue** | 4, 5, 6 | about **12.5** | FR-B-03/04/05/06/09, X-3, X-8, NFR-REL-02 |
| **B1c — credit requests** | 1 (`CreditRequest` migration), 7 | about **8.5** | FR-B-11, decision 4 |

Total about **39 working days** for one engineer. The r4 confirmation added about 2.5 days: the conditional start claim and sweep (R4-1), the late door checks (m-1, m-5), the backstop and `QUEUED` changes (m-4, m-6), and the duplicate-intent expire and refund (m-3). Review r3 had added about 3.5 days: per-item release accounting and leftover return (R3-2, R3-4), the started-only sweep, job backstop and late-message handling (R3-3), and the request-id fixes (r3-a). Review r2 had added about 5.5 days: item-keyed settlement (M-a), reserving shortfalls against the item's own hold (M-b), the `hold_state` sweep and re-reserve (M-c), batch jobs for automatic grading (M-f), and the request-recovery paths (M-d, M-e, TTL). Review r1 had added about 6 days: the entry-point rule and its tests (B2), per-item settlement and sweep (M1, M2), the pre-call check (M3), atomic promotion and on-commit launches (M4, M5, ALSO), and the request-lifecycle fixes (B3, M6, M7). The owner rulings of 2026-10-29/30 had already added 3.5 days.

Each slice gets its own gate on the Epic B branch: NOTES first, red by call-site revert, exact Ran, mutants with fragments, a break run for edited pre-existing tests, and makemigrations. B1b and B1c can run in parallel after B1a if a second engineer is free.

---

## 3. Design

### 3.1 `AIJob` (03a §2.2, with these changes)

The columns are as 03a lists them, plus:

| Change | Why |
|---|---|
| `job_type` gains `SINGLE_CALL`, `SINGLE_ITEM` and `UPLOAD` | Every hold is a durable row (§3.2); `SINGLE_ITEM` is the per-paper job made by the §3.4 entry rule |
| `requested_by` may be a student | Student uploads bill the teacher, and `wallet` is the teacher's |
| New `unbilled_credits` (int, default 0) | Overrun absorbed at settlement (§3.5) |
| New `overrun_charged` (int, default 0) | The part of a charge billed from free balance beyond the hold: `chargeable − drawn` per settlement (review r2 §9.15) |
| New `hold_drawn` (int, default 0) | The amount actually taken off `CreditWallet.reserved_credits` by settlements. Release and the invariant use it, never `consumed_credits` (review r1 B1) |
| New `completed_items` (int, default 0) | Incremented by one conditional UPDATE per finished item; the UPDATE that reaches `total_items` decides the job's next state exactly once (review r1 ALSO) |
| New `warned_expiry_at` | Records that the pre-expiry warning was sent |
| New `held_per_item` | The per-paper hold size |
| `school_id` | Captured as 03a says |

**State machine** (03a's, made exact):

```
ESTIMATED ──reserve ok──► RESERVED ──first item claimed──► RUNNING
    │                        │                               ├──all items done──► SETTLED
    │                        │                               ├──hold or balance runs out with items left──► QUEUED_PENDING_CREDITS
    │                        │                               └──every item failed, nothing settled──► FAILED
    ├──teacher proceeds on INSUFFICIENT──► QUEUED_PENDING_CREDITS
    │                                         ├──sweep reserves for ≥1 item──► RESERVED
    │                                         └──now ≥ expires_at──► EXPIRED
    any non-terminal ──teacher cancels / cancel_requested_at──► CANCELLED (hold released)
```

**Changes from 03a:**
- `PARTIALLY_COMPLETE` is not stored. A queued job with `completed_items > 0` is the partial state, and the API reports it as such.
- For `SINGLE_CALL`, `SINGLE_ITEM` and `UPLOAD` jobs, only `RESERVED → RUNNING → SETTLED | FAILED` occurs.

Each transition is a conditional UPDATE on `(id, status)`, and its row count is the result (NFR-REL-01). Every terminal transition releases what is left of the hold in the same transaction as the status change.

### 3.2 Holds: `billing/holds.py`

**Spendable balance.** It is `total_remaining_credits() − wallet.reserved_credits`, floored at 0. Every place that today answers "can this teacher spend?" reads it when the flag is on. That covers the upload door, `can_user_access_ai`'s zero check, the estimate verdict and the warnings.

**Reserve** (`reserve(wallet, amount, job)`). One short `transaction.atomic()`:
1. `w = lock_wallet_first(wallet)`.
2. `avail = w.total_remaining_credits() − w.reserved_credits`.
3. **One conditional UPDATE:**
   `UPDATE billing_creditwallet SET reserved_credits = reserved_credits + :n WHERE id = :w AND reserved_credits + :n <= :total_remaining`.
   A row count of 1 means granted.
4. In the same transaction: `AIJob.reserved_credits += n`, and `reservation_claimed_at = now()` as a timestamp only. It is **not** a fencing token and is never rewritten to fence items (review r1 M2).

There is **no network I/O inside this transaction.** `n` can be smaller than asked for in batch mode: reserve whole papers that fit (§3.7).

Here `job_hold_left = job.reserved_credits − job.hold_drawn − job.released_credits`.

**Per-item hold accounting (review r2 M-b, M-c).** An item with a task row carries its own hold on `BackgroundProcessingTask`: `hold_credits` (`held_per_item` plus any shortfall extensions and re-reserves), `hold_drawn` (what its settlement took), `released_credits` (what was returned to the wallet counter: leftover at settle, the whole hold on failure, or by the sweep) and `hold_state`. Two derived amounts (review r3 R3-1):
- `item_hold_left = hold_credits − hold_drawn − released_credits`, used by settle;
- `item_hold_avail = item_hold_left − the open unit's tally`, used by the pre-call check.

**Per-item invariant (review r3 R3-2):** `hold_credits − hold_drawn − released_credits ≥ 0` for every item, and the job's `reserved_credits`, `hold_drawn` and `released_credits` equal the sums over its items, updated in the same transactions. For paths with no task row (E-4, E-5, `SINGLE_CALL`) the job has exactly one item, so the item's hold is the job's.

**Settle** (`settle(job, item, actual)`). One `transaction.atomic()`:
1. `lock_wallet_first`.
2. **Per-item idempotency (review r1 M2, m4; review r2 M-a).** Claim the settlement first by inserting the settlement row with the unique key `(item_id, retry_count)`. A redelivered or duplicate attempt hits the unique key and writes nothing. That row is the only fence; there is no `settled_at` column.
   - `item_id` is the **`BackgroundProcessingTask` id whenever one exists**: batch items, E-1/E-3 papers (grade_engine receives `processing_task_id`), item retries, and every file under an upload door job (each file has its own task row, so two files under one door job each settle once).
   - `retry_count` is the task row's own counter, so a retry in place (`students/item_retry.py:~143`, same row) gets a new key and is billed.
   - Only the synchronous paths with no task row (E-4, E-5) and `SINGLE_CALL` use the job id, with `retry_count = 0`.
3. `chargeable = min(actual, item_hold_left + (total_remaining − reserved_credits))`. A settlement never takes another job's or another item's hold.
4. `consume_credits(chargeable, …)`: the existing method, ledger and usage log unchanged, so B0's guards apply. `task_id` is the settlement key, so each attempt has its own ledger identity.
5. `drawn = min(item_hold_left, chargeable)`, where `item_hold_left` is the item's own hold (§3.4); `reserved_credits −= drawn`; `job.hold_drawn += drawn`.
6. `job.consumed_credits += chargeable`; `job.overrun_charged += chargeable − drawn` (billed from free balance beyond the hold; metric `credit_overrun_charged`); `job.unbilled_credits += actual − chargeable` (only what could not be billed; metric `credit_overrun`).
7. **Return the leftover in the same transaction (review r3 R3-4):** `leftover = item_hold_left` after step 5; `reserved_credits −= leftover`; `item.released_credits += leftover`, and the job's the same. A failed item returns its **whole** remaining hold in its fail transaction, with no charge. Freed credits are therefore spendable as soon as each paper finishes, not when the job ends.

**Release** (`release(job)`). With R3-4 every item returns its own leftover, so release is a backstop at terminal transitions. It returns `job.reserved_credits − job.hold_drawn − job.released_credits` (0 in normal operation) to the wallet counter, adds it to `released_credits`, and is idempotent through the status transition. The amount is never negative, because `hold_drawn ≤ reserved_credits` by construction (review r1 B1).

**Never negative.** `CreditWallet` gets `CHECK (reserved_credits >= 0)`. A bookkeeping error then fails loudly in the transaction instead of drifting.

**Stale-hold sweep (per item; review r1 M1, review r2 M-c).** Every 5 minutes, under `AutoGrader/beat_locks.py`'s decorator. Each held item carries `BackgroundProcessingTask.hold_state` (`HELD`/`RETURNED`).
- **Starting is a claim (review r4 R4-1):** every unit entry runs one conditional UPDATE, `SET started_at = now() WHERE id = :item AND hold_state = 'HELD'`. Zero rows means the hold was returned: the run exits as a late message (below). This replaces any read-then-exit check, so a run and the sweep cannot both win. `started_at` is otherwise set once (`students/task_tracking.py:178`; `claim_processing_task_start` at `:190` refreshes it only on the extraction path, and the B1a survey confirms nothing else moves it while held).
- **Which items (review r3 R3-3a):** only items that have **started** (`started_at` set), and only once `started_at + visibility_timeout` (3600 s, `AutoGrader/settings.py:575`) `+ GRADING_TASK_TIME_LIMIT_SECONDS` (25 min, `students/services.py:297`) `+` a margin has passed with no settlement row. Before that, Celery may still redeliver it (`CELERY_TASK_ACKS_LATE`, `:899`). Launched-but-unstarted items are **never** swept one by one: a broker backlog is normal. The 30-minute grading claim (`students/services.py:306`) is not the sweep's clock.
- **What, in one transaction (review r3 R3-2, review r4 R4-1):** conditional `hold_state HELD → RETURNED` with `WHERE hold_state = 'HELD' AND started_at < :cutoff`, so a run that re-claimed the item since the sweep read it is not swept; `amount = hold_credits − hold_drawn − released_credits`; `reserved_credits −= amount`; `item.released_credits += amount` and the job's the same; mark the item `FAILED` with a retryable reason; increment `completed_items`. The job can therefore always reach `QUEUED`/`SETTLED` even if the message never returns.
- **Job-level backstop for unstarted items (review r3 R3-3a, review r4 m-4):** when no item of a `RESERVED`/`RUNNING` batch job, or of a `RESERVED` `UPLOAD` door job, has started or settled for **X = 36 hours**, one transaction fails all its unstarted items (retryable), returns their holds as above, and counts them. Justification: the stall it must not mistake for an outage is this job's items waiting behind other work. The worst case the plan sizes for is a 300-paper batch ahead of it (the Gate 6 batch; the code has no batch cap, confirmed in the B1b survey) with every paper hitting the 25-minute time limit on the default 4 worker slots (`scripts/start-worker.sh:9`): 300 × 25 min / 4 ≈ 31 h, plus margin = 36 h. X is a `BillingSettings` field (`unstarted_backstop_hours`, default 36). Concurrency is the deployed `CELERY_WORKER_CONCURRENCY` (`scripts/start-worker.sh:9`, default 4) × the number of worker services; the B1b survey confirms both and the largest batch, and sets X = largest batch × 25 min / total concurrency + 5 h (review r4 backstop note).
- **A late message (review r3 R3-3b, R3-3c):**
  - a **grading** item whose `hold_state` is `RETURNED`, or which is already `FAILED`, **exits without running** at unit entry. The paper stays retryable through item retry (E-2), which re-reserves on the same task row (`hold_credits += n`, `hold_state → HELD`);
  - a **door `UPLOAD`** item whose hold was returned first checks for a settlement row with its `(item_id, retry_count)`: if one exists, the file was already processed and settled, and it exits (review r4 m-5). Otherwise it runs under a **new `UPLOAD` job** with its own hold and its own release, never against the old job. If that new hold cannot be reserved, the file still runs: the explicit exemption in §3.5 and §9 item 15 (review r3 r3-b), because the file exists only in the message.
- **At settlement**, a run that finds its item `RETURNED` (swept after it started, which R4-1's conditional sweep makes possible only past the cutoff) settles its charge under the overrun rules but **does not increment `completed_items`**: the sweep already counted the item (review r4 R4-1).
- `SINGLE_CALL` jobs (no broker message) are released after `GRADING_TASK_TIME_LIMIT_SECONDS` plus a margin with no settlement.

**Invariant.** `wallet.reserved_credits = Σ(open jobs' reserved_credits − hold_drawn − released_credits)`, together with the per-item invariant above. It is checked:
- by a test after every concurrency run, and after the overrun-then-release test H-1;
- daily, by a beat task that emits `credit_ledger_anomaly{kind="hold_drift"}` on any mismatch. That task reports and does not repair.

### 3.3 `execute_graded_task` with holds (flag on)

The order becomes:
1. B0 guard.
2. `prompt_version`.
3. Cancellation.
4. Access and billing target, unchanged.
5. **Hold.**
6. Provider call.
7. **Accumulate or settle.**

The hold works like this:
- **If a metered unit is open** (a ContextVar in `billing/holds.py`, the same idiom as `billing/refunds.py` and `billing/zero_credit.py`), the call draws on that unit's job. There is no new reservation per call. **Before the call**, the pre-call fit check runs (§3.4); after the response the actual usage is **added to the unit's tally**. Nothing is charged until the unit ends.
- **Otherwise**, the call opens a `SINGLE_CALL` job:
  1. reserve the existing per-call estimate;
  2. if the reserve is refused → `InsufficientCreditsError`, **before the provider call**. This replaces `:5640–5649`;
  3. after the response, settle `actual` and release the rest. On an exception, release only.

`call_record.note_estimate` and `note_billed` are unchanged. `record_billing_task_id` stays for callers still under a refund scope while the flag is off.

**Chokepoint.** The provider is still reached only through `__ai_model` from `execute_graded_task`; B0's AST test is unchanged and must stay green. B1 adds two assertions (§6.2 M-B4, M-B16):
- inside `execute_graded_task`, the metered `__ai_model` call is dominated by either the single-call reserve or the unit's pre-call check;
- `metered_unit`'s entry always ends in a held job: a runtime test per entry point in §3.4 (E-1…E-7) asserts that the provider mock is never called while the open unit's job has `hold_left = 0` and no fit was granted. An AST check over one function cannot see callers, so the entry-point tests carry this.

### 3.4 The metered unit, replacing `billing_refund_scope` (decision 2)

`metered_unit(job, item)` is a context manager:
- **On entry: the entry rule (review r1 B2).** If `job` is supplied, it must be an open job with a hold for this item. Entry first runs the R4-1 start claim (§3.2). If it matches zero rows (`hold_state` is `RETURNED`) or the item is `FAILED`, a grading unit **exits without running** (the paper stays retryable), and a door `UPLOAD` item opens a **new `UPLOAD` job** with its own hold (review r3 R3-3b/c, superseding r2 M-c's re-reserve at entry). If **no job is supplied**, the unit creates a per-item `AIJob` (`SINGLE_ITEM`, `total_items = 1`) and reserves the paper's p90 (§3.6) under the wallet lock **before the first provider call**. If the reserve does not fit, it raises `InsufficientCreditsError` with reason `INSUFFICIENT_CREDITS` before any provider call. Batch paths supply their job and queue as in §3.7; every other path goes through this rule. Then it opens the tally.
- **Before each provider call (review r1 M3, review r2 M-b):** the pre-call fit check, against **the item's own hold**, not the job's: `item_hold_avail = hold_credits − hold_drawn − released_credits − this unit's tally` (review r3 R3-1). Under the wallet lock:
  1. if `this_call_estimate ≤ item_hold_avail`, proceed;
  2. otherwise **reserve the shortfall** (`this_call_estimate − item_hold_avail`) through `reserve()`, which extends this item's hold (recorded as an extension on the item); proceed if granted;
  3. if the shortfall cannot be reserved, raise `InsufficientCreditsError` before the call. **Exception:** a late door job (the new `UPLOAD` job of §3.2) never refuses; it proceeds under the r3-b exemption (review r4 m-1).

  Free balance is therefore never shared by two units: whatever a unit may spend beyond its hold is reserved first. Two items in one batch cannot draw each other's holds, so a held item never finds its credits gone (§3.7 Fit with S7c). What can still be billed beyond the hold is only the difference between a call's estimate and its actual cost (§3.5).
- **On a normal exit**, it settles the tally once (§3.2 settle), with one `CreditUsageLog`/`CreditLedger` set per bucket as today. The settlement key is `(item_id, retry_count)` with `item_id` the task row id when one exists (§3.2), so a retry in place (`students/item_retry.py:~143` reuses the row, `retry_count` moves on) gets its own ledger identity, and a later manual `refund_credits` targets one attempt, not all of them (review r1 m4, review r2 M-a).
- **On an exception**, it charges nothing and returns the item's whole remaining hold in the fail transaction (review r3 R3-4).

**Entry points that reach a unit site** (all go through the entry rule; each has a test E-n that asserts the provider mock is not called when the reserve is refused, and that a granted reserve is drawn and the rest released):

| Id | Entry point | Unit site |
|---|---|---|
| E-1 | Single-paper grade: `grade_engine_async` launched from `students/views.py:~964` and `assignments/views.py:~1785` | `ai_processor/services.py:4635` |
| E-2 | Item retry, `students/item_retry.py:~151`; also the manual resume of an EXPIRED batch (§3.7). Item retry enters **with no job** and gets a new `SINGLE_ITEM` job, because entering with the EXPIRED job is refused (review r2 m-c) | as E-1 |
| E-3 | Automatic grading of a whole assignment: `auto_grade_due_assignment` (`assignments/tasks.py:1668`, call at `:1733`) and `_relaunch_failed_dispatches` (`:1616`, call at `:1662`). These are **batch paths**: they create a `BATCH_GRADING` job as in §3.7 and supply it (review r2 M-f). Only a single-paper `only_if_ungraded` launch outside them uses the per-item rule | as E-1 |
| E-4 | Synchronous student upload, `students/views.py:~610` → `upload_answers_engine` | `students/services.py:962` / `:1365` |
| E-5 | Synchronous raw-text edit, `students/views.py:~794` → `update_submission_from_raw_text` | `students/services.py:1709` |
| E-6 | Async raw-text edit, `assignments/tasks.py:~450` → `update_submission_from_raw_text` | `students/services.py:1709` |
| E-7 | The three upload-async routes behind the upload door (`assignments/upload_door.py:1-5`) | `assignments/file_uploads.py:132`; the door's hold is the supplied job; a late door item runs under a new `UPLOAD` job (§3.2) |

The B1a NOTES survey re-derives this table from the code before any test is written. Any further entry point found is added, with a test.

It replaces `billing_refund_scope` at the five sites in §0.8 when the flag is on. With the flag off, the old scope runs unchanged. Nesting follows the refund scope's rule: an inner unit's tally passes to the outer unit on success.

**Second opinion.** Today `:4574` catches `InsufficientCreditsError` and flags the paper. Under a unit, the second grader draws on the same paper's hold. If the paper's hold and the free balance cannot cover the second grader's estimate, the **pre-call fit check** raises the same exception before the call, and the same flag is set. The behaviour is unchanged, and now it has a mechanism.

### 3.5 Overrun

The per-paper hold is the paper's p90 (§3.6). If the actual usage exceeds hold plus free balance:
- the excess is **absorbed and recorded** in `AIJob.unbilled_credits`;
- the metric `credit_overrun` is emitted, with `job_type` and an amount bucket.

The balance never goes below zero, no other job's hold is taken, and the paper is not failed: the work was done. The metric threshold is in §7.

**Charging free balance beyond the hold is a documented deviation from NFR-CRD-03** ("no process can spend credits that were not reserved"). When a call's actual cost exceeds what the item holds, settlement bills the excess from free balance (capped at what is free, never another hold) and records it in `AIJob.overrun_charged` (metric `credit_overrun_charged`). Only what cannot be billed at all goes to `unbilled_credits` (metric `credit_overrun`). Because every anticipated call's estimate is reserved before it runs (§3.4), the billed excess is bounded by the difference between estimates and actual costs, and at most by the spendable balance at settlement (review r4 m-2). It is kept deliberately: the provider has already been paid, and the alternative is unbilled cost.

**Exemption: a late door upload (review r3 r3-b, SM decision).** A door `UPLOAD` item that comes back after its hold was returned runs under a new `UPLOAD` job (§3.2). If that job cannot reserve, the file is **still processed**, because the file exists only in the Celery message and the public promise is that work is never lost. Its cost goes to `overrun_charged` from free balance, then to `unbilled_credits`, bounded to one file's extraction. Test S-7.

Both are listed for the owner in §9 item 15.

### 3.6 Estimate band (X-3, FR-B-03/04, D-A8)

**Source:** `AICallRecord` (`ai_processor/models.py:254`). It has, per call:
- `processing_task_id`, `assignment_id`, `task_type`, `feature`, `outcome`;
- `questions`, `images`, `pdf_items`;
- `charged_credits`.

`CreditUsageLog` cannot serve this, because each call has its own uuid `task_id` and no link to the paper.

**Per-paper cost** = `Σ charged_credits` grouped by `processing_task_id`, for grading tasks whose item ended `SUCCESS`, over the last 90 days.

**Shape key:** the question-count bucket (1–5, 6–10, 11–20, 21+) × `has_images`.

**Paper estimate**, using the first source with enough history:
1. this assignment's own papers (n ≥ 10);
2. the teacher's papers of the same shape (n ≥ 30);
3. all papers of the same shape (n ≥ 30);
4. a cold-start fallback: the existing `estimate_messages_cost` × `BillingSettings.cold_start_cost_factor` (default 3, measured before launch; tier 3b and the "rough" label are in §9 item 6).

From the chosen source take `p50_paper`, `p90_paper`, the mean and σ.

**Batch of n papers:**
- `p50 = n · p50_paper`;
- `p90 = n · mean + 1.28 · σ · √n` when n ≥ 10, otherwise `n · p90_paper`.

**Verdict** against spendable `S`:

| Verdict | Condition |
|---|---|
| `SUFFICIENT` | `S ≥ p90` |
| `MARGINAL` | `p50 ≤ S < p90` |
| `INSUFFICIENT` | `S < p50` |

The boundary tests (FR-B-04) are: exactly `p90` gives SUFFICIENT, `p90 − 1` gives MARGINAL, and 0 gives INSUFFICIENT. The projected remaining balance is reported at both p50 and p90.

**Endpoint:** `POST /api/v1/billing/estimate` with `{assignment_id, submission_ids | "all_ungraded"}`. It returns:
- `{p50, p90, verdict, spendable, projected_remaining_p50, projected_remaining_p90, source, sample_size}`;
- `source`, which tells the client how confident the estimate is.

It is advisory: no endpoint refuses a teacher because of the verdict (D-A8).

**Accuracy (QA-B-03).** A management command, `backtest_credit_estimates`:
- replays the last 90 days of batches;
- reports the error distribution, actual/p50, as quantiles by batch size (1, 2–9, 10–29, 30+) and by shape;
- reports **band coverage**: the share of batches with actual ≤ p90. The target is 90% ± 5.

The results are reported, not averaged, and the report is committed as evidence.

**Scheduled drift check (owner ruling 2026-10-29).** The same backtest also runs automatically
as a monthly Celery beat task (beat-locked), over the trailing 90 days. If band coverage
(actual ≤ p90) falls below **85%** overall, or below 80% for any shape/batch-size cell with at least
30 batches, it emits `credit_estimate_drift` (NFR-OBS-03) and emails the super admin with the
failing cells. Each run is stored as a summary row (date, coverage per cell, sample sizes) so the trend
is visible. It only reports; it never changes estimates by itself. About +0.5 day in B1b.

### 3.7 Batch jobs and the queue (decision 1, X-8, NFR-REL-02)

**Create.** The grade-batch and grade-all routes (which launch `grade_batch_async`, `assignments/tasks.py:1436`) create the `AIJob` (`BATCH_GRADING`) with the estimate fields filled in. So do the unattended paths, `auto_grade_due_assignment` (`:1668`) and `_relaunch_failed_dispatches` (`:1616`) (review r2 M-f): an automatic run that runs out of credits queues its remainder, expires and notifies exactly like a teacher-started batch. Every `BackgroundProcessingTask` item gets `job` and `item_index`, as now, and `hold_state = HELD` when its hold is taken.

**Reserve.** In one transaction: `k = min(n, floor(spendable / p90_paper))` papers get held, at `held_per_item = p90_paper` each, and items `1..k` are claimed with a conditional `PENDING→LAUNCHED` UPDATE each. The Celery messages for the claimed items are sent through `grade_engine_async` (`:646`) **in `transaction.on_commit`** (review r1 M5), so no worker can start before the `AIJob` row and the hold exist. The same on-commit rule applies to promotion and to the upload door.
- If `k = 0` and the teacher proceeds (D-A8), the job goes straight to `QUEUED_PENDING_CREDITS`.
- Items `k+1..n` stay `PENDING` and are **not launched**: no Celery message exists for them.

**Run.** Each item runs inside `metered_unit(job, item)`. Every finished item (settled or failed) increments `completed_items` with one conditional UPDATE. **Only the UPDATE that brings `completed_items` up to the number of launched items** moves the job on (review r1 ALSO), exactly once:
- if unheld items remain → `QUEUED_PENDING_CREDITS`, with `stopped_at_item`, `queued_at` and `expires_at`. The transition **asserts the job's hold left is 0** (`reserved_credits − hold_drawn − released_credits = 0`), because every item returned its leftover in its own transaction (review r3 R3-4); a non-zero value does not raise: it emits `credit_ledger_anomaly{kind="hold_drift"}` and releases the remainder through the backstop's release path (review r4 m-6);
- otherwise → `SETTLED`.

**Fit with S7c.** `credits_exhausted_at` stays as it is for the flag-off path. With the flag on, an item can no longer find the wallet empty mid-call, because it holds its own credits and reserves any shortfall before a call (§3.4). S7c's stop then fires only for items without a hold, which do not exist. The guard stays as defence in depth.

**Promotion.** Two triggers:
- a sweep every 5 minutes, beat-locked;
- a hook on **every credit entry written through `CreditLedger.record`** (review r1 m3) **and through `after_bulk_create`** (`billing/models.py:~1515`) for credit types, because `refund_credits` writes its REFUND rows with `bulk_create` (`billing/services.py:~1567`), not `record` (review r2 m-a). That covers `_grant_overage_blocks`, the individual overage grant (`billing/services.py:1611`), renewals and trials, license allocation and renewal, `refund_credits` and grants generally. The hook enqueues a promotion Celery task for that wallet in `transaction.on_commit`; it never runs the promoter inline, so a promoter error cannot 500 a webhook after its commit. Paths that free credits without a ledger entry (hold releases) are covered by the sweep. The **documented bound**: a queued job is promoted within 5 minutes of credits becoming spendable.

**Atomic promotion (review r1 M4).** For each queued job, oldest `queued_at` first, in **one transaction**:
1. conditional UPDATE `QUEUED_PENDING_CREDITS → RESERVED` on `(id, status)`. Zero rows means another promoter or the expiry sweep won; stop;
2. reserve for as many of the remaining papers as fit;
3. claim each of those items with a conditional `PENDING→LAUNCHED` UPDATE;
4. launch the claimed items in `transaction.on_commit`.

If the reserve grants nothing, the transaction rolls back and the job stays queued. Expiry uses the same conditional UPDATE on `QUEUED_PENDING_CREDITS`, so promotion and expiry cannot both win, and no reserve is left without a running job. No job-level token is rewritten on promotion; items from an earlier reservation settle through their own settlement keys. The papers are already stored (§0.9), so nothing is re-uploaded.

**Lifetime.**
- `expires_at = queued_at + SubscriptionPlan.queue_expiry_days` (default **14**; owner ruling 2026-10-29). Both values are fields on `SubscriptionPlan`, editable by the super admin wherever plans are edited today, so the owner can change them without a deploy. `expires_at` is fixed when the job queues; a later change applies to newly queued jobs only.
- At `expires_at − SubscriptionPlan.queue_expiry_warning_hours` (default **48**), a warning email goes out and `warned_expiry_at` is set.
- At `expires_at`, the job becomes `EXPIRED` (conditional UPDATE on `QUEUED_PENDING_CREDITS`). Unlaunched items become `FAILED` with `INSUFFICIENT_CREDITS_MID_BATCH`, which is retryable (`students/item_retry.py`). An expired batch can still be resumed by hand after a top-up, through item retry, which goes through the §3.4 entry rule (E-2), so **nothing is lost**.

**Notifications.** The teacher is told when a job:
- is queued (with counts done and left, and a link to buy or request credits);
- is about to expire;
- is promoted;
- expires.

The channel is email through `send_email_task`, always sent, as billing mail (§9 item 7). Each state is also exposed in the job API.

### 3.8 `CreditRequest` (decision 4, FR-B-11)

**Table:** `billing_creditrequest`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `teacher` | FK → `CustomUser`, CASCADE | The requester |
| `license_subscription` | FK → `LicenseSubscription`, CASCADE | Fixes 03a's `school` (§0.7). The admin is `license_subscription.admin_user` |
| `requested_blocks` | PositiveInt | In overage blocks, the unit the purchase uses |
| `message` | CharField(500) | Teacher's note; no student data (validated by length only) |
| `status` | CharField | `PENDING` / `APPROVED` / `GRANTED` / `DECLINED` / `WITHDRAWN` / `CLOSED` |
| `ai_job` | FK → `AIJob`, SET_NULL, null | The queued job that prompted the request, if any |
| `purchase_intent` | FK → `LicenseOveragePurchaseIntent`, SET_NULL, null | Set on approve (Stripe route) |
| `offline_request` | FK → `LicenseOverageOfflineRequest`, SET_NULL, null | Set on approve (offline route) |

`credit_request_id` is also written **onto the `LicenseOveragePurchaseIntent` when the intent row is created, before `Session.create`** (new nullable column, review r3 r3-a), **into the Stripe checkout metadata** (`billing/license_service.py:~3336`) and **onto the `LicenseOverageOfflineRequest`** (new nullable column), so fulfilment can find the request even if the link in step 4 of approve was never written (review r2 M-e). `initiate_overage_purchase` gains an optional `credit_request_id` parameter for this; it is otherwise unchanged.
| `decided_by_id`, `decided_at`, `decline_reason` (CharField 500) | | |
| `created_at`, `updated_at` | | |

Index on `(license_subscription_id, status, created_at DESC)`, used for the admin's inbox and counts. A partial unique index on `(teacher_id) WHERE status IN ('PENDING', 'APPROVED')` allows one open request per teacher (review r1 B3). An approved request whose checkout is still open blocks a second request, and a reverted request can never collide with a newer one inside a webhook transaction.

**States:**

```
PENDING ─approve─► APPROVED ─intent COMPLETED / offline approved─► GRANTED
   │                  ├─checkout.session.expired, stale-APPROVED sweep, unlinked-APPROVED sweep, offline request rejected, or intent FAILED (teacher still on the license)─► PENDING (note recorded)
   │                  └─teacher left the license, or intent FAILED because the teacher was skipped─► CLOSED
   ├─decline─► DECLINED
   ├─teacher withdraws─► WITHDRAWN
   └─teacher left the license─► CLOSED
```

"Answered" means anything other than `PENDING`. `CLOSED` is terminal (review r1 M7).

**Abandoned checkout (review r1 B3).** `LicenseOveragePurchaseStatus` has only `PENDING`/`COMPLETED`/`FAILED` (`billing/models.py:1981`), and only `checkout.session.completed` is wired (`billing/webhooks.py:103`). B1c adds both:
- a `checkout.session.expired` handler, dispatched like the completed one. For a `license_overage_purchase_checkout` session it marks the intent `FAILED` (abandoned) and reverts its linked request `APPROVED → PENDING` with a note, by conditional UPDATE;
- belt and braces: a beat-locked sweep that reverts `APPROVED` requests whose intent is still `PENDING` and whose **stored `session_expires_at`** has passed (plus a margin). `_create_overage_checkout` (`billing/license_service.py:3277`, `Session.create` at `~:3326`) passes no `expires_at`, so Stripe's default 24 h applies; B1c stores the created session's `expires_at` on the intent (beside `stripe_checkout_session_id`, `billing/models.py:2042`) and sweeps on that, not on an assumed TTL (review r2 TTL). Offline-route requests are not swept on time: they wait on the super admin by design;
- the **unlinked-`APPROVED` sweep** (review r2 M-e): a request `APPROVED` for more than N minutes (N = 15) with neither `purchase_intent` nor `offline_request` linked, and with no intent or offline request carrying its `credit_request_id`, goes back to `PENDING` with a note. If one does carry its id, the sweep links it instead;
- **offline rejection (review r2 M-d):** a hook in `reject_overage_offline_request` (`billing/license_service.py:3659`), in its transaction, moves the linked request `APPROVED → PENDING` by conditional UPDATE, with the rejection reason noted;
- `checkout.session.expired` is evaluated for `billing/event_replay.py`'s `AUTO_REPLAYABLE` allowlist (`:84`), which needs its written idempotency proof; and the production Stripe endpoint must subscribe to the event, an ops step recorded in the staging proof (review r2 m-b).

**Teacher departure (review r1 M7, review r2 m-e).** When a teacher leaves the license (`remove_teacher_from_license`, `billing/license_service.py:1949`), and also when the license is cancelled or expires, the affected `PENDING` and `APPROVED` requests move to `CLOSED` with a reason. The B1c survey lists every path that ends a teacher's license access and hooks each one. If an intent then fails because that teacher was skipped at fulfilment (the §9 item 3 refund path), the request moves to `CLOSED`, never back to `PENDING`.

**Approve, ordered (review r1 M6):**
1. conditional UPDATE `PENDING → APPROVED` on `(id, status)`, committed. Zero rows → 409;
2. call `initiate_overage_purchase(license_sub, admin, total_blocks, {teacher_id: blocks}, payment_method=…, credit_request_id=request.id)`, **outside any transaction**. Its only change is the new optional parameter (review r3 r3-a). It makes a network call to Stripe (`billing/license_service.py:~3326`);
3. on any exception from step 2, conditional UPDATE `APPROVED → PENDING` with the error noted, and return the error;
4. on success, link the intent or offline request to the request.
- `GRANTED` is set when the webhook or offline approval fulfils the intent. A hook goes after `_handle_license_overage_checkout_completed` and `approve_overage_offline_request`, in the same transaction. It **matches the request by `credit_request_id`** from the intent, the session metadata or the offline request, not only by the link, so a crash between approve's steps 1 and 4 still ends in `GRANTED` (review r2 M-e). It accepts a request in `PENDING` or `APPROVED` (an unlinked sweep may have reverted it), and **fails any other open intent linked to the same request**, so one request never buys twice (review r3 r3-a). Failing that intent also calls `stripe.checkout.Session.expire` on its session in `transaction.on_commit`. If its session is paid anyway (the expire raced the payment), `_handle_license_overage_checkout_completed` **refuses to grant on a `FAILED` intent** (today it only skips `COMPLETED`, `billing/stripe_service.py:~3250`) and refunds the payment through the §9 item 3 refund path (`billing/payment_refunds.py`), notifying the admin (review r4 m-3).
- Credits landing then trigger queue promotion (§3.7).
- A super admin's immediate grant goes straight to `GRANTED`.

**API**, under `/api/v1/billing/credit-requests/`:

| Method and path | Who | Effect |
|---|---|---|
| `POST /` | Licensed teacher | Create. 409 if one is already pending; 400 if the teacher has no license |
| `GET /` | Teacher: own requests. Admin: their license's requests | List, filterable by `status` |
| `GET /counts` | License admin | `{pending, answered, granted, declined}` |
| `POST /{id}/approve` | License admin, super admin | Body `{blocks?, payment_method}`. Returns the purchase result (`checkout` URL, `offline_request_pending` or `granted`) |
| `POST /{id}/decline` | License admin, super admin | Body `{reason}` |
| `POST /{id}/withdraw` | The requesting teacher | Only while `PENDING` |

Approve and decline are conditional UPDATEs on `status = 'PENDING'`. A double-click gives one effect and one 409. Withdraw after approve is refused (409), because withdraw is conditional on `PENDING` too.

The admin is notified by email and by the inbox count. The teacher is notified by email when the request is answered.

**No in-app notification bell (owner ruling 2026-10-29).** Notification is email plus the inbox page and its count badge, nothing else.

### 3.9 Threshold warnings (FR-B-05)

At every settlement, compare spendable with the cycle allocation from `plan_remaining_credits` (`billing/models.py:~700`). The levels are 25%, 10% and 0%.
- One email per level per billing cycle. It is recorded in two new `CreditWallet` fields, `low_balance_warned_level` and `low_balance_warned_cycle_start`.
- The level is exposed as `balance_status` on `CreditWalletSerializer` (`billing/serializers.py:873`), behind `GET credits/wallet` (§9 item 8).

`BetaProfile.has_hit_80_percent` and `has_hit_cap` are analytics flags, not warnings. They are left alone.

### 3.10 Actual cost and the gap metric (FR-B-09, NFR-OBS-03)

- `AIJob.consumed_credits` and `unbilled_credits` are the actual cost.
- `GET /api/v1/billing/jobs/{id}` (owner or license admin) returns the estimate, verdict, actual, item counts, `stopped_at_item`, state and `expires_at`.
- Synchronous AI endpoints add `ai_job_id` to their JSON responses through a shared helper, so a client can fetch the actual cost (the count of views whose body is not a JSON object is taken in the B1b survey, §9 item 9).
- When a job settles with an estimate, two metrics are emitted:
  - `credit_estimate_gap` (actual/p50, bucketed);
  - `credit_estimate_band_hit` (actual ≤ p90).

  Their thresholds are in §7.

### 3.11 Where B1 sits with B0's zero-credit guards

- `reserve()` calls `assert_not_zero_credit(CREDIT_RESERVE)`, a new code added to `billing/zero_credit.py` beside `MODEL_CALL`…`BUCKET_MUTATION` (`:70–75`).
- `AIJob` creation inside a scope is refused through the same guard.
- Settlement goes through `consume_credits`, which is already guarded.

So a zero-credit operation can neither hold nor charge, and B0's helper `assert_operation_is_zero_credit` needs no change. The hold ContextVar and the zero-credit ContextVar are independent. Test Z-1 asserts that a hold open in an outer frame does not let a zero-credit inner frame through.

---

## 4. Migrations (docs/MIGRATIONS.md)

| Migration | Class | Reversible | Notes |
|---|---|---|---|
| `billing`: create `AIJob` (with `hold_drawn`, `completed_items`) | Additive | Yes, drop | New table, so indexes need no `CONCURRENTLY` |
| `billing`: create the settlement-key table (unique `(item_id, retry_count)`; `item_id` is the task row id, or the job id on E-4/E-5/`SINGLE_CALL`) | Additive | Yes, drop | New table; it is the only settlement fence |
| `billing`: `CreditWallet.reserved_credits` (int, default 0, `db_default=0`), `low_balance_warned_level`, `low_balance_warned_cycle_start` (null) | Additive | Yes, with a condition | `db_default` so code that is rolled back still inserts (H-56 rule). Reverse **only with `BILLING_HOLDS_ENABLED` off and every `reserved_credits` at 0**; the reverse migration asserts that and refuses otherwise, because reversing with holds open throws away hold state |
| `billing`: `CHECK (reserved_credits >= 0)` | Additive | Yes | Django's `AddConstraint` has no `NOT VALID` option, so it is `RunSQL` (`ADD CONSTRAINT … NOT VALID`, then `VALIDATE CONSTRAINT` in a separate migration) inside `SeparateDatabaseAndState`, with the model `Meta.constraints` as the state side (review r2 m-d) |
| `students`: `BackgroundProcessingTask.job` FK, null, SET_NULL, **`db_index=False`**, plus `hold_state` (null), `hold_credits`, `hold_drawn`, `released_credits` | Additive | Yes | On Django 5.2, `AddField` of an FK would build its index inline and take SHARE ROW EXCLUSIVE while validating. So: the column via `SeparateDatabaseAndState` with `RunSQL` (`ADD COLUMN`, then `ADD CONSTRAINT … FOREIGN KEY … NOT VALID`, then `VALIDATE CONSTRAINT` separately), `db_index=False` on the state side; `hold_credits`, `hold_drawn` and `released_credits` (int, `db_default=0`) are plain additive columns; and the index built by a separate `AddIndexConcurrently` in an `atomic = False` migration (precedent `billing/migrations/0051_creditusagelog_task_id_index.py`, `0061`) (review r2 m-d). There is no `settled_at` (review r2 M-a) |
| `billing`: `AIJob.overrun_charged`; `BillingSettings.unstarted_backstop_hours` (default 36); `LicenseOveragePurchaseIntent.session_expires_at` (null) and `.credit_request_id` (null); `LicenseOverageOfflineRequest.credit_request_id` (null) | Additive | Yes | The intent and offline-request columns in slice B1c |
| `billing`: `SubscriptionPlan.queue_expiry_days` (default 14), `queue_expiry_warning_hours` (default 48), with `db_default` | Additive | Yes | Slice B1a schema, used in B1b |
| `billing`: create `BillingSettings` (one row, `cold_start_cost_factor` default 3.0) | Additive | Yes, drop | Slice B1b |
| `billing`: create the drift-summary table (run date, coverage per cell, sample sizes) | Additive | Yes, drop | Slice B1b |
| `audit`: `RetentionClass` gains `FINANCIAL` (choices only, no schema change beyond the choices state) | Additive | Yes | **No retag migration** (review r2 M-g): `AuditEvent` is append-only (`audit/models.py:~10–12`; `AppendOnlyQuerySet.update` raises, `billing/immutable.py:114`). New rows store `FINANCIAL`; `sweep_audit_retention` (`audit/tasks.py:48`) excludes the FINANCIAL actions by action from the `GENERAL` cutoff, so old `GENERAL`-tagged credit rows are kept too, and applies the 7-year cutoff to them. It excludes FINANCIAL actions from the `STUDENT_RECORD` delete too (an event raised to `STUDENT_RECORD` must not lose its 7 years), and records `deleted_financial` in its `AUDIT_RETENTION_SWEEP` metadata (allowlist key added). **Rollback effect:** if B1 code is rolled back, the old sweep filters only `GENERAL` and `STUDENT_RECORD`, so `FINANCIAL`-tagged rows are never deleted (kept, not lost), while old credit rows still tagged `GENERAL` fall back to the 365-day cutoff (review r3 M-g caveats) |
| `billing`: create `CreditRequest` (partial unique index on `status IN ('PENDING','APPROVED')`) | Additive | Yes | Slice B1c |

There is **no contract step**, so no three-deploy schedule is needed. Turning the flag off leaves the new columns unused and harmless. `makemigrations --check` is part of each slice gate.

---

## 5. Feature flags (NFR-MNT-05)

| Flag | What it switches | Default |
|---|---|---|
| `BILLING_HOLDS_ENABLED` | Holds, metered units, batch jobs and queue, the estimate verdict, warnings | `False` |
| `CREDIT_REQUESTS_ENABLED` | The `CreditRequest` API and its hooks | `False` |

Both are `env.bool` in `AutoGrader/settings.py`, the existing pattern (`:598`, `:685`, `:695`).

**Both paths are live code while the flag is off.** The metering tests run with the flag on and off (`override_settings`), and the strict full run at the Epic B landing is taken with the flag **off**, then repeated for `billing ai_processor assignments students` with it **on**. Before the default flips there is a staging proof:
- a real batch;
- a forced exhaustion;
- a top-up through overage;
- the job's own promotion;
- the production Stripe webhook endpoint subscribed to `checkout.session.expired` (ops step, review r2 m-b).

---

## 6. Test plan

### 6.1 Acceptance mapping

| Requirement | Test |
|---|---|
| FR-B-02 (already met) | Unchanged: the existing reconstruction test stays green, and B1 adds holds and settlements to its mixed sequence |
| FR-B-03 | The backtest command's report (quantiles by batch size and shape) committed as evidence; the endpoint returns a band before execution |
| FR-B-04 | Boundary tests: `S = p90` gives SUFFICIENT, `p90 − 1` gives MARGINAL, `S = 0` gives INSUFFICIENT; projected remaining at p50 and p90 |
| FR-B-05 | Live (not simulated) exhaustion in a 30-paper batch: graded papers stay graded, the remainder is queued with `expires_at`, the teacher is emailed; warnings at 25/10/0 fire once per cycle |
| FR-B-06 | The worker is killed mid-run (SIGKILL on a real Celery worker): completed items are untouched, the hold is released by the sweep, the queued remainder is promoted after a top-up without re-upload, and `stopped_at_item` is correct |
| FR-B-07 / NFR-CRD-03 | §6.3 R-1 to R-4: no provider call without a hold; the number of provider calls equals the holds granted; never negative |
| NFR-CRD-04 (decision 2) | A failed paper in a unit writes no `CreditUsageLog`/`CreditLedger` row and leaves `reserved_credits` restored; a provider error on a single call writes none |
| FR-B-08 / NFR-CRD-05 | §6.3 F-1 to F-3 |
| FR-B-09 | The job API returns the actual cost; the gap metric is emitted once per settled job |
| FR-B-11 | API tests for every state transition, the counts and the 409 on a double decision; approve → intent → GRANTED through the real webhook handler with a Stripe fixture |
| NFR-REL-01 | Settle (same item redelivered), release, promote and decide each delivered twice give one effect (the per-item settlement key / status UPDATE) |
| NFR-REL-02 | No task waits for credits: the queued remainder has no Celery message (asserted against the broker in the test settings) |

### 6.2 Mutation matrix (NFR-TST-07)

Mutants are identified by function name and exact text; the line number is only a hint. The kill fragments are written in NOTES before any mutant runs, after tracing "the next guard down".

| # | Mutant | Expected kill |
|---|---|---|
| M-B1 | Reserve's UPDATE without the `reserved_credits + n <= total` condition | R-1 (more holds than credits) |
| M-B2 | Reserve without `lock_wallet_first` | R-4 on PG 16 (or R-1, depending on the EPQ finding in §0.3) |
| M-B3 | Settle takes `min(actual, total_remaining)`, ignoring other jobs' holds | R-3 |
| M-B4 | The single-call hold removed from `execute_graded_task` | Provider-call-without-hold test plus the AST dominance test |
| M-B5 | The metered unit settles on exception | The no-charge-on-failure test |
| M-B6 | The settlement-key claim dropped from settle | D-1 (the same batch item redelivered after its ack is lost settles once) |
| M-B7 | Release not idempotent (no status condition) | Double-release test |
| M-B8 | Verdict computed against p50 | FR-B-04 boundary test |
| M-B9 | The promoter launches every remaining item, ignoring `k` | Promotion test (`reserved ≤ spendable`) |
| M-B10 | Expiry deletes the items instead of failing them as retryable | Expiry test (item still retryable) |
| M-B11 | The stale-hold sweep keyed on the batch job's `updated_at` instead of each item's clock | S-1 (a batch with a broker backlog older than 30 min keeps its hold) |
| M-B12 | `CreditRequest` approve without the `status='PENDING'` condition | Double-approve test |
| M-B13 | `reserve()`'s `assert_not_zero_credit(CREDIT_RESERVE)` removed | Z-1 |
| M-B14 | Overrun charged beyond the available balance instead of absorbed | Overrun test (`InsufficientCreditsError` raised, or a negative-bucket assert) |
| M-B15 | Release computed as `reserved − consumed` instead of `reserved − hold_drawn − released` | H-1 (overrun then release: `reserved_credits` back to its starting value, invariant holds; the `CHECK` does not fire) |
| M-B16 | The entry rule removed: `metered_unit` without a job opens a tally with no reserve | E-1…E-7 (provider mock called with no hold) |
| M-B17 | The `checkout.session.expired` handler does not revert the request | C-1 (abandoned checkout returns the request to `PENDING`) |
| M-B18 | The pre-call fit check removed from the unit | U-1 (a unit with its hold used up makes 0 further provider calls) |
| M-B19 | Promotion without the conditional `QUEUED→RESERVED` UPDATE | P-1 (sweep vs grant hook on a barrier: each item launched once, one hold) |
| M-B20 | Launch sent inline instead of in `on_commit` | P-3 (a worker started before commit finds no job and no hold; the test asserts no message before commit) |
| M-B21 | The pre-call check compares against job `hold_left + spendable` without reserving the shortfall | U-2 (two units on one free balance: the second call is refused or reserved, never both run unreserved) and U-3 |
| M-B22 | The sweep returns a hold without failing and counting the item | S-3 (job reaches `QUEUED`/`SETTLED` after an item's message never returns) |
| M-B23 | Settlement keyed on the job id for an E-1 paper | D-3 (a redelivered single-paper grade settles once) |
| M-B24 | The `GRANTED` hook matches only by link, not by `credit_request_id` | C-8 (crash between approve steps 1 and 4 still ends `GRANTED`) |
| M-B25 | The sweep does not add to the item's `released_credits` | S-3 (wallet and per-item invariants after the sweep and the terminal release) |
| M-B26 | The sweep also takes launched-but-unstarted items | S-5 (a broker backlog older than 90 minutes is not swept) |
| M-B27 | A late grading message for a `RETURNED` item runs instead of exiting | S-6 (a late item for a `SETTLED` job leaves the invariant intact and charges nothing) |
| M-B28 | Settle does not return the item's leftover | P-5 (promotion uses the freed credits before the job ends; the `QUEUED` assert holds) |
| M-B29 | The `GRANTED` hook does not fail the other intent linked to the request | C-9 (double purchase: one grant, the other intent failed) |
| M-B30 | The pre-call check uses `item_hold_left` (ignores the tally) | U-1 (a unit with its hold used up makes 0 further provider calls) |
| M-B31 | Unit entry reads `hold_state` and exits instead of the conditional start claim; or the sweep drops `started_at < cutoff` | S-8 (a redelivery that starts after a long backlog is not swept mid-run) |
| M-B32 | The completed-checkout handler grants on a `FAILED` intent | C-9 (the failed intent's session is paid: no grant, and a refund) |

### 6.3 Gate-specific tests

These run on real Postgres and real Redis, with barrier-synchronised threads (NFR-TST-06). The concurrency tests run locally on PostgreSQL 18.6 **and** in the CI job that already uses `postgres:16` (`.github/workflows/tests.yml:57`) for NFR-TST-11. Neither run alone is quoted as production behaviour.

| Id | Test |
|---|---|
| R-1 | 200 threads on a barrier reserve against a wallet that fits exactly 7. Exactly 7 succeed, `reserved_credits` equals 7 × n, and the invariant holds |
| R-2 | Two 30-paper batches start together on a balance that fits 12 papers. The provider mock counts calls: calls equal granted holds, no paper is charged without a hold, the sum charged ≤ the starting balance, and the remainders are queued |
| R-3 | A settlement overrun while another job holds credits: the other job's hold is untouched, and the overrun is recorded |
| R-4 | §0.3's EPQ question: a conditional UPDATE with a bucket subquery and no wallet lock, raced against `consume_credits`. Records whether it over-reserves on PG 16 and on 18.6. The result decides whether M-B2 is killed by R-4 or R-1 |
| F-1 to F-3 | For each grant path in §0.6, a failure is forced after the first teacher's bucket is created (patching `CreditLedger.record` to raise for the second teacher). No bucket, ledger row, `overage_blocks_used` change or `BillingTransaction` survives for anyone, and the intent or offline request is unchanged |
| Z-1 | The zero-credit scope inside a held unit: `reserve` and `consume` both raise `ZeroCreditViolation` |
| Adversarial | A teacher reserving against another teacher's job id (404); a forged `reservation_claimed_at`; approving a request from another license (404); withdrawing someone else's request (404) |
| Failure | Redis down during promotion (the beat lock fails closed, nothing launched twice); the provider times out inside a unit (nothing charged, hold kept for the next item); a worker killed between reserve and settle (the sweep releases) |
| H-1 | (a) Overrun then release: a `SINGLE_CALL` holds 50, actual is 80, free balance is 200. After settle and release, `reserved_credits` equals its value before the job, `hold_drawn = 50`, `overrun_charged = 30`, the invariant holds, and the `CHECK` never fires. (b) Free balance 0: hold 100, actual 80 → charge 80, `unbilled_credits = 0`, leftover 20 returned in the settle transaction (review r3 R3-1) |
| E-1…E-7 | One test per entry point in §3.4: with the reserve refused, the provider mock is not called and `INSUFFICIENT_CREDITS` is returned; with it granted, the hold is drawn and the rest released |
| U-1 | A unit whose hold and spendable are used up: the next provider call is refused by the pre-call check (0 further calls), including a chunked extraction (`_extract_answers_chunked`) |
| D-1 | Same item redelivered (settle, worker killed before the ack, redelivery after the claim goes stale): one settlement, one ledger set |
| D-2 | Retry in place of an item: each attempt has its own settlement key; a manual `refund_credits` on one attempt refunds that attempt only |
| S-1 | Batch with items waiting in the broker longer than `GRADING_CLAIM_STALE_AFTER`: the sweep keeps their holds; a **started** item past `started_at + visibility_timeout + time limit` with no settlement has its hold returned (review r3 r3-c) |
| P-1 | Promotion by the sweep and by the grant hook on a barrier: each item launched once, one hold per item |
| P-2 | Promotion vs expiry on a barrier: exactly one wins; no reserve is left without a running job; the invariant holds |
| P-3 | Every launch (batch, promotion, door) is sent on commit: no broker message exists before the transaction commits |
| P-4 | Two items finishing at once on a barrier: the job moves to `QUEUED`/`SETTLED` exactly once |
| C-1 | Abandoned checkout: a `checkout.session.expired` fixture reverts the request to `PENDING` and fails the intent |
| C-2 | Stale-`APPROVED` sweep reverts an approved request whose intent is past the session lifetime; an offline-route request is not swept |
| C-3 | With a request `APPROVED`, a second request from the same teacher is refused (unique index) |
| C-4 | A Stripe exception during approve leaves the request `PENDING` with the error noted and no intent linked |
| C-5 | A teacher leaving the license moves their `PENDING`/`APPROVED` requests to `CLOSED` |
| C-6 | An intent failing because its teacher was skipped closes the request, never reverts it to `PENDING` |
| C-7 | `reject_overage_offline_request` on a request's offline purchase moves the request `APPROVED → PENDING`; the teacher can then ask again |
| C-8 | Crash between approve steps 1 and 4: (a) before `Session.create`, the unlinked-`APPROVED` sweep returns the request to `PENDING`; (b) after it, the webhook grants and the hook finds the request by `credit_request_id` and sets `GRANTED` |
| D-3 | E-1 single-paper grade redelivered (`grade_engine_async` delivered twice): one settlement, keyed on the task row |
| D-4 | Two files under one upload door job each settle once; a retry in place of a task row settles again with its new `retry_count` |
| U-2 | Barrier: two units on one wallet with free balance S, both needing beyond their holds: the shortfalls are reserved, the sum charged ≤ S + their holds, and nothing beyond is unbilled except actual-vs-estimate differences |
| U-3 | Barrier: two items in one batch: neither draws the other's hold; a held item started after them still has its full hold |
| S-2 | An item killed and redelivered after `visibility_timeout`: before the sweep window its hold is untouched and the redelivery settles normally; after a sweep, the late message exits (S-6) |
| S-3 | An item whose message never returns: the sweep fails it (retryable), adds its hold to `released_credits`, counts it, and the job moves to `QUEUED`/`SETTLED`; the wallet and per-item invariants hold after the sweep and after the terminal release |
| S-4 | A swept upload door item that comes back runs under a new `UPLOAD` job with its own hold and release; the old job's accounting is untouched |
| S-5 | A broker backlog of more than 90 minutes (items launched, not started): nothing is swept; after X hours with no item started or settled, the backstop fails the unstarted items in one transaction and returns their holds |
| S-6 | A late grading message for an item of a `SETTLED` job (its hold returned): it exits without a provider call or charge, the paper stays retryable, and the invariant holds |
| S-7 | A late door item whose new `UPLOAD` job cannot reserve: the pre-call check does not refuse it (m-1), the file is still processed, the cost goes to `overrun_charged` then `unbilled_credits`; nothing is lost |
| S-8 | Barrier: a redelivered item starts (start claim) just as the sweep runs on a stale `started_at`: exactly one wins; if the run won, it is not swept mid-run; if the sweep won, the run exits as late and nothing is double-counted |
| S-9 | A late door item whose file already settled (settlement row exists): it exits without a provider call or charge |
| P-5 | A batch item settles under its hold: the leftover is spendable at once, and a queued job on the same wallet is promoted with it before the first job ends |
| C-9 | Double purchase: two intents carry the same `credit_request_id`; the first completed grants and moves the request to `GRANTED`; the other intent is failed and its session expired on commit. Then a `checkout.session.completed` fixture pays the failed intent's session anyway: no grant, a refund through the §9 item 3 path, and the admin notified |
| B-1 | An automatic run (`auto_grade_due_assignment`) on 30 papers exhausts credits: the remainder is queued with `expires_at`, the teacher is emailed, and promotion resumes it after a top-up |
| R-5 | `sweep_audit_retention` keeps FINANCIAL-action rows (including old `GENERAL`-tagged and any `STUDENT_RECORD`-raised ones) past 365 days and past 3 years, deletes them after 7 years, and reports `deleted_financial` |
| Gate 6 | Added latency of reserve and settle per call, measured as p50/p95 (§9 item 13); the estimate endpoint's cost on 90 days of `AICallRecord` (index check: `processing_task_id`, `created_at`) |

---

## 7. The six items from 05 §7

**1. Audit actions added.** These are the values 05 §2.5 listed for Epic B, plus the credit-request ones. Retention classes come from `audit/volume.py:61` `retention_class_of`.

| Action | Emitted when | Retention |
|---|---|---|
| `AIJOB_QUEUED` | A job enters `QUEUED_PENDING_CREDITS` | General (12 months, D-A6) |
| `AIJOB_EXPIRED` | A job expires | General |
| `CREDIT_REQUEST_CREATE` | A teacher creates a request | FINANCIAL (7 years, §9 item 11) |
| `CREDIT_REQUEST_DECIDE` | Approve, decline, withdraw or close (`decision` in the metadata) | FINANCIAL |

Promotion and settlement need no new action:
- a settlement already emits `CREDIT_TRANSACTION`, through the ledger;
- promotion uses `STATE_CHANGE`, with `from`/`to` in the metadata.

The metadata allowlist gains `job_id`, `items_left`, `expires_at`, `request_id` and `decision`. This depends on 05 Q7 (one audit-vocabulary task, or each epic adds its own).

**2. Student deletion/export walk (NFR-CMP-04).**
- `AIJob.requested_by` can be a student (student uploads). It cascades on user deletion, and `meta` holds no student data, so add it to the walk as "deleted with the user, nothing exported".
- `CreditRequest` is teachers only. Not in the student walk.

**3. `10 Gates.md` applicability.**

| Gate | Applies | Basis |
|---|---|---|
| 1 Baseline / regression | Yes | `billing ai_processor assignments students` with the flag on and off, exact Ran per slice |
| 2 Mutation | Yes | §6.2, 32 mutants |
| 3 Concurrency | Yes | R-1 to R-4, real threads, PG 18.6 and PG 16 (CI) |
| 4 Adversarial | Yes | §6.3 |
| 5 Failure / recovery | Yes | Worker kill, Redis down, provider timeout, grant-path forced failures |
| 6 Stress / scale | Yes | Per-call hold latency; estimate query on 90 days; a 300-paper batch reserve/promote |
| 7 Real infrastructure | Yes | Real Postgres, Redis, a Celery worker; Stripe through the webhook handler with fixtures (no live Stripe) |
| 8 Live / end-to-end | Yes, on staging | Flag on in staging only: a real batch, a forced exhaustion, a top-up, the promotion; a credit request approved through Stripe test mode |
| 9 Security / isolation | Yes | New resources: role-matrix rows below, cross-license and cross-teacher 404s |

`10 Gates.md` defines Gates 1–9 only (06 §9 item 9).

**4. Migrations.** All additive and reversible, with no contract step (§4).

**5. Feature flags.** `BILLING_HOLDS_ENABLED`, `CREDIT_REQUESTS_ENABLED`, both default off (§5).

**6. Role-by-resource matrix rows (NFR-SEC-01).**

| Resource | Student | Teacher | School Admin | Super Admin |
|---|---|---|---|---|
| `AIJob` (read) | None (404) | Own jobs | Jobs of teachers on their license: counts and states, no student fields | All |
| `AIJob` (cancel) | None | Own, non-terminal | None | All |
| Estimate endpoint | None (403) | Assignments in courses they can reach | Same rule as teacher for their own courses | All |
| `CreditRequest` create / withdraw | None | Own; licensed teachers only | None | None |
| `CreditRequest` list / counts | None | Own | Their license only | All |
| `CreditRequest` approve / decline | None | None | Their license only | All |

---

## 8. Files touched

| File | Change |
|---|---|
| `billing/holds.py` | New: reserve, settle, release, metered unit, `spendable_credits`, sweep, invariant |
| `billing/estimates.py` | New: band, verdict, backtest core |
| `billing/management/commands/backtest_credit_estimates.py` | New |
| `billing/models.py` | `AIJob`, `CreditRequest`, three `CreditWallet` fields |
| `billing/zero_credit.py` | New code `CREDIT_RESERVE` |
| `billing/tasks.py` | Promotion/expiry sweep, stale-hold sweep, daily invariant check |
| `billing/views.py`, `billing/serializers.py`, `billing/urls.py` | Estimate, jobs and credit-request endpoints |
| `billing/license_service.py`, `billing/stripe_service.py` | `initiate_overage_purchase`'s `credit_request_id` parameter; the `CreditRequest` `GRANTED`/`CLOSED` hooks after fulfilment; `CLOSED` on teacher departure; departed-teacher refund |
| `billing/stripe_service.py` `_handle_license_overage_checkout_completed` (`:~3250`) | Refuse to grant on a `FAILED` intent; refund (review r4 m-3) |
| `billing/webhooks.py`, `billing/event_replay.py` | Dispatch `checkout.session.expired`; its replay allowlist entry only with the written idempotency proof |
| `billing/models.py` `CreditLedger.record` (`:1505`) and `after_bulk_create` (`:~1515`) | On-commit promotion task for credit entries |
| `ai_processor/services.py` | `execute_graded_task` holds; the unit at `:4635` |
| `students/services.py`, `assignments/file_uploads.py` | The unit replaces the refund scope at four sites (flag on) |
| `students/views.py`, `assignments/views.py`, `students/item_retry.py` | No logic change expected: their paths reach the unit sites and go through the entry rule. Touched only if the B1a survey finds a path that bypasses a unit |
| `assignments/tasks.py` | Batch routes, `auto_grade_due_assignment` and `_relaunch_failed_dispatches` create the `AIJob`, reserve, claim items and launch on commit; promotion |
| `assignments/upload_door.py` | Hold at the door |
| `students/models.py` | `BackgroundProcessingTask.job` |
| `audit/enums.py`, `audit/metadata.py`, `audit/volume.py`, `audit/tasks.py` | Four actions, the allowlist keys (including `deleted_financial`), the FINANCIAL class and its sweep cutoffs |
| `AutoGrader/settings.py` | Two flags and three beat entries |
| Tests: `billing/tests/test_holds*.py`, `test_estimates.py`, `test_credit_requests.py`, `test_grant_atomicity.py`, `test_entry_points.py`, `ai_processor/tests_metering_holds.py` | New |
| `docs/evidence/epic-b1a/`, `-b1b/`, `-b1c/` | Evidence |

`ai_processor/services.py` is 6,383 lines and shared with other streams. B1's edits there are confined to `execute_graded_task` and `grade_student_submission`.

---

## 9. Open items and unverified claims

1. **EPQ behaviour** (§0.3): whether a conditional UPDATE with a bucket subquery over-reserves under READ COMMITTED. The design does not depend on the answer (it takes the wallet lock), but M-B2's killing test does. R-4 settles it on PG 16 and 18.6.
2. **FR-B-08 wording** (§0.6): there is no admin balance to debit. Record that the requirement is met by allocate-at-purchase.
3. **Decided (owner, 2026-10-29):** teachers skipped at fulfilment after the school paid. The other teachers are granted as now; the skipped share is **refunded automatically** to the school (Stripe partial refund through the existing `billing/payment_refunds.py` path; for offline payments, an offline refund record for the super admin), and the admin is notified. Added to B1a: about **+1.5 days**, with a test that forces a teacher to leave between checkout and webhook.
4. **Decided (owner, 2026-10-29):** queue lifetime 14 days, warning 48 hours before, **as editable settings** (`SubscriptionPlan.queue_expiry_days`, `queue_expiry_warning_hours`) (§3.7). Migration adds both with defaults; about +0.5 day in B1b.
5. **Decided (owner, 2026-10-29):** uploads are not queued (§0.9). Refuse at the door with a hold.
6. **Settled (SM ruling 2026-10-30): cold-start estimate.**
   (a) Before the guess, add tier 3b: all papers of any shape, scaled per question (n ≥ 30).
   (b) The factor is not hard-coded. It lives in a new one-row `BillingSettings` model (`cold_start_cost_factor`, default 3.0), editable in Django admin. The repo has no platform-wide editable-value model yet (billing/models.py has only per-plan and per-license tables), so this is the smallest option. Before launch, `backtest_credit_estimates --cold-start` runs read-only against production to measure the ratio actual / `estimate_messages_cost` (`ai_processor/services.py:5721`), and that value replaces 3.0.
   (c) The monthly drift task also reports a suggested factor. A person applies it; nothing self-adjusts.
   (d) Tier-4 estimates carry `source = "rough"` and p90 × 1.5. About +0.5 day in B1b.
7. **Settled: emails.** The existing billing mails go through `AutoGrader.tasks.send_email_task` (`AutoGrader/tasks.py:90`, retrying; used at `billing/services.py:2198` and in `billing/license_service.py`) and are not gated by any `notify_*` flag. Those flags (`users/models.py:408–424`, read via `users.services.get_opted_in_school_admins`, `users/services.py:202`) cover activity mail only. B1's mails are billing/account mail: queued, expiry warning, promoted, expired, low-balance (one per level per cycle), request received (admin) and request answered (teacher). They follow the billing precedent: `send_email_task`, always sent, with no new opt-out flag.
8. **Settled: screen.** The teacher credits screen reads `GET credits/wallet` (`billing/views.py:1463`), serialized by `CreditWalletSerializer` (`billing/serializers.py:873`). `balance_status` (and `reserved_credits`, spendable) are added there.
9. **Moved to the B1b survey.** The count of synchronous AI views with a non-JSON-object body is part of B1b's NOTES survey, written before code (it only affects where `ai_job_id` is exposed). No design depends on it.
10. **Settled (SM ruling): index built concurrently regardless of size.** `AddIndexConcurrently` in an `atomic = False` migration, following the repo precedent `billing/migrations/0051_creditusagelog_task_id_index.py` and `0061_add_audit_table_time_indexes.py`, and docs/MIGRATIONS.md line 93. The production row count is no longer needed. This works only with the FK declared `db_index=False` and its constraint added `NOT VALID` then validated (review r1 m1; §4).
11. **Settled (SM ruling): new FINANCIAL retention class, 7 years.** Today `CREDIT_TRANSACTION` falls in `GENERAL` (365 days: `audit/enums.py:40–45`, `audit/volume.py:55–58`), so the who/when/why of every charge is swept after a year, while the ledger rows themselves are never deleted (NFR-CRD-02). Add `FINANCIAL` (7 × 365 days) to `RetentionClass`, `RETENTION_DAYS` and `retention_class_of`, covering `CREDIT_TRANSACTION` and B1's new credit, purchase, refund and credit-request actions. Rows carry ids and amounts, no student content. Follow-up when Epic B lands: one line in docs/GRADE_A_PLUS_BACKEND_DATA_PRIVACY_SECURITY_COMPLIANCE.md (it states 12 months / 3 years). B1a step 1, about +0.5 day. No retag migration of existing rows: the sweep keeps FINANCIAL actions by action (review r2 M-g; §4). **History older than 365 days at deploy is already gone**: the current sweep will have deleted those credit events, so the 7 years start from what still exists on the deploy date (the ledger rows themselves are complete). The rollback effect is in §4 (review r3).
12. **05 Q7, audit vocabulary:** B1 adds its four actions itself unless the Feature Lead rules for one shared task.
13. **Settled: Gate 6 measurement.** Run M = 500 billed single calls with the provider stubbed, flag off and then flag on, on the same DB. Report the median and p95 added wall time per call, and the bytes per `AIJob` row (`pg_total_relation_size` / rows). Project rows per day from `audit/volume.py`'s derived rates. Pass: added p95 ≤ 5 ms per call, with storage per month stated in NOTES. On a fail, use the fallback already described: a wallet-counter hold for `SINGLE_CALL`, with the sweep sourced from the hold counter's timestamp.
14. **Effort** (about 39 days including gate evidence, after reviews r1–r4) assumes one engineer and excludes SM review, staging proof and the Epic B strict run.
15. **For the owner: two documented deviations from NFR-CRD-03** (review r1 m2, rewritten after reviews r2 and r3; §3.5). NFR-CRD-03 says "no process can spend credits that were not reserved".
    - **(a) Estimate-versus-actual overrun.** B1 reserves every anticipated call's estimated cost before the call runs, including any shortfall beyond the paper's hold (§3.4). But an AI call's actual cost can exceed its estimate, and the provider has already been paid by then. In that case B1 bills the excess from the teacher's free balance (never another job's or item's hold, never below zero) and records it in `AIJob.overrun_charged` (metric `credit_overrun_charged`). Only what cannot be billed at all, because no free balance is left, goes to `AIJob.unbilled_credits` (metric `credit_overrun`). The billed excess is bounded by the spendable balance at settlement.
    - **(b) Late door upload, an explicit exemption (SM decision, review r3 r3-b).** A door upload whose message comes back after its hold was returned runs under a new upload job. If that job cannot reserve, the file is still processed, because the file exists only in the message and the public promise is that work is never lost. Its cost goes to `overrun_charged` from free balance, then to `unbilled_credits`, bounded to one file's extraction.

    The requirement is met except for these two recorded cases, which are deviations, recorded rather than hidden.
