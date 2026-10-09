# Epic A completion: S6 (FR-A-06 reason codes) and S7 (FR-A-07 per-item batch results). Design

Hardening Engineer (grade-automator-plus-d5), 2026-09-29. Input for the Security Engineer's `08_epic_a_completion_plan.md`. Design only, with no code branch: the slices are cut from phase2/epic-a after 0b's `task/epic-a-mypy-stubs` lands.

**Review status.** This was researched read-only, and each load-bearing file:line was re-checked by hand on `96bb182`:
- `billing/refusals.py:27,50-60`;
- the bare `raise Exception` at `ai_processor/services.py:4098`, which has no `from`;
- the whole-batch 413 at `students/views.py:1244-1248`;
- the emitter regex at `audit/emitter.py:73,251-254`;
- the ENROLLED-only lookup at `students/services.py:1192-1201`;
- `users/renderers.py:87-94`;
- `_grading_failure_error_class` at `assignments/tasks.py:450-460`;
- the upload-async loop.

Caveats:
- The #8 claim that a concurrent first-create IntegrityError re-bills the extraction is **inferred, not run**.
- §4.4's `NOT_RETRYABLE` is an 11th code outside the QA catalogue. It is a request-level refusal, not an item condition, and it has to be added to the enum with the others kept alongside.
- **Also a live Phase 1 bug on beta, not only on this branch:** upload-async validates size inside the dispatch loop, after the session and the earlier files' tasks exist (beta `e7e4bdf`, `assignments/views.py:991/1008/1028`). It is raised with the SM as a hardening candidate, so it can be fixed on beta ahead of S7a.

Tree read: `Grade-Automator-Plus-phase2-epic-a` @ `96bb182` (phase2/epic-a). All `file:line` references are on that tree unless prefixed `docs/`.
Sources: `docs/phase2/architecture/01a_requirements_specification.md:60-61,76,207,233,269,305`; QA doc `docs/backend/phase 2/Phase 2 Part 1 QA Testing Requirements.docx` §7.2 (QA-ERR-01..04); `04_epic_a_implementation_plan.md` §3.1 (l.494-524), §10 (l.950-951); `03_architecture.md` §9.10 (l.692), §9.12a (l.721); `03a_data_model.md` §4.5 (BackgroundProcessingTask columns) and AIJob (l.130-160).

**QA catalogue diff (plan §3.1 action item, now closed):** QA-ERR-02 lists exactly the same 10 conditions as FR-A-06, and nothing more. The plan's 10 ids map 1:1. There are no QA additions to import.

---

## 0. Scoping change: FR-A-07 moves into Epic A

- **Before:** `04_epic_a_implementation_plan.md:951` placed FR-A-07 "out of Epic A's direct scope … `GradingBatchItem` … Epic B/E territory. Epic A supplies the taxonomy/reason codes this consumes."
- **Now:** the founder has put FR-A-07 in "Epic A completion".
- **What this implies:**

| # | Implication |
|---|---|
| 1 | Epic A takes a **migration on `students.BackgroundProcessingTask`** (the architecture's replacement for `GradingBatchItem`, `03_architecture.md:721`): `reason_code`, `retry_count`, `item_index` (per `03a_data_model.md` §4.5), plus `trace_id` (for QA-ERR-04). The **`job` FK → `billing.AIJob` stays in Epic B**, because AIJob does not exist on this branch (`git grep AIJob` finds nothing). |
| 2 | The `GET /api/v1/tasks/session-results/{id}` contract changes. That is a frontend contract (FE-A-03), so it needs FE coordination. |
| 3 | For INSUFFICIENT_CREDITS_MID_BATCH, Epic A can deliver only a **partial** FR-B-06: a derived `stopped_at_item`, short-circuiting the remaining items, and resume through retry. Reservation, `QUEUED_PENDING_CREDITS` and auto-resume remain Epic B, which will re-point `stopped_at_item` onto `AIJob`. The S7 columns are exactly the ones §4.5 prescribes, so none of this work is thrown away. |
| 4 | Per-item retry of **upload** items without re-upload depends on persisting the submission files, which are not stored today (see §3). That needs a founder decision (F4). |
| 5 | The legacy `BatchUploadSession.results` JSON path (scheduled grade and auto-grade) has to be retired or brought up to parity. Otherwise those batches can never carry codes. |
| 6 | Plan §10's FR-A-07 row must be rewritten as "owned: 30/12 test + per-item retry test". |

---

## 1. Catalogue (the 10 codes)

Each value is UPPER_SNAKE, which the `AuditEvent.reason_code` validator already requires (`audit/emitter.py:73,251-254`). `{…}` marks a safe placeholder. The messages go only to the uploading or grading teacher, who is entitled to see the file name and the name read from the paper (QA-ERR-03). Placeholders never go into audit metadata.

| # | Stable id | ErrorClass | Display message | Remediation hint | HTTP (sync) / item | Retryable as-is |
|---|---|---|---|---|---|---|
| 1 | `MISSING_STUDENT_NAME` | USER | "We couldn't match {file_name} to a student: {name_state}." where `name_state` is one of "no name was found on the paper", "the name "{extracted_name}" doesn't match anyone on the roster", or "the name "{extracted_name}" matches more than one student" | "Choose the student this paper belongs to, or add them to the course." | 422 / item | no (resolve by assigning a student) |
| 2 | `STUDENT_NOT_ON_ROSTER` | USER | "{file_name} belongs to {student_display}, who isn't enrolled in this course." | "Add the student to the course roster, then retry this paper." | 422 / item | no (enrol first, then retry) |
| 3 | `FILE_UNREADABLE` | USER | "We couldn't read {file_name}. It may be damaged, password-protected or incomplete." | "Re-export or re-scan the file and upload it again." | 422 (today 400) / item | no (replace the file) |
| 4 | `FILE_TYPE_UNSUPPORTED` | USER | "{file_name} is a {detected_type} file, which isn't supported. Accepted types: {accepted_types}." (`accepted_types` = "PDF, JPEG, PNG, GIF, WebP") | "Save or export the file as a PDF or an image, and upload it again." | 415 (today 400) / item | no |
| 5 | `FILE_TOO_LARGE` | USER | "{file_name} is {actual} and the limit is {limit}." where `{actual}/{limit}` is either "63.2 MB / 50 MB" or "312 pages / 300 pages" or "9000x9000 px / 40 MP" (params carry `dimension: bytes\|pages\|pixels`) | "Split the file, remove blank pages or scan at a lower resolution, then upload again." | 413 / item | no |
| 6 | `SUBMISSION_EMPTY` | USER | "{file_name} has no student answers to grade." | "Check that the right file was uploaded, then upload it again or mark the submission as missing." | 422 / item | no |
| 7 | `RUBRIC_MISSING` | VALIDATION | "This assignment has no rubric to grade against, so grading hasn't started. No credits were used." | "Add questions and a rubric to the assignment, then grade again." | 409 / whole request (before any item is queued) | no |
| 8 | `DUPLICATE_SUBMISSION` | USER | "{file_name} is a second submission for {student_display}. The existing submission ({existing_ref}) was kept." | "Choose which submission to keep." | 409 / item | no (resolve keep/replace) |
| 9 | `PROVIDER_FAILURE` | PROVIDER (MODEL for a model refusal or unusable output) | "The grading service couldn't finish this item. {credit_clause}" where `credit_clause` = "No credits were charged." or "The credits were refunded." (F1) | "Try again in a few minutes. If it keeps happening, contact support and quote the reference." | 503 + `Retry-After` (today 500) / item | **yes** |
| 10 | `INSUFFICIENT_CREDITS_MID_BATCH` | USER | "Credits ran out after {completed} of {total} items. The finished items are saved." | "Top up credits, then resume the remaining items. There is no need to upload them again." | n/a (202 already returned) / item + session | **yes** (after top-up) |

- **Kept alongside the 10 (they already exist):**
  - `INSUFFICIENT_CREDITS`: the pre-flight 402 on an empty wallet.
  - `AI_FEATURE_NOT_AVAILABLE`: 403.
  - The ad-hoc auth audit codes `ACCOUNT_LOCKED`, `WRONG_PASSWORD`, `INVALID_CREDENTIALS` (`users/serializers.py:428,447-449`) and `REFRESH_TOKEN_MISSING`/`_INVALID` (`users/views.py:1087,1099`). These should join the enum so the emitter can validate against it.
- **Every coded body also carries:**
  - `error_class`
  - `retryable`
  - `params`
  - `reference`
- **Audit-only codes added after this design** (members of `AutoGrader.reason_codes.AUDIT_ONLY_CODES`: no response spec, never sent to a client):
  - `COURSE_NOT_REACHABLE` ("Course not reachable"), added by H-38 tasks N3 (merged at `857d6a78`, 2026-10-02).
    - **Meaning:** the teacher a grading run would act for can no longer reach the course (removed from its school, or no longer the course's teacher). Nothing is graded or charged.
    - **Recorded on:** `GRADING_FAILED`, outcome FAILURE, error class USER, filed under the course's school.
    - **Emitted by:** `assignments/tasks.py`, the three H-38 run-time refusals: `grade_engine_async` (target: the submission), `grade_batch_async` and `auto_grade_due_assignment` (target: the teacher, with the assignment's id in the metadata).
    - **What the client sees instead:** the uncoded "This course wasn't found." `CourseNotReachableError` stays uncoded (SM ruling, 2026-10-02).

---

## 2. Current-code map (per condition)

**Legend.** A **distinct** condition has its own exception type or status, a stable signal a client can branch on. A **generic** condition shares a type or status with other conditions and differs only in its message prose; that is a defect under QA-ERR-02. **Undetected** means no check exists.

**Two facts apply to every row:**

- **(a) No per-condition machine code exists anywhere.** DRF's `exception_handler` renders `{"detail": msg}` and drops `ErrorDetail.code`. So `ParseError`'s `parse_error` and `PayloadTooLarge.default_code = "file_too_large"` (`AutoGrader/uploads.py:18`) never reach the body. The only codes on the wire are `refusal_response`'s two.
- **(b) The wire envelope.** The body is always wrapped by `users/renderers.py:139-166` as `{"success": false, "message": <flattened>, "error": {"field_errors": <view payload>}}`, and `X-Request-ID` is added as a header (`AutoGrader/middleware.py:68`). For async items, the text lands in `BackgroundProcessingTask.error` via `describe_background_task_error` (`students/task_tracking.py:36,225-243`). It surfaces through `session-results` → `failure_list[].error` (`users/views.py:2096-2102`).

### 2.1 Existing refusal-code mechanism (`billing/refusals.py`): how it works

| Piece | Where | Behaviour |
|---|---|---|
| `PERMANENT_AI_REFUSALS` | `billing/refusals.py:27` | `(AIFeatureNotAvailableError, InsufficientCreditsError)`. This is a **retry-policy** classifier: these are never retried in-process or by Celery. Consumers: `assignments/tasks.py:73` (UPLOAD_REFUSALS), `:456` (error class); `dashboard/tasks.py:82,252`; `students/task_tracking.py:228` (log level). |
| `_REFUSAL_HTTP` | `:32-43` | Maps type → (status, code): `InsufficientCreditsError` → 402 `"insufficient_credits"`; `AIFeatureNotAvailableError` → 403 `"ai_feature_not_available"`. Checked with isinstance, in order. `EmptyWalletError` (`billing/errors.py:9-16`) subclasses InsufficientCreditsError, so `HasCreditBalance` gets 402 too. |
| `refusal_response(exc)` | `:50-60` | `Response({"error": describe_user_error(exc), "code": code}, status)`, or None for a non-refusal. The message comes from `describe_user_error`. For credits it is always the fixed `INSUFFICIENT_CREDITS_MESSAGE` (`AutoGrader/error_messages.py:60-63`, `billing/errors.py:23-26`), so balance and deficit never leak. |
| `log_refusal` | `:63-73` | WARNING, no traceback, and the raw text stays server-side. |
| Global hook | `users/exceptions.py:20-25` | `custom_exception_handler` calls `refusal_response` first for any uncaught refusal (this is the `HasCreditBalance` path). Everything else goes to DRF and then to a 500. |
| View callers | `students/views.py:123-137` (`_failure_response`: refusal → coded; user-facing → 400 `{"error"}`; else → 500 fallback), `billing/views.py:2736-2740`, `dashboard/views.py:202-204` | |
| Renderer | `users/renderers.py:87-94` | `flatten_errors` hides a string `code` key from `message`. Wire result: `error.field_errors = {"error": msg, "code": "insufficient_credits"}`. Tests already assert this path: `ai_processor/tests_superadmin_unmetered_both_flags.py:204,243`. |

**Verdict: extend it, do not build a parallel system.** Reuse the type → (status, code) table, the single response builder, the global-handler hook and the renderer path. Two caveats:

- **Keep `PERMANENT_AI_REFUSALS` as the retry-policy tuple.** Reason codes are orthogonal: `PROVIDER_FAILURE` is retryable, and a refusal is not.
- **Adding envelope keys breaks `flatten_errors`.** Keys such as `remediation` or `reference` make `shown_keys` > 1 (`users/renderers.py:89-94`), and the `message` becomes a numbered dump of every field. The renderer must learn the envelope keys (part of S6a).
- **The two legacy codes are lowercase.** They fail the audit `reason_code` regex, so the envelope needs a separate UPPER `reason_code` field (see §4.2).

### 2.2 Per-condition sites

| # | Site (file:line) | What it does today | Status / body | Generic? |
|---|---|---|---|---|
| **1 Missing / unmatched name** | `students/services.py:1192-1196` | `CannotAssociateStudentError("Student name cannot be found in the submission")` | Reached **only** on teacher proxy uploads (`assignments/tasks.py:814,840`, i.e. batch-upload). Caught as UPLOAD_REFUSALS `assignments/tasks.py:879-897`, which sets the item FAILURE with the message verbatim (passthrough `error_messages.py:64-67`) and `session.update_result(file_name,"FAILED")` without a code. | **Generic.** One type for 3 cases. |
| | `students/services.py:1217-1220` | No enrolled match gives the same type: "Student not among the enrolled students in the course". The **name read is not stated**. | same | generic |
| | `students/services.py:1221-1228` | Ambiguous match gives the same type, with the name quoted. | same | generic |
| | Per-item isolation | Per-item ✓: other files continue. No match/add path ✗. The billed extraction is **not refunded**, because `upload_answers_engine` (`students/services.py:824-976`) has no `billing_refund_scope`, and `execute_graded_task` only registers charges with an open scope (`ai_processor/services.py:4584-4587`). | | |
| **2 Not on roster** | `students/services.py:1198-1201` | The lookup searches **only ENROLLED students of this course**. A real student who is PENDING, dropped, or in another of the teacher's courses therefore gets the #1 "unmatched" text. | as #1 | **Not distinguished.** Indistinguishable from #1 (defect). |
| **3 Unreadable / corrupt** | `assignments/services.py:377-392,410-418` (image) | `ParseError("{name} could not be read as an image…")` / "could not be decoded…" names the file ✓. | Sync student upload: raised outside the try (`students/views.py:526`) → 400 `{"detail": msg}`. Async/batch: `assignments/tasks.py:824-831` → `InvalidUploadFileError` (a ParseError subclass, `assignments/exceptions.py:12-19`) → item error. | **Generic.** Shares ParseError/400 with #4, #5-pages, #5-pixels and #6, and with "not open" and "no files" (`students/views.py:493,497`). |
| | `ai_processor/services.py:5044-5045,5087-5091` | PDF: `ValueError(f"Could not read this PDF: {e}")`, wrapped as a ParseError at `assignments/services.py:444-447`. **The file is not named, and raw PyMuPDF/poppler text is embedded (QA-ERR-03 risk).** | same | generic |
| | `ai_processor/services.py:5052-5056` | "This file is not a PDF…" is content sniffing, and is really a type mismatch. | same | generic |
| | `AutoGrader/error_messages.py:148-156` | Infra classifier message: "…may be corrupted, password-protected, **or in an unsupported format**." This **conflates #3 with #4** (a QA-ERR-02 #3 defect). It is used for async task errors. | item text | generic |
| **4 Unsupported type** | `assignments/services.py:457-461` | `ParseError("Unsupported format: {name}. Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed.")` states the accepted types ✓. It decides on the **client-declared** `content_type` only. | 400 sync / item text async | **Generic** (ParseError 400). |
| | `students/views.py:503-506,601-604` | "Invalid file upload. Only images … and PDFs are allowed." It fires only when the object is not an `UploadedFile`, so it is **not a type check** (effectively dead). | 400 | n/a |
| | `ai_processor/services.py:5033-5036` | `"Unsupported file type: {content_type}"` is unreachable through `prepare_ai_content`, which only routes the PDF content_type there. | | |
| | `assignments/views.py:1002-1007` | The OpenAPI schema documents 415, but no code returns 415. | | |
| **5 Too large / page limit** | `AutoGrader/uploads.py:15-38` | `PayloadTooLarge` (413). The message states the actual MB and the 50 MB limit ✓. Callers: `students/views.py:508,606,1248`; `assignments/views.py:906` (per-item in sync upload ✓), `:1066`. | 413 `{"detail": msg}` (code dropped) | **Distinct** (own type and status), but no code on the wire. |
| | `students/views.py:1247-1248` | batch-upload validates **every** file first, so one oversized file gives a 413 for the **whole batch** (the comment at l.1244-1246 says this is intended). | 413 whole request | wholesale failure |
| | `assignments/views.py:1056-1066` | upload-async validates **inside** the dispatch loop, so a later oversized file returns 413 after the earlier files' tasks were already queued (half-queued session). | 413 + orphan tasks | bug |
| | `ai_processor/services.py:5061-5065` | Page limit: "PDF has {n} pages, which exceeds the maximum of 300…" states both ✓. It becomes a ParseError. | 400 | **Generic** |
| | `assignments/services.py:394-400` | Megapixels: states both ✓. ParseError. | 400 | generic |
| | `ai_processor/tools.py:351-354` → `assignments/services.py:408-409` | ImageCompressionError states the cap in bytes, not the actual size ✗. Infra text at `error_messages.py:143-147` gives no numbers. | 400 / item | generic |
| **6 Empty / blank** | `ai_processor/services.py:5058-5059` | "This PDF has no pages." becomes a ParseError. | 400 | generic |
| | `students/views.py:685-686,727-728`; `students/services.py:1124-1125` | Empty raw_input: ParseError "raw_input is required." A `ValueError` in the service would be a 500 via `_failure_response`; the views pre-empt it. | 400 | generic |
| | `ai_processor/services.py:2128-2132` | The `if not content` guard is **unreachable for uploads**, because content always starts with the prompt text item (`assignments/services.py:422`). | | |
| | `students/services.py:862-868` | Only `isinstance(answers, list)` is checked. `[]` or all-`BLANK` answers are **accepted, saved, charged and later graded** (`ai_processor/services.py:1889-2090` only re-reads blanks). | success | **Undetected** (blank page / no answers) |
| **7 Missing rubric** | `students/services.py:274-289,448-474`; `ai_processor/services.py:3693-3706` | There is no check. Missing or malformed `assignment.questions` is silently coerced to `[]`, and the **billed** LLM call proceeds. A per-question rubric with fewer than 2 levels is silently treated as "no rubric" (`ai_processor/services.py:2296-2323`). grade (`students/views.py:785-817`), grade-async (`:832-874`), grade-all (`assignments/views.py:1624-1688`) and schedule (`:1701-1745`) do not check either. The only "no questions" check is in download-pdf (`assignments/views.py:1862`). | charged | **Undetected** |
| **8 Duplicate** | `students/services.py:903-929` | Proxy upload onto an existing **ungraded** row **silently overwrites** `answers`. Two files in one batch that resolve to the same student both report SUCCESS with the same `submission_id`. Only a graded row (`SubmissionAlreadyGradedError`, `:1009-1013`) or one being graded (`:1014-1018`) is refused: 409 sync (`students/views.py:119-120`) or item text. A concurrent first-create race hits `unique_student_submission_per_assignment` (`students/models.py:216-219`). I **infer, not verify**, that the IntegrityError takes the generic retry branch (`assignments/tasks.py:898-913`) and re-bills the extraction. | success / 409 | **Undetected** as a conflict |
| **9 Provider failure** | `ai_processor/services.py:662-727` | This is the raw OpenRouter call. Wrappers `raise Exception(f"Error during AI model: {e}") from e` sit at `:781,837,1859,4336,4691`. | | |
| | `ai_processor/services.py:4098` | `extract_grade_with_retry` raises a bare `Exception("All 3 attempts failed…")` **without `from last_error`** and outside the except block. That leaves no `__cause__`/`__context__`, so `classify_infra_error` (`error_messages.py:175-204`) cannot classify **grading** provider failures. They get the generic "We couldn't grade this submission" (`assignments/tasks.py:617-626`), and `_grading_failure_error_class` returns **SYSTEM** (`assignments/tasks.py:450-460`). (Answer extraction does keep `from`: `:2216-2218`.) | item text | **Generic** |
| | `students/views.py:811-817` | Sync grade → `_failure_response` → **500** with fallback text. | 500 `{"error"}` | generic |
| | `AutoGrader/error_messages.py:116-142` | For async items, timeout, rate limit, connection and 5xx each get **distinct prose**, but no code. The same classifier also emits file messages (`:143-156`). | item text | generic |
| | `assignments/tasks.py:456-457` | **Misclassification:** `PERMANENT_AI_REFUSALS` (credits/plan) → `ErrorClass.MODEL`. There is no content-policy/refusal detection at all (no `finish_reason`/`refusal` check). | audit | defect |
| | Credits | `execute_graded_task` charges only after a successful response (`ai_processor/services.py:4546-4573`), so a failed call is **not consumed**. Earlier charges in the same run are **refunded** where a scope is open: grading (`:3541`, `students/services.py:463`), assignment upload (`assignments/file_uploads.py:121`), submission edit (`students/services.py:1139`). **Answer extraction has no scope**, so the chunks charged before a mid-extraction failure are kept. | | |
| **10 Credits mid-batch** | `users/permissions.py:41-49` | At request time, `HasCreditBalance` raises `EmptyWalletError` only when the balance is ≤ 0. `users/exceptions.py:20-25` then returns 402 `{"error": INSUFFICIENT_CREDITS_MESSAGE, "code": "insufficient_credits"}`. | 402 coded | **Distinct** (the only real code), but not mid-batch |
| | `ai_processor/services.py:4533-4542` | Per call: `InsufficientCreditsError` when the balance is below the estimate. | | |
| | Mid-batch today | batch-upload (`students/views.py:1260-1295`) and grade-all (`assignments/views.py:1642-1678`) fan out **N independent parallel Celery tasks**. Credits running out means each later task fails on its own with the same fixed message: upload via UPLOAD_REFUSALS (`assignments/tasks.py:879-897`, not retried), grade via the generic branch (`:616-664`). Completed items are durable ✓ because each commits alone. **There is no `stopped_at_item`, and the failures can be non-contiguous.** Nothing tells mid-batch apart from pre-flight: both are `insufficient_credits`. **Resume:** grading is de facto resumable by re-running grade-all (it filters `graded_at__isnull=True`, `assignments/views.py:1629`). Uploads are **not** resumable without re-upload, because the bytes live only in the Celery message (`students/views.py:1270-1272`) and are never persisted. | item text | generic |

### 2.3 Tally

| Signal today | Conditions |
|---|---|
| Distinct type/status (still no per-condition code on the wire) | #5 byte-size (413), #10 pre-flight credit (402 `insufficient_credits`). Partial for both: pages are generic, and mid-batch is not distinguished. |
| Generic (detected; distinct only by prose, shared exception or status) | #1, #2 (in fact **not** distinguished from #1), #3, #4, #9 |
| Undetected | #6 (except zero-page PDF / empty text), #7, #8 |

**Machine-readable per-condition code today: 0 of 10.**

**Incidental QA-ERR-03 findings:**

- `"Could not read this PDF: {e}"` exposes raw library text (`ai_processor/services.py:5045,5091`).
- `upload_assignment_async` writes `str(e)` raw into `session.results` (`assignments/tasks.py:1081`). It is visible through the legacy `session-results` path.
- The legacy `grade_all_submissions` puts `str(e)` plus a stack trace into Celery meta (`assignments/tasks.py:158-166`). This task is not dispatched.
- Second-opinion `"error": str(e)` stores the credit-exception text (balance) in `feedback` (`ai_processor/services.py:3504`).

**QA-ERR-04 today:**

- Sync errors: the reference is only in the `X-Request-ID` header, not the body.
- Per-item failures: none. `BackgroundProcessingTask` has no trace column (`students/models.py:353-403`).

---

## 3. Batch route inventory (FR-A-07)

All URLs are under `/api/v1/`. "Item result today" lists the exact field names.

| Route | View / task | Sync? | Item result today | Per-item retry today | Gap to "30 items / 12 failures → 12 item reason codes" |
|---|---|---|---|---|---|
| `POST submissions/{assignment_id}/batch-upload` | `students/views.py:1231-1318` → `upload_answers_engine_async` `assignments/tasks.py:793-931` | async | 202 `{session_id, message, tasks:[{file_name, task_id}]}`. Per item via `GET tasks/session-results/{session_id}` (`users/views.py:2066-2194`): `{progress, percent, is_complete, success_count, failure_count, cancelled_count, pending_count, resource_type, resource_id, action, additional_ids, success_list[], failure_list[], cancelled_list[], pending_list[]}`. Each entry is `{status, file_name, task_id, error, context}` (`users/serializers.py:564-600`). | **No.** The file bytes are not persisted, so a retry means re-uploading. | Per-item **text** only, with no `reason_code`/`remediation`/`retryable`/`reference`. One oversized file gives a 413 for the whole batch (`:1247-1248`). |
| `POST assignments/{id}/grade-all` | `assignments/views.py:1617-1688` → `grade_engine_async` `assignments/tasks.py:463-664` | async | 202 `{session_id, message, tasks:[{file_name:"Submission for <name>", task_id}]}` → session-results, as above | **No endpoint.** The manual workaround is `POST submissions/{id}/grade-async` (`students/views.py:826-874`) or re-running grade-all. | No codes. Credit exhaustion yields N identical texts and no `stopped_at_item`. |
| `POST assignments/{id}/schedule_grade_all_submission` (no url_path, so the method name is used) and the Beat `auto_grade_due_assignment` | `assignments/views.py:1696-1745` → `grade_batch_async` `assignments/tasks.py:1085-1121`; `auto_grade_due_assignment` `:1124-1155` | async | **No tracked tasks** (`.delay` directly), so session-results takes the **legacy `session.results` path** (`users/views.py:2139-2190`): `task_id: null`, `context: null`, `pending_list: []`. GRADE entries **drop `submission_id`** (`students/models.py:300-312` adds it only for SUBMISSION/ASSIGNMENT). Session-creation errors are swallowed (`assignments/tasks.py:1110-1112`). | No | Items are identified only by the string "Submission for <name>". No codes and no retry handle. |
| `POST assignments/{id}/publish-all-grades` | `assignments/views.py:1754-1820` | sync | `{message, total_graded, ungraded_count}`: **counts only**. A per-student notify failure is only logged (`:1796-1802`). | n/a | No per-item list at all. The low-risk fix is a list of the ungraded/not-published items with a reason. |
| `POST assignments/upload` | `assignments/views.py:826-965` | sync | 207 `{successful:[{file_name, assignment, already_uploaded}], failed:[{file_name, error}], summary:{total, successful, failed}}`. When every file succeeds it returns a **bare list** `successful` (201); when every file fails, a **bare list** `failed` (400). | By re-uploading. The SHA-256 fingerprint makes that safe and uncharged (`assignments/file_uploads.py:1-18`). | Per-item text, no code. The response shape changes with the outcome. |
| `POST assignments/upload-async` | `assignments/views.py:1011-1105` → `upload_assignment_async` `assignments/tasks.py:986-1082` | async | session-results as above | No | No codes. The mid-loop 413 leaves a half-queued session (`:1066`). |
| `POST course/{id}/bulk-add-students` (roster import) | `classrooms/views.py:1383-1423` → `classrooms/services/roster_import.py:338-402` | sync | 200 `{total_processed, success_count, failure_count, results:[{name, status: enrolled\|invited\|skipped\|failed, type?, error?}]}`. **No row index.** | By resubmitting rows ("Already enrolled" skip makes that idempotent) | Per-row text, no code, no row number. These conditions (bad row, staff email, other school) are **not** among the 10 codes, so they need catalogue additions (F9). |
| `POST license-subscriptions/{id}/add_teachers` | `billing/license_views.py:296-344` → `license_service.add_teachers_batch` (`billing/license_service.py:1614-1635`) | sync | `{successful:int, failed:int, errors:[{teacher_email, error}]}`. **Successes are not listed.** The whole call runs inside `transaction.atomic()`, so any raise rolls back all rows. | No | Counts plus a failures-only list. Out of FR-A-06's catalogue. |
| `POST license-subscriptions/{id}/remove_teachers` | `billing/license_views.py:346-406` | sync | `{successful, failed, errors:[{teacher_id, error}]}` | No | Same. |
| `POST course/{id}/topics` | `classrooms/views.py:1749-1776` | sync | all-or-nothing `many=True` serializer, 400 with per-index errors | n/a | Not partial-success by design. Recommend leaving it out of scope. |
| (internal) license carry-forward | `billing/license_service.py:735-798` | in checkout/webhook | `[{email, successful, teacher_id, error, carried_forward}]` | No | Not a user endpoint. Out of scope. |
| (dead) `grade_all_submissions` | `assignments/tasks.py:77-188` | async | Stops the whole batch at the first failure; raw error plus traceback in Celery meta | — | Not dispatched. Delete it or leave it. |
| Bulk copy / bulk delete | — | — | **None exist** (`ASSIGNMENT_COPY` is only an audit verb, `audit/enums.py:58`) | — | Nothing to do until BE-E-01. |

**Routes with no per-item result at all:**

- publish-all-grades (counts only)
- add_teachers and remove_teachers (failures only, no successes)
- scheduled grade and auto-grade (legacy results with no task_id, no submission_id and no context)

**Routes with per-item text but no codes:** batch-upload, grade-all, upload-async, assignments/upload, bulk-add-students.

**Per-item retry endpoint:** none on any route.

---

## 4. Proposed design

### 4.1 Where ReasonCode lives

| Artifact | Location | Why |
|---|---|---|
| `class ReasonCode(models.TextChoices)`: the 10, plus `INSUFFICIENT_CREDITS`, `AI_FEATURE_NOT_AVAILABLE`, plus the 5 existing auth audit codes | **`audit/enums.py`**, next to `ErrorClass` | The plan's `audit/taxonomy.py` was consolidated into `audit/enums.py` on this branch, and that file already holds `ErrorClass`. It is the vocabulary the emitter validates, so `emit()` can switch from regex-only (`audit/emitter.py:251-254`) to enum membership. It is a code constant, not a table (`03_architecture.md:692`). |
| `REASON_CODES: dict[ReasonCode, ReasonSpec]`, where `ReasonSpec = (error_class, http_status, message_template, remediation, retryable, allowed_params)` | **new `AutoGrader/reason_codes.py`**, beside `AutoGrader/error_messages.py` | This is the user-facing message layer, and billing, students, assignments and users already import `error_messages`. It keeps DRF/HTTP out of the audit app. A completeness test ties the enum and the specs together. |
| `class CodedError(Exception)`: `reason_code`, `params`, and an optional `detail` (server-only) | `AutoGrader/reason_codes.py` | This is one base type. Existing exceptions **re-parent** onto it rather than being replaced, so `UPLOAD_REFUSALS`, `SUBMISSION_CLOSED_ERRORS` and `is_user_facing_error` keep working: `InvalidUploadFileError` → subclasses `FileUnreadableError`/`FileTypeUnsupportedError`/`FileTooLargeError`/`SubmissionEmptyError`; `CannotAssociateStudentError` → `StudentNameUnmatchedError`/`StudentNotOnRosterError`; new `RubricMissingError`, `DuplicateSubmissionError`, `ProviderFailureError` (retry loops raise it `from last_error`, which fixes `ai_processor/services.py:4098`). `PayloadTooLarge` gains a reason code. |
| Response builder | `coded_response(exc)` in `AutoGrader/reason_codes.py`. `billing/refusals.refusal_response` delegates to it for its two types and keeps its None-for-other contract. `users/exceptions.py` calls `coded_response` before the DRF handler. `students/views._failure_response` calls it first. | Extends the existing mechanism, with no parallel path. `PERMANENT_AI_REFUSALS` is unchanged, because it answers the retry question. |

### 4.2 Error envelope (the view payload; the renderer still wraps it)

```json
{"error": "<display message>",            // unchanged key; flatten_errors shows it as `message`
 "code": "insufficient_credits",          // LEGACY, present only for the 2 existing refusals (F8)
 "reason_code": "FILE_TOO_LARGE",         // the FR-A-06 contract, UPPER_SNAKE
 "error_class": "USER",
 "remediation": "Split the file ...",
 "retryable": false,
 "params": {"dimension": "pages", "actual": 312, "limit": 300},
 "reference": "<X-Request-ID / trace_id>"}   // QA-ERR-04, also still in the header
```

**SM ruling (2026-09-30, S6b N1):** `params` are machine-readable. For FILE_TOO_LARGE: `dimension` ∈ {`pages`, `bytes`, `pixels`}, `limit` is always an int, and `actual` is an int **when known**. It is omitted, never invented, when the true size is unknown (e.g. Pillow refusing a decompression bomb). Human formatting ("63.2 MB") lives only in the display message.

**SM ruling (2026-10-01, Epic A S7d): the sync-only email exception.**
- **The rule:** no coded `params` key may be an email or an id (`tests_codederror_serialization`), because a CodedError raised in a task reaches the Celery result backend.
- **The exception:** exactly three QA-approved codes whose approved text names the address may carry `email`: `ROW_EMAIL_INVALID`, `TEACHER_EMAIL_NOT_BUSINESS`, `TEACHER_ALREADY_ON_LICENCE`. (There were four until 2026-10-05, when `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` was retired for the neutral `TEACHER_CANNOT_JOIN_YET`, which takes no param: H-85.) They are item codes of SYNC routes (roster import rows, licence teachers), answered only to the requester in a result list.
- **Guarded** (`SYNC_ONLY_EMAIL_CODES`):
  - none of the four is ever raised;
  - no task module names them or their entry builders (`import_roster`, `add_teachers_batch`);
  - the set of codes with an `email` param is exactly these four.
- **Condition (b):** no log line or audit record carries these params. Tests assert that the address reaches no log record.

**SM ruling (2026-09-30, X-5):** `reference` is always the **server-owned** id (the response `X-Request-ID` / `trace_id`), never a client-supplied inbound `X-Request-ID`. An inbound id is kept only as `client_request_id`. Pinned by `test_qa_err_04_an_inbound_request_id_is_never_the_reference` (S5 R3).

On the wire this lands at `error.field_errors.*`, which is where clients already read `code`. `users/renderers.py:87-94` must treat `reason_code`, `error_class`, `remediation`, `retryable`, `params` and `reference` like `code`, so `message` stays the single display sentence. An alternative is to lift the envelope to `error.reason` (F7). `params` holds only whitelisted scalar keys per spec, and never an exception string (QA-ERR-03).

### 4.3 Per-item batch result shape (session-results entry, backward compatible)

```json
{"item_id": "<BackgroundProcessingTask.id>", "item_index": 7, "task_id": "<celery id>",
 "file_name": "p07.pdf", "submission_id": "…|null",
 "status": "FAILURE", "reason_code": "MISSING_STUDENT_NAME", "error_class": "USER",
 "message": "…", "error": "…(same as message; legacy)", "remediation": "…",
 "retryable": false, "resolutions": ["assign_student", "add_to_roster"],
 "retry_count": 0, "reference": "<trace_id>", "context": {…}}
```

- **New session-level fields:** `failure_codes: {code: count}`, `stopped_at_item` (null unless there is an INSUFFICIENT_CREDITS_MID_BATCH), `resumable: bool`.
- **Storage:** add `reason_code`, `retry_count`, `item_index` and `trace_id` to `BackgroundProcessingTask` (additive migration, `03a_data_model.md` §4.5 minus the `job` FK). `mark_processing_task_failure` (`students/task_tracking.py:225-243`) writes `reason_code` from the exception's code, and falls back to `SYSTEM`-class with no code for unclassified faults. That fallback is itself visible and countable.
- **Legacy paths:** `grade_batch_async` and `auto_grade_due_assignment` must create tracked tasks (as grade-all does), so the legacy `session.results` path stops being authoritative.

### 4.4 Per-item retry endpoints

| Endpoint | Body | Effect |
|---|---|---|
| `POST tasks/session/{session_id}/items/{item_id}/retry` | — | Allowed only when `retryable` (PROVIDER_FAILURE, INSUFFICIENT_CREDITS_MID_BATCH). The same row gets `retry_count += 1`, is reset to PENDING, and is relaunched with a new `celery_task_id`. Returns 202 with the item. Otherwise 409 with `reason_code` NOT_RETRYABLE. |
| `POST tasks/session/{session_id}/retry-failed` | `{"reason_codes": [...]}` (optional) | Bulk version of the above. Returns 202 `{retried:[item_id], skipped:[{item_id, reason_code}]}`. |
| `POST tasks/session/{session_id}/items/{item_id}/resolve` | `{"action": "assign_student", "student_id"}` \| `{"action": "keep_existing"\|"replace_existing"}` \| multipart `{"action":"replace_file", file}` | Covers #1, #2 (after enrolment), #8 and #3-6. `assign_student` writes the submission from the **already-extracted answers** kept in item `meta`, so the teacher is not billed again. This needs F2/F4. |

- **Retrying grade items:** these re-dispatch `grade_engine_async` for `submission_id`, which is always possible.
- **Retrying upload items:** these need the file bytes (F4). Without persistence, `retry` for an upload item answers "re-upload this file" (`resolve: replace_file`).

### 4.5 INSUFFICIENT_CREDITS_MID_BATCH and FR-B-06 `stopped_at_item`

- **Choosing the code:** `InsufficientCreditsError` inside an item task whose session already has at least one item STARTED or SUCCESS gets `INSUFFICIENT_CREDITS_MID_BATCH`. At request time the existing 402 `INSUFFICIENT_CREDITS` stays.
- **Short-circuit:** on the first mid-batch exhaustion, set `meta.credits_exhausted_at` on the session. Items that are still PENDING check it before any provider call and fail with the same code, without a call. This makes the stopping point meaningful despite the parallel fan-out.
- **Derived `stopped_at_item`:** `min(item_index)` over items with that code. It is computed in session-results, and no column is added now. Epic B moves it to `AIJob.stopped_at_item` (`03a_data_model.md:148`), fed by the same `item_index`.
- **Resume:** `retry-failed {"reason_codes": ["INSUFFICIENT_CREDITS_MID_BATCH"]}` after a top-up. Completed items are untouched (they already commit per item). No re-upload is needed for grade items; for upload items it needs F4.
- **Deferred to Epic B:** reservation, the `QUEUED_PENDING_CREDITS` auto-resume, and expiry (X-8).

### 4.6 Test plan (API level; each asserts its **own** code, never a shared one)

| Code | Reproduction | Assertions beyond the code |
|---|---|---|
| MISSING_STUDENT_NAME | batch-upload with mocked extraction returning `student_name: ""`; a second case with an unmatched name; a third with an ambiguous name | Other items succeed; the item names the file and (for unmatched/ambiguous) the extracted name; `resolutions` includes assign/add |
| STUDENT_NOT_ON_ROSTER | the name matches the teacher's student who is PENDING or enrolled in a different course | The code is ≠ MISSING_STUDENT_NAME; the remediation mentions the roster |
| FILE_UNREADABLE | a PNG header with a garbage body; a truncated PDF | ≠ FILE_TYPE_UNSUPPORTED; the file is named; no library text in the body |
| FILE_TYPE_UNSUPPORTED | a `text/plain` upload | 415; the message contains all accepted types |
| FILE_TOO_LARGE | (a) bytes over the limit (patch `MAX_UPLOAD_SIZE_BYTES`); (b) pages over the limit (patch `MAX_PAGE_COUNT`) | 413; `params.actual` and `params.limit` both present and both in the message |
| SUBMISSION_EMPTY | zero-page PDF; extraction mocked with every answer BLANK | error_class USER; no grading task queued |
| RUBRIC_MISSING | `assignment.questions = None`, via grade, grade-async and grade-all | 409; **AI mock not called; CreditLedger unchanged** |
| DUPLICATE_SUBMISSION | two files in one batch for one student; one file onto an existing ungraded submission | The conflicting submission is identified; the first submission's answers are unchanged |
| PROVIDER_FAILURE | mock `APITimeoutError` / `InternalServerError` on grading and extraction | 503 sync; error_class PROVIDER; ledger net zero per F1; no "Error during AI model" or provider text in the body |
| INSUFFICIENT_CREDITS_MID_BATCH | wallet funded for k items; grade-all over n > k | Completed items untouched; `stopped_at_item` reported; `retry-failed` after top-up completes the rest |
| **30/12** | batch-upload of 30 files with 12 engineered failures spread over ≥5 codes | `failure_count == 12` **and** `len(failure_list) == 12` **and** every entry has a non-null `reason_code` equal to that file's expected code; 18 successes |
| Catalogue completeness | reflection | Every `ReasonCode` has a spec; the values match `audit/emitter.py:73`; the 10 FR-A-06 ids are present |
| QA-ERR-03 | parametrised over codes, with a raising mock whose text contains a sentinel | The sentinel, "Traceback", and the exception class names are absent from every body and item |
| QA-ERR-04 | every coded body | `reference == response["X-Request-ID"]`; an item's `reference` resolves to `AuditEvent.trace_id`; with an inbound `X-Request-ID`, `reference` != the inbound value (X-5) |
| Per-item retry | a PROVIDER_FAILURE item, then retry | `retry_count == 1`; the same `item_id`; a non-retryable code gives 409 |

---

## 5. Suggested split

| Slice | Content | Size |
|---|---|---|
| **S6a: catalogue and plumbing** | `ReasonCode` enum; `AutoGrader/reason_codes.py` (specs, `CodedError`, `coded_response`); `refusal_response` delegation; handler hook; renderer `flatten_errors` fix; `reference` in the body; completeness, QA-ERR-03 and QA-ERR-04 tests | M (1-1.5 d) |
| **S6b: file codes #3/#4/#5/#6-file** | Split the ParseError sites into coded subclasses (`assignments/services.py:377-461`, `ai_processor/services.py:5033-5065`); remove the raw `{e}` from PDF errors; split `error_messages.py:148-156`; 415/413/422 | M (1 d) |
| **S6c: identity codes #1/#2 (+ #8 deferred per F3)** | Split `CannotAssociateStudentError`; a roster-vs-unmatched lookup (the teacher's students, not only ENROLLED); `DUPLICATE_SUBMISSION` defined in the enum but not raised; `replaced_existing: true` on an overwrite | S-M (1 d) |
| **S6d: grading gates #7/#9 (+ #6 for empty files only, per §6.1)** | Rubric gate (F5) before the claim and before any dispatch (grade, grade-async, grade-all, schedule, auto-grade); `SUBMISSION_EMPTY` for zero-page and empty-text inputs, **no blank-answer gate**; `ProviderFailureError` `from last_error`; fix `_grading_failure_error_class`; sync 503; F1 refund scope around answer extraction | M (1-1.5 d; the blank-answer gate is removed and the extraction refund scope is added) |
| **S7a: per-item model and results contract** | Migration (`reason_code`, `retry_count`, `item_index`, `trace_id`); write the code in `mark_processing_task_failure`; the new session-results shape; tracked tasks for `grade_batch_async`/`auto_grade`; per-item 413 in batch-upload and upload-async; 30/12 test | M-L (2 d) |
| **S7b: per-item retry** | `retry` and `retry-failed` for grade items; upload items answer "re-upload this file" (F4). `resolve` is deferred (F2/F3) | M (1.5-2 d) |
| **S7c: credits mid-batch** | Session short-circuit, derived `stopped_at_item`, resume via retry-failed | S-M (1 d) |
| **S7d (in scope per F9; needs QA-approved extra codes)** | Codes and per-item lists for the sync batch routes: assignments/upload shape, bulk-add-students row index and codes, add/remove_teachers success lists, publish-all-grades | M (1-1.5 d) |

**Order:** S6a → (S6b ∥ S6c ∥ S6d) → S7a → S7c → S7b → S7d. S7a can start after S6a, since it only needs the enum and `CodedError`.

---

## 6. Open questions for the founder

### 6.0 Founder answers (2026-09-29, all nine answered; relayed by the Security Engineer from the SM) and what they change

| # | Answer | Design impact |
|---|---|---|
| F1 | **Always refund a failed item**, including answer extraction's partial chunk charges | S6d gains a `billing_refund_scope` around `upload_answers_engine` (`students/services.py:824-976`), like grading and assignment upload. `PROVIDER_FAILURE`'s `credit_clause` becomes "The credits were refunded." whenever anything was charged. The test asserts that the ledger nets to zero **for an extraction that fails mid-chunk**. |
| F4 | **No file persistence now**; decide in Epic B | S7b: `retry` on an upload item answers "re-upload this file" (`resolve: replace_file`). Only grade items are retried in place. S7b shrinks by the +1 d. Resuming a mid-batch upload after a top-up means re-uploading the unfinished files, and the message must say so. |
| F5 | RUBRIC_MISSING = **no questions, or any question with no marking guide at all**. NOT the "fewer than 2 levels" rule | The S6d gate checks `questions` empty/null, or any question whose rubric or marking guide is absent or empty. A 1-level rubric is **not** RUBRIC_MISSING and keeps today's handling. The test covers all three cases. |
| F6 | SUBMISSION_EMPTY = **zero pages, or every answer blank**. The teacher may still choose to grade it as 0 | **Superseded by the final ruling in §6.1 (founder-approved 2026-09-29):** in Epic A, `SUBMISSION_EMPTY` covers empty files only. All-blank papers keep today's AI-graded, editable-0 behaviour, and blank-answer detection is deferred (a stated gap). |
| F8 | (SM) **Keep the lowercase legacy `code` for one release** beside `reason_code` | As in §4.2. Add a removal ticket for the release after. |
| F9 | **Yes**: roster import and licence add/remove teachers get the full per-item treatment (row numbers, codes, success lists). **S7d is in scope** | S7d stops being optional. Those routes need reason codes **beyond QA-ERR-02's 10** (for example a bad row, a staff email, another school, already enrolled, a teacher not in the school, a licence at capacity). QA owns catalogue additions, so the codes must be proposed to QA before S7d is built. `add_teachers_batch`'s all-or-nothing `transaction.atomic()` must become per-row (a savepoint per row) to report per-item results honestly. |
| F2 | **Keep current behaviour** (it needs the frontend). There is no assign-student resolve and no kept extracted answers | Items fail with `MISSING_STUDENT_NAME` / `STUDENT_NOT_ON_ROSTER`, and the teacher re-uploads. `resolutions` is not emitted. The #1/#2 split in S6c is unaffected. Under F1 the failed item's extraction charge is **refunded**, so a re-upload does not pay twice. |
| F3 | **Keep current behaviour**: the silent overwrite of an ungraded submission stays, and duplicates are not refused. SM addition: an item that overwrote an existing ungraded submission carries an informational, additive `replaced_existing: true` (**not** a failure) | `DUPLICATE_SUBMISSION` (#8) is **defined in the enum but deferred** until the frontend has keep/replace buttons. **This is a known, stated partial gap against FR-A-06 (9 of 10 conditions coded) and QA-ERR-02 #8.** S6c drops duplicate detection and instead sets `replaced_existing` on the item result (S7a shape), with a test that two files for one student in one batch mark the second item `replaced_existing: true`. The existing 409 for an already-graded or in-grading submission is unchanged. |
| F7 | **Approved**: the envelope stays in `error.field_errors`, with precise statuses (415 / 422 / 409 / 503 + `Retry-After`; 413 unchanged). The frontend tests against **staging** before anything reaches beta (staging and main are both deployed) | S6a is unblocked. The renderer's `flatten_errors` fix is as in §4.2, and the §1 status column stands. **Release gating:** S6b/S6d status changes ship to staging first, and the frontend confirms them there before the beta merge. Record that sign-off in the S6 evidence. |
| — | **`resolve` endpoint deferred** with F2/F3. `retry` and `retry-failed` stay in scope (PROVIDER_FAILURE, INSUFFICIENT_CREDITS_MID_BATCH) | S7b shrinks to retry and retry-failed for grade items. Upload items answer "re-upload this file" (F4). F6's "grade it as 0" is decided below (§6.1). |


### 6.1 F6 "the teacher may grade it as 0": checked on the code (phase2/epic-a @ `cc34081`)

**SM ruling (2026-09-29):** SUBMISSION_EMPTY stays a failure, with no AI call and no charge. The teacher records 0 through the existing manual grading path, and **no new endpoint** is built. The condition attached was to verify that the path works in both cases. **It does not, in either case, as the code stands:**

| Case | What exists after the S6d gate | Existing route | Works? |
|---|---|---|---|
| **(a) Grade item; the row exists, every answer blank** | A `StudentSubmission` row whose `feedback` is empty and `graded_at` is null, because the gate refuses before any grading | `PATCH submissions/{id}/update-grade` (`students/views.py:1025-1110`) | **No.** It returns 400 "Submission has not be graded yet" when `submission.feedback` is empty (`:1040-1044`), and it takes max points from `feedback.grading_summary` or `submission.max_points`, both unset here (`:1063-1078`). It is also gated by `HasCreditBalance`, so a teacher with an empty wallet cannot even enter a 0. `publish-all-grades` only publishes rows with `graded_at` and `score` set (`assignments/views.py:~1791`), so the 0 could not be published either. The plain `PATCH submissions/{id}` is a billed raw-text re-extraction (`:679-700`), not a score edit. |
| **(b) Upload item; the file had no pages** | **No row.** `prepare_ai_content` raises "This PDF has no pages" (`ai_processor/services.py:~5058`) inside `upload_answers_engine_async` (`assignments/tasks.py:~825`) **before** `upload_answers_engine` creates the submission (`:~836`) | None. `StudentSubmissionViewSet.create` raises for everyone (`students/views.py:348`), the student upload routes are `IsStudent`, and teacher batch-upload needs a readable file. The models have **no "missing" or "excused" state** (no such field or choice in `students/models.py` or `assignments/models.py`) | **No.** The teacher has nothing to grade, and no route records a 0 or "missing" for that student. |

**Consequence:** today an all-blank paper is AI-graded and charged, so it gets feedback, and update-grade then works. The S6d gate removes that path. Without a change, the founder's "may still grade it as 0" becomes impossible in (a) and was already impossible in (b).

**Options for the SM** (none built; ordered smallest first):
1. **(a) only:** a small change to the **existing** `update-grade` route. If the submission is ungraded **and** its last processing item failed with `SUBMISSION_EMPTY`, accept the score, taking max points from the assignment's question points, write a minimal `grading_summary`, and stamp `graded_at`. Waive `HasCreditBalance` for that route, since it makes no AI call. This is a change to an existing route, not a new endpoint, and it adds about 0.5 d to S6d.
2. **(b):** there is no fix without new behaviour. Either (i) accept the gap and state it: the teacher asks the student to re-submit, or grades outside the app; or (ii) log a product item for a "missing / excused / zero" submission status (it overlaps H-38-F1's orphaned-course work, and neither exists today).
3. **Alternatively**, keep today's behaviour for (a): don't gate all-blank papers, and AI-grade them as today. That is charged, which contradicts F6's "no AI call, no charge" reading.

Recommendation: option 1 for (a), and 2(i) for (b), stated as a known gap in 08.

**Ruling (SM recommendation, APPROVED by the founder 2026-09-29; final):**
- **(b) Zero-page / empty file:** the item fails with `SUBMISSION_EMPTY`. The behaviour is otherwise unchanged: it already fails with no row and no charge. That the teacher cannot record 0 or "missing" is a **pre-existing gap**: there is no missing/excused/zero status and no teacher route that creates a row. It is logged as a product item for later, and nothing is built in Epic A.
- **(a) All-blank paper (the row exists):** **no gate for now.** Today's behaviour stays: AI-graded, charged, and a normal editable 0 through update-grade. The free, no-AI, manual-0 route needs option 1 (extend update-grade, waive `HasCreditBalance`, about 0.5 d) **and** frontend confirmation that the grade screen can edit an ungraded submission. It is recorded as the follow-up for when the frontend is ready, like F2/F3.
- **So in Epic A, FR-A-06's `SUBMISSION_EMPTY` fires for empty files only** (zero pages, empty text input). **Blank-answer detection is deferred, and this is a stated partial gap**, alongside `DUPLICATE_SUBMISSION`.

| # | Question | Today |
|---|---|---|
| **F1** | #9: for a failed model call, is the policy "not consumed" or "refunded"? | Both, partly. The failing call is never charged; earlier charges in the run are refunded for grading and assignment upload. **Answer extraction keeps partial chunk charges** (no refund scope). |
| **F2** | Should a user-error failure that happens **after** a billed extraction (#1/#2/#8 are detected post-extraction) be refunded? Or should the extracted answers be kept so that "assign student" completes it without a second charge? | Charged and discarded |
| **F3** | Duplicate policy: refuse the second submission and let the teacher choose (QA wording), instead of today's silent overwrite? | Silent overwrite of an ungraded row |
| **F4** | May the submission source files be persisted, so a failed or unpaid upload item can be retried without re-upload (FR-B-06)? This is student work, so it needs a retention class under A6. | Bytes live only in the Celery message |
| **F5** | Definition of RUBRIC_MISSING: `questions` empty/null only, or also any open-ended question whose rubric has fewer than 2 levels? | Undetected |
| **F6** | Definition of SUBMISSION_EMPTY: zero pages, zero answers, or every answer BLANK? And may an all-blank paper still be graded 0 if the teacher chooses? | Undetected |
| **F7** | FE contract (FE-GL-08): the status changes (400→415/422, 500→503) and where the envelope sits (`error.field_errors` vs `error.reason`) | — |
| **F8** | Should the lowercase `code` values (`insufficient_credits`, `ai_feature_not_available`) be kept for one release beside `reason_code`? | Frontend and tests read them |
| **F9** | Does FR-A-07 cover the sync non-grading batches (roster import, license add/remove teachers)? Those need codes beyond the QA catalogue's 10, and QA owns any additions. | Per-item text, or counts only |
