# 08 — Epic B1 Implementation Plan (credit holds, estimate band, queued work, credit requests)

**Status:** draft for SM review. Nothing here is implemented.
**Companion to:** [05_epics_b_to_i_roadmap.md](05_epics_b_to_i_roadmap.md) (§2.4 B0/B1 split, §3.1 Epic B, §7 plan contents),
[01a_requirements_specification.md](01a_requirements_specification.md) (FR-B-02…09, FR-B-11; NFR-CRD-01…05; NFR-REL-01/02; NFR-TST-06/07/11; D-01, D-02, D-04, D-A8),
[03_architecture.md](03_architecture.md) (§2.2, Stage 2, X-3, X-8),
[03a_data_model.md](03a_data_model.md) (§2.2 `AIJob`, §3.4 `CreditRequest`, §4.5 `BackgroundProcessingTask`),
[06_epic_b0_implementation_plan.md](06_epic_b0_implementation_plan.md) (style; the zero-credit guards B1 sits beside).
**Size:** L. About 18 working days for one engineer, including each slice's gate evidence, in three slices (§2.1). SM review, the staging proof and the Epic B strict run are not included.
**Code facts are from `task/epic-b0-r2` at d808a9ce** (beta f5d2d577 plus B0). Anything I did not
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

`reason_code`, `retry_count` and `item_index` exist (`students/models.py:~590–606`), and so does `trace_id`, all from Epic A. **Only `job` (FK → `AIJob`) is new.**

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
| 1 | Schema (additive):<br>• `AIJob`<br>• `CreditWallet.reserved_credits` and the two warning fields<br>• `BackgroundProcessingTask.job`<br>• `CreditRequest`<br>Model tests, `makemigrations --check`, migration reversal test | 1.5 |
| 2 | Hold service `billing/holds.py`:<br>• reserve / settle / release<br>• `spendable_credits()`<br>• the stale-hold sweep<br>• the invariant check (§3.2–3.5)<br>Unit tests | 2.5 |
| 3 | Wire the holds into `execute_graded_task`:<br>• single-call hold, replacing `:5640–5649`<br>• the metered unit at the five refund-scope sites<br>• hold at the upload door<br>Behind the `BILLING_HOLDS_ENABLED` flag | 2 |
| 4 | Estimate band from `AICallRecord`, verdict, the `estimate` endpoint, and the backtest command (§3.6) | 2 |
| 5 | Batch jobs:<br>• `grade_batch_async` / grade-all create an `AIJob`<br>• reserve per paper<br>• queue the remainder<br>• promotion and expiry sweep<br>• promotion triggered when a grant lands<br>• warnings and notifications<br>• fit with S7c (§3.7) | 3 |
| 6 | Threshold warnings, actual cost in the job API, the gap metric (§3.9–3.10) | 1 |
| 7 | `CreditRequest`:<br>• API<br>• admin list with counts<br>• approve (→ `initiate_overage_purchase`) and decline<br>• the purchase intent moves the request to `GRANTED`<br>• audit<br>• role matrix rows (§3.8) | 2.5 |
| 8 | FR-B-08 forced-failure proofs on the three grant paths (§6.3 F-1 to F-3) | 0.5 |
| 9 | Gate evidence: mutation matrix, barrier concurrency (local, then the PG 16 CI job), adversarial and failure runs, NOTES | 1 per slice, so 3 |
| | **Total** | **about 18 days** |

The order is a hard sequence from 1 to 3. Step 4 can start in parallel with step 3. Step 7 depends only on step 1 and the existing purchase service. Step 8 is independent.

### 2.1 Slices (for gating; nothing lands before all of Epic B)

| Slice | Steps | Days (gate included) | What it proves |
|---|---|---|---|
| **B1a — holds** | 1 (all schema except `CreditRequest`), 2, 3, 8 | about 7.5 | FR-B-07, NFR-CRD-03/04, FR-B-08 atomicity |
| **B1b — jobs, estimate, queue** | 4, 5, 6 | about 7 | FR-B-03/04/05/06/09, X-3, X-8, NFR-REL-02 |
| **B1c — credit requests** | 1 (`CreditRequest` migration), 7 | about 3.5 | FR-B-11, decision 4 |

Each slice gets its own gate on the Epic B branch: NOTES first, red by call-site revert, exact Ran, mutants with fragments, a break run for edited pre-existing tests, and makemigrations. B1b and B1c can run in parallel after B1a if a second engineer is free.

---

## 3. Design

### 3.1 `AIJob` (03a §2.2, with these changes)

The columns are as 03a lists them, plus:

| Change | Why |
|---|---|
| `job_type` gains `SINGLE_CALL` and `UPLOAD` | Every hold is a durable row (§3.2) |
| `requested_by` may be a student | Student uploads bill the teacher, and `wallet` is the teacher's |
| New `unbilled_credits` (int, default 0) | Overrun absorbed at settlement (§3.5) |
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
- For `SINGLE_CALL` and `UPLOAD` jobs, only `RESERVED → RUNNING → SETTLED | FAILED` occurs.

Each transition is a conditional UPDATE on `(id, status)`, and its row count is the result (NFR-REL-01). Every terminal transition releases what is left of the hold in the same transaction as the status change.

### 3.2 Holds: `billing/holds.py`

**Spendable balance.** It is `total_remaining_credits() − wallet.reserved_credits`, floored at 0. Every place that today answers "can this teacher spend?" reads it when the flag is on. That covers the upload door, `can_user_access_ai`'s zero check, the estimate verdict and the warnings.

**Reserve** (`reserve(wallet, amount, job)`). One short `transaction.atomic()`:
1. `w = lock_wallet_first(wallet)`.
2. `avail = w.total_remaining_credits() − w.reserved_credits`.
3. **One conditional UPDATE:**
   `UPDATE billing_creditwallet SET reserved_credits = reserved_credits + :n WHERE id = :w AND reserved_credits + :n <= :total_remaining`.
   A row count of 1 means granted.
4. In the same transaction: `AIJob.reserved_credits += n` and `reservation_claimed_at = now()`, where `reservation_claimed_at` is the fencing token.

There is **no network I/O inside this transaction.** `n` can be smaller than asked for in batch mode: reserve whole papers that fit (§3.7).

**Settle** (`settle(job, actual, unit_token)`). One `transaction.atomic()`:
1. `lock_wallet_first`.
2. `chargeable = min(actual, job_hold_left + (total_remaining − reserved_credits))`. A settlement never takes another job's hold.
3. `consume_credits(chargeable, …)`: the existing method, ledger and usage log unchanged, so B0's guards apply.
4. `reserved_credits −= min(job_hold_left, actual)`.
5. `job.consumed_credits += chargeable`, and `job.unbilled_credits += actual − chargeable`.
6. The fencing check: the UPDATE on `AIJob` includes `reservation_claimed_at = :token`. A settlement from a stale or redelivered worker matches zero rows and writes nothing.

**Release** (`release(job)`). It returns `reserved − consumed` to the wallet counter, records it in `released_credits`, and is idempotent through the status transition.

**Stale-hold sweep.** Every 5 minutes, under `AutoGrader/beat_locks.py`'s decorator. It releases holds on `SINGLE_CALL`/`UPLOAD`/`RUNNING` jobs whose `updated_at` is older than `GRADING_CLAIM_STALE_AFTER` (`students/services.py:306`) plus a margin. This covers a worker killed between reserve and settle.

**Invariant.** `wallet.reserved_credits = Σ(open jobs' reserved − consumed − released)`. It is checked:
- by a test after every concurrency run;
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
- **If a metered unit is open** (a ContextVar in `billing/holds.py`, the same idiom as `billing/refunds.py` and `billing/zero_credit.py`), the call draws on that unit's job. There is no new reservation per call, and after the response the actual usage is **added to the unit's tally**. Nothing is charged until the unit ends.
- **Otherwise**, the call opens a `SINGLE_CALL` job:
  1. reserve the existing per-call estimate;
  2. if the reserve is refused → `InsufficientCreditsError`, **before the provider call**. This replaces `:5640–5649`;
  3. after the response, settle `actual` and release the rest. On an exception, release only.

`call_record.note_estimate` and `note_billed` are unchanged. `record_billing_task_id` stays for callers still under a refund scope while the flag is off.

**Chokepoint.** The provider is still reached only through `__ai_model` from `execute_graded_task`; B0's AST test is unchanged and must stay green. B1 adds one AST assertion: inside `execute_graded_task`, the metered `__ai_model` call is dominated by a hold call (§6.2 M-B4).

### 3.4 The metered unit, replacing `billing_refund_scope` (decision 2)

`metered_unit(job, item)` is a context manager:
- **On entry**, it opens the tally.
- **On a normal exit**, it settles the tally once (§3.2 settle), with one `CreditUsageLog`/`CreditLedger` set per bucket as today. `task_id` is the item's id, so `refund_credits` still works for a later manual refund.
- **On an exception**, it settles nothing and leaves the hold to the job's next item or to release.

It replaces `billing_refund_scope` at the five sites in §0.8 when the flag is on. With the flag off, the old scope runs unchanged. Nesting follows the refund scope's rule: an inner unit's tally passes to the outer unit on success.

**Second opinion.** Today `:4574` catches `InsufficientCreditsError` and flags the paper. Under a unit, the second grader draws on the same paper's hold. If the paper's hold and the free balance are both used up, the same exception is raised and the same flag is set. The behaviour is unchanged.

### 3.5 Overrun

The per-paper hold is the paper's p90 (§3.6). If the actual usage exceeds hold plus free balance:
- the excess is **absorbed and recorded** in `AIJob.unbilled_credits`;
- the metric `credit_overrun` is emitted, with `job_type` and an amount bucket.

The balance never goes below zero, no other job's hold is taken, and the paper is not failed: the work was done. The metric threshold is in §7.

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
4. a cold-start fallback: the existing `estimate_messages_cost` × 3. The factor is **[UNVERIFIED]**, to be set by the backtest.

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

**Create.** The grade-batch and grade-all routes (which launch `grade_batch_async`, `assignments/tasks.py:1436`) create the `AIJob` (`BATCH_GRADING`) with the estimate fields filled in. Every `BackgroundProcessingTask` item gets `job` and `item_index`, as now.

**Reserve.** `k = min(n, floor(spendable / p90_paper))` papers get held, at `held_per_item = p90_paper` each. Items `1..k` are launched as today, through `grade_engine_async` (`:646`).
- If `k = 0` and the teacher proceeds (D-A8), the job goes straight to `QUEUED_PENDING_CREDITS`.
- Items `k+1..n` stay `PENDING` and are **not launched**: no Celery message exists for them.

**Run.** Each item runs inside `metered_unit(job, item)`. When the last held item finishes:
- if unheld items remain → `QUEUED_PENDING_CREDITS`, with `stopped_at_item = k+1`, `queued_at` and `expires_at`;
- otherwise → `SETTLED`.

**Fit with S7c.** `credits_exhausted_at` stays as it is for the flag-off path. With the flag on, an item can no longer find the wallet empty mid-call, because it holds its credits. S7c's stop then fires only for items without a hold, which do not exist. The guard stays as defence in depth.

**Promotion.** Two triggers:
- a sweep every 5 minutes, beat-locked;
- an `on_commit` hook on every grant path: `_grant_overage_blocks`, the monthly refresh and manual grants, scoped to that wallet.

For each queued job, oldest `queued_at` first, the promoter reserves for as many of the remaining papers as fit, launches them, and moves the job to `RESERVED`. The papers are already stored (§0.9), so nothing is re-uploaded.

**Lifetime.**
- `expires_at = queued_at + SubscriptionPlan.queue_expiry_days` (default **14**; owner ruling 2026-10-29). Both values are fields on `SubscriptionPlan`, editable by the super admin wherever plans are edited today, so the owner can change them without a deploy. `expires_at` is fixed when the job queues; a later change applies to newly queued jobs only.
- At `expires_at − SubscriptionPlan.queue_expiry_warning_hours` (default **48**), a warning email goes out and `warned_expiry_at` is set.
- At `expires_at`, the job becomes `EXPIRED`. Unlaunched items become `FAILED` with `INSUFFICIENT_CREDITS_MID_BATCH`, which is retryable (`students/item_retry.py`). An expired batch can still be resumed by hand after a top-up, so **nothing is lost**.

**Notifications.** The teacher is told when a job:
- is queued (with counts done and left, and a link to buy or request credits);
- is about to expire;
- is promoted;
- expires.

The channel is email, through the existing opted-in notification helpers **[UNVERIFIED: which helper and opt-in flag; `students/services.py:1079` is the pattern]**. Each state is also exposed in the job API.

### 3.8 `CreditRequest` (decision 4, FR-B-11)

**Table:** `billing_creditrequest`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `teacher` | FK → `CustomUser`, CASCADE | The requester |
| `license_subscription` | FK → `LicenseSubscription`, CASCADE | Fixes 03a's `school` (§0.7). The admin is `license_subscription.admin_user` |
| `requested_blocks` | PositiveInt | In overage blocks, the unit the purchase uses |
| `message` | CharField(500) | Teacher's note; no student data (validated by length only) |
| `status` | CharField | `PENDING` / `APPROVED` / `GRANTED` / `DECLINED` / `WITHDRAWN` |
| `ai_job` | FK → `AIJob`, SET_NULL, null | The queued job that prompted the request, if any |
| `purchase_intent` | FK → `LicenseOveragePurchaseIntent`, SET_NULL, null | Set on approve (Stripe route) |
| `offline_request` | FK → `LicenseOverageOfflineRequest`, SET_NULL, null | Set on approve (offline route) |
| `decided_by_id`, `decided_at`, `decline_reason` (CharField 500) | | |
| `created_at`, `updated_at` | | |

Index on `(license_subscription_id, status, created_at DESC)`, used for the admin's inbox and counts. A partial unique index on `(teacher_id) WHERE status = 'PENDING'` allows one open request per teacher.

**States:**

```
PENDING ─approve─► APPROVED ─intent COMPLETED / offline approved─► GRANTED
   │                  └─intent FAILED, or checkout abandoned past expiry─► PENDING (note recorded)
   ├─decline─► DECLINED
   └─teacher withdraws─► WITHDRAWN
```

"Answered" means anything other than `PENDING`.

**Approve** calls `initiate_overage_purchase(license_sub, admin, total_blocks, {teacher_id: blocks}, payment_method=…)` unchanged, then links the intent or offline request.
- `GRANTED` is set when the webhook or offline approval fulfils the intent. A hook goes after `_handle_license_overage_checkout_completed` and `approve_overage_offline_request`, in the same transaction.
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

Approve and decline are conditional UPDATEs on `status = 'PENDING'`. A double-click gives one effect and one 409.

The admin is notified by email and by the inbox count. The teacher is notified by email when the request is answered.

**No in-app notification bell (owner ruling 2026-10-29).** Notification is email plus the inbox page and its count badge, nothing else.

### 3.9 Threshold warnings (FR-B-05)

At every settlement, compare spendable with the cycle allocation from `plan_remaining_credits` (`billing/models.py:~700`). The levels are 25%, 10% and 0%.
- One email per level per billing cycle. It is recorded in two new `CreditWallet` fields, `low_balance_warned_level` and `low_balance_warned_cycle_start`.
- The level is exposed as `balance_status` on the existing wallet or usage endpoint **[UNVERIFIED: which serializer]**.

`BetaProfile.has_hit_80_percent` and `has_hit_cap` are analytics flags, not warnings. They are left alone.

### 3.10 Actual cost and the gap metric (FR-B-09, NFR-OBS-03)

- `AIJob.consumed_credits` and `unbilled_credits` are the actual cost.
- `GET /api/v1/billing/jobs/{id}` (owner or license admin) returns the estimate, verdict, actual, item counts, `stopped_at_item`, state and `expires_at`.
- Synchronous AI endpoints add `ai_job_id` to their JSON responses through a shared helper, so a client can fetch the actual cost **[UNVERIFIED: the number of views whose body is not a JSON object]**.
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
| `billing`: create `AIJob` | Additive | Yes, drop | New table, so indexes need no `CONCURRENTLY` |
| `billing`: `CreditWallet.reserved_credits` (int, default 0, `db_default=0`), `low_balance_warned_level`, `low_balance_warned_cycle_start` (null) | Additive | Yes | `db_default` so code that is rolled back still inserts (H-56 rule) |
| `students`: `BackgroundProcessingTask.job` FK, null, SET_NULL | Additive | Yes | The FK index is built with `AddIndexConcurrently` in a non-atomic migration if the table is large **[UNVERIFIED: row count in production]** |
| `billing`: create `CreditRequest` | Additive | Yes | Slice B1c |

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
- the job's own promotion.

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
| NFR-REL-01 | Settle, release, promote and decide each delivered twice give one effect (the fencing token / status UPDATE) |
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
| M-B6 | The fencing token dropped from the settle UPDATE | Duplicate-settle test |
| M-B7 | Release not idempotent (no status condition) | Double-release test |
| M-B8 | Verdict computed against p50 | FR-B-04 boundary test |
| M-B9 | The promoter launches every remaining item, ignoring `k` | Promotion test (`reserved ≤ spendable`) |
| M-B10 | Expiry deletes the items instead of failing them as retryable | Expiry test (item still retryable) |
| M-B11 | The stale-hold sweep's age cutoff removed | Sweep test (a fresh hold must survive) |
| M-B12 | `CreditRequest` approve without the `status='PENDING'` condition | Double-approve test |
| M-B13 | `reserve()`'s `assert_not_zero_credit(CREDIT_RESERVE)` removed | Z-1 |
| M-B14 | Overrun charged beyond the available balance instead of absorbed | Overrun test (`InsufficientCreditsError` raised, or a negative-bucket assert) |

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
| Gate 6 | Added latency of reserve and settle per call, measured as p50/p95 over 1,000 calls; the estimate endpoint's cost on 90 days of `AICallRecord` (index check: `processing_task_id`, `created_at`) |

---

## 7. The six items from 05 §7

**1. Audit actions added.** These are the values 05 §2.5 listed for Epic B, plus the credit-request ones. Retention classes come from `audit/volume.py:61` `retention_class_of`.

| Action | Emitted when | Retention |
|---|---|---|
| `AIJOB_QUEUED` | A job enters `QUEUED_PENDING_CREDITS` | General (12 months, D-A6) |
| `AIJOB_EXPIRED` | A job expires | General |
| `CREDIT_REQUEST_CREATE` | A teacher creates a request | Same class as `CREDIT_TRANSACTION` **[UNVERIFIED: which class that is]** |
| `CREDIT_REQUEST_DECIDE` | Approve, decline or withdraw (`decision` in the metadata) | Same as above |

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
| 2 Mutation | Yes | §6.2, 14 mutants |
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
| `billing/license_service.py`, `billing/stripe_service.py` | `on_commit` promotion hook and the `CreditRequest` `GRANTED` hook after fulfilment (two small hunks each) |
| `ai_processor/services.py` | `execute_graded_task` holds; the unit at `:4635` |
| `students/services.py`, `assignments/file_uploads.py` | The unit replaces the refund scope at four sites (flag on) |
| `assignments/tasks.py` | Batch routes create the `AIJob`, reserve, and leave the rest unlaunched |
| `assignments/upload_door.py` | Hold at the door |
| `students/models.py` | `BackgroundProcessingTask.job` |
| `audit/enums.py`, `audit/metadata.py` | Four actions and the allowlist keys |
| `AutoGrader/settings.py` | Two flags and three beat entries |
| Tests: `billing/tests/test_holds*.py`, `test_estimates.py`, `test_credit_requests.py`, `test_grant_atomicity.py`, `ai_processor/tests_metering_holds.py` | New |
| `docs/evidence/epic-b1a/`, `-b1b/`, `-b1c/` | Evidence |

`ai_processor/services.py` is 6,383 lines and shared with other streams. B1's edits there are confined to `execute_graded_task` and `grade_student_submission`.

---

## 9. Open items and unverified claims

1. **EPQ behaviour** (§0.3): whether a conditional UPDATE with a bucket subquery over-reserves under READ COMMITTED. The design does not depend on the answer (it takes the wallet lock), but M-B2's killing test does. R-4 settles it on PG 16 and 18.6.
2. **FR-B-08 wording** (§0.6): there is no admin balance to debit. Record that the requirement is met by allocate-at-purchase.
3. **Decided (owner, 2026-10-29):** teachers skipped at fulfilment after the school paid. The other teachers are granted as now; the skipped share is **refunded automatically** to the school (Stripe partial refund through the existing `billing/payment_refunds.py` path; for offline payments, an offline refund record for the super admin), and the admin is notified. Added to B1a: about **+1.5 days**, with a test that forces a teacher to leave between checkout and webhook.
4. **Decided (owner, 2026-10-29):** queue lifetime 14 days, warning 48 hours before, **as editable settings** (`SubscriptionPlan.queue_expiry_days`, `queue_expiry_warning_hours`) (§3.7). Migration adds both with defaults; about +0.5 day in B1b.
5. **Decided (owner, 2026-10-29):** uploads are not queued (§0.9). Refuse at the door with a hold.
6. **Cold-start factor** (§3.6): `estimate_messages_cost × 3` is a placeholder until the backtest sets it.
7. **Notification helper and opt-in flag** for the teacher emails (§3.7, §3.9) **[UNVERIFIED]**.
8. **Which serializer** carries `balance_status` (§3.9) **[UNVERIFIED]**.
9. **How many synchronous AI views** have a body that is not a JSON object, for `ai_job_id` (§3.10) **[UNVERIFIED]**.
10. **`BackgroundProcessingTask` row count in production** (§4), which decides whether its index is built with `AddIndexConcurrently` **[UNVERIFIED]**.
11. **Retention class of `CREDIT_TRANSACTION`** (§7.1) **[UNVERIFIED]**.
12. **05 Q7, audit vocabulary:** B1 adds its four actions itself unless the Feature Lead rules for one shared task.
13. **The cost of one `AIJob` row per billed call** (`SINGLE_CALL`). It is comparable to `AICallRecord`'s one row per call, which already exists. Gate 6 measures it. If it is too costly, single calls could use a wallet-counter hold with no row, but the stale-hold sweep then needs a different source.
14. **Effort** (about 18 days including gate evidence) assumes one engineer and excludes SM review, staging proof and the Epic B strict run.
