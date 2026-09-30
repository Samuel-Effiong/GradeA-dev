# Verification: Epic A S6b @ 46c8c15

**Verifier:** Verification Engineer 2 (v2). **Author:** 1a (Verification Engineer 1, author for this slice). **Date:** 2026-09-30.
**Branch:** task/epic-a-s6b @ **46c8c15** (docs-only over cbd70de; code change 11ae0ef; phase2/epic-a 9a91431 merged in as 2e0c5f9). Design: 08a §1 codes 3–6, §4.2, §4.6, §5 S6b. SM scope ruling: single-file refusals only; the two batch 413 problems are S7a.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at 46c8c15. Rule 15: v2's probes, mutants and the changed modules. 1a's `assignments` regression (661 OK) and the changed modules outside assignments (293 OK) are cited.

**Verdict: VERIFIED-WITH-NOTES.** N1 is for QA/the SM (catalogue shape).

## Static
- **Coverage map:** every touched production file (8: `AutoGrader/error_messages.py`, `AutoGrader/uploads.py`, `ai_processor/services.py`, `ai_processor/tools.py`, `assignments/exceptions.py`, `file_uploads.py`, `services.py`, `tasks.py`) has covering modules in EVIDENCE.
- **No production code matches on the old error texts** ("Could not read this PDF", "not a PDF", "could not be read as an image", "Unsupported format", "…in an unsupported format", "megapixel"). A grep of non-test code finds only the new raise sites, so the rewording breaks no caller.
- **The design decisions hold:**
  - a photo labelled PDF → FILE_UNREADABLE, because the #4 template would contradict itself; the SM accepted this, and the lost hint is a QA catalogue item;
  - checks run declared type → zero bytes → content;
  - an empty assignment file → FILE_UNREADABLE;
  - coded refusals pass through the background task unwrapped (so S7a can store the code).
- 1a's fixture notes on v2's draft were checked independently and applied:
  - the page limit is `PDFService.MAX_PAGE_COUNT`;
  - `b"%PDF-1.4\n%%EOF\n"` is a broken document, correctly FILE_UNREADABLE; a well-formed zero-page PDF is 1a's `ZERO_PAGE_PDF`;
  - the bytes are built before `fitz.open` is patched.

## Evidence
| Check | Result |
|---|---|
| v2 probes (`tests_vf2_s6b_probe.py`, 11, through the student sync upload route, funded wallet) + `assignments.tests_file_reason_codes`, `ai_processor.tests_pdf_type_validation`, `assignments.tests_security`, `AutoGrader.tests_uploads`, `tests_error_messages`, `tests_reason_codes`, the S6a probe | **153 OK** |
| PNG header + garbage / truncated PDF | 422 `FILE_UNREADABLE` naming the file (≠ FILE_TYPE_UNSUPPORTED) |
| `text/plain` | 415 `FILE_TYPE_UNSUPPORTED`, `detected_type` TXT, all five accepted types in the message |
| text declared `image/png` | 422 `FILE_UNREADABLE` (the declared type is supported; the content isn't readable) |
| bytes over the cap / pages over the limit | 413 `FILE_TOO_LARGE`, `params` {actual, limit, dimension: bytes / pages}, both numbers in the message |
| a well-formed zero-page PDF / a zero-byte PDF | 422 `SUBMISSION_EMPTY` (≠ FILE_UNREADABLE) |
| an empty `.txt` (order) | 415 `FILE_TYPE_UNSUPPORTED` (≠ SUBMISSION_EMPTY): the type is checked first |
| QA-ERR-03: `fitz.open` / `PIL.Image.open` raising a SENTINEL + "Traceback" | 422 `FILE_UNREADABLE`; the sentinel, "Traceback" and the exception class names are absent from the body |
| every probe | `reference == X-Request-ID`; **AI provider never called; CreditLedger unchanged** |
| v2 mutants (`vf_s6b_mutants.py`, 3) | **3/3 KILLED** by 1a's own tests (and v2's probes): the empty check before the type check; `dimension` dropped from the bytes params; `dimension` dropped from the pages params |
| 1a's gates | reproduce-first 20/20 fail on 064a278; 12/12 mutants; assignments 661 OK; outside-assignments 293 OK (committed) |

## Notes
- **N1 (catalogue shape, QA/SM).** `FILE_TOO_LARGE`'s `params.actual` / `params.limit` are **display strings** ("3 pages", "460 bytes", "63.2 MB"). 08a §4.2's envelope example shows them as **numbers** (`"actual": 312, "limit": 300`) beside `dimension`. The strings are right for the message, but a client that branches or compares on `params` would have to parse text. The options: numeric `actual`/`limit` (with `dimension` as the unit) plus the formatted strings only in the message, or amend 08a's example. This is the frontend's (F7) call on staging.
- **N2 (already stated by the author).** The 400 → 415/422/413 status changes ship to staging first for the F7 frontend check before any beta merge.

Logs: `runs/s6b_run1.log`, `runs/s6b_run2_mutants.log`.
