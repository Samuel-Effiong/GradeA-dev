# Section 9 (`ocr_processor`) — verification evidence

Preserved record of the §9 pass of `docs/CODEBASE_AUDIT_SECTIONS.md`.

**This is NOT the definitive release gate.** It was run in an isolated
worktree on an uncommitted change. The definitive gate is a fresh run on
the final merged, committed tree.

---

## 1. Tree under test

| | |
|---|---|
| Worktree | `Grade-Automator-Plus-section-9-ocr-processor-review` |
| Branch | `task/section-9-ocr-processor-review` |
| HEAD commit | `1373eaeac02d14d6ab79d532be56db1eb7fc4585` |
| HEAD subject | *Version the cache instead of wildcard-flushing it, and fix the test gate* |
| Working tree | **DIRTY** — `ai_processor/services.py` modified, `docs/CODEBASE_AUDIT_SECTIONS.md` modified, `ai_processor/tests_pdf_type_validation.py` new |
| **Gate fingerprint** | **`ce28db19f9055e534d9f5aa7e04748056afc40fe9446a8f1040e8891907e239d`** |

Computed immediately before the full regression run started. It covers the
code fix, the new tests and the §9 text correction. This evidence file and
the task-table row were written afterwards; neither is code.

> Reproduce, and never inherit:
> ```sh
> { git diff HEAD; git ls-files --others --exclude-standard -z \
>     | sort -z | xargs -0 -r sha256sum; } | sha256sum
> ```

### 1a. What the worktree does NOT contain

The worktree starts from HEAD, so it excludes the main tree's uncommitted
work from other sessions. Two things matter here:

- **`assignments/services.py`, main tree:** swaps the shared `pdf_service`
  object for a fresh `PDFService(uploaded_file)` per upload. This fix only
  touches `PDFService.extract`, which that change calls unchanged, so the
  two combine without overlap. The same forged-file defect was reproduced on
  the main tree's code too (see §3).
- **`ai_processor/tests_pdf_service_concurrency.py` and
  `tests_answer_benchmark_inputs.py`** (untracked, main tree) were **not
  run**. The first tests the per-upload change above, which HEAD does not
  have. The second imports the untracked `ai_processor/benchmark/answers/`.
  Its `UnreadableUploadTest` accepts `ParseError` or `PDFPageCountError`.
  After this fix, the "image bytes named .pdf" case raises `ParseError`, so
  it should still pass. That is inferred, not run.

## 2. Infrastructure — probed

| | |
|---|---|
| Python | 3.12.10 |
| PostgreSQL | 18.6 |
| Redis | 8.0.5 |
| Django / DRF | 5.2.6 / 3.16.1 |
| PyMuPDF | 1.23.8 |
| pdf2image | 1.17.0 |
| poppler (`pdftoppm`) | 26.01.0 |
| Pillow | 12.0.0 |
| Test DB | `test_section_9_ocr_processor_review` (worktree-unique) |

## 3. The defect, reproduced before fixing

Forged files were sent through the real
`AssignmentProcessingService.prepare_ai_content`, with no mocks.

| Upload | HEAD | Main tree (uncommitted) |
|---|---|---|
| Shell script / zip / `%PDF` garbage / zero bytes, labelled `application/pdf` | 400 | 400 |
| Text labelled `image/png` | 400 | 400 |
| Real PDF labelled `text/html` or `application/octet-stream` | 400 | 400 |
| **PNG or JPEG bytes labelled `application/pdf`** | **unhandled `PDFPageCountError` → 500** | **same** |
| Real PDF, honest label | accepted | accepted |
| 50 MB + 1 byte | 413 | — |

**Root cause (probed):** `fitz.open(stream=..., filetype="pdf")` opens PNG
and JPEG bytes as a one-page document with `is_pdf=False`. That passes the
page-count checks, and `pdftoppm` then fails outside any handler.

## 4. Mutation results

Each mutant was applied to `ai_processor/services.py` and run against
`ai_processor.tests_pdf_type_validation`. The file was then restored from a
uniquely named copy, and its md5 was checked against the fixed version
(`975f98a2480f2dedb73422431247bd87`) after every restore.

| Mutant | Result |
|---|---|
| M1 — remove the `is_pdf` refusal | **killed** — 6 failures |
| M2 — stop catching `PDFPageCountError` / `PDFSyntaxError` | **killed** — 2 errors |
| M3 — catch `Exception` (blames a missing/timed-out poppler on the file) | **killed** — 2 errors |

All 3 restores were md5-verified. New test file unmutated: **7 tests, OK**.

## 5. Pre-commit

`pre-commit run --files ai_processor/services.py
ai_processor/tests_pdf_type_validation.py` returned exit 0 with no files
rewritten. The seven `ocr_processor/` files also returned exit 0 (unchanged).

## 6. Full repository regression

`python manage.py test --settings=settings_worktree --keepdb --noinput`
(no labels, so every app):

| | |
|---|---|
| Started / finished | 2026-09-14 13:56:14 / 14:17:27 (+01:00) |
| Result | **3866 tests, OK (skipped=12), exit 0** — 0 FAIL, 0 ERROR |
| `services.py` after run | md5-identical to the fixed version (no mutant leaked into the run) |

The run ran after the mutation step, never alongside it, because mutants
edit `services.py` on disk.

## 7. Open — not fixed here (needs sign-off)

**Multi-file sync assignment upload hides already-charged work.** A probe
(scratchpad, not committed) posted `good.png` then `forged.png` to
`assignment-upload`. It measured **1 AI extraction run (credits charged), 1
assignment created, response 400** naming only `forged.png`, with no mention
of `good.png`. Cause: `prepare_ai_content` is called outside the per-file
`try` at `assignments/views.py:858`. The fix changes that endpoint's
response for this case (400 → 207 with `successful`/`failed`), which is a
public API contract change, so it was not applied.
