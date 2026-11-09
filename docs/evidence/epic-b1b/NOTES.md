# Epic B1b — evidence notes

**Plan:** [08b_b1b_plan_amendment.md](../../phase2/architecture/08b_b1b_plan_amendment.md) ("08b"), approved by the owner 2026-11-09, with the SM's recommendations on Q1–Q11. It amends [08_epic_b1_implementation_plan.md](../../phase2/architecture/08_epic_b1_implementation_plan.md) ("the plan").
**The 16 new texts in 08b §7 are not approved.** Each is built behind its `_APPROVED` constant, set False. No text goes live until the owner approves it.
**Branch:** `task/epic-b1b`, off `origin/staging` 4aff59a43. The first commit, 40d462207, carries 08b in. Staging bc7dd7a89 (H-300) was merged `--no-ff` as 28c0c8a1e: no conflicts, and `makemigrations --check --dry-run` printed "No changes detected" (rc=0).
**Doctrine (08b §8, as updated by the SM 2026-11-09):**
- Every near run carries the full standing guard list, now **66 labels** (08b §8 still says 22; the SM's GO of 2026-11-09 sets 66). Near runs are run serially.
- Runs go under `flock ~/.machine-fullsuite.lock` in `h284_deps_env`, with `free -m` ≥ 4000 MB available.
- There is no full suite. The PG 16 CI runs only on the owner's go.
- Stripe and Cloudinary are always mocked. Nothing is pushed.

---

## §0. B1b-0: the survey (before any code)

All facts below are at **28c0c8a1e**. The merge changed only `billing/stripe_service.py` among the `.py` files (H-300), and nothing 08b cites lives there.

### §0.1 08b §1.1 line references: re-derived

Every reference in 08b §1.1 holds unchanged (08b cites 240567975):

| What | 08b §1.1 | Now |
|---|---|---|
| `execute_graded_task` / `hold_for_call` | `ai_processor/services.py:5484` / `:5667` | same |
| `estimate_messages_cost` / `estimate_total_token` / `+= 20000` | `:5754` / `:5807` / `:5823` | same |
| `metered_unit(` in grading | `:4637` | same |
| `AICallRecord` | `ai_processor/models.py:254` | same |
| `grade_engine_async` / `grade_batch_async` / `_relaunch_failed_dispatches` / `auto_grade_due_assignment` | `assignments/tasks.py:648` / `:1466` / `:1646` / `:1698` | same; the `_dispatch_tracked_grading(` calls are at `:1542` (in `grade_batch_async`) and `:1758` (auto-grade) |
| `_dispatch_tracked_grading` / `_launch_tracked_grading` | `:1555` / `:1583` | same |
| `grade_all_submission` / its own loop / `schedule_grade_all_submission` / `PeriodicTask` | `assignments/views.py:1791` / `:1812` / `:1872` / `:1896–1903` | same. The ungraded filter is at `:1798` |
| `CreditLedger.record` / `after_bulk_create` | `billing/models.py:1587` / `:1596` | same |
| `refund_credits` / its `bulk_create` | `billing/services.py:1445` / `:1567` | same |
| `total_remaining_credits` / `plan_remaining_credits` | `:762` / `:782` | same (`billing/models.py`) |
| `low_balance_warned_level` / `_cycle_start` | `billing/models.py:724` / `:729` | same |
| `CreditWalletSerializer` | `billing/serializers.py:873` | same |
| `queue_expiry_days` / `queue_expiry_warning_hours` | `billing/models.py:424` / `:429` | same |
| `AIJob` / `BillingSettings` | `:1787` / `:1935` | same. `billing/admin.py` still registers only `StripeEvent` (`:22`) and `LicenseStripeMutationIntent` (`:64`) |
| `AIJobStatus`, eight states | `billing/models.py:165–172` | same (the class is at `:163`) |
| `billing/holds.py` | 1,145 lines; `sweep_stale_holds` `:584` | same |
| Beat entries | `AutoGrader/settings.py:941–950` | same. The comment is at `:941–942`, the sweep at `:943–946` and the invariants at `:948–951`. The sweep entry is always scheduled; the task itself does nothing with the flag off |
| `LATE_DOOR_EMAIL_TEXTS` / `LATE_DOOR_EMAIL_APPROVED = False` | `billing/holds.py:1082` / `:1095` | same |
| The remediation "no `{`" pin | `AutoGrader/tests_reason_codes.py:258` | same |
| `REUPLOAD` | `students/item_retry.py:47–51` | same |
| The `AICallRecord` guard `ALLOWED` | `ai_processor/tests_call_record_static.py` | `:359` |

### §0.2 The plan's entry-point table (plan §3.4, E-1…E-7): re-derived

The plan cites d808a9ce. All seven entry points still exist and none is new; only line numbers moved:

| # | Plan says | Now |
|---|---|---|
| E-1 | `students/views.py:~964`, `assignments/views.py:~1785` launch `grade_engine_async`; unit `ai_processor/services.py:4635` | `students/views.py:996` (also the scheduled `PeriodicTask` at `:1071`); `assignments/views.py:1824` (inside grade-all's own loop); unit `:4637` |
| E-2 | `students/item_retry.py:~151` | `:154` |
| E-3 | `auto_grade_due_assignment` `:1668` (call `:1733`), `_relaunch_failed_dispatches` `:1616` (call `:1662`) | `:1698` (call `:1758`), `:1646` |
| E-4 | `students/views.py:~610` → `upload_answers_engine`; `students/services.py:962` / `:1365` | `students/views.py:632`; `upload_answers_engine` at `students/services.py:1508`, its `metered_unit(` at `:1539`; the grading pipeline's `metered_unit(` at `:1130` (`_run_grading_pipeline`, `:1110`, called from `grade_engine` at `:824`) |
| E-5 | `students/views.py:~794` → `update_submission_from_raw_text`, `students/services.py:1709` | `students/views.py:826`; `update_submission_from_raw_text` `:1844`, its `metered_unit(` `:1884` |
| E-6 | `assignments/tasks.py:~450` | `:452` |
| E-7 | `assignments/upload_door.py:1-5`; `assignments/file_uploads.py:132` | same |

All non-test `metered_unit(` uses: `ai_processor/services.py:4637`, `students/services.py:1130`, `:1539`, `:1884` and `assignments/file_uploads.py:132`. Five in all, matching E-1…E-7.

### §0.3 08b §1.6: AI responses that are not JSON objects, re-derived from every `execute_graded_task` caller

**Method:**
- `execute_graded_task(` has **17 call sites**, all inside `AIProcessor` (`ai_processor/services.py`).
- I mapped each to its enclosing method (AST), then followed every public method to its non-test callers.
- (`billing/testing/holds.py:143` is a test helper.)

| Call site(s) | Method | Reached from |
|---|---|---|
| `:1312`, `:1371`, `:1515`, `:1692` | `extract_assignment`, `extract_assignment_image`, the two chunked extractors | `AssignmentProcessingService.extract_assignment_data` (`assignments/services.py:1033`). That is reached from `update_assignment_from_extraction` (`:1125`, sync views below) and from `assignments/file_uploads.py:136` (async, E-7). The benchmark runner is not a route |
| `:2172`, `:2434`, `:2623`, `:2893` | answer extraction, blank-answer check, second reading | `upload_answers_engine` (sync E-4, async E-7) |
| `:3614`, `:3893`, `:4928` | grading | `grade_engine` → `_run_grading_pipeline` (sync `grade` view; async E-1/E-2/E-3) |
| `:5341` | `generate_assignment_from_prompt` | `assignments/views.py:1489` (sync) |
| `:5448` | `formatted_grade` | `assignments/tasks.py:957`, `:1200` (async only) |
| `:5872` | `custom_ai_prompt` | `billing/views.py:2732`, `dashboard/views.py:208` (sync) |
| `:6025` | `generate_student_summary` | `classrooms/tasks.py:64` (async only) |
| `:6072`, `:6136` | weekly narratives | `dashboard/tasks.py:80`, `:251` (async only) |

**The synchronous AI routes, those that call the provider inside the request (8):**

| Route | View | AI-success body |
|---|---|---|
| Grade one submission | `students/views.py` `grade` `:929` | `Response(serializer.data)` `:945` |
| Student upload | `students/views.py` `upload_answers` `:589` | `Response(serializer.data)` `:655` |
| Raw-text edit | `students/views.py` `partial_update` `:805` | `Response(serializer.data)` `:842` |
| Create assignment (extraction) | `assignments/views.py` `create` `:418` | `Response(serializer.data)` `:476` |
| Edit assignment (re-extraction) | `assignments/views.py` `partial_update` `:610` | `Response(serializer.data)` `:674` |
| Generate an assignment | `assignments/views.py` `generate_assignment_from_prompt` `:1440` | `Response(data)` at `:1560` and `:1593`; `data` is a dict built at the call site |
| Custom AI prompt | `billing/views.py` `custom_ai_prompt` `:2603` | `Response(serializer.data)` `:2752` (`CustomAIReply`) |
| Dashboard AI chat | `dashboard/views.py` `run_dashboard_ai_chat` `:193` | `Response(CustomAIReply(...).data)` `:242` |

**Result: 0 AI-success bodies that are not JSON objects.** That matches 08b's first-pass count. `ai_job_id` can be added to all eight.

**Corrections to 08b §1.6's first pass (the count does not change):**
1. `teacher_feedback` (`:1105`), `update_grade` (`:1185`) and `classrooms` `student_summary` (`:1824`) are **not** synchronous AI routes. They dispatch Celery tasks (`formatted_grade_async`, `generate_student_summary`), so their response carries no AI result, and the job id comes from the task.
   - The non-AI bare-string branch is still at `students/views.py:1161` ("Submission has not be graded yet"). It stays untouched, because it is not an AI body.
2. The first pass missed **four** synchronous AI routes: assignment `create`, `generate_assignment_from_prompt`, `custom_ai_prompt` and the dashboard chat. All four return JSON objects.
3. **For information:**
   - Assignment `create` has no `HasCreditBalance` permission. The viewset uses `(IsAuthenticated, IsTeacherOrReadOnly)`, and only `partial_update` checks inline at `:620`.
   - With the flag on, its provider calls are still held. Outside a metered unit, `hold_for_call` (`billing/holds.py:1018`) opens a single-call job (`begin_single_call`), and with the flag off, today's balance check in `execute_graded_task` applies.
   - So there is no B1b change, but B1b-4's estimate endpoint and the `ai_job_id` work must treat these single-call routes as their own case.

### §0.4 The Cloudinary private-type spike (08b §3.2)

**Question:** can `django-cloudinary-storage`'s `RawMediaCloudinaryStorage` upload with `type="private"` or `"authenticated"`?
**Method:** I read the installed code (`django-cloudinary-storage==0.3.0`, `cloudinary==1.44.1`, both pinned in `requirements.txt:37`, `:25`). No network call was made.

**Answer: no. `students/kept_files.py` calls the Cloudinary SDK directly,** as 08b §3.2 allows. The storage class can't be used here, for these reasons:
- `MediaCloudinaryStorage._upload` builds fixed options: `use_filename=True`, `resource_type`, `tags` and `folder`. There is no way to pass `type`, so every upload is `type="upload"`, which is **public**.
- `use_filename=True` puts the original file name into the public id. 08b requires that the name never contain it.
- `_open` and `exists` fetch the asset's **unsigned delivery URL** with `requests.get` or `requests.head`. That would fail for a private asset, and it is a URL fetch, not an authenticated API call.
- `RawMediaCloudinaryStorage` only overrides `RESOURCE_TYPE`.

**What the SDK offers, and what `kept_files.py` will use** (all present in 1.44.1):
- **Store:** `cloudinary.uploader.upload(file, public_id="kept_uploads/<uuid>", resource_type="raw", type="private", overwrite=False)`. The SDK passes `type` through as an upload parameter: it is in `__SIMPLE_UPLOAD_PARAMS` (`cloudinary/utils.py:77`, entry at `:82`), which `build_upload_params` (`:1157`) reads.
- **Load:** `cloudinary.utils.private_download_url(public_id, format, resource_type="raw", type="private", expires_at=now+300)` (`utils.py:992`). This is a signed API download URL, fetched server-side by the worker only and never sent to a browser. The worker then checks the bytes' sha256.
- **Delete:** `cloudinary.uploader.destroy(public_id, resource_type="raw", type="private", invalidate=True)` (`uploader.py:340`). `type` must match the upload.
- **Configuration:** already done. `cloudinary_storage` and `cloudinary` are in `INSTALLED_APPS` (`settings.py:372–373`), and `cloudinary_storage/app_settings.py:32–40` calls `cloudinary.config(...)` from `CLOUDINARY_STORAGE` (`settings.py:1212`). Those are three required env values, so they are set wherever the app boots.

**`type="private"` rather than `"authenticated"`:** both refuse public delivery. A private asset is reachable only through the signed API download, so a leaked delivery URL can't fetch it. An authenticated asset can be delivered by a signed delivery URL. **Proposal: `private`**, because nothing in F4 needs delivery URLs.

**A correction to 08b §3.2 (no design change):**
- 08b cites "Cloudinary STORAGES at `settings.py:1190`" as the storage the project already uses. In fact `STORAGES["default"]` is `MediaCloudinaryStorage` only when `ENVIRONMENT` is `prod`, `dev` or `local` (`settings.py:1189–1206`); any other value gets `FileSystemStorage`.
- `ENVIRONMENT` accepts only those three values (`:131`, `:148`), and beta runs as `prod`. **Staging's `ENVIRONMENT` value is not in git [UNVERIFIED].**
- `kept_files.py` doesn't depend on `STORAGES` either way. It calls the SDK, which is configured from `CLOUDINARY_STORAGE` in every environment. So "storage measured on staging" (B1b-5) works whatever staging's `ENVIRONMENT` is.

**Tests:** every test uses a fake in place of the three SDK calls. `AutoGrader/tests_network_guard.py` exists and blocks real network calls, and `BENCHMARK_ARCHIVE_ENABLED` is off under test (`settings.py:1619–1620`), which is the precedent.

### §0.5 Other 08b references checked

- `MAX_UPLOAD_SIZE_BYTES = 50 MB`: `AutoGrader/uploads.py:45`.
- `build_async_upload_payload` / `rebuild_uploaded_file`: `assignments/services.py:703` / `:714`.
- `MAX_ROWS = 2000`: `classrooms/services/roster_import.py:50`.
- Worker concurrency default 4: `scripts/start-worker.sh:8–9`.

### §0.6 The B1b-0 result

- **No STOP.**
- Every 08b §1.1 reference and every E-table entry point is confirmed. The E-table line numbers are updated above.
- The non-JSON count is 0. The list of synchronous AI routes is corrected to 8.
- The spike settles 08b §3.2's [UNVERIFIED] item: the SDK is called directly, with `type="private"`.

**Next:** a checkpoint with the SM before B1b-1 (FB-1).

---

## Switch-on checklist additions (for 08b §6.2 item 5)

- **Cloudinary credentials** (SM, 2026-11-09): confirm that `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` and `CLOUDINARY_API_SECRET` (read into `CLOUDINARY_STORAGE`, `AutoGrader/settings.py:1212`) are set on staging and production, on web, worker and beat.
- **Standing rules for `students/kept_files.py`** (SM, 2026-11-09):
  - It uses the SDK directly, with `type="private"`, and never `use_filename`.
  - It never returns a URL to any caller outside the module. `private_download_url` is used only inside it, server-side.
  - It must pass `AutoGrader.tests_network_guard`, `ai_processor.tests_ssrf_guard` and `AutoGrader.tests_credscan`. In tests it uses the fake only.

---

- **Large flag-on deletes hold the wallet lock for about 3.3 s per 1,000 task rows** (B1b-1 §1.23, after C4 moved to `post_delete`; it was 5.23 s at §1.17: an assignment cascade of 1,000 rows in one open job takes a median of 3.35 s inside the delete's transaction, with the teacher's wallet locked). Read this before switch-on, and before any admin bulk delete with the flag on.

---

## §1. B1b-1: FB-1 (08b §5.1), the hold leaked when a HELD item is deleted

### §1.1 The design, as ruled by the SM (2026-11-09)

**A design finding before any code** (sent to the SM; option (A) approved):
- `check_invariants` (`billing/holds.py:641`) has two checks:
  - (1) per wallet, `reserved_credits = Σ jobs (reserved − drawn − released)`;
  - (2) per job, `item_sum`: the job's `(reserved_credits, hold_drawn, released_credits)` equal the sum over its live task rows.
- §5.1's receiver keeps (1) true. But after a delete that leaves some of a job's rows, (2) reports `item_sum job=…`, because the job still counts the deleted row and the live rows don't.
- This happens for real: `students/data_erase._delete_entries` deletes one student's rows (their submissions cascade the teacher's batch items, and the rows they requested go directly), and a teacher's batch job spans many students.
- **The same false alarm exists today without FB-1:** deleting a SETTLED item from a job that still has other items. With the flag on, the daily invariant report would show it after every such erasure. **This fix removes it too**, and test T-9 pins that exact case.

**The fix, option (A):**
1. **The migration (its own commit):** three `AIJob` columns, `deleted_items_held`, `deleted_items_drawn` and `deleted_items_released`.
   - Each is an integer with `default=0` and `db_default=0` (the house rollback pattern, like 0070), plus a CHECK `>= 0`.
   - Additive; `check_migration_safety` must report it so.
   - Reverse: drops the columns. **No reverse guard.** Nothing is lost that the live rows and the ledger don't keep. After a rollback, the jobs that lost rows would again show the pre-existing `item_sum` false alarm, which is today's behavior.
2. **The receiver:** `pre_delete` on `BackgroundProcessingTask`, connected in `BillingConfig.ready()` beside §21n's. It runs whatever the flag says, as §21n's does. For a row with a `job_id`:
   - **Lock order (house rule):** wallet first (`_lock_wallet_of(job)`), then the job and the **fresh** task row with `select_for_update`. `hold_state` is read from the fresh row, never from the instance. If the wallet is gone (a §21n wallet-less job), nothing moves.
   - (i) If the fresh row is `HELD`: return its left amount exactly as `fail_item` does. That means the wallet `−left`, the row `RETURNED` with `released_credits +left`, the job `released_credits +left` and `failed_items +1`, and a `CREDIT_HOLD_RELEASED` audit row with `reason_label="failed"`.
   - (ii) Then add the row's final `hold_credits`, `hold_drawn` and `released_credits` to the job's `deleted_items_*`.
   - (iii) If the job is still open, and no live row other than this one is `HELD` or unheld and unlaunched, it calls `finish_job` with the **terminal rule** (SM ruling C4, below).
3. **`item_sum` becomes** job totals = live Σ + `deleted_items_*`. Check (1) is unchanged.

**SM rulings on the conditions:**
- **C4, the terminal rule.** The plan's last-item transition (plan 08 §3.7 "Run": `completed_items` reaching the launched count → `QUEUED_PENDING_CREDITS` or `SETTLED`) **does not exist in code yet**. `Unit.settle_root` says "A shared batch or door job finishes in step 5, not here" (`billing/holds.py:853`); only a job the unit owns finishes (`:854` SETTLED, `:868` FAILED).
  - So the receiver uses a terminal rule taken from the code that exists. If the job has a `SettlementRecord`, it ends `SETTLED` (mirrors `:854`, and the plan's "otherwise → `SETTLED`"). With none, it ends `FAILED` (mirrors `:868` and the sweep's job-level branch, `:624`).
  - It is **never `QUEUED`.** A job with unheld, unlaunched items left stays open.
  - **B1b-6 obligation:** replace this rule with a call to B1b-6's single last-item helper, and add a BATCH_GRADING delete-road test there. No BATCH_GRADING job is created anywhere today.
- **C5, the audit label.** `reason_label` **is shown to admins.** The audit API returns `metadata` raw to super admins (`audit/views.py:132`) and to school admins (`:152`), through `AuditEventSerializer` (`audit/serializers.py:96`). A new value would be new visible wording, so the receiver uses the **existing** label `"failed"` (`fail_item`'s, `billing/holds.py:394`).
  - **Admins will see `"failed"` for an item deleted while it was held.** Why: the receiver returns the hold exactly as a failure does, and "deleted" would be new wording for the owner.
- **C3, the two receivers.** On a teacher's deletion, the wallet receiver (§21n, `finish_job` CANCELLED) and this one (task rows through `requested_by` CASCADE) both fire, in the collector's order. Whichever runs second must find the row `RETURNED` and move nothing. Tests T-10 and T-11 force each order.
- **Lock order with erasure:** `erase_student` locks the student row and then the student's wallet (`students/data_erase.py:106`, W-1). The receiver then locks the *teacher's* wallet. No path locks a teacher's wallet and then a student's, and no user row is locked after a wallet, so W-1 holds.

### §1.2 The BEFORE timing (declared before it runs)

- **File:** `billing/tests/test_holds_fb1_cascade_timing.py`, opt-in (`RUN_LOAD_TESTS=1`, the repo convention); skipped otherwise, so the billing near gains **one skip**.
- **What it measures:** one `assignment.delete()` cascading 1,000 task rows, 3 runs per shape.
  - Shape `no_job`: 1,000 rows with no job.
  - Shape `one_job`: 1,000 rows in one UPLOAD job, 900 RETURNED and 100 HELD.
  - It records the wall time and the query count. It asserts nothing about time.
- **The command, at the commit that adds this section:**
  `RUN_LOAD_TESTS=1 flock ~/.machine-fullsuite.lock python manage.py test billing.tests.test_holds_fb1_cascade_timing --noinput`
- **Prediction:** `Ran 1`, OK.
- **Expected numbers:** both shapes take the same path today (fast delete, a handful of queries, no per-row signal), so both are expected to be low tens of milliseconds and fewer than 20 queries. The exact numbers are recorded, not predicted.

**Result (run at cdeeb695f, under `flock`, 8198 MB available): Ran 1, OK, as predicted.** Log: `step1/step1_timing_BEFORE_cdeeb695.log`.

| Shape | Seconds (3 runs) | Median | Queries per run |
|---|---|---|---|
| `no_job` | 0.0254, 0.0203, 0.0188 | **0.0203 s** | 15, 15, 15 |
| `one_job` | 0.0206, 0.0183, 0.0170 | **0.0183 s** | 15, 15, 15 |

Fast delete is in effect today: 15 queries for 1,000 rows, whatever their holds.

### §1.3 A correction to the B1b-0 survey (§0.1)

- **`AIJob.estimate_p50` and `estimate_p90` already exist.** They were added in B1a's 0074 (`billing/models.py:1838–1839`) and are pinned by `billing/tests/test_holds_schema.py:90–91`.
- 08b §1.1 ("No p50/p90 columns") and §1.3 item 3 are wrong. My §0.1 row for the `AIJob` columns said "same" without checking that claim.
- **Effect on B1b-4:** only `estimate_source` is new. That leaves one additive column, not three. Reported to the SM.
- **SM ruling (2026-11-09): correction accepted.** 08b §1.1 and §1.3 item 3 overstated the new columns. B1b-4 needs only the one additive `estimate_source` column. 08b is not edited here; the SM fixes the owner-facing document.

### §1.4 The tests (written first; declared before the red run)

**File:** `billing/tests/test_holds_deleted_items.py`. `TransactionTestCase`, with the flag on except in T-13. 16 tests.

| # | Test | What it pins |
|---|---|---|
| T-1 | instance delete | the row was HELD in an UPLOAD job with a HELD sibling. The wallet is back to the start plus the sibling; `check_invariants` is `[]`; the job stays RESERVED; `failed_items` 1; `deleted_items_*` = (300, 0, 300); one `CREDIT_HOLD_RELEASED` with `reason_label` "failed", the row's id and 300 |
| T-2 | QuerySet delete | the same |
| T-3 | full erasure (`erase_student`, "full") | the same. The job spans two students' submissions, so this is the subset case |
| T-4 | course-scoped erasure | the same |
| T-5 | assignment cascade | every row goes (one settled, one HELD): the wallet is back to the start, the books agree, the job is SETTLED, and the deleted held row's hold is returned once |
| T-6 | batch-session cascade | every row goes, none settled: the job is FAILED, `failed_items` 2 |
| T-7 | C4: UPLOAD with a settled sibling | the job ends SETTLED |
| T-8 | C4: UPLOAD, every row deleted, none settled | the job ends FAILED |
| T-8b | C4: SINGLE_ITEM deleted while held | the job ends FAILED, `failed_items` 1 |
| T-9 | the pre-existing false alarm | a SETTLED row is deleted from a job with a HELD row left: nothing is returned and no release row is written; the books agree; `deleted_items_*` = (300, 120, 180) |
| T-10 | C3: the wallet receiver first | `finish_job(CANCELLED)`, then the row delete: nothing more moves, and the hold is returned once |
| T-11 | C3: the row delete first | then `finish_job(CANCELLED)`: the deleted row's hold is returned once and the wallet is back to the start |
| T-12 | a teacher deleted end to end | their own HELD row in a job a student requested: the job survives with no wallet and is not open, `deleted_items_*` are counted, and the hold is returned once |
| T-13 | the flag off | a row held while the flag was on is still returned when it is deleted with the flag off |
| T-14 | the mid-run characterization | a unit whose row is deleted inside it: its exit raises `BackgroundProcessingTask.DoesNotExist` (`settle` → `_lock_rows`). The hold is returned and no `SettlementRecord` is written. Pinned as found |
| T-15 | the schema | the three columns default to 0, and each refuses −1 (`IntegrityError`) |

**Red prediction (at the commit that adds this section, today's code): `Ran 16`, `FAILED (failures=13, errors=3)`.**
- **Errors (3):** T-10, T-12 and T-15. They reach `deleted_items_*`, which doesn't exist yet (`AttributeError`). T-10 and T-12 pass their earlier assertions today, because the wallet receiver returns the hold.
- **Failures (13):** the rest.
  - T-1…T-8b, T-11, T-13 and T-14 fail on the reserved-credits assertion: the hold leaks.
  - T-9 fails on `check_invariants`, which reports `item_sum`.
- Any other split is a STOP.
- **Command:** `flock ~/.machine-fullsuite.lock python manage.py test billing.tests.test_holds_deleted_items --noinput`.

### §1.5 The plants (kills declared before the fix)

Each is planted on the green fix, run against the new file, and restored. The sha256 of `billing/holds.py`, and of `billing/apps.py` for PFB-1, is checked after each restore.

| Plant | The change | Kill: the tests that must fail, and a fragment |
|---|---|---|
| **PFB-1** | the receiver is not connected (`billing/apps.py`: the `pre_delete.connect` for `BackgroundProcessingTask` removed) | T-1…T-11, T-13 and T-14 fail with `reserved`. T-12 and T-15 are not required to fail: the wallet receiver covers T-12's hold, and T-15 is schema only |
| **PFB-2** | the receiver never calls `finish_job` (step iii removed) | T-5 (`'RESERVED' != 'SETTLED'`), T-6, T-7, T-8 and T-8b (`!= 'FAILED'`) |
| **PFB-3** | the receiver skips the `deleted_items_*` update (step ii removed) | T-1…T-4 and T-9 fail with `item_sum job=` |

### §1.6 The red run: STOP, prediction mismatch (recorded as it is)

**Label (SM, 2026-11-09): prediction wrong: test-fixture bug (T-3/T-4 IntegrityError before reaching the code under test).**

**Result (at a28b6a517, today's code, under `flock`, 9725 MB available): Ran 16, FAILED (failures=11, errors=5).** Predicted: failures=13, errors=3. Log: `step1/step1_red_a28b6a51.log`.
- **13 of the 16 matched:** T-1, T-2, T-5…T-9, T-11, T-13 and T-14 failed as declared, and T-10, T-12 and T-15 errored as declared (`AttributeError: 'AIJob' object has no attribute 'deleted_items_held'`). The leak shows in every reserved assertion: T-1 and T-2 read `600 != 300`, and T-9 reports `item_sum job=…`.
- **The mismatch: T-3 and T-4 ERRORED instead of failing.** The cause is `django.db.utils.IntegrityError: null value in column "answers" of relation "students_studentsubmission"`.
  - It comes from the test's own fixture (`enrolled_student`, test file line 88). `StudentSubmission.answers` (`students/models.py:41`) is a non-null JSONField, and the fixture creates the submission without it.
  - It is a bug in the new test, not in the code under test. Neither test reached the erasure.
- **Proposed fix (for the SM's ruling):** the fixture passes `answers={}`, with nothing else changed. Then re-run red, declared again as `Ran 16, FAILED (failures=13, errors=3)`, recorded beside this run and not replacing it.

### §1.7 Red run 2 (declared before it runs; SM ruling 2026-11-09)

- **The only change:** `enrolled_student()` passes `answers={}` to `StudentSubmission.objects.create` (test file line 89). Nothing else changes.
- **Prediction (as in §1.4): `Ran 16`, `FAILED (failures=13, errors=3)`.**
  - Errors: T-10, T-12 and T-15 (`AttributeError`).
  - Failures: the other 13. T-3 and T-4 now reach the erasure and fail on `reserved` (`600 != 300`), like T-1.
- **A STOP if** any test other than T-3 and T-4 changes outcome from run 1, or the split differs.

**Result (at fbc63bbec, under `flock`, 9100 MB available): Ran 16, FAILED (failures=13, errors=3), as declared.** Log: `step1/step1_red2_fbc63bbe.log`.
- Only T-3 and T-4 changed from run 1. Both now fail on `600 != 300 : reserved`, like T-1.
- Every other test has the same outcome and message as in run 1.

### §1.8 Two test corrections found by reading, before any green run (declared; red run 3 proves the red outcomes don't change)

While writing the receiver, I read the code under the tests' assumptions and found two assertions that a correct fix would still fail. They are bugs in the new tests, found before the fix exists:
1. **The fixture job's status.**
   - `make_job` leaves a job `ESTIMATED` (the model default, `billing/models.py:1796`), and `reserve` doesn't move it.
   - T-1…T-4 (through `assert_road`) and T-9 assert `RESERVED`. A real door job is set to `RESERVED` by `open_door_job` (`billing/holds.py:1058`).
   - **Correction:** the fixture's `job()` passes `status=RESERVED`, as a door job has. T-12 builds its own job and is unchanged.
2. **T-11's audit count.**
   - `released_credits_for` also adds rows with no `item_id`. In T-11, `finish_job(CANCELLED)` writes a job-level row (300, the kept row's hold), so a correct fix would read 600.
   - **Correction:** T-11 asserts that exactly one release row names the deleted row, with 300 credits.

**Red run 3, declared before it runs (code under test unchanged at 4dfa4478b plus these test edits):**
- `Ran 16`, `FAILED (failures=13, errors=3)`.
- The same tests fail or error with the same messages as run 2, with one exception: T-10, T-12 and T-15 now get past `AttributeError` (the 0081 columns exist) and fail instead.
  - T-10: `(0, 0, 0) != (300, 0, 300)` on `deleted_items`.
  - T-12: the same.
  - T-15: the −1 `IntegrityError`s are raised by the NOT VALID check from 0081 on new writes, so **T-15 passes**.
- **So the corrected prediction is `Ran 16`, `FAILED (failures=15)`, with T-15 OK.**
  - The 15 failures are T-1…T-14: T-1…T-8b, T-11, T-13 and T-14 on `reserved`; T-9 on `item_sum`; T-10 and T-12 on `deleted_items`.
  - Any other result is a STOP.

**Red run 3 result (at 71eb3a7f0, under `flock`, 8142 MB available): Ran 16, FAILED (failures=15), with T-15 OK, as declared.** Log: `step1/step1_red3_71eb3a7f.log`. T-10 and T-12 now fail on `(0, 0, 0) != (300, 0, 300)`. Every other test fails with the same message as in run 2.

### §1.9 The fix, and the green run (declared)

**The fix:**
- `billing/holds.py`: `return_hold_on_item_delete` and `_job_has_rows_to_run`, as designed in §1.1. `check_invariants`' `item_sum` now compares the job's totals with live Σ + `deleted_items_*`.
- `billing/apps.py`: the receiver is connected in `ready()`, with `dispatch_uid="billing.holds.return_hold_on_item_delete"`.

**Green prediction:** `billing.tests.test_holds_deleted_items`, `Ran 16`, OK. Any other result is a STOP.

**Label (SM, 2026-11-09): prediction wrong: test bug (pk read after instance.delete()).**

**Green result (at 4edc94ca2, under `flock`, 8279 MB available): Ran 16, FAILED (failures=2). STOP, the prediction was wrong.** Log: `step1/step1_green_4edc94ca.log`.
- **T-1:** `AssertionError: '<the row id>' != 'None'` on the release row's `item_id`.
- **T-11:** `Lists differ: [] != [300]`.
- **Diagnosis (a one-off traced run of T-1 with a print in the receiver, since removed; `billing/holds.py` was checked against the commit with `git status`, clean):**
  - The receiver writes the right id: the traced run's row held `74e1ff86-…` and the receiver saw the same pk.
  - **The tests are wrong.** Django's `Model.delete()` sets the deleted instance's `pk` to `None`. T-1 passes `doomed.pk` to `assert_road` *after* `doomed.delete()`, and T-11 filters on `str(doomed.pk)` after it, so both compare against `'None'`.
  - **T-10 has the same bug but passes by coincidence:** `released_credits_for(job, None)` counts the job-level row from `finish_job`.
  - Roads that don't call `delete()` on that instance (QuerySet, erasure, cascades, a teacher's deletion) keep the pk, and they passed.
- **Proposed fix (for the SM's ruling):** T-1, T-10 and T-11 save the row's pk before the delete and use it after. Nothing else changes. Then green run 2, declared `Ran 16`, OK.
- **SM ruling (2026-11-09):** the pk-before-delete fix is approved. T-1, T-10 and T-11 now save the pk before `delete()` (applied; nothing else changed). The SM adds plant PFB-4 and asks for one more read of all 16 tests (§1.10).

### §1.10 A read of all 16 tests for the same family of mistakes (SM condition 3)

**(a) A field read from a deleted instance.** I looked at every `.delete()` in the file and every later use of the instance it was called on:
- `instance.delete()` and a later read: **T-1, T-10 and T-11**, now fixed.
- `instance.delete()` with no later read of that instance: T-7 (`doomed`), T-8b (`item`), T-9 (`settled`) and T-13 (`doomed`).
- The delete is of *another* object, and the test's own instance isn't the one the collector nulls:
  - T-2, T-8 and T-14: QuerySet deletes;
  - T-3 and T-4: the erasure's QuerySet deletes;
  - T-5 (`assignment.delete()`; `assignment.pk` isn't read afterwards), T-6 (`batch.delete()`, the same) and T-12 (`teacher.delete()`; `self.teacher.pk` isn't read afterwards).
- **Found: none beyond the three fixed.**

**(b) A count that includes job-level rows.** `released_credits_for(job, pk)` adds the rows naming the item **and every row with no `item_id`** (`finish_job`'s). It is used by `assert_returned_once` in T-5, T-10 and T-12.
- **T-5 (assignment cascade):** the receiver returns the held row's hold on an item-named row. `finish_job` then returns 0 and writes no row, whichever row the collector visits first.
  - **Found: a gap.** If the receiver wrote its row with no `item_id` (PFB-4), T-5 would still pass.
  - **Proposal:** T-5 asserts that exactly one release row exists, that it names the held row, and that it carries 300.
- **T-10 (the wallet receiver first):** here the receiver correctly returns **nothing**. `finish_job(CANCELLED)` has already returned the hold on its job-level row, so the single release row is job-level by design.
  - **Found: T-10 cannot kill PFB-4 by construction.** In T-10's scenario the receiver writes no release row at all, so a receiver that would write it without `item_id` behaves the same.
  - **Proposal:** T-10 asserts what it should prove: exactly one release row, job-level (no `item_id`), with 300, and **zero rows naming the deleted item**. That kills a different plant: a receiver that returns the hold again, which is the double-return T-10 exists to catch.
  - The SM's condition says T-10 must kill PFB-4 or it's a STOP. That is a ruling question, sent to the SM before any run.
- **T-12 (a teacher deleted end to end):** which receiver runs first depends on the collector's order. The test's point is "returned exactly once, in total", so a sum over all release rows is the right assertion.
  - **Proposal:** T-12 asserts that exactly one release row exists and that it carries 300. That pins "once" without depending on the order. Under PFB-4 it may survive if the task row's receiver runs first; that is expected and declared.
- **T-1…T-4 (`assert_road`) and T-11:** these already filter on the item's id exactly. T-9 asserts zero rows. **Found: no further problems.**

**(c) A fixture status that differs from what the real road leaves.**
- The real statuses are:
  - door job: RESERVED (`billing/holds.py:1059`);
  - retry or late-door successor: RESERVED (`:494`);
  - SINGLE_ITEM opened by a unit: RUNNING (`:814`);
  - SINGLE_CALL: RUNNING (`:936`).
- The fixture's `job()` gives RESERVED to every UPLOAD job (matches the door) and to T-8b's SINGLE_ITEM job (matches a retry successor, not a unit-opened job). T-8b's assertion holds for either, because `finish_job` moves any open status.
- **Found:** T-12 builds its UPLOAD job with `make_job` (ESTIMATED), where a real door job is RESERVED. The outcome is the same (any open status → not open). **Proposal:** T-12 passes `status=RESERVED`, to match the road.
- The fixture's settled rows keep the default task status, where a real settled row is SUCCESS. The receiver only reads that status for rows with no `hold_state`, and a settled row is RETURNED, so the outcome is the same. **Found: no change needed**, but noted.

### §1.11 Lesson for B1b-2 and later (SM, 2026-11-09)

This step had three test-only bugs: a NOT NULL fixture field, a fixture status that differed from the road, and a pk read after `instance.delete()`. Each was caught before it could hide a code fault, and each is recorded as it happened. **The pattern:** a new test's fixture or assertion assumed something about Django or the model without reading the code. Before declaring any red run from B1b-2 on, I read each new test against:
1. every NOT NULL field of each model it creates;
2. the status and fields the real road leaves on each row it builds;
3. every value read after a `delete()`;
4. every audit or row count, for whether it includes rows that the code under test didn't write;
5. for each plant, whether each test meant to kill it can see the plant at all in its scenario.

### §1.12 SM rulings on §1.10, and the declarations (committed before any run)

**The SM's condition (2) is corrected (SM, 2026-11-09).**
- The condition was that T-10 must kill PFB-4. It can't, by construction: in T-10, `finish_job(CANCELLED)` returns the hold first, so the receiver correctly returns 0 and writes no release row, and a receiver that would write it without `item_id` behaves the same.
- I first proposed a PFB-5 (the hold state read from the instance) for T-10, then withdrew it before any run, for two reasons:
  - the instance Django passes to `pre_delete` is the test's own object, whose `hold_state` is stale `None`;
  - after `finish_job`, the row's left amount is 0, so nothing can return twice in T-10's scenario.
- **The SM dropped PFB-5.**

**Test edits (approved), all in the new file:**
- **T-5:** exactly one release row, naming the held row, with 300.
- **T-10:** exactly one release row, job-level (no `item_id`), with 300, and no row naming the item.
- **T-12:** the job is built `RESERVED` (as `open_door_job` leaves it), and there is exactly one release row, with 300.
- The now-unused helpers `released_credits_for` and `assert_returned_once` are removed.
- Settled fixture rows keep the default task status (no change, SM).

**Which test guards C3's "never returned twice":**
- **T-12** asserts exactly one release row across both receivers on a real teacher deletion. T-11 (the row delete, then `finish_job`) also asserts exactly one row naming the item, and that the wallet is back to the start.
- **No plant proves that either test fails when a second return happens on a HELD row.**
  - With the code as written, both receivers re-read the locked row, and `_left` is 0 once a hold has been returned. So there is no realistic one-line plant that makes a second return happen; it would need a contrived change.
  - **An open gap, recorded for B1b-6.** The SM said not to build a contrived plant now. When B1b-6 adds the last-item helper, which is a second writer of the same rows, it adds a double-return plant there.

**Declarations:**
- **Green run 2:** `billing.tests.test_holds_deleted_items`, `Ran 16`, OK.
- **Plants:** each runs the same file. Each is planted on the green fix, then restored, and the sha256 of every touched file is checked against the commit.

| Plant | Change | Declared kills (must FAIL) | Declared survivors |
|---|---|---|---|
| PFB-1 | `billing/apps.py`: the `BackgroundProcessingTask` `pre_delete.connect` removed | T-1…T-11, T-13, T-14 (on `reserved`, `deleted_items` or `item_sum`) | T-12 (the wallet receiver returns the hold; its `deleted_items` assertion then fails, so T-12 may also fail, declared as allowed), T-15 |
| PFB-2 | the receiver's `finish_job` call removed | T-5, T-6, T-7, T-8, T-8b (status `!= 'SETTLED'`/`'FAILED'`) | the rest |
| PFB-3 | the receiver's `deleted_items_*` update removed (the `failed_items` and `released_credits` updates are kept) | T-1…T-4 and T-9 (`item_sum job=`), T-5, T-6 and T-10 (`deleted_items`), T-12 | the rest are allowed either way |
| PFB-4 | the receiver's release row written with `item=None` | T-1…T-4 (`item_id`), T-5, T-11 | T-10 (by construction), T-12 (order-dependent, accepted) |

- **Pass rule:** every declared kill fails. A declared kill that survives is a STOP. Other tests may fail only where the table says "allowed".

### §1.13 Green run 2 and the plants: results

**Green run 2 (at 95171398a, under `flock`, 8211 MB available): Ran 16, OK, as declared.** Log: `step1/step1_green2_95171398.log`.

**Plants** (run at 95171398a). Each plant was applied by `scratchpad/plant.py` and restored with `git checkout`. After every restore, `sha256sum -c` matched the committed files: `billing/holds.py` 199403aa…a32ff and `billing/apps.py` 3e3ff104…19273. Logs: `step1/step1_plant_PFB-{1,2,3,4}.log`.

| Plant | Result | Failed | Against the declaration |
|---|---|---|---|
| PFB-1 (not connected) | FAILED (failures=15) | T-1…T-14 | **killed.** Every declared kill failed. T-12 failed (allowed), and T-15 survived (declared). Fragments include `600 != 300 : reserved`, `300 != 0` and `item_sum job=` |
| PFB-2 (no `finish_job`) | FAILED (failures=5) | T-5, T-6, T-7, T-8, T-8b | **killed, exactly as declared.** `'RESERVED' != AIJobStatus.SETTLED` / `FAILED` |
| PFB-3 (no `deleted_items_*`) | FAILED (failures=13) | T-1…T-7, T-9…T-14 | **killed.** Every declared kill (T-1…T-6, T-9, T-10, T-12) failed. The rest are allowed. Fragments: `item_sum job=` and `(0, 0, 0) != (300, 0, 300)` |
| PFB-4 (release row with no `item_id`) | FAILED (failures=2, errors=4) | T-1…T-4 (**ERROR**), T-5, T-11 (FAIL) | **killed**, and T-10 and T-12 survived as declared. **One difference in form, recorded:** T-1…T-4 ERRORED with `KeyError: 'item_id'` (`assert_road` reads `metadata["item_id"]`, and the planted row has no such key) rather than failing an assertion. They are killed on the declared fragment `item_id`. T-5: `[(None, 300)] != [('<id>', 300)]`; T-11: `[] != [300]` |

### §1.14 The AFTER timing (declared before it runs)

- **The same file and command as §1.2,** at the commit that adds this section.
- **Prediction: Ran 1, OK.** The numbers are recorded, not predicted. What I expect:
  - `no_job`: fast delete is now off for the model, so the collector loads the 1,000 rows and sends 1,000 signals. Each signal returns before any query (`job_id` is None). That means a few more queries than 15, and more time than 0.02 s.
  - `one_job`: about 8 queries per row (the job, the wallet lock, the job lock, the row lock, the update, the "rows left" check and, on rows near the end, the settlement check). So several thousand queries, and probably seconds rather than milliseconds.
- If it is too slow, 08b §5.1's fallback (filter to HELD rows first in the erasure and cascade paths) **needs an SM ruling**. I don't apply it here.

**AFTER result (at 894d4ea3e, under `flock`, 8334 MB available): Ran 1, OK, as predicted.** Log: `step1/step1_timing_AFTER_894d4ea3.log`.

| Shape | Seconds (3 runs) | Median | Queries | BEFORE median |
|---|---|---|---|---|
| `no_job` | 0.0429, 0.0535, 0.0482 | **0.048 s** | 25, 25, 25 | 0.020 s, 15 queries |
| `one_job` (900 RETURNED, 100 HELD) | 8.68, 8.86, 9.34 | **8.86 s** | 8925, *0, 0* | 0.018 s, 15 queries |

- **The one-job query counts are unreliable after run 1.** Django keeps `connection.queries_log` as a `deque(maxlen=9000)` (`django/db/backends/base/base.py`), and `CaptureQueriesContext` takes a slice of it by index. Once the deque is full the slice comes back empty, so runs 2 and 3 read 0. Run 1's 8,925 is near the cap, so it's a lower bound: about 9 queries per row.
- **The cost:** an assignment cascade of 1,000 rows in a flag-on job goes from 0.02 s to about 9 s. All of that runs inside the delete's transaction, with the teacher's wallet row locked from the first HELD row onward. Rows from the flag-off era (no job) cost little: 0.048 s.
- **Sent to the SM before the near run** (08b §5.1: "Fallback if it is too slow … needs an SM ruling"), because any change here changes the code the near run tests.

### §1.15 The cost ruling: option (b'), a cheaper receiver that keeps the lock order (SM, 2026-11-09)

**The ruling path:**
- The SM chose option (b), a cheap path for RETURNED rows, on condition that it keep the lock order: the wallet first, never an `AIJob` UPDATE before the wallet lock, because the reverse order risks a deadlock against `settle` and `finish_job`.
- Option (d) was rejected.
- Before building (b), I found that its premise was wrong, and the SM approved the safer variant (b').

**The correction (SM): "RETURNED is terminal, so trusting the instance is safe" is false.**
- A row can go from RETURNED back to HELD:
  - `_sweep_item` returns a STARTED row whose run may still be going (`billing/holds.py:556–557`);
  - that run's next provider call goes through `ensure_call_fits` (`:242`) and then `_reserve_locked(…, item_row)` (`:225`), which sets `hold_state=HELD` with `hold_credits += shortfall`.
- So an instance the collector loaded as RETURNED may be HELD when the receiver runs. Trusting it would skip the return, which is the FB-1 leak. T-17 pins this case.

**The lock-order argument (why a plain fresh read is enough once the wallet is locked):**
- **Every writer of a task row's hold columns holds that job's wallet lock**, taken first through `_lock_wallet_of`:
  - `_reserve_locked` `:225` (its callers `reserve` and `ensure_call_fits` lock first);
  - `settle` `:347`;
  - `fail_item` `:386`;
  - `finish_job` `:423`;
  - the sweep `:557`;
  - the receiver itself.

  Those are every `hold_state` write outside tests and migrations, by grep. `students/task_tracking.py:127` only filters on it.
- **Every write of an `AIJob.status` (SM condition 1)**, by grep of `create`, `update`, `save` and `bulk_update` on `AIJob` outside tests:
  - `finish_job` `:408` (the conditional UPDATE to a terminal status): **under `_lock_wallet_of` (`:405`)**.
  - `create_job` `:196` (ESTIMATED), `_open_successor_job` `:494`/`:496`, `Unit.before_call` `:814` (RUNNING), `begin_single_call` `:936` and `open_door_job` `:1059`: these are **not under the wallet lock**, but each sets the status of a **brand-new job before any task row points at it**.
    - Successors leave the task row on its old job (`:469`, "the old job and the task row are untouched").
    - `before_call` writes RUNNING before it attaches the row (`:814` and then `:818`).
    - The door sets RESERVED before `hold_door_item` attaches files.
    - Single calls have no task row.
  - No admin, QA console or management-command code names `AIJob` (grep: only `billing/holds.py`, `billing/models.py`, `billing/testing/holds.py`, `students/models.py`, `students/student_data_map.py`, `audit/serializers.py`). `billing/admin.py` doesn't register it.
  - **Result:** for any job a task row points to, every later status write holds the wallet lock. So the status read in step 2 is stable while the receiver holds that lock, and step 4 doesn't need to re-read the job FOR UPDATE.
  - **B1b-6 obligation:** promotion and expiry will add status writes on jobs that have rows, and they must take the wallet lock first, or this receiver must re-read the job FOR UPDATE.

**The receiver (b'):**
1. Lock the wallet, found through the job: `select_for_update(of=("self",)).filter(ai_jobs__pk=…)`. None means no job, or a wallet-less one.
2. A plain fresh read of the row's hold columns and its job's status, in one query.
3. HELD: the full path, as before.
4. Not HELD: one F() UPDATE of `deleted_items_*` from the fresh numbers, then C4's checks only if the job is open.
- The C4 rule moves into `_finish_if_nothing_left` (one place, two callers).
- **SM condition (2):** inside an open transaction (Collector.delete's), the receiver opens no savepoint per row. Outside one it opens its own atomic.
  - Django's outermost `atomic` always begins a full transaction, whatever `savepoint` says, so the explicit guard documents the rule rather than changing behavior.
  - T-18 pins the property: called outside any transaction, a failure after the wallet moved leaves nothing applied.

### §1.16 The declarations for (b') (committed before any run)

**New tests:** T-17 (a stale RETURNED instance whose row was HELD again) and T-18 (outside a transaction, nothing half-applies). **18 tests.**

**Green run 3:** `billing.tests.test_holds_deleted_items`, **`Ran 18`, OK.**

**Plants:** all on the new code, restored by sha256 as before.

| Plant | The change | Kills (must not pass) | Allowed either way |
|---|---|---|---|
| PFB-1 | the `BackgroundProcessingTask` connect removed | T-1…T-11, T-13, T-14, T-17 | T-12. T-15 and T-18 (which calls the receiver directly) survive |
| PFB-2 | `_finish_if_nothing_left` returns at once | T-5, T-6, T-7, T-8, T-8b | the rest pass |
| PFB-3 | the **HELD path's** `deleted_items_*` update removed | T-1…T-6, T-13, T-14, T-17 | T-12 and the rest. T-10 is now on the not-HELD path, so it moves to PFB-7 |
| PFB-4 | the release row written with `item=None` | T-1…T-5, T-11 | T-10 and T-12 survive (declared) |
| PFB-6 | the not-HELD path also moves the wallet by `−(hold_credits − hold_drawn)` | T-9, T-5 (an ERROR on the reserved ≥ 0 check is allowed) | T-10 and the rest |
| PFB-7 | the not-HELD path's `deleted_items_*` update removed | T-10, T-9, T-5 | T-12 and the rest |
| PFB-8 | the instance's RETURNED is trusted: the not-HELD path is taken when `instance.hold_state == RETURNED`, whatever the fresh read says | T-17 | the rest |

**The timing:** the same file. The query count now uses `connection.execute_wrapper`. Prediction: `Ran 1`, OK.
- **Rule (SM):** if the one-job median is ≤ 2 s for 1,000 rows, this step is done.
- Above 2 s, (c) is not built now. Instead it becomes a B1b-3 speed item and a switch-on checklist line: "large flag-on deletes hold the wallet lock for N s".

### §1.17 Results for (b')

**Green run 3 (at b9e2d4169, under `flock`, 8022 MB available): Ran 18, OK, as declared.** Log: `step1/step1_green3_b9e2d416.log`.

**Plants (at b9e2d4169):** each was applied by `scratchpad/plant2.py` and restored with `git checkout`. After every restore, `sha256sum -c` matched: `billing/holds.py` c2e49f1b…92b0af, `billing/apps.py` 3e3ff104…19273. Logs: `step1/step1_plant3_PFB-*.log`.

| Plant | Result | Not passing | Against the declaration |
|---|---|---|---|
| PFB-1 | FAILED (failures=16) | T-1…T-14, T-17 | killed. T-12 failed (allowed); T-15 and T-18 survived (declared) |
| PFB-2 | FAILED (failures=5) | T-5, T-6, T-7, T-8, T-8b | killed, exactly as declared |
| PFB-3 | FAILED (failures=12) | T-1…T-7, T-11…T-14, T-17 | killed. Every declared kill failed; T-7, T-11 and T-12 are allowed |
| PFB-4 | FAILED (failures=2, errors=4) | T-1…T-4 (ERROR, `KeyError: 'item_id'`), T-5, T-11 | killed. T-10 and T-12 survived (declared) |
| PFB-6 | FAILED (failures=1, errors=2) | T-9 (FAIL), T-5 (ERROR), T-10 (ERROR) | killed. The errors are the reserved ≥ 0 check (allowed); T-10 is allowed |
| PFB-7 | FAILED (failures=3) | T-5, T-9, T-10 | killed, exactly as declared |
| PFB-8 | FAILED (failures=1) | T-17 | killed, exactly as declared |

**Timing (at b9e2d4169, `execute_wrapper` count, 8401 MB available): Ran 1, OK.** Log: `step1/step1_timing_AFTER2_b9e2d416.log`.

| Shape | Seconds (3 runs) | Median | Queries (counted) |
|---|---|---|---|
| `no_job` | 0.0439, 0.0435, 0.0415 | **0.044 s** | 23, 23, 23 |
| `one_job` | 5.36, 5.09, 5.23 | **5.23 s** | 5727, 5719, 5733 |

- Before FB-1: 0.018 s and 15 queries. The first receiver: 8.86 s and about 9 queries per row.
- **Above the SM's 2 s line.** Per the ruling, (c) is not built now.
- **Why 5.7 queries per row and not 3:** the job stays open until its last HELD row goes. So every RETURNED row also runs C4's checks (the job read and the "rows left" EXISTS): wallet lock, fresh read, UPDATE, job read, EXISTS, which is 5 queries. The 100 HELD rows take about 10 each.
- **Recorded as:**
  1. **A B1b-3 speed item:** cut the per-row cost of large deletes. Candidates: (c)'s bulk detach pre-pass, or running C4 once per job at the end of the cascade instead of once per row.
  2. **The switch-on checklist line** added at the top of this file.

### §1.18 The B1b-1 near run (declared before it runs)

Both runs are at the commit that adds this section, with the flag unset, under `flock`, with `free -m` ≥ 4000 MB available.

**Run A, the apps:**
- **Labels:** `billing students classrooms assignments users AutoGrader.tests_migration_rollback_defaults`. The rollback test is added because this step adds billing 0081 and 0082.
- **Options:** `--parallel 4`, as in B1a.
- **Counted with `DiscoverRunner.build_suite`** (`scratchpad/count.py`, `sys.argv` set to `test` so the settings see a test run): billing 2638, students 1066, classrooms 813, assignments 824, users 982, the rollback test 12. **Total 6335.**
- **Skips:**
  - **19 decorated** (counted from `__unittest_skip__` on the class or method; this includes the opt-in FB-1 timing test);
  - **4 at run time:** `users.tests_google_auth.LiveGoogleEndpointContractTests` (2, live Google is opt-in), `LiveContractSkipGuardTests` (1, `CI_REQUIRE_NETWORK` not set), and `users.tests_activity_middleware_load.RedisAvailabilityTests` (1, `CI_REQUIRE_REDIS` not set). Redis answers PONG here, so its load tests run. These are the same 4 as B1a §21x.
- **Prediction: Ran 6335, OK (skipped=23).**

**Run B, the 66 standing guard labels:**
- Run **serially** (no `--parallel`), so `AutoGrader.tests_redis_test_isolation`'s fork tests run.
- **Counted:** 1146, with 1 decorated skip. That matches staging's guard sweep (bc7dd7a89: Ran 1146 OK, skipped=1, serial).
- **Prediction: Ran 1146, OK (skipped=1).**

Any failure, or a different count or skip number, is a STOP.

**Results (at 601e9fee7, flag unset, under `flock`):**
- **Run A (7585 MB available):** Found 6335, **Ran 6335 in 341.755s, OK (skipped=23)**, rc 0, 0 FAIL or ERROR lines. **As declared.** Log: `step1/step1_near_apps_601e9fee.log.gz`.
- **Run B (7984 MB available, serial):** **Ran 1146 in 160.526s, OK (skipped=1)**, rc 0. **As declared.** Log: `step1/step1_near_guards_601e9fee.log.gz`.

### §1.19 B1b-1 summary (for the SM's checkpoint)

**FB-1 is fixed.** Deleting a task row returns its credit hold whichever road deletes it: instance, QuerySet, full or course-scoped erasure, an assignment, batch-session or teacher cascade. It does so with the flag on or off.
- The fix also clears the pre-existing `item_sum` false alarm for any row deleted from a job that keeps other rows.
- **What changed:**
  - billing 0081 (three `deleted_items_*` columns and a NOT VALID check) and 0082 (VALIDATE);
  - `return_hold_on_item_delete` plus `_finish_if_nothing_left` in `billing/holds.py`, connected in `billing/apps.py`;
  - `item_sum` now reads live Σ + `deleted_items_*`.
- **Evidence:**
  - three red runs and three green runs, each declared first; the mismatches are recorded as they happened (§1.6, §1.9);
  - 18 tests in `billing/tests/test_holds_deleted_items.py`;
  - plants PFB-1…4 and 6…8, all killed (§1.17);
  - the near runs above.
- **Open items carried forward:**
  1. **B1b-3:** large flag-on deletes cost about 5.7 queries per row (5.23 s per 1,000 rows, with the wallet locked). This is also on the switch-on checklist.
  2. **B1b-6:**
     - replace C4's terminal rule with the last-item helper, and add a BATCH_GRADING delete test;
     - every new job-status write (promotion, expiry) must take the wallet lock first;
     - add a double-return plant for the C3 rule (§1.12).
  3. **B1b-4:** only `estimate_source` is a new column (§1.3).
- **Test-writing lessons** are in §1.11, to apply from B1b-2 on.

### §1.20 SM review of d81556a56: "C4 may never fire in a cascade". Characterize first (declared)

**The SM's finding:** `Collector.delete` sends `pre_delete` for every instance before it deletes any row. So the last row's siblings are still in the table, and `_job_has_rows_to_run` keeps the job open.

**My reading before the run:**
- `_job_has_rows_to_run` doesn't ask whether siblings *exist*. It asks whether a sibling is **HELD**, or has **no hold and is unfinished**.
- In a cascade, each HELD sibling's own `pre_delete` has already set it RETURNED (`hold_state` is updated before the delete). Its fresh state then reads RETURNED when a later sibling checks.
- So for HELD and RETURNED rows, C4 fires on whichever row the collector visits last. That is why T-5 (settled plus HELD) and T-6 (two HELD) passed as cascades.
- **But the finding holds for one kind of row:** a row with a job, **no hold, and not finished**. Its `pre_delete` doesn't change it, so it still counts as "to run" when the last sibling checks, and the job is left open with zero rows.
  - Today such a row exists only for a moment: the unit attaches the job (`billing/holds.py:818`) and then reserves (`ensure_call_fits`); the door attaches (`:1070`), holds, and detaches on refusal (`:1073`).
  - **From B1b-6 on, queued unheld papers make it common.**

**Tests (new, 20 in all):**
- **T-19 (the SM's characterization):** an assignment cascade of one open job with a HELD row and a RETURNED row (`fail_item`, no settlement). It asserts that no rows are left, the wallet is back to the start, the books agree, the job is FAILED, and `deleted_items_*` = (600, 0, 600).
- **T-20 (the case the reading predicts):** an assignment cascade of one open job with a HELD row and a never-held pending row. It asserts the same, plus `deleted_items_*` = (300, 0, 300) and the job FAILED.

**Prediction (today's code, d81556a56 plus these tests): `Ran 20`, `FAILED (failures=1)`.**
- T-19 passes.
- **T-20 fails on its last assertion:** `'RESERVED' != AIJobStatus.FAILED`. The job is left open with zero rows.
- Every other assertion in T-20 passes, and T-1…T-18 pass.
- Any other result is a STOP.

**Result (at e1d18dd52, under `flock`, 9010 MB available): Ran 20, OK. STOP, the prediction was wrong** (declared: FAILED, failures=1, T-20). Log: `step1/step1_cascade_char_e1d18dd5.log`.
- **Diagnosis:** a one-off traced run of T-20 alone (a print at the receiver's entry, since removed; `billing/holds.py` was restored with `git checkout`, sha256 c2e49f1b…92b0af, clean).
  - The collector visited the **pending row first** (`hold_state None`) and the **HELD row last**. Its C4 check then saw the pending sibling as "to run" and left the job open.
  - **That run FAILED as predicted:** `AssertionError: 'RESERVED' != AIJobStatus.FAILED`.
  - In the full-file run the collector visited the rows in the other order. The pending row came last, excluded itself, found the HELD sibling already RETURNED, and finished the job.
  - **So the outcome depends on the visit order.** The rows carry random UUIDs, and the cascade's collect query has no ordering that fixes which comes last.
- **The SM's finding is confirmed** for any job whose cascade includes a never-held unfinished row: the job is left open with zero rows unless that row happens to be visited last. With two or more such rows it is always left open, because the last one visited still sees the other.
- T-19 (HELD plus RETURNED) passes in either order, as read.
- **Proposed next steps (for the SM's ruling):**
  1. T-20 gets a **second pending row**, so it fails whatever the order on today's code.
  2. **The SM's preferred fix:** C4 moves to a `post_delete` receiver, after the batch delete and in the same transaction. There it takes the wallet lock, reads the job status fresh, checks for rows left (accurate now) and finishes the job. `pre_delete` keeps only the hold return and the `deleted_items_*` carry.
  3. PFB-9 (the `post_delete` check skipped), killed by T-20 (and T-5, T-6, T-7, T-8, T-8b, T-19), plus PFB-1…8 re-run, the timing and the near runs, each declared first.

### §1.21 The SM's ruling on §1.20 (GO on 1–3, 2026-11-09) and the red for the two-pending T-20 (declared)

**The B1b-6 consequence of the order-dependence:** the pre_delete C4 check can't see which siblings the same collector run is about to delete. So any rule that decides "the last item is gone" inside `pre_delete` depends on the visit order. B1b-6's last-item helper must therefore run **after** the rows are gone (in `post_delete`, or after the batch delete), never in `pre_delete`. Its BATCH delete test must include two or more never-held pending rows, so it fails whatever the order.

**(a) "Terminal never reverts": the evidence.**
- Read from `billing/holds.py` at 68442c89c. There are seven `AIJob` status writes:
  - **:409–410, `finish_job`:** the only write on an existing job. It is conditional, `filter(pk=job.pk, status__in=OPEN_STATUSES).update(status=to_status)`, so it can't move a terminal job.
  - **:494 and :496, the successor:** these write a **new** job (`new_job`, made by `create_job` at :480) and never the old one.
  - **:814** (unit `before_call`), **:936** (`begin_single_call`) and **:1059** (`open_door_job`): each writes a job made by `create_job` a few lines above, in the same function. At :936 the refused road calls `finish_job(FAILED)` and raises first, so :936 never follows a terminal write.
  - **`create_job`** (:196) sets the first status of a new job.
- No admin, QA console or management command writes `AIJob.status` (grep: the only non-test, non-migration files that name AIJob are `billing/holds.py`, `billing/models.py`, `billing/testing/holds.py`, `students/models.py`, `students/student_data_map.py` and `audit/serializers.py`; none of the last five writes a status).
- **So once a job is terminal it stays terminal,** and the `post_delete` receiver may return early on a terminal status read without the lock.
- **B1b-6 obligation:** B1b-6 adds QUEUED→RUNNING on an existing job. Every new status write must be **open→open or open→terminal only**, conditional on the current status, the way `finish_job` is (`status__in=<the open from-states>`), and under the wallet lock. No write may move a terminal job. B1b-6 must re-run this grep and record the new list.

**(b) Can an AIJob be deleted in the same collector run as its rows? No.**
- Every FK on AIJob is `on_delete=SET_NULL`: `requested_by`, `wallet`, `course` and `assignment` (`billing/models.py:1806`, `:1816`, `:1824`, `:1831`).
- `BackgroundProcessingTask.job` is `SET_NULL` too (`students/models.py:635–637`).
- So no cascade from a user, wallet, course, assignment or task row reaches an AIJob. A collector run that deletes BPT rows only nulls AIJob FKs; it never deletes the job. An AIJob can be deleted only by deleting it directly, and then its rows are detached (`job_id` NULL), not deleted, so the receivers return at `instance.job_id is None` or read nothing.
- **No test is added for it, because the shape can't happen.** But the `post_delete` receiver still reads the job with `.filter(...).first()` and returns quietly on `None`, so a future CASCADE can't raise `DoesNotExist` mid-delete. If a CASCADE to AIJob is ever added, that change must add the test.

**(c) The near-run count, from `DiscoverRunner.build_suite().countTestCases()`** (scratchpad `count.py`, run at the working tree with T-19/T-20 in place):
- **Apps (billing students classrooms assignments users AutoGrader.tests_migration_rollback_defaults): 6337.** That is billing 2640, students 1066, classrooms 813, assignments 824, users 982, and AutoGrader rollback 12. There are 19 decorated skips.
- **Where the +2 comes from:** the B1b-1 near run at 601e9fee7 ran 6335. The file `test_holds_deleted_items.py` went from 18 to 20 tests (T-19 and T-20); nothing else changed. So 6335 + 2 = 6337, and the recount agrees. My earlier "6339" was an arithmetic slip, corrected before any run.
- **The 66 guards: 1146,** with 1 decorated skip. None of the 66 labels is in this file, so it is unchanged.

**The red, declared (T-20 now has two never-held pending rows, `job(total_items=3)`):**
- The command is `manage.py test billing.tests.test_holds_deleted_items --noinput`, under `flock`, at the commit that carries this section.
- **Prediction (today's code, `billing/holds.py` sha256 c2e49f1b…92b0af): `Ran 20`, `FAILED (failures=1)`.**
  - **T-20 fails on its last assertion:** `'RESERVED' != AIJobStatus.FAILED`, whatever the visit order (the last row visited always sees the other pending row).
  - Every other assertion in T-20 passes: no rows are left, reserved is back to the start, the books agree, and `deleted_items_*` = (300, 0, 300).
  - T-1…T-19 pass.
- Any other result is a STOP.

**Red result (at 5cb2620bd, under `flock`, 7043 MB available; `billing/holds.py` sha256 c2e49f1b…92b0af): `Ran 20`, `FAILED (failures=1)`, as declared.** Log: `step1/step1_red_t20two_5cb2620b.log`.
- T-20 failed at `test_holds_deleted_items.py:497` with `AssertionError: 'RESERVED' != AIJobStatus.FAILED`. Every earlier assertion in T-20 passed.
- T-1…T-19 passed.

### §1.22 The fix: C4 moves to `post_delete` (SM GO, §1.20 step 2), and the declarations (committed before any run)

**The code:**
- **`billing/holds.py`: new `finish_job_left_empty_on_item_delete`,** a `post_delete` receiver on `BackgroundProcessingTask`, connected in `BillingConfig.ready()` (`dispatch_uid` `billing.holds.finish_job_left_empty_on_item_delete`). In order:
  1. It returns if `instance.job_id` is None.
  2. It reads the job status without a lock. It returns quietly if the job is gone (`.first()`, §1.21 (b)) or if the status is terminal (it never reverts, §1.21 (a)).
  3. Otherwise it applies the same savepoint guard as `pre_delete` and takes the wallet lock (the same join).
  4. It reads the job again (`.filter().first()`) and returns unless the job is still open.
  5. It asks `_job_has_rows_to_run(job_pk)`. That is the same HELD-or-(unheld and unfinished) EXISTS, but with no exclusion, because the deleted rows are gone now.
  6. It calls `finish_job(SETTLED if SettlementRecord else FAILED)`.
- **`return_hold_on_item_delete` (`pre_delete`)** keeps only the hold return and the `deleted_items_*` carry. `_finish_if_nothing_left` and its two calls are removed, and so is the `job__status` field of the fresh read. The lock order is unchanged: the wallet first.
- **Why `post_delete` is accurate:** `Collector.delete` runs `delete_batch` for every row of the model **before** it sends any `post_delete` for that model. So the first `post_delete` already sees the table without any of the batch's rows.
- **Cost:** in a cascade, the first row that finds the job open and empty finishes it, and every later row costs one status read.

**One test change, forced by the fix:**
- T-18 patched `_finish_if_nothing_left`, which no longer exists. It now patches `_audit`, the HELD path's **last** call (after `_move_wallet` and both row updates).
- It still proves the same thing: a failure after the wallet moved undoes it all.
- No assertion changed.

**Green, declared:** `billing.tests.test_holds_deleted_items`, **`Ran 20`, OK.** Any other result is a STOP.

**Plants:** each is applied by `scratchpad/plant3.py` and restored with `git checkout`, then checked with `sha256sum -c` against this commit's `billing/holds.py` and `billing/apps.py`.

| Plant | The change | Kills (must not pass) | Allowed either way |
|---|---|---|---|
| PFB-1 | the `pre_delete` connect removed (`post_delete` stays) | T-1…T-6, T-9, T-10, T-11, T-13, T-14, T-17, T-19, T-20 | T-7, T-8, T-8b, T-12 (`post_delete`'s `finish_job` may clean up the leak). T-15 and T-18 survive |
| PFB-2 | `post_delete`'s `finish_job` call replaced by `pass` | T-5, T-6, T-7, T-8, T-8b, T-19, T-20 | none: the rest pass |
| PFB-3 | the HELD path's `deleted_items_*` update removed | T-1…T-6, T-13, T-14, T-17, T-19, T-20 | T-7, T-11, T-12 |
| PFB-4 | the release row written with `item=None` | T-1…T-5, T-11 | T-10 and T-12 survive (declared). T-19 and T-20 don't read release rows, so they pass |
| PFB-6 | the not-HELD path also moves the wallet by −(hold_credits − hold_drawn) | T-9, T-5 | T-10, T-19 (an ERROR on the reserved ≥ 0 check is allowed). T-20's pending rows move 0 |
| PFB-7 | the not-HELD path's `deleted_items_*` update removed | T-5, T-9, T-10, T-19 | T-20 passes (its pending rows carry zeros) |
| PFB-8 | the instance's RETURNED is trusted | T-17 | none |
| **PFB-9** | the `post_delete` check skipped: the receiver returns at once | T-5, T-6, T-7, T-8, T-8b, T-19, T-20 | none |
| **PFB-9b** (mine, beyond the GO) | the new receiver connected to `pre_delete` instead of `post_delete`, which is C4's old placement | **T-20** | none expected. The pre_delete hold return runs first (connected first), so a HELD row reads RETURNED by its own check. In T-5/T-6/T-7/T-8/T-8b/T-19 the last row visited finishes the job, as in §1.20 |

PFB-9b is the plant that pins the order-dependence. It differs from PFB-9 because its receiver *runs*, only too early.

**The timing:** the same file, on the fix. Prediction: **`Ran 1`, OK.**
- `no_job` stays about 23 queries: the new receiver returns at `job_id is None` with no query.
- `one_job` should drop from about 5,727 queries. Each RETURNED row goes from 5 queries (wallet lock, fresh read, UPDATE, job read, EXISTS) to 4 (three in `pre_delete`, one status read in `post_delete`). The C4 work runs once.
- **My estimate:** about 4,600 queries and about 4.2 s. That is still above the 2 s line, so the B1b-3 speed item and the switch-on checklist line stand, with the number updated.
- Nothing is asserted about the time.

### §1.23 Results for the `post_delete` fix (at 8f00a1447)

**Green run 4 (under `flock`, 7304 MB available): `Ran 20`, OK, as declared.** Log: `step1/step1_green4_8f00a144.log`.

**Plants:** each was applied by `scratchpad/plant3.py` and restored with `git checkout`. After every restore, `sha256sum -c` matched: `billing/holds.py` 5bf58994…2b6062 and `billing/apps.py` 02caeabd…c2cd47f. Logs: `step1/step1_plant4_PFB-*.log`.

| Plant | Result | Not passing | Against the declaration |
|---|---|---|---|
| PFB-1 | FAILED (failures=17) | T-1…T-6, T-7, T-8b, T-9…T-14, T-17, T-19, T-20 | killed. Every declared kill failed. T-7, T-8b and T-12 failed (allowed); T-8 passed (allowed); T-15 and T-18 survived (declared) |
| PFB-2 | FAILED (failures=7) | T-5, T-6, T-7, T-8, T-8b, T-19, T-20 | killed, exactly as declared |
| PFB-3 | FAILED (failures=14) | T-1…T-7, T-11…T-14, T-17, T-19, T-20 | killed. Every declared kill failed; T-7, T-11 and T-12 are allowed |
| PFB-4 | FAILED (failures=2, errors=4) | T-1…T-4 (ERROR, `KeyError: 'item_id'`), T-5, T-11 | killed, as at §1.17. T-10, T-12, T-19 and T-20 passed (declared) |
| PFB-6 | FAILED (failures=1, errors=3) | T-9 (FAIL `120 != 300`), T-5, T-10, T-19 (ERROR, `creditwallet_reserved_credits_nonneg`) | killed. T-10 and T-19 are allowed |
| PFB-7 | FAILED (failures=4) | T-5, T-9, T-10, T-19 | killed, exactly as declared |
| PFB-8 | FAILED (failures=1) | T-17 | killed, exactly as declared |
| **PFB-9** | FAILED (failures=7) | T-5, T-6, T-7, T-8, T-8b, T-19, T-20 | killed, exactly as declared |
| **PFB-9b** | FAILED (failures=1) | **T-20** | killed, exactly as declared: C4 in `pre_delete` is caught by T-20 alone |

**Timing (under `flock`, `RUN_LOAD_TESTS=1`, 7572 MB available): `Ran 1`, OK.** Log: `step1/step1_timing_AFTER3_8f00a144.log`.

| Shape | Seconds (3 runs) | Median | Queries (counted) |
|---|---|---|---|
| `no_job` | 0.0446, 0.0423, 0.0443 | **0.044 s** | 23, 23, 23 |
| `one_job` | 3.30, 3.35, 3.43 | **3.35 s** | 4735, 4735, 4735 |

- The estimate was about 4,600 queries and 4.2 s. The count is within 3% of that, and the time is lower.
- Against §1.17 (5.23 s, about 5,727 queries), that is 992 fewer queries, about one per RETURNED row (900), and about 1.9 s faster.
- The count is now the same in every run (4,735 ×3, where §1.17 varied). C4 runs once, in the first `post_delete`, instead of in every `pre_delete` while the job is open.
- **Still above the SM's 2 s line.** The B1b-3 speed item stands, and the switch-on checklist line now reads "about 3.3 s per 1,000".

### §1.24 The B1b-1 near run, again (declared before it runs; at the commit that carries this section)

The counts are from `DiscoverRunner.build_suite().countTestCases()` (§1.21 (c)). Neither of them changes at 8f00a1447: the test file still has 20 tests.

- **Apps:** `billing students classrooms assignments users AutoGrader.tests_migration_rollback_defaults`, `--parallel 4`, with `BILLING_HOLDS_ENABLED` unset. **Prediction: `Ran 6337`, `OK (skipped=23)`.** That is 19 decorated skips plus the 4 runtime skips, as at §1.18.
- **The 66 standing guards, serially:** **Prediction: `Ran 1146`, `OK (skipped=1)`.**
- Both run under `flock`. Any other result is a STOP.
- `makemigrations --check` is run first. Prediction: `No changes detected`.

**Results (at 9a6af9ad5):**
- **`makemigrations --check --dry-run`:** `No changes detected`, as declared.
- **Apps (7608 MB available, `--parallel 4`, flag unset): `Ran 6337 tests in 320.702s`, `OK (skipped=23)`.** As declared. Log: `step1/step1_near2_apps_9a6af9ad.log.gz`.
- **The 66 guards (7476 MB available, serial): `Ran 1146 tests in 139.514s`, `OK (skipped=1)`, rc 0.** As declared. Log: `step1/step1_near2_guards_9a6af9ad.log.gz`.

### §1.25 B1b-1 summary after the SM's review (for the checkpoint)

- **The finding** ("C4 may never fire in a cascade") is confirmed. The outcome depended on the collector's visit order (§1.20). T-20 now has two never-held pending rows, so it fails on the old code whatever the order (red, §1.21).
- **The fix:** C4 moved to a `post_delete` receiver (§1.22). `pre_delete` keeps only the hold return and the `deleted_items_*` carry. The lock order is unchanged: the wallet first.
- **Results:**
  - green: Ran 20 OK;
  - plants: PFB-1…9 and PFB-9b all killed as declared, with PFB-9b (C4 in `pre_delete`) killed by T-20 alone;
  - timing: 3.35 s and 4,735 queries per 1,000 rows (was 5.23 s);
  - near: apps 6337 OK (skipped=23), and the 66 guards 1146 OK (skipped=1).
- **Carried forward:**
  - **B1b-3:** the speed item (3.35 s > 2 s); the switch-on checklist line is updated.
  - **B1b-6:**
    - The last-item helper runs after the rows are gone, never in `pre_delete`.
    - Its BATCH delete test needs two or more never-held pending rows.
    - Status writes are open→open or open→terminal only, conditional on the current status and under the wallet lock. Re-run the §1.21 (a) grep.
    - A double-return plant.
  - **If a CASCADE to AIJob is ever added:** add the "job deleted in the same collector run" test (§1.21 (b)).

---

## §2 B1b-2: the flag-on-day list (08b §5.2). SM GO 2026-11-09, after B1b-1 was ACCEPTED at d4c733b51

**The SM's rules:**
- Survey first, then report the count and the classification **before** any test is edited.
- No test is weakened to pass.
- Each test gets a flag-on and a flag-off meaning, or a stated reason why only one applies.
- The checkpoint is at the survey.
- **Landing:** B1b lands on staging as one batch at the end. At the start of each step, merge `origin/staging` if it has moved.
  - At this step's start, `origin/staging` is bc7dd7a89, which is already in the branch (`merge-base --is-ancestor`). Nothing was merged.

### §2.1 The survey, part 1: static (no run)

- **The 45 still exist by name.** Each of the 45 full ids from B1a §21q (copied here as `step2/expected45.tsv`: class, kind, id) was loaded alone with `DiscoverRunner.build_suite([id])`. Each loads exactly 1 test. None is missing or renamed.
- **The counts**, from `build_suite` with `BILLING_HOLDS_ENABLED=True` in the environment:
  - **§21m's labels:** `billing ai_processor assignments students users audit.tests_h273_query_filters audit.tests_route_coverage`. There are **7040** tests: billing 2640, ai_processor 1474, assignments 824, students 1066, users 982, audit 54. That is 6875 at §21q, plus what staging (156 non-merge commits since 042c2642) and B1b-1 added.
  - **Plus the 66 guards** (08b §5.2: "the same labels plus all 66 guards"): there are **7396** tests, all with unique ids. The extra 356 are the guards outside those labels: AutoGrader 322, audit 13 and classrooms 21.
  - There are 25 decorated skips.

### §2.2 The survey, part 2: the flag-on run (declared before it runs)

- **The command:**
  - `BILLING_HOLDS_ENABLED=True` from the environment only, under `flock`, `--parallel 4`, at the commit that carries this section.
  - The labels are §21m's plus the 66 guards (scratchpad `flagon_labels.txt`).
- **Prediction: `Ran 7396`, `FAILED (failures=37, errors=8)`. The failing set is exactly the 45 in `step2/expected45.tsv`, each as its listed kind.**
- **The skip count is recorded, not predicted.** There are 25 decorated skips, and I can't count the runtime skips with the flag on without the run. §21q had 28 at 6875 tests.
- **This is a survey run.** If a test outside the 45 fails, it is the survey's finding, not something to fix here:
  - it is classified by reading its assertion and the flag-on path;
  - it goes to the SM with the count;
  - no test is edited.
- A listed test that passes, or one that fails as the other kind, is also reported as a finding.

**Result (at 590c1bd33, under `flock`, 7831 MB available): `Ran 7396 tests in 556.583s`, `FAILED (failures=37, errors=8, skipped=31)`. As declared.** Log: `step2/step2_flagon_survey_590c1bd3.log.gz`.
- The failing set, parsed from the FAIL/ERROR headers and compared with `diff` against `step2/expected45.tsv` (kind and id), is **exactly the 45**, each as its listed kind.
- No test outside the 45 failed, and none of the 45 passed.
- 31 tests were skipped (recorded): 25 decorated and 6 at runtime.

### §2.3 The classification (survey only: no test is edited until the SM rules)

The failing assertion of each test is quoted from the log above. A test's **flag-off meaning** is what it proves today. Its **flag-on meaning** is the same scenario under holds.

**B1, the flag and migration guards (6):**

| Test | Flag on, it fails at | Flag off | Flag on | Proposal |
|---|---|---|---|---|
| `test_holds_schema.PlanAndSettingsTests.test_holds_flag_defaults_off` | `:375` `True is not False` | the shipped default is off | n/a: the test is about the default, not the running value | **Not a pin.** `override_settings(False)` would make it vacuous, which weakens it. Instead, read the setting in a subprocess with `BILLING_HOLDS_ENABLED` removed from the environment (`.env` has no such key: `grep -c` gives 0) and assert False. It then holds in either environment. **SM ruling Q1** |
| `test_holds_schema.ReverseGuardTests.test_reverse_is_refused_while_a_wallet_holds_credits` | `:263`: the flag message is raised before the open-hold check | the open-hold half of 0074's guard | the flag half is **already** `test_reverse_is_refused_with_the_flag_on` (`:266`) | pin flag-off. **Survey correction:** 08b's "add one flag-on test that 0074's reverse is refused with the flag on" already exists, so no new test is needed |
| `test_holds_schema.ReverseGuardTests.test_reverse_is_allowed_with_the_flag_off_and_no_hold_open` | `:276` ERROR, refused (flag on) | flag-off by name | `:266`, as above | pin flag-off |
| `test_audit_identity_migration.AuditIdentityMigrationTests` ×3 (`test_backfill_is_idempotent`, `test_the_restore_brings_back_every_apps_leaf`, `test_user_id_survives_and_email_is_backfilled`) | `_migrate` `:163` ERROR, 0074 refused | the audit identity backfill and restore | **only flag-off applies:** these migrate back past 0074, which is refused with the flag on by design (pinned by `:266`) | pin flag-off on the class. The guard reads `django.conf.settings` when it is called, so `override_settings` reaches it |

**B2, the door launch deferred to `on_commit` (26).** `launch_at_door` (`assignments/upload_door.py:184`) calls the launch directly with the flag off. With the flag on it pre-assigns a uuid, writes it on the row, and sends at `on_commit`, which never runs under `TestCase`. The failures are all "never launched": `launch_processing_task` called 0 times, `[] != ['s0.png', …]`, `(0, 1) != (2, 1)`, `'PENDING' != FAILURE`, `0 != 2`, `2 != 12`, and `ValueError: not enough values to unpack` (TheSessionResultsShape ×3).
- **25 of 26:** wrap the request in `self.captureOnCommitCallbacks(execute=True)`, with the assertions unchanged. With the flag off, the wrapper changes nothing (the launch is direct); with the flag on, it runs the deferred send. So one test has one meaning both ways, and it is run both ways in the evidence (08b).
  - The files: `students.tests_h223_batch_runs_what_fits` ×8, `students.tests_h274_empty_student_answer_doors` ×4, `assignments.tests_h274_empty_upload_doors` ×1, `students.tests_batch_item_results` ×6, `students.tests_upload_credit_door` ×4, `students.tests_credits_mid_batch` ×1 and `billing.tests.test_h38_part2_removed_teacher_routes` ×1.
  - **A caution, to be declared per file before the edit:** several of these files post through a shared helper (`Base` in h223, `run_batch` in h274, the `BatchFixture` ones). Wrapping the helper also wraps tests that are not on the list. I will list those by name and run them both ways too, or wrap at the test instead where the helper is shared more widely.
- **1 of 26 can't keep its assertion with the flag on:** `students.tests_upload_credit_door.StudentUploadDoorTest.test_a_wallet_above_the_estimate_is_queued_as_before`.
  - It asserts `response.data["task_id"] == "task-1"`, the mocked launch's `.id` (`:208`). With the flag on, the response carries the door's pre-assigned uuid (`'1579b68c-…' != 'task-1'`).
  - That is a flag-off-only assertion. Its flag-on meaning (the response carries the id the row and the launch use) is **already** pinned on the same student route by `billing.tests.test_holds_door_commit.test_c2_the_response_task_id_is_the_preassigned_id_on_the_row`.
  - **Proposal:** pin it flag-off, unchanged, and cite c2. No new twin. **SM ruling Q2**
  - The other `"task-1"` lines in that file (`:220`, `:244`, `:260`, `:272`, `:317`, `:356`) set the mock, but no assertion reads them (grep).

**B3, the charge-then-refund ledger shape (12).** These prove the flag-off refund scope: a charge, then a refund on failure. With the flag on, a failed call is never charged, and settle charges once. The failures say exactly that: `1000 != 0`, `500 != 0`, `3000 != 0` (no refund row to net), `refund_credits called 0 times` ×2, `1 != 2`, `0 != 1 : a call that timed out is never charged`, `[] != [3300, 3300, 3300]`, `([1100], []) != ([1100], [3300])`, `([], []) != ([], [1100])`, `[] != [1100, 1100, 1100]`, and the ERROR in `test_successful_run_keeps_every_charge` (its fake picks the batch by the usage-log count, which stays 0 until settle).
- **The approach (08b):**
  - **Pin each flag-off, unchanged.** The flag-off refund path stays live code until it is deleted, which is not in B1b.
  - **Add a flag-on twin per scenario,** at the same entry point. It uses the B1a flag-on fakes (`billed(teacher, n, result)` with `patch.object(AIProcessor, PROVIDER, return_value=ai_response(n))`, as `test_holds_entry` E-1…E-8 do). Each twin asserts:
    - no usage row for a failed call;
    - one charge at settle for what succeeded;
    - no refund row;
    - `reserved_credits` back to 0;
    - the job FAILED or SETTLED as the scenario ends.
  - The twins go **in the same file**, in a class pinned `BILLING_HOLDS_ENABLED=True`, so they share the scenario's fixture. **SM ruling Q3** (same file, or a new `billing/tests/test_holds_flag_on_twins.py`).
- **The 12 and their twins:**
  - `billing.tests.test_grading_refund_scope.GradingRefundScopeTest`: `test_failure_during_persistence_refunds_the_ai_charge` → the twin: persistence dies after a billed grade, nothing is charged and the hold is returned. `test_malformed_summary_also_refunds` → the same, for a malformed summary.
  - `students.tests_answers_unreadable…test_the_edits_refusal_refunds_the_charge` → the edit's refusal charges nothing.
  - `students.tests_async_edit_path.ExtractionTaskTest.test_failure_after_the_charge_refunds_it` → a failure after the billed call charges nothing.
  - `students.tests_s6d_extraction_refund…test_a_mid_chunk_failure_nets_the_ledger_to_zero` → a mid-chunk failure leaves no usage row; the ledger is zero by construction.
  - `ai_processor.tests_grading_pipeline.PerAttemptRefundSemanticsTest.test_failed_attempt_is_refunded_successful_attempt_is_kept` → one charge at settle, for the successful attempt only.
  - `ai_processor.tests_grading_pipeline.FailedGradingRunRefundsEveryChargeTest.test_successful_run_keeps_every_charge` → a successful two-batch run is charged once at settle, the sum of both batches. Its fake is driven by call order, not the usage-log count.
  - `ai_processor.tests_h256_provider_timeout.TimedOutRunRefundsTest.test_a_run_that_times_out_refunds_what_it_charged` → a timed-out call is never charged (the failing message already says so) and the hold is returned.
  - `assignments.tests_upload_batch_billing`: `FailedFilesAreRefundedTest` ×2 and `LeftoverClaimsTest` ×1 → the failed or taken-over file charges nothing, its hold is returned, and a retry is still possible.
  - `assignments.tests_upload_task_retry_policy.AssignmentUploadTaskBillingTest.test_an_unusable_response_is_refunded_and_the_file_released` → charges nothing and the file is released.
- **Existing flag-on coverage, for the record (it doesn't replace the twins, because the entry points differ):**
  - `test_holds_entry` E-8 (a failed **sync** assignment upload charges nothing);
  - `test_holds_unit` "a unit that raises charges nothing";
  - `test_holds_single_call` "a provider failure charges nothing".

**B4, the patched balance (1):** `billing.tests_h281_individual_credit_wording.TheRaiseSitesTagTheRefusal.test_r8_execute_graded_task_nothing_left_individual`.
- Flag on, it fails at `:433` with `reached the provider`. The patched `total_remaining_credits` and an estimate of 0 skip `begin_single_call`'s reserve.
- **Flag-off meaning:** the flag-off balance check tags the refusal as individual.
- **Proposal (08b):** pin it flag-off, unchanged, and add a flag-on twin on a real path:
  - an individual teacher with a real 0-credit wallet;
  - an estimate above 0;
  - the provider patched to raise;
  - assert `hold_for_call` refuses before the provider, with `error.individual is True`.
- `test_holds_single_call`'s "cannot be held is refused before the provider" doesn't check the individual tag, so the twin is new.

**The totals for the SM:**
- 45 tests, all accounted for: B1 6, B2 26, B3 12, B4 1.
- **Flag-off pins:** 19 (B1 5, B2 1, B3 12, B4 1).
- **`captureOnCommitCallbacks` wraps:** 25, with assertions unchanged.
- **Rewritten:** 1 (`test_holds_flag_defaults_off`, Q1).
- **New flag-on tests:** 13 (B3 12, B4 1). The B1 flag-on test already exists (`:266`), and B2's one flag-on case is c2.
- No assertion is loosened anywhere.

### §2.4 The SM's rulings on the survey (2026-11-09): ACCEPTED

- **Q1, APPROVED:**
  - The subprocess runs with `BILLING_HOLDS_ENABLED` removed from its environment, the same `DJANGO_SETTINGS_MODULE` and no DB access, and asserts False.
  - **Plant:** flip the shipped default in settings; the test must go red. Restored by sha256.
- **Q2, APPROVED:** the test is pinned flag-off and left unchanged.
  - A comment cites c2 by its full test id.
  - NOTES confirm that c2 hits the same student route (URL name), not just the same door.
- **Q3:** the twins go in the same file as the original, in a class pinned `BILLING_HOLDS_ENABLED=True`.
- **(a) B2 helper wraps:** every off-list test that a helper-level wrap touches is declared by name and run both flag ways. If a helper wrap turns any flag-off test from red to green or from green to red, that is a STOP.
- **(b) Plants for the 13 twins,** declared before the build. At least:
  - B3: settle charges twice, or a refund row is written with the flag on. The twins must kill both.
  - B3: a hold left reserved after a failed call. The twins' `reserved == 0` check must kill it.
  - B4: `hold_for_call` skipped for `individual=True`. The B4 twin must kill it.
- **The build order:** B1, B4, B2 file by file, B3 file by file, with a declaration before every run. Then:
  - the §5.2 flag-on evidence run;
  - the flag-off near run (the apps plus the 66 guards, serially), with counts from `build_suite`;
  - then the checkpoint.

### §2.5 B1: the edits and the declarations (committed before any run)

**The edits:**
- **`billing/tests/test_holds_schema.py`:**
  - `PlanAndSettingsTests.test_holds_flag_defaults_off` is **rewritten** (Q1). It runs `sys.executable -c "<script>"` with `cwd=settings.BASE_DIR`.
    - The environment is the parent's, minus `BILLING_HOLDS_ENABLED`, with the same `DJANGO_SETTINGS_MODULE`.
    - The script sets `sys.argv = ['manage.py', 'test']` (as the test runner does) and prints `repr(settings.BILLING_HOLDS_ENABLED)`. Reading settings configures them lazily; no `django.setup()` runs and no DB is touched.
    - The test asserts rc 0, then that the last stdout line is `'False'`.
  - `ReverseGuardTests.test_reverse_is_refused_while_a_wallet_holds_credits` and `test_reverse_is_allowed_with_the_flag_off_and_no_hold_open` are pinned with `@override_settings(BILLING_HOLDS_ENABLED=False)`, with their bodies unchanged.
- **`billing/tests/test_audit_identity_migration.py`:** `AuditIdentityMigrationTests` is pinned at class level with `@override_settings(BILLING_HOLDS_ENABLED=False)`, with the bodies unchanged. A comment names the flag-on half by its full id.
- **The "break" side of the pins** is already in evidence. The survey run (§2.2, same bodies, no pin, flag on) shows each one failing on 0074's refusal. A pin changes only which flag value the test sees.

**The runs** (`billing.tests.test_holds_schema billing.tests.test_audit_identity_migration`, **26 tests** by `build_suite`: 23 + 3; under `flock`, serial):
- **R1, flag on from the environment (`BILLING_HOLDS_ENABLED=True`): `Ran 26`, OK.**
- **R2, flag unset: `Ran 26`, OK.**
- **PB1-1, the plant:** `AutoGrader/settings.py:698` gets `default=True` in place of `default=False`, and the run is made with the flag unset. **Prediction: `Ran 26`, `FAILED (failures=1)`.**
  - The failure is `test_holds_flag_defaults_off`, on its last assertion: `'True' != 'False'`.
  - Everything else passes, because the pins hold whatever the default is.
  - The file is restored with `git checkout` and `sha256sum -c`.
- Any other result is a STOP.

**B1 results (at edfb9b8f5, under `flock`):** all as declared. Logs: `step2/b1_*_edfb9b8f.log`.
- R1 (flag on): `Ran 26`, OK.
- R2 (flag unset): `Ran 26`, OK.
- PB1-1 (`default=True` in settings): `Ran 26`, `FAILED (failures=1)`. The failure is `test_holds_flag_defaults_off`, with `AssertionError: 'True' != 'False'`.
  - Restored with `git checkout`; `sha256sum -c` matched `AutoGrader/settings.py` and both test files.

### §2.6 B4: the edits and the declarations (committed before any run)

**The edits (`billing/tests_h281_individual_credit_wording.py`):**
- **The pin:** `TheRaiseSitesTagTheRefusal.test_r8_execute_graded_task_nothing_left_individual` gets `@override_settings(BILLING_HOLDS_ENABLED=False)`, with the body unchanged. A comment names its flag-on twin.
- **The twin (new):** `TheHoldRefusalIsTaggedToo.test_r8_flag_on_hold_for_call_refuses_individual`, in a class pinned `BILLING_HOLDS_ENABLED=True`.
  - The setup:
    - an individual teacher (`individual_teacher()`: a real wallet with no credits and no license);
    - `can_user_access_ai` patched True (as R8 does);
    - `estimate_messages_cost` patched to **1,000** (above 0);
    - the provider patched to raise `AssertionError("reached the provider")`.
  - Nothing is patched on the wallet.
  - It asserts:
    - `InsufficientCreditsError` with `.individual is True`;
    - the SINGLE_CALL job is FAILED;
    - `reserved_credits == 0`;
    - no `CreditUsageLog` row for the wallet.
- **The file goes from 44 to 45 tests** (`build_suite`).

**Plants** (`scratchpad/plant_b12.py`, each restored with `git checkout` and checked with `sha256sum -c` against this commit's `billing/holds.py`). Each runs on this file with the flag **unset**, so only the twin (pinned on) reaches the hold path.

| Plant | The change | Prediction |
|---|---|---|
| PB4-1 (SM) | `hold_for_call` returns None for an individual teacher | `FAILED (failures=1)`: the twin, with `AssertionError: reached the provider` |
| PB4-2 (mine) | `_refusal` tags `individual=False` | `FAILED (failures=1)`: the twin, with `False is not True` |

**The runs** (`billing.tests_h281_individual_credit_wording`, 45 tests, under `flock`):
- **R1, flag on: `Ran 45`, OK.**
- **R2, flag unset: `Ran 45`, OK.**
- Any other result is a STOP.

**B4 results (at a00c4e86f, under `flock`):** all as declared. Logs: `step2/b4_*_a00c4e86.log`.
- R1 (flag on): `Ran 45`, OK.
- R2 (flag unset): `Ran 45`, OK.
- PB4-1: `FAILED (failures=1)`, the twin, with `AssertionError: reached the provider`.
- PB4-2: `FAILED (failures=1)`, the twin, with `AssertionError: False is not True`.
- Both were restored; `sha256sum -c` matched `billing/holds.py`.

### §2.7 B2: how the wraps are placed, and the first file

**Every wrap is at the test, not in a shared helper.** `with self.captureOnCommitCallbacks(execute=True):` goes around the request inside each listed test, and the helpers are left unchanged. So **no off-list test is touched** (SM (a)), and none can go from red to green or from green to red. Each file's diff will show only listed tests changing.

**The B2 plant (one run over all the B2 files, after the last file):**
- **PB2-1:** with the flag on, `launch_at_door` registers no `on_commit` send. It still pre-assigns and writes the id, but the message never goes.
- The wrapped tests must fail on "never launched" with the flag on, which proves each wrap is what runs the launch. That is declared with the last file.

**File 1, `students.tests_upload_credit_door` (17 tests by `build_suite`):**
- **Wrapped (4):**
  - `StudentUploadDoorTest.test_a_file_the_door_cannot_read_is_left_to_the_task`;
  - `StudentUploadDoorTest.test_a_teacher_without_a_wallet_is_left_to_the_permission` (inside its permission patch);
  - `TeacherBatchDoorTest.test_a_funded_teacher_is_queued_as_before`;
  - `AssignmentUploadDoorTest.test_a_funded_teacher_is_queued_as_before`.
- **Pinned flag-off, unchanged (1, Q2):** `StudentUploadDoorTest.test_a_wallet_above_the_estimate_is_queued_as_before`.
  - Its comment cites `billing.tests.test_holds_door_commit.DoorLaunchOnCommitTests.test_c2_the_response_task_id_is_the_preassigned_id_on_the_row`.
  - **Same route, confirmed:** c2's `student_upload` (`test_holds_door_commit.py:38–43`) posts to `reverse("student-submission-upload-async", kwargs={"assignment_id": …})`. That is the same URL name as this test's `_upload` (`tests_upload_credit_door.py:167`). Both patch the same launch, `students.views.launch_processing_task` (`STUDENT_LAUNCH`, `:28`).
- No assertion changed.

**The runs** (`students.tests_upload_credit_door` plus c2 by full id: **18** tests; under `flock`):
- **R1, flag on: `Ran 18`, OK.**
- **R2, flag unset: `Ran 18`, OK.**
- Any other result is a STOP.
