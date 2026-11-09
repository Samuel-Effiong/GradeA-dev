# 08b — Epic B1b plan amendment (estimate, batch queue, kept files, flag-on)

**Status:** APPROVED by the owner 2026-11-09, with the SM's recommendations on Q1–Q11. The 16 new texts in §7 are NOT approved yet: they are built behind their `_APPROVED` constants, set False, with no text live.
**Amends:** [08_epic_b1_implementation_plan.md](08_epic_b1_implementation_plan.md) ("the plan"). Where this document and the plan disagree, this document wins for B1b. The plan file itself is not edited.
**Code facts are from `origin/staging` at 240567975** (B1a accepted and pushed, `BILLING_HOLDS_ENABLED` off). The B1a record is [docs/evidence/epic-b1a/NOTES.md](../../evidence/epic-b1a/NOTES.md) ("NOTES"). Anything I did not confirm in code is marked **[UNVERIFIED]**.
**Author:** fixes-worker (Lane 1), for the SM (grade-automator-plus-12) and the owner, 2026-11-08.

---

## Summary in plain terms

**What B1b does for teachers:**
- **Before a batch runs,** the teacher sees an estimate of what it will cost: a likely figure and a high figure. They also see a verdict on whether their balance is enough: "enough", "might not be enough" or "not enough". The estimate is advice only. Nobody is blocked by it.
- **If credits run out partway through a batch,** the papers already graded stay graded. The rest wait in a queue and start again automatically as soon as credits arrive. They wait for **30 days** (the owner's decision). The teacher gets an email when the batch is queued, 48 hours before the queue expires, when it restarts, and when it expires.
- **Uploaded files are kept for 30 days,** so a teacher never has to upload a file again to retry it. After 30 days the file is deleted, and the screen says so.
- **Teachers get an email** when their balance falls to 25%, to 10% and to 0% of their monthly credits. They get one email per level per month.
- **After a job finishes,** the teacher and the school admin can see what it really cost. Internally, we track how far the estimates are from the real cost.

**What must happen before credit holds are switched on (the B1a leftovers):**
- Fix one bug, **FB-1**: deleting an upload item at the wrong moment can lock some of a teacher's credits forever.
- Update **45 existing tests** that assume holds are off.
- Make each AI call cheaper to bill. Today holds add 22 database queries and about 33 ms per call, which is within the owner's 40 ms budget. The target is to roughly halve that.

**What the owner decides:**
- This plan.
- The **new texts** in §7, one by one. Nothing goes live without approval.
- Storing student files with our file-storage provider, Cloudinary (§3). This is a new kind of student data we keep.
- When holds are switched on for real (§6). B1b ends with an evidence pack on staging; production stays off until the owner says go.
- The answers to the open questions in §9.

**Size:** about **26.5 working days** for one engineer, including each step's gate evidence. That is twice the plan's 12.5 days. The extra time is for the kept files (F4), the flag-on fixes B1a left for B1b, and the query trimming. SM review and the production switch-on are not included.

---

## 1. The plan's B1b slice, re-checked against today's code

The plan's B1b slice is steps 4, 5 and 6 (§2.1). The design in plan §3.6, §3.7, §3.9 and §3.10 stands, except where this section or §2–§5 changes it.

### 1.1 Corrected line references (plan cites d808a9ce; today is 240567975)

| What | Plan says | Today |
|---|---|---|
| `execute_graded_task` | `ai_processor/services.py:5480` | `:5484`; the B1a hold is `credit_holds.hold_for_call` at `:5667` |
| `estimate_messages_cost` / `estimate_total_token` / flat `+= 20000` | `:5721` / `:5774` / `:5790` | `:5754` / `:5807` / `:5823` |
| `grade_student_submission`'s unit | `:4635` (refund scope) | `metered_unit(` at `:4637` (B1a) |
| `AICallRecord` | `ai_processor/models.py:254` | unchanged |
| `grade_engine_async` | `assignments/tasks.py:646` | `:648` |
| `grade_batch_async` | `:1436` | `:1466` |
| `_relaunch_failed_dispatches` | `:1616` | `:1646` |
| `auto_grade_due_assignment` | `:1668` | `:1698` (`_dispatch_tracked_grading` call at `:1758`) |
| Batch dispatch helper | not cited | `_dispatch_tracked_grading` `assignments/tasks.py:1555`, `_launch_tracked_grading` `:1583` |
| Grade-all route | "the grade-batch and grade-all routes" | `grade_all_submission` `assignments/views.py:1791`. It **has its own copy** of the dispatch loop (from `:1812`), not the helper. The scheduled route `schedule_grade_all_submission` (`:1872`) runs `grade_batch_async` through a one-off `PeriodicTask` (`:1896–1903`) |
| `CreditLedger.record` / `after_bulk_create` | `billing/models.py:1505` / `:~1515` | `:1587` / `:1596` |
| `refund_credits`'s `bulk_create` | `billing/services.py:~1567` | `refund_credits` `:1445`, `bulk_create` `:1567` |
| `total_remaining_credits` / `plan_remaining_credits` | `:683` / `:~700` | `:762` / `:782` |
| `CreditWallet.low_balance_warned_level` / `_cycle_start` | new in B1 | exist (B1a 0074), `billing/models.py:724`, `:729` |
| `CreditWalletSerializer` / `GET credits/wallet` | `billing/serializers.py:873` / `billing/views.py:1463` | unchanged |
| `SubscriptionPlan.queue_expiry_days` / `_warning_hours` | new in B1a | exist (0074), `billing/models.py:424`, `:429`, defaults 14 and 48; **nothing reads them** |
| `BillingSettings` | new in B1b | exists (B1a), `billing/models.py:1935`: `cold_start_cost_factor` 3.0, `unstarted_backstop_hours` 36, `hold_sweep_margin_minutes` 15. **Not registered in Django admin** (`billing/admin.py` registers only `StripeEvent` and `LicenseStripeMutationIntent`), although the plan says it is edited there. **It stays unregistered** (SM ruling 2026-11-08, §1.3 item 5) |
| `AIJob` estimate/queue columns | new in B1 | exist (B1a): `sufficiency_verdict`, `held_per_item`, `completed_items`, `failed_items`, `stopped_at_item`, `queued_at`, `expires_at`, `warned_expiry_at`, `cancel_requested_at`, `meta`. **No p50/p90 columns** (see §1.3) |
| `AIJobStatus` | 03a's states | all eight exist: `ESTIMATED` … `CANCELLED` (`billing/models.py:165–172`) |

### 1.2 What B1a already built that B1b uses

B1a built more of the machinery than the plan's slice table assumes:
- `billing/holds.py` (1,145 lines): `reserve`, `settle`, `fail_item`, `finish_job`, `claim_item_start`, the per-item sweep and job-level branch (`sweep_stale_holds`, `:584`), `check_invariants`, `metered_unit`, the door job, and the successor jobs for late and retried items.
- The beat entries `sweep-stale-credit-holds` (every 300 s) and `check-credit-hold-invariants-daily` (`AutoGrader/settings.py:941–950`). **The sweep does nothing while the flag is off.** That matters for rollback (§6.3).
- **Not built:** no `BATCH_GRADING` job is ever created. Batch items today run as `SINGLE_ITEM` jobs through the entry rule, one per paper (NOTES §14). Nothing queues, promotes or expires.

So B1b step 5 is mostly wiring: the batch dispatch, promotion and expiry sit on top of the B1a hold service.

### 1.3 Step 4: estimate band and verdict (plan §3.6). Changes found in the survey

1. **A guard forbids reading `AICallRecord`.** `ai_processor/tests_call_record_static.NothingReadsOrExposesTheTable` allows only `ai_processor/models.py`, `ai_processor/call_record.py` and the label in `students/student_data_map.py` to name the model. The estimate must read it.
   - **Proposal:** add exactly one file to `ALLOWED`: `billing/estimates.py`.
   - That file reads aggregates only (sums and counts of `charged_credits` per `processing_task_id`). It never returns rows, and it never exposes the table through a route, serializer or admin. The guard's "nothing exposes it" checks stay as they are.
   - **SM ruling (2026-11-08): approved**, exactly that one file, aggregates only. A new test pins that `billing/estimates.py` never returns `AICallRecord` rows or querysets: its public functions return numbers and plain dicts only.
2. **Indexes.** `AICallRecord` has an index on `created_at` only. It has none on `processing_task_id`, `assignment_id` or `billed_user_id`. The estimate's three history tiers filter by assignment, by teacher and by shape over 90 days.
   - **Proposal:** add `(assignment_id, created_at)` and `(billed_user_id, created_at)` with `AddIndexConcurrently`, **each in its own `atomic = False` migration** (the house pattern, 0051/0061). This is additive. **SM ruling (2026-11-08): approved.**
   - Gate 6 measures the estimate query on 90 days of rows before and after the indexes.
   - Adding an index changes nothing the static guard pins: it pins columns, not indexes [UNVERIFIED until the guard runs].
3. **Where p50/p90 live on the job.** `AIJob` has `sufficiency_verdict` but no estimate numbers.
   - **Proposal:** three additive nullable columns: `estimate_p50`, `estimate_p90` (int) and `estimate_source` (short char).
   - They are not kept in `meta`, because the gap metric and the job API read them, and JSON is not a typed contract.
4. **The cold start, tier 3b, the drift task and its summary table** are as the plan's §3.6 and §9 item 6 say. The drift-summary table is a new table (additive).
5. **`BillingSettings` is not registered in Django admin** (SM ruling 2026-11-08). An admin page needs new labels, which is owner wording. Plan §9 item 6(b)'s "editable in Django admin" is replaced by **a management command**, `billing_settings`:
   - `billing_settings --show` prints the current row;
   - `billing_settings --set cold_start_cost_factor=2.4` (one field per `--set`, validated by the model field's type and bounds) changes it;
   - each change writes a `STATE_CHANGE` audit row with the field, old and new value, and the operator given by `--by <email>` (required).
   - The command is operator-facing (run on the platform shell), not user-facing, so it adds no app wording. A command rather than env: these are database values the drift task's suggested factor (plan §9 item 6(c)) is applied to by a person, without a deploy. `QUEUE_LIFETIME_DAYS` stays an env setting, as the owner decided (§2).

### 1.4 Step 5: batch jobs and the queue (plan §3.7). Changes found in the survey

1. **One dispatch path.** There are two copies of the batch loop: `_dispatch_tracked_grading` (`assignments/tasks.py:1555`, used by `grade_batch_async` and auto-grade) and `grade_all_submission`'s own loop (`assignments/views.py:1812` onward).
   - **Proposal (SM ruling 2026-11-08: approved):** first a **characterization test**, committed and green on today's code, pinning the view's current behavior: items, indexes, audit rows and the response body. Then the view calls the same helper and the characterization test stays green unchanged.
   - The view calls the same helper. B1b then puts the `BATCH_GRADING` job, the reserve of `k` papers, the per-item claims and the on-commit launch in that one helper.
   - The view's response is unchanged: the same `tasks_data` per item.
   - Plants: one per caller, proving that each one goes through the helper.
2. **Upload batches are not queued** (plan §9 item 5 stands). With F4 (§3) an upload item that stops for credits keeps its file, and the teacher retries it by hand from that file. It does not re-run on its own. §9 Q2 asks the owner to confirm this.
3. **Queue lifetime:** see §2. It replaces plan §3.7 "Lifetime".
4. **The backstop X = 36 hours, owner-decided (2026-11-09).** See §1.6.
5. **Expiry:** as the plan says. Unlaunched items become `FAILED` with `INSUFFICIENT_CREDITS_MID_BATCH`, which is retryable. Grading items need no file; upload items are never queued.

### 1.5 Step 6: warnings, actual cost and the gap metric (plan §3.9, §3.10)

- **Thresholds** as the plan says: 25%, 10% and 0% of the cycle allocation, at settlement, one email per level per cycle. The two wallet fields already exist (B1a).
- **`balance_status`, `reserved_credits` and spendable** are added to `CreditWalletSerializer` (`billing/serializers.py:873`). That adds fields to an existing response, so it is additive for the app.
- **`GET /api/v1/billing/jobs/{id}`** as the plan says, now reading `estimate_p50` and `estimate_p90` (§1.3).
- **`ai_job_id` on synchronous AI responses**: see the survey count in §1.6.

### 1.6 The survey items the plan defers to B1b

| Item | What the repo says | What is still needed |
|---|---|---|
| **Worker concurrency** | `scripts/start-worker.sh:8–9`: `celery … worker --concurrency="${CELERY_WORKER_CONCURRENCY:-4}"`. `docs/ops/railway-services.md:11`: worker replicas "scale as needed". | **The deployed values are not in git.** No longer needed before B1b-6, since X is owner-decided (below). Recorded in NOTES for information if they become available |
| **Largest batch** | **No cap in code.** Grade-all takes every ungraded submission of the assignment (`assignments/views.py:1798`). The upload routes cap each file at 50 MB (`AutoGrader/uploads.py:45`) but not the number of files (`students/views.py:1467`). The nearest bound is roster import's `MAX_ROWS = 2000` (`classrooms/services/roster_import.py:50`), and that bounds one import, not a course | The production maximum: the largest count of submissions in one assignment, read-only from production. No longer needed before B1b-6; recorded in NOTES for information if available. **Recommendation:** add no cap in B1b |
| **The backstop X** | `BillingSettings.unstarted_backstop_hours`, default 36 (B1a) | The formula from review r4: X = largest batch × 25 min ÷ (concurrency × replicas) + 5 h. With the plan's 300-paper Gate 6 batch at 4 × 1: 300 × 25 / 4 = 31.25 h, + 5 = **36 h**, which is today's default. **Owner decision (2026-11-09): X stays at 36 hours, the default.** The production figures are not needed to set it |
| **Non-JSON AI views** (plan §9 item 9) | First pass over the credit-gated views (`HasCreditBalance`): the synchronous AI ones are `students/views.py` `grade` (`:929`), `update_grade` (`:1185`), `teacher_feedback` (`:1105`), `upload_answers` (`:589`), `partial_update` (`:805`); `classrooms/views.py` `student_summary` (`:1824`); `assignments/views.py` `partial_update` (`:610`). Every AI-success response is `Response(serializer.data)`, a JSON object. **One non-AI branch returns a bare string:** `teacher_feedback` `:1161`, "Submission has not be graded yet" | First-pass count: **0** AI-success bodies that are not JSON objects. The B1b NOTES survey re-derives the list from every `execute_graded_task` caller, not only the gated views, before code. `ai_job_id` is added only to JSON-object bodies |

---

## 2. Owner decision 2026-11-08: queued papers wait 30 days (replaces plan §9 item 4)

### 2.1 The setting

- **`QUEUE_LIFETIME_DAYS`**, an environment variable read in `AutoGrader/settings.py` with the existing pattern: `env.int("QUEUE_LIFETIME_DAYS", default=30)`. When it is unset, the value is 30.
- **Validation at start-up:** it must be an integer from 1 to 365. Anything else stops the process at start-up with a clear message. A typo must not silently become 0 days or a huge number.
- **The warning stays 48 hours before expiry.** It is a code constant, `QUEUE_EXPIRY_WARNING_HOURS = 48`, not a setting, because the owner did not ask for one.
  - The warning goes out at `expires_at − 48 h`, or at queue time if that moment has already passed. That case only arises with a lifetime of 1 or 2 days, for example on staging.
- **`expires_at = queued_at + QUEUE_LIFETIME_DAYS`** is fixed when a job queues, as the plan says. Changing the variable affects newly queued jobs only. A test pins this.
- **The same variable is read by every service** (web, worker, beat). It is set at the platform's shared level, not per service, so the expiry sweep and the queue writer cannot disagree. The switch-on checklist (§6.2) verifies it.
- **One variable drives three things:** the queue lifetime, the kept-file lifetime (§3.2), and the day count in the approved texts (§4). That is the owner's intent: a resumed paper always still has its file, and the text always matches.

### 2.2 The unused `SubscriptionPlan` fields (0074, default 14)

**Recommendation: leave them in the schema, unused, and mark them deprecated in their `help_text`. Do not drop them in B1b.** **SM ruling (2026-11-08): approved**; the drop comes later through expand → migrate → contract.

Why:
- **Dropping a column is non-additive** under the house rule (docs/MIGRATIONS.md §2, "Drop a column or table"). It needs the three-deploy expand → migrate → contract process (§3): first stop the code naming the field (state-only `SeparateDatabaseAndState` removal, so the column keeps its `db_default` for any old code still running), and only in a later deploy drop the column, with `# expand-contract-step: contract`.
- **The rollback risk is real here.** B1a's code (on staging now) has both fields on the model, so its ORM selects them. If B1b dropped the columns in one step and was then rolled back to B1a, every `SubscriptionPlan` query would fail. That breaks billing pages, not only B1b.
- **Leaving them costs nothing.** Two small integer columns on a table of a few dozen rows; no serializer (`billing/serializers.py:72–96`) or admin exposes them. Only `billing/tests/test_holds_schema.py:353–360` pins their defaults.
- **The cost of leaving them:** someone might later think they are live. Two guards prevent that:
  - the `help_text` changes to "Deprecated (owner 2026-11-08): unused; the queue lifetime is QUEUE_LIFETIME_DAYS." This is a state-only `AlterField` (help text is not in the database), so it is additive;
  - a static test asserts that no code outside migrations and that test names `queue_expiry_days` or `queue_expiry_warning_hours`.
- **Migration-safety class:** an additive, state-only `AlterField` (help text). `check_migration_safety.py` classifies `AlterField` as review-required. The NOTES record the diff and show that the SQL is empty (`sqlmigrate` prints no statements).
- **The drop is then a separate, later item** for the owner to schedule (§9 Q4), done as a proper contract step once B1b has been live long enough that nobody would roll back past it.

---

## 3. F4: uploaded files are kept, so a resume never needs a re-upload

### 3.1 What happens today

- An uploaded answer or assignment file exists **only inside the Celery message**: `build_async_upload_payload` (`assignments/services.py:703`) base64-encodes the whole file into the task arguments, and `rebuild_uploaded_file` (`:714`) rebuilds it in the worker (`assignments/tasks.py:1048`, `:1330`).
- The three routes that do this are `students/views.py:744` (async answer upload), `students/views.py:1560` (teacher batch upload) and `assignments/views.py:1184` (async assignment upload).
- So an upload item that fails, or stops for credits, can never be retried. `students/item_retry.py:42–51` refuses it with `REUPLOAD`.
- **A side effect worth fixing anyway:** a 50 MB file becomes about 67 MB of base64 inside a Redis message, and stays in Redis until the worker takes it.

### 3.2 The design

**Where the files live.** Cloudinary, the storage provider the project already uses and configures (`AutoGrader/settings.py:1190–1215`, `CLOUDINARY_STORAGE`), as **raw, private** assets.
- They are stored under a dedicated prefix, `kept_uploads/<uuid>`, with an unguessable random name. The name never contains the student's name or the file name.
- Delivery is **private or authenticated, never public**. No URL is ever sent to a browser. The worker downloads the bytes server-side with the API key.
- **[UNVERIFIED]:** whether `django-cloudinary-storage`'s `RawMediaCloudinaryStorage` can upload with `type="private"` or `"authenticated"`. If it cannot, a small `students/kept_files.py` calls the Cloudinary SDK directly. A one-hour spike in the step's survey settles this before any code.
- **Why not the database or a disk:** Postgres would grow by the full file bytes (up to 50 MB a file) and make backups and restores heavier. The web and worker run as separate Railway services, so a local disk is not shared between them.
- **Not in tests:** every test uses an in-memory fake storage. The existing backstop (`BENCHMARK_ARCHIVE_ENABLED` is off under test, `settings.py:1614–1620`) is the precedent, and `AutoGrader.tests_network_guard` already blocks real network calls in tests.

**The record.** A new table, `students.KeptUpload`:

| Column | Notes |
|---|---|
| `id` | UUID |
| `processing_task` | One-to-one → `BackgroundProcessingTask`, CASCADE. One file per item |
| `storage_key` | The Cloudinary public id (the random name) |
| `original_name`, `content_type`, `size_bytes` | What `rebuild_uploaded_file` needs, plus the size for the cost report |
| `sha256` | Lets the worker verify the bytes it downloads |
| `kept_until` | `created_at + QUEUE_LIFETIME_DAYS` (§2) |
| `purged_at` | Null until the bytes are deleted |
| `created_at` | |

**The flow.**
1. **At the door, before the Celery message.** Each of the three routes stores the file, writes the `KeptUpload` row in the same transaction as the item row, and sends a message that carries the `KeptUpload` id instead of the bytes.
2. **If the store fails** (Cloudinary is down), the route falls back to today's behavior for that file: the bytes go in the message, and the item is marked "not kept", so it is never offered for retry from a kept file. **Recommendation, §9 Q3: fall back rather than refuse,** because refusing every upload during a storage outage is worse than losing the retry option for those files. The fallback is counted (metric `kept_upload_fallback`).
3. **The worker** loads the bytes by id and checks their sha256. A mismatch or a missing asset fails the item with a coded, retryable reason.
4. **Retry** (`students/item_retry.py`): an upload item whose `KeptUpload` is present and not purged is retried in place, on the same row, like a grading item. Its `retry_count` moves on and it gets a new settlement key, as for grading (plan §3.4). An upload item whose file was purged, or never kept, is refused with the approved text 2 (§4).
5. **The late door upload** (plan §3.5) changes. The file is no longer lost: it was kept at the door. So a late door item that cannot reserve is failed as retryable, `INSUFFICIENT_CREDITS`, and the approved late-door email says "Your file is kept for 30 days … retry it" (§4).

**Retention and purge.**
- **Retention is at least the queue lifetime, tied to the same setting.** `kept_until = created_at + QUEUE_LIFETIME_DAYS`. The upload happens before any queueing, so a kept file always outlives any queue it could be in. Upload batches are not queued anyway (§1.4).
- **The purge** is a beat task, `purge_kept_uploads`: hourly, beat-locked (`AutoGrader/beat_locks.py`), in batches of 200. For each row past `kept_until` with `purged_at` null, it deletes the asset, then sets `purged_at`. Rows are kept (with `purged_at` set), so the retry refusal can tell the teacher that the file was deleted after 30 days.
- **A file is also deleted early:**
  - when its item succeeds and the submission is written. The file is no longer needed for a resume. **Recommendation, §9 Q3b: delete on success,** because that keeps the least student data;
  - when the student or course is erased (§3.3), and when the row is deleted by cascade.
- **A delete that fails** (Cloudinary error) leaves `purged_at` null, and the next run retries it. A row more than 24 hours past `kept_until` and still unpurged emits `kept_upload_purge_overdue`, so a stuck purge is visible.
- **The bound the evidence proves:** no asset exists past `kept_until` + 1 hour, except while Cloudinary itself is failing, and that case is alerted.

### 3.3 Student data (NFR-CMP-04): this is new retention of student work

- `students.KeptUpload` is added to `students/student_data_map.py` as **DELETE**, matched through its processing task's student, with the purge after `QUEUE_LIFETIME_DAYS` (30) days. That is the proposal and the SM's lean (2026-11-08); it goes to the owner as §9 Q10.
- **Erasure** deletes the Cloudinary asset, not only the row. A `pre_delete` handler cannot do network I/O safely inside the erasure transaction, so the erasure collects the storage keys and deletes the assets in `transaction.on_commit`. Any asset whose delete fails is handled by the purge's overdue alert.
- **Export:** the student export lists that a kept file exists (name, size, kept until), not the bytes. This is an `EXPORT_FIELDS` decision for `students/data_export.py`, made explicit so that `FieldListGuardTests.test_every_field_is_decided_exactly_once` stays green.
- **The privacy document** (docs/GRADE_A_PLUS_BACKEND_DATA_PRIVACY_SECURITY_COMPLIANCE.md) needs one line: uploaded files are kept for `QUEUE_LIFETIME_DAYS` (30) days in Cloudinary, then deleted. **Cloudinary becomes a processor of student work**, where today it holds profile images and benchmark archives. **This is the owner's decision** (§9 Q1).
- **Guards that will see this:** `students.tests_student_data_map`, `students.tests_app_boundaries`, `AutoGrader.tests_network_guard`, `ai_processor.tests_ssrf_guard` and `AutoGrader.tests_credscan`. All 66 run in every near run anyway (§8).

### 3.4 Cost and storage

- **What we know:** files are at most 50 MB each (`AutoGrader/uploads.py:45`). Nothing in the database records upload sizes today, so the average is **not known**.
- **What is held at once (steady state):** roughly uploads per day × average size × 30 days. With files deleted on success (§3.2), most files are held for minutes, not 30 days. The steady state is then driven by **failed and stopped items only**.

| Example, per 100 active teachers | Files per month | Held if every file is kept 30 days | Held if deleted on success (assume 5% fail or stop) |
|---|---|---|---|
| 30 uploads per teacher per busy day × 22 days, 2 MB average | 66,000 | ~132 GB | ~6.6 GB |
| Same, 5 MB average | 66,000 | ~330 GB | ~16.5 GB |

  The 30 per day and the 22 days are NOTES §21d's Gate 6 workload assumptions, reused for uploads. The 2 MB and 5 MB averages and the 5% are **assumptions, not measurements**.
- **Transfer:** each file crosses to Cloudinary once and back to the worker once, or more on retry. Redis no longer carries the bytes.
- **Price:** Cloudinary bills by "credits" that combine storage, transfer and transformations, on plan tiers. **[UNVERIFIED]:** our plan tier and its headroom. The owner or the SM reads the Cloudinary account's usage page before switch-on. I have not quoted a price.
- **The step's evidence measures, on staging:** average and p95 file size, files per day, and the held total after a week. NOTES projects a monthly figure from those.

### 3.5 The §6.3 wording correction: the hold is returned, not carried over

The plan's §6.3 "Failure" row says "the provider times out inside a unit (nothing charged, **hold kept for the next item**)". Since review r3 R3-4, and in B1a's code (`fail_item`, `billing/holds.py:368`), a failed item **returns its whole remaining hold to the wallet** in its fail transaction. Nothing is carried over to the next item. The next item runs on **its own** hold. B1a's test says this already: `test_t1_a_provider_timeout_charges_nothing_and_the_next_item_runs_on_its_hold`.

Corrected text: "the provider times out inside a unit: nothing charged, **the item's hold is returned to the wallet**, and the next item runs on its own hold." This amendment records the correction; the plan file is not edited.

---

## 4. Owner decision: the three approved upload texts take the day count from the setting

The source is `team/approved_wording_2026-11-08.md`, including its amendment: the number of days is filled in from the queue-lifetime setting; otherwise the wording is verbatim. With the default of 30, each text reads exactly as approved.

| # | Where | Approved text (with `{days}` from `QUEUE_LIFETIME_DAYS`) |
|---|---|---|
| 1 | `INSUFFICIENT_CREDITS_MID_BATCH` remediation, `AutoGrader/reason_codes.py:254–256` | "Top up credits, then resume the remaining items. Your files are kept for {days} days, so you won't need to upload them again." |
| 2 | `REUPLOAD`, `students/item_retry.py:47–51` (the `why` of `NOT_RETRYABLE`) | "This upload can't be retried because its file was deleted after {days} days. Upload the file again." |
| 3 | `LATE_DOOR_EMAIL_TEXTS`, `billing/holds.py:1082–1094` | Subject unchanged: "We couldn't process {file_name}". Individual: "We couldn't process {file_name} because your credits ran out while it was waiting. Nothing was charged for it. Your file is kept for {days} days. Top up your credits, then retry it. You won't need to upload it again." License: the same, with "Ask your school administrator for more credits, then retry it." |

**How the day count is filled in:**
- **Text 3** is a format string already; it gains `{days}`.
- **Text 2** is a server constant passed as `why`. It becomes a function returning the string formatted with the setting.
- **Text 1 is the hard one.** Reason-code remediations take no placeholders today, and a test pins that: `AutoGrader/tests_reason_codes.py:258` asserts `"{" not in spec.remediation` for every code.
  - **Proposal:** the remediation stays a plain string, built once from the setting when the catalogue loads (`f"…kept for {settings.QUEUE_LIFETIME_DAYS} days…"`). The pinned test stays as it is and stays green, because the stored remediation contains no `{`. A new test asserts that the built text equals the approved text with `days = 30`, and with an overridden value. That gives no new placeholder machinery and no change to the catalogue rule. **SM ruling (2026-11-08): approved.**
  - Each text also gets a test that pins it verbatim at the default.
- **"1 days":** a lifetime of 1 would read "kept for 1 days". The owner approved only the number. Production uses 30; staging proofs may use 1 (§6). **Recommendation, §9 Q5:** accept "1 days" on staging only, and add no singular wording. Production start-up validation could require at least 2; I have not proposed that, because the owner did not ask for it.
- **The switch.** `LATE_DOOR_EMAIL_APPROVED` (`billing/holds.py:1095`) becomes `True` in B1b, in the same commit as text 3, and only after F4 is built and its gate has passed (§8, step B1b-8). The texts promise a kept file, so they must not go live before the file is kept.

---

## 5. Flag-on prerequisites: all done before `BILLING_HOLDS_ENABLED` can be turned on

### 5.1 FB-1: the reservation leaked when a HELD item is deleted (NOTES §21v-3)

**The bug.**
- Deleting a `BackgroundProcessingTask` row while it is `HELD` leaves its `hold_credits` inside `wallet.reserved_credits` forever.
- The rows are deleted by a student's full erasure, a teacher's course erasure, an assignment cascade, or any other delete.
- In an `UPLOAD` job that already has a settlement, nothing returns those credits. `sweep_stale_holds` (`billing/holds.py:584`) walks item rows, which are gone, and its job-level branch skips jobs that have a `SettlementRecord`. The job never finishes.

**The fix** (proposed in §21v-3, unchanged here):
- A `pre_delete` receiver on `BackgroundProcessingTask`, connected in `ready()` like §21n's.
- For a row with `hold_state = HELD` and a job, it returns that item's hold the way `fail_item` does: the wallet is locked first, the item is set `RETURNED`, `released_credits` is counted on the item and the job, and a `CREDIT_HOLD_RELEASED` audit row is written.
- Then, if no `HELD` item is left, it finishes the job.

**Tests** (`TransactionTestCase`, flag on), one per delete road:
- the instance delete;
- the QuerySet delete;
- the full erasure;
- the course-scoped erasure;
- the assignment cascade;
- the batch-session cascade.

Each test asserts that `wallet.reserved_credits` is back to its starting value and that `check_invariants` passes.

**Plants**, restored by sha256:
- PFB-1, the receiver not connected: every delete road test fails;
- PFB-2, the receiver does not finish the job: the job-finish assertion fails.

**The cost to weigh.**
- The receiver turns off Django's fast delete for `BackgroundProcessingTask`, so a bulk delete or cascade loads the rows and sends signals.
- The step measures a cascade of 1,000 items before and after, and records it in NOTES.
- **Fallback if it is too slow:** filter the queryset to `HELD` rows first in the erasure and cascade paths. That needs an SM ruling.

### 5.2 The §21m flag-on-day list: 45 tests

Each class gets one approach. Every changed test follows the doctrine: the edit is committed with its reason, a break run shows that the test still fails for the right reason, and nothing is weakened to pass.

| Class | Tests | Approach |
|---|---|---|
| **B1, the flag and migration guards** | 6 (`billing.tests.test_holds_schema` ×3, `billing.tests.test_audit_identity_migration` ×3) | These tests are about flag-off behavior: the default is off, and 0074's reverse is refused while the flag is on. Pin them with `@override_settings(BILLING_HOLDS_ENABLED=False)`, so they say what they mean whatever the environment says. Add one flag-on test that 0074's reverse is refused with the flag on, the guard's other half |
| **B2, the on-commit door launch** | 26 (`students.tests_h223_batch_runs_what_fits` ×8, `students.tests_h274_empty_student_answer_doors` ×4, `assignments.tests_h274_empty_upload_doors` ×1, `students.tests_batch_item_results` ×6, `students.tests_upload_credit_door` ×5, `students.tests_credits_mid_batch` ×1, `billing.tests.test_h38_part2_removed_teacher_routes` ×1) | Wrap the request in `self.captureOnCommitCallbacks(execute=True)`, so the deferred launch runs as it does in production. These tests stay `TestCase`, which is faster than `TransactionTestCase`. The assertions are unchanged. They must then pass with the flag **on and off**, so each is run both ways in the red/green evidence |
| **B3, the charge-then-refund ledger shape** | 12 (`billing.tests.test_grading_refund_scope` ×2, `students.tests_answers_unreadable` ×1, `students.tests_async_edit_path` ×1, `students.tests_s6d_extraction_refund` ×1, `ai_processor.tests_grading_pipeline` ×2, `ai_processor.tests_h256_provider_timeout` ×1, `assignments.tests_upload_batch_billing` ×3, `assignments.tests_upload_task_retry_policy` ×1) | These test the **flag-off** refund scope, which stays live code while the flag is off (plan §5). So: pin each as flag-off with `override_settings`, unchanged. Then add a **flag-on twin** for each one's scenario, asserting the hold ledger shape: no usage row for a failed call, one charge at settle, no refund row, the hold returned. The flag-off path keeps its coverage until it is deleted, which is not in B1b |
| **B4, the patched balance** | 1 (`billing.tests_h281_individual_credit_wording … test_r8_execute_graded_task_nothing_left_individual`) | Pin it flag-off (`override_settings`), unchanged. Add a flag-on twin that reaches the refusal on a real path: an estimate above 0, `total_remaining_credits` 0, and `hold_for_call` refuses before the provider with the same individual wording |

**The evidence:** the flag-on exploratory run of §21m is repeated with the same labels plus all 66 guards (§8). The expectation is OK, with **0** failures and the B4/B3 twins counted. Then the flag-off near is repeated, and it must also be OK.

### 5.3 Gate 6: trimming the 22 extra queries

**Where B1a stands** (NOTES §21i, §21k, §21l):
- Holds add 22 queries per call (47 against 25).
- On measurement 3's method, they add **32.6 ms at p95**, within the owner's **40 ms** budget.
- The settle transaction is the biggest part: 34 queries against flag-off's 19, about +13.7 ms.

**The targets.**
- **At most +10 queries per billed single call** (from +22), counted by the §21h profile method, which is re-run and committed.
- **Added p95 of at most 20 ms** on measurement 3's method (`TransactionTestCase`, 2000 calls per mode, interleaved), with the 40 ms budget unchanged as the pass/fail line.
- **A query-count test** (`assertNumQueries` on one billed single call with the flag on) pins the new count, so a later change cannot silently add the queries back.

**Candidates.** These are to be confirmed against the profile before any change; none is assumed:
- fold settle's separate job and item counter UPDATEs into single `F()` UPDATEs;
- drop re-reads (`refresh_from_db`) after a locked UPDATE whose result is already known;
- emit the two hold audit rows (`RESERVED`, `SETTLED`) without their own nested `atomic()`;
- skip the second wallet lock inside `consume_credits` when settle already holds it.

**The rule:** each trim is a refactor with **no behavior change**.
- The full B1a hold test set (Ran 141, NOTES §21j), the B1a mutant battery (all killed again, fragments unchanged) and the invariant checks must stay green after each trim.
- A trim that changes a fragment is a STOP.

### 5.4 Gate 6: the 40 ms p95 budget (owner, 2026-11-08)

The budget stands as the owner set it (§21l). After the trim, measurement 4 is run with the same method as measurement 3 and recorded beside measurements 1–3, never replacing them.
- **Pass:** added p95 of at most 40 ms.
- **Reported against the trim target:** 20 ms.

B1b adds two more Gate 6 measurements:
- the estimate endpoint on 90 days of `AICallRecord`, before and after the indexes (§1.3);
- a 300-paper batch's reserve and promotion, timed.

---

## 6. The switch-on itself (the owner's decision)

### 6.1 What B1b delivers, and what it does not

B1b ends with the flag still **off** in production. The switch-on is a separate, owner-approved step. B1b delivers the code, the gate evidence, and a staging proof with the flag on.

### 6.2 The evidence shown to the owner before production

1. **The test evidence:**
   - the flag-on near and the flag-off near over the same labels plus all 66 guards, both OK, with exact Ran counts declared in advance;
   - the B1a and B1b mutant batteries, all killed;
   - the PG 16 CI run of the concurrency tests. That CI job runs the full suite, so **each run needs the owner's go** (standing rule: no full suite without permission).
2. **The Gate 6 numbers:** measurement 4 against 40 ms, the query count, the estimate query time and the batch timing.
3. **The staging proof, with the flag on in staging only:**
   - a real batch;
   - a forced exhaustion with papers queued;
   - a top-up through overage, and the automatic promotion within 5 minutes;
   - a queue expiry, with `QUEUE_LIFETIME_DAYS=1` on staging, and the warning email;
   - an upload stopped for credits and retried from its kept file, with no re-upload;
   - a kept file purged on time;
   - a late-door email received;
   - a low-balance email at 25%;
   - the estimate band shown before a batch.

   Each comes with screenshots or log lines and the job rows.
4. **A soak:** at least **7 days** with the flag on in staging. During it, the daily invariant check reports **0** `hold_drift`, and no item stays `HELD` past the sweep window.
5. **The settings checklist,** read from the platform: `BILLING_HOLDS_ENABLED` and `QUEUE_LIFETIME_DAYS` set identically on web, worker and beat; the worker concurrency recorded; Cloudinary usage headroom recorded.

### 6.3 How it is turned on, and the rollback

**On:**
- Set `BILLING_HOLDS_ENABLED=true` once, at the platform's shared level, so web, worker and beat change together.
- Restart all three services.
- **A mixed state is the main risk.** For example, the web reserving at the door while an old worker settles with the flag off. So the switch is one deploy of all services, at a quiet hour the owner picks.

**Off (rollback). Turning the flag off REQUIRES the drain first** (SM ruling 2026-11-08). The sweep does nothing while the flag is off (`AutoGrader/settings.py:941–944`), so turning the flag off before the drain would strand open holds and queued papers.

To drain while the flag is still on, B1b adds one more env setting, **`BILLING_HOLDS_DRAINING`** (`env.bool`, default false). While it is true:
- no new hold, job or queue entry is created. New work runs the flag-off path, exactly as it does with the flag off;
- what is already held keeps running: settle, fail, the sweep, the invariant check;
- promotion stops, so nothing new is launched from the queue.

The procedure, in order:
1. Set `BILLING_HOLDS_DRAINING=true` at the shared level and restart all three services.
2. Run **`billing_holds_drain`**. It refuses unless the flag is on and draining is on in the running settings. Then it:
   - expires every `QUEUED_PENDING_CREDITS` job now. Its unlaunched items become `FAILED`, `INSUFFICIENT_CREDITS_MID_BATCH`, which is retryable by hand, so nothing is lost. No expiry emails are sent during a drain;
   - waits for running items to settle, fail or be swept (the sweep window), reporting progress;
   - verifies that every wallet's `reserved_credits` is 0 and that `check_invariants` passes, and prints **DRAINED**. Otherwise it exits non-zero and names the wallets and jobs still open.
3. Only after **DRAINED**: set `BILLING_HOLDS_ENABLED=false`, unset `BILLING_HOLDS_DRAINING`, and restart all three services.

**The emergency case** (a defect that cannot wait for the sweep window): the flag may be turned off at once **only on an explicit order from the owner or the SM, recorded in NOTES (who, when, why)**. It is never the documented default path. Then `billing_holds_drain --flag-off-emergency` runs afterwards and returns every remaining hold directly, through the sweep's code path, without waiting. With the flag off, the spendable balance ignores `reserved_credits`, so teachers are not blocked meanwhile. This is the exception; the normal path above is the default.

Kept files keep their own lifetime and purge as normal; the purge does not depend on the flag.

The drain is tested (`TransactionTestCase`): draining mode creates no holds, the normal drain ends DRAINED, an open hold makes it exit non-zero, and the emergency mode returns holds. It is run once on staging at the end of the soak as part of the proof, and holds are turned back on afterwards.

---

## 7. New user-facing texts B1b needs: drafts for owner approval

None of these is live until the owner approves it. Each goes into code verbatim, behind a `…_APPROVED` constant like `LATE_DOOR_EMAIL_APPROVED`, so an unapproved text is never sent. `{days}` is `QUEUE_LIFETIME_DAYS` (§2). Other `{…}` are filled in from the job. Plain American English, no jargon.

### 7.1 The estimate verdict (shown before a batch runs)

| Verdict | Draft |
|---|---|
| `SUFFICIENT` | "You have enough credits for this batch. It will likely use about {p50} credits, and up to about {p90}." |
| `MARGINAL` | "You may not have enough credits for this batch. It will likely use about {p50} credits, but it could use up to {p90}, and you have {spendable}. If you run out, the remaining papers will wait until you add credits." |
| `INSUFFICIENT` | "You don't have enough credits for this batch. It will likely use about {p50} credits, and you have {spendable}. You can still start it: papers will be graded until your credits run out, and the rest will wait until you add credits." |
| Rough estimate (`source = "rough"`) | Added after any verdict: "This is a rough estimate, because there aren't many graded papers like these yet." |

### 7.2 The queue (batch emails; subject and body)

| When | Subject | Body |
|---|---|---|
| Queued | "Your grading batch is waiting for credits" | "Your credits ran out while grading {assignment}. {done} of {total} papers were graded and saved. The other {left} papers will start again on their own when you add credits. They will wait for {days} days, until {expires_on}." Then one of: "Top up your credits to continue." (individual) / "Ask your school administrator for more credits to continue." (license) |
| Warning, 48 h before | "Your waiting papers expire in 2 days" | "{left} papers from {assignment} are still waiting for credits. If you don't add credits by {expires_on}, they will stop waiting. You can still grade them later by retrying them." |
| Restarted | "Your grading batch has started again" | "Credits arrived, so grading {assignment} has started again for {started} of the {left} waiting papers." |
| Expired | "Your waiting papers have stopped waiting" | "{left} papers from {assignment} waited {days} days for credits and have stopped waiting. Nothing was charged for them. You can grade them at any time by retrying them after you add credits." |

### 7.3 The job's state on screen (the job API returns a code; the app shows these)

| State | Draft |
|---|---|
| Queued | "Waiting for credits: {left} papers left. They wait until {expires_on}." |
| Expired | "Stopped waiting on {expired_on}. Retry these papers after adding credits." |

### 7.4 Low-balance emails (one per level per billing cycle)

| Level | Subject | Body |
|---|---|---|
| 25% | "You have 25% of your credits left" | "You've used 75% of this cycle's credits. You have {remaining} credits left until {cycle_end}." Then the individual or license line, as in 7.2 |
| 10% | "You have 10% of your credits left" | "You've used 90% of this cycle's credits. You have {remaining} credits left until {cycle_end}." Then the line as above |
| 0% | "You're out of credits" | "You've used all of this cycle's credits. Grading stops until you add credits or your credits renew on {cycle_end}. Batches that run out will wait for {days} days." Then the line as above |

**The `balance_status` label on the credits screen:** "Running low" (25%), "Very low" (10%) and "Out of credits" (0%).

### 7.5 Kept files

| Where | Draft |
|---|---|
| An upload item that stopped and still has its file (the retry button's hint) | "Retry this file. It's kept until {kept_until}, so you don't need to upload it again." |
| An upload item whose file was never kept, because storage was down at upload (§3.2) | "This upload can't be retried because its file wasn't saved. Upload the file again." |

The "deleted after {days} days" case is approved text 2 (§4).

### 7.6 Not new

- `INSUFFICIENT_CREDITS_MID_BATCH`'s message and its "none finished" variant are unchanged.
- The student late-door notice reuses `STUDENT_UPLOAD_NOT_PROCESSED`, which is already approved and on.

---

## 8. Build order, gates and days

**The doctrine is as in B1a:**
- the expectations are committed in NOTES before any run;
- red by call-site revert, then green;
- planted bugs restored by sha256, with kill fragments declared first;
- exact Ran counts;
- a break run for every edited pre-existing test;
- `makemigrations --check` after every step and every merge;
- every near run includes **all 66 standing guard labels** (the standing list; 22 until the SM set 66 on 2026-11-09), run serially;
- any mismatch is a STOP to the SM, recorded as it is.

Evidence goes in `docs/evidence/epic-b1b/`. All Stripe and Cloudinary calls are mocked.

| # | Step | Gate evidence | Days |
|---|---|---|---|
| B1b-0 | NOTES survey: re-derive every line reference in §1.1 and the E-table; the §1.6 non-JSON count; the Cloudinary private-type spike (§3.2) | Survey committed | 0.5 |
| B1b-1 | **FB-1** (§5.1) | Red, green, PFB-1/2, cascade timing | 1.5 |
| B1b-2 | **The flag-on-day list** (§5.2): B1, B2, B3 + twins, B4 + twin | Break runs; flag-on and flag-off nears both OK | 3.5 |
| B1b-3 | **Gate 6 trim** (§5.3) | Profile before and after; B1a battery re-run; `assertNumQueries` pin; measurement 4 | 2 |
| B1b-4 | **Estimate** (plan step 4 + §1.3): band, verdict, endpoint, backtest, cold start + tier 3b, drift task + summary table, the `AICallRecord` guard entry, indexes, `estimate_*` columns, the `billing_settings` command | Red/green, plants M-B8 and new ones (tier order, p90 √n), backtest report, estimate Gate 6 | 4 |
| B1b-5 | **F4, kept files** (§3): `KeptUpload`, storage module, the three routes, worker load, retry of upload items, purge, erasure and export, privacy line | Red/green, plants (purge skips, retry ignores purge, erasure leaves the asset), storage measured on staging | 4 |
| B1b-6 | **Batch jobs and the queue** (plan step 5 + §1.4 + §2): one dispatch helper, the `BATCH_GRADING` job, reserve `k`, claims, on-commit launch, last-item transition, promotion (sweep + ledger hooks), expiry, `QUEUE_LIFETIME_DAYS`, plan-field deprecation, notifications, auto-grade | Red/green, plan mutants M-B9/10/19/20/28 and the queue tests P-1…P-5, B-1, 300-paper Gate 6 | 7.5 |
| B1b-7 | **Warnings, actual cost, gap metric** (plan step 6) | Red/green, the once-per-cycle plant | 1 |
| B1b-8 | **Approved texts and the switch** (§4): the three texts from the setting; `LATE_DOOR_EMAIL_APPROVED = True`; the §7 texts the owner approved | Verbatim pins at the default and an override | 0.5 |
| B1b-9 | **The drain** (§6.3): `BILLING_HOLDS_DRAINING`, `billing_holds_drain` and its emergency mode | Red/green; staging drain | 1 |
| B1b-10 | **The B1b gate:** full battery, the flag-on and flag-off nears with all 66 guards, the PG 16 CI (full suite: the owner's go first), NOTES summary | As declared | 1 |
| | **Total** | | **26.5** |

**Why this order:**
- **FB-1, the flag-on list and the trim come first.** After them, a flag-on near run is green, so every later step is checked with the flag on and off from a clean base. Today a flag-on near carries 45 expected failures, which would hide new ones.
- **The trim comes before the batch work,** so batch timing is measured on the final settle path.
- **F4 comes before the queue and the texts,** because the approved texts promise kept files.
- Steps B1b-4 and B1b-5 are independent and could run in parallel with a second engineer.

**Compared with the plan:** the plan's B1b was 12.5 days (steps 4, 5 and 6 plus 1.5 for the gate). The differences:
- the B1a leftovers: +7 (FB-1 1.5, the 45 tests 3.5, the trim 2);
- F4: +4;
- the estimate additions: +1 (the guard, the indexes, the columns, the settings command);
- the single dispatch helper: +0.5;
- the texts: +0.5;
- the drain and draining mode: +1;
- the survey: +0.5;
- the gate: −0.5, because the near-run guard work is now spread across the steps.

**Not included:** SM review, the 7-day staging soak (calendar time, not work), the production switch-on, and the later contract migration for the plan fields (§2.2).

---

## 9. Open questions for the owner, each with a recommendation

| # | Question | Recommendation |
|---|---|---|
| Q1 | **May uploaded student files be kept in Cloudinary** (private, deleted after `QUEUE_LIFETIME_DAYS` days or sooner), making Cloudinary a processor of student work? | **Yes.** It is already our storage provider, the files are private and never shown by URL, and erasure deletes them. The privacy document gets one line (§3.3) |
| Q2 | Should upload batches that run out of credits **queue and restart on their own**, like grading batches? | **No, not in B1b.** They keep their files and are retried by hand (approved text 1 says "resume"). The door already refuses what certainly can't be paid. Automatic restart for uploads can follow later if teachers ask |
| Q3 | If file storage is down at upload time, **refuse the upload or accept it without keeping the file?** | **Accept without keeping** (today's behavior). The teacher loses only the retry option for that file, and §7.5's text says so |
| Q3b | **Delete a kept file as soon as its item succeeds,** or keep every file the full 30 days? | **Delete on success.** It keeps the least student data and cuts storage by about 95% in the §3.4 example. Nothing needs the file after a success |
| Q4 | The unused 14-day plan fields: **leave them (deprecated) now and drop them later?** | **Leave them now** (§2.2): dropping them safely takes three deploys, and a one-step drop would break a rollback. Schedule the drop after B1b has been live for a month |
| Q5 | A lifetime of 1 makes the texts read "1 days". **Accept that on staging only?** | **Yes.** Production stays at 30. No singular wording is added |
| Q6 | The §7 drafts | Approve, change or reject each one. Each stays off until it is approved |
| Q7 | **The Gate 6 trim target:** at most +10 queries and 20 ms added p95, with 40 ms the pass line | **Accept.** It halves the cost without a redesign. Missing the 20 ms target is reported, not a STOP, as long as 40 ms holds |
| Q8 | **The switch-on evidence and soak** (§6.2): 7 days on staging with 0 drift | **Accept.** Production switch-on is a separate go from the owner |
| Q9 | Add a cap on batch size? | **No.** The backstop is fixed at 36 h by the owner (§1.6). Revisit if a batch over 1,000 papers is ever seen |
| Q10 | **The kept file's role in the student data map:** deleted with the student (DELETE), and purged after `QUEUE_LIFETIME_DAYS` (30) days? | **Yes, DELETE with the 30-day purge** (the SM's lean too). The export lists that a file exists, not the bytes (§3.3) |
| Q11 | **The Cloudinary plan's storage headroom** (§3.4), which only the owner's account shows | **Read it before switch-on** and record it in NOTES. (The worker concurrency, replica count and largest batch are no longer needed: the owner fixed the backstop at 36 h on 2026-11-09, §1.6) |
