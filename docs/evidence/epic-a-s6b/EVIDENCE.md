# Epic A S6b: the file codes #3–#6 (FR-A-06, 08a §5). Author's evidence

**Author:** 1a (grade-automator-plus-c2), assigned by the SM on 2026-09-30. **Verifier:** v2. The author does not verify this.
**Branch:** `task/epic-a-s6b`, off phase2/epic-a `5811e15`. The change is `11ae0ef`; phase2/epic-a `9a91431` was merged in as `2e0c5f9` (no conflicts) so the gates run on the current tree. One compression-path test was added after the gates, and it lands in the same commit as this evidence (see Runs).

## What changed
Every refused upload answers with its own reason code and the status F7 fixed for it, instead of the shared `ParseError` 400.

| # | Code | Status | Raised for |
|---|---|---|---|
| 3 | `FILE_UNREADABLE` | 422 | An image or PDF the parser can't read (damaged, truncated, encrypted); a file whose bytes aren't its declared type (a photo labelled `application/pdf`) |
| 4 | `FILE_TYPE_UNSUPPORTED` | 415 | A declared type that is neither an accepted image type nor PDF. `detected_type` is the extension in upper case (`TXT`), else the declared type |
| 5 | `FILE_TOO_LARGE` | 413 | Bytes (`validate_upload_size`: `PayloadTooLarge`, now coded); pages (`PDFService.MAX_PAGE_COUNT`); pixels (`MAX_IMAGE_PIXELS`, or Pillow's own bomb limit); an image no compression brings under the cap. `params`: `actual`, `limit`, `dimension` (`bytes`/`pages`/`pixels`) |
| 6 | `SUBMISSION_EMPTY` | 422 | An empty **file** only: zero bytes, or a PDF with zero pages (08a §6.1's final ruling; blank answers are not refused) |

**Order of checks** in `prepare_ai_content`: the declared type first (415), then zero bytes (422), then what reading the bytes finds. So an empty `.txt` file is an unsupported type, not an empty submission.

**Types** (`assignments/exceptions.py`): `FileUnreadableError`, `FileTypeUnsupportedError` and `SubmissionEmptyError` are `CodedError` + `InvalidUploadFileError`. `FileTooLargeError` is `PayloadTooLarge` + `InvalidUploadFileError`. Being `InvalidUploadFileError`s, they stay in `UPLOAD_REFUSALS` (never retried) and are user-facing. Each class's `status_code` matches its spec.

**Keeping the code through the background path:** `assignments/tasks.py` and `assignments/file_uploads.py` used to re-wrap every `ParseError` in a fresh, uncoded `InvalidUploadFileError`. A coded refusal now passes through as itself, so S7a can store the item's `reason_code`.

**QA-ERR-03:** `PDFService` raised `ValueError(f"Could not read this PDF: {e}")`, embedding PyMuPDF's or poppler's text, and the base answered a truncated PDF with poppler's own lines ("Syntax Error: Couldn't find trailer dictionary …"). It now raises `PDFUnreadableError`, `PDFEmptyError` or `PDFTooManyPagesError` (still `ValueError`s) with no library text. The text stays on `__cause__`, and the user sees only the spec's template.

**`error_messages.py` (the §2.2 #3 split):** the parser-failure message said "…or in an unsupported format", conflating #3 with #4. It now says only unreadable. An unsupported type is decided before any parser runs and has its own code.

## Design decisions for the verifier and the SM
1. **A photo labelled PDF is `FILE_UNREADABLE`, not `FILE_TYPE_UNSUPPORTED`.** Its real type (an image) is supported, so the #4 template ("photo.pdf is a PNG file, which isn't supported. Accepted types: … PNG …") would contradict itself. The precise reason ("This file is not a PDF…") stays server-side on `__cause__`. What's lost is the old hint "upload it as an image instead": the #3 remediation is "Re-export or re-scan the file". A catalogue text change would restore it, and QA owns the catalogue.
2. **An empty ASSIGNMENT file is `FILE_UNREADABLE`.** `SUBMISSION_EMPTY`'s text is about student answers, so `upload_assignment_file` maps it.
3. **A zero-byte file is `SUBMISSION_EMPTY`** (it is empty). Before, it was "could not be read as an image" or a PyMuPDF error.
4. **SM scope ruling (2026-09-30): batch behaviour is unchanged.** S6b gives `FILE_TOO_LARGE` its code, its 413 and its params (actual, limit, dimension) wherever a **single-file** size or page check refuses today. 08a's two batch 413 problems stay in **S7a** (per-item 413):
   - **upload-async** validates size inside its dispatch loop, after the earlier files are queued (a half-queued session);
   - **batch-upload** fails the whole batch when one file is too large.

   Both now answer the coded 413 body, but they refuse exactly what and when they did before.
5. **Also not in S6b (by the 08a split):** the per-item `reason_code` column and the session-results shape (S7a); the sync `assignments/upload` per-file codes (S7d; its messages are the coded ones already); `SUBMISSION_EMPTY` for empty **text** input (S6d, which I've told d5).
6. **F7 release gating:** these status changes (400 → 415/422/413) ship to staging first, and the frontend confirms them there before any beta merge (08a §6.0 F7).

## Existing tests changed (asserting the coded answer, never weaker)
| Test | Was | Now |
|---|---|---|
| `tests_upload_task_retry_policy.NOT_A_PDF` (5 uses) | "not a PDF" in the item's message | "couldn't read photo.pdf": still the file's own message, never the fallback |
| `tests_upload_batch_billing.UNREADABLE_PDF_ERROR` | "Could not read this PDF" | "couldn't read b.pdf" |
| `tests_pdf_type_validation`: the ParseError test and two HTTP tests | 400 and "not a PDF" | the student route: 422 and `FILE_UNREADABLE` naming the file. The teacher route: its per-file 400 (S7d) carrying the coded message. The service test: `FILE_UNREADABLE`, and the "not a PDF" reason still on `__cause__` |
| `tests_security` pixel-cap tests (3) | "megapixel" in the message | `reason_code == FILE_TOO_LARGE`, `dimension == "pixels"` and the exact `actual`. The under-the-cap test asserts it was **not** a pixel refusal (the same strength; the old word check would now be vacuous) |

## Coverage: each touched production file, and the modules that test it
| Production file | Covering modules |
|---|---|
| `assignments/services.py` (`prepare_ai_content`, `_compress_uploaded_image`, helpers) | `assignments.tests_file_reason_codes`, `assignments.tests_security`, `ai_processor.tests_pdf_type_validation`, `ai_processor.tests_answer_benchmark_inputs`, `ai_processor.tests_pdf_service_concurrency`; the `assignments` regression |
| `assignments/exceptions.py` | `assignments.tests_file_reason_codes`; the `assignments` regression |
| `assignments/tasks.py` (re-raise) | `assignments.tests_file_reason_codes.TheBackgroundUploadKeepsTheCode`, `assignments.tests_upload_task_retry_policy` |
| `assignments/file_uploads.py` (re-raise, empty → unreadable) | `assignments.tests_file_reason_codes.AnAssignmentFileWithNoPagesIsUnreadable…`, `assignments.tests_upload_batch_billing` |
| `ai_processor/services.py` (`PDFService`) | `ai_processor.tests_pdf_type_validation`, `ai_processor.tests_pdf_service_concurrency`, `assignments.tests_file_reason_codes` |
| `ai_processor/tools.py` (`ImageCompressionError`) | `AutoGrader.tests_error_messages`, `assignments.tests_file_reason_codes` (`test_an_image_too_large_even_compressed_is_too_large`, image and PDF paths) |
| `AutoGrader/uploads.py` | `AutoGrader.tests_uploads`, `assignments.tests_file_reason_codes`; callers in `students.tests*` (upload views) |
| `AutoGrader/error_messages.py` | `AutoGrader.tests_error_messages`, `assignments.tests_file_reason_codes` |
| Routes whose answers changed: `students/views.py` upload, upload-async and batch-upload (unchanged code) | `students.tests`, `students.tests_upload_pipeline`, `tests_epic_a_submission_upload_audit`, `tests_proxy_upload_attribution`, `tests_submission_tenancy`, `tests_submission_concurrency`, `tests_post_grading_submission_lock`, `tests_async_edit_path`, `tests_grading_hardening`; `billing.tests.test_refusal_handling`, `test_h38_part2_removed_teacher_routes`; `dashboard.tests_cache_matrix_status_summary` |

## Runs (rule 15; each under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, its own test DB, `EXEMPT_EMAIL_DOMAINS` empty)
| Run | Tree | Result | Log |
|---|---|---|---|
| Reproduce first: the new module on the **unchanged** epic tip | `064a278` | **20 of 20 fail** (16 failures, 4 errors on the not-yet-existing types). Every condition answered the shared 400, and a truncated PDF's body carried poppler's raw text | `repro_064a278.txt` |
| Changed modules outside `assignments` (the list in the coverage table: `ai_processor`, `AutoGrader`, `students` upload routes, `billing` refusal/H-38, `dashboard`) | `2e0c5f9` | **Ran 293, OK** (skipped 1), 424 MB peak, 0:57 | `changed_modules.txt` |
| ONE owning-app regression: `assignments` (includes every `assignments` changed module) | `2e0c5f9` | **Ran 661, OK** (skipped 13), 331 MB peak, 2:07 | `regression_assignments.txt` |
| The new module with the compression test added (the file committed with this evidence; sha256 `f1598ae9da2ab4ff…`) | `2e0c5f9` + that test | **Ran 21, OK** | `new_module_with_compression.txt` |

`SUMMARY.txt` is the run sequence. The logs are trimmed to the test ids that ran plus the summaries, with emails redacted.

## Mutants (author's)
`mutate.py`; results in `mutants.txt`. Each is restored with a sha256 check against the commit's blob. **12 of 12 killed.**

| Mutant | Killed by |
|---|---|
| M1 the type check removed (an undeclared type silently accepted) | 4, including the 415 tests |
| M2 the zero-byte check removed | `test_a_zero_byte_file_is_empty` |
| M3 zero pages → unreadable | `test_a_pdf_with_no_pages_is_empty` and the type test |
| M4 pixel cap → unreadable | the pixels test and 2 `tests_security` bomb tests |
| M5 too many pages → unreadable | the pages test and the 413 type test |
| M6 the task re-wraps the coded refusal (code lost) | `test_the_item_fails_with_the_coded_refusal_itself` |
| M7 the empty assignment file not mapped | `test_the_teacher_sees_the_unreadable_message` |
| M8 the byte cap bound at definition time | `test_the_cap_is_read_at_call_time` and the bytes test |
| M9 the infra text conflates #3/#4 again | `test_a_parser_failure_reads_as_unreadable_only` |
| M10 the unsupported type named by MIME only | the `TXT` test |
| M11 / M12 compression failure → unreadable (image / PDF path) | `test_an_image_too_large_even_compressed_is_too_large` |

**Not mutated, because nothing can observe them (stated, not hidden):**
- Restoring the library text inside `PDFService`'s own message: that text no longer reaches any message, since the user sees only the spec's template (pinned by the QA-ERR-03 sentinel tests).
- A coded type's `status_code` attribute: the handler answers with the spec's status.
