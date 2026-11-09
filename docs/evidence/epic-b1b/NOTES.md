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
