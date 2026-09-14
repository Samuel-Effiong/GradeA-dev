# Section 9 (`ocr_processor`) — verification evidence

Preserved record of the §9 review and remediation of
`docs/CODEBASE_AUDIT_SECTIONS.md`.

**Status: GAPS FOUND — remediation committed, strict final gate PENDING.**
Nothing here is the definitive gate. §8 is that gate, and it has not run yet.

---

## 1. What this section covers, and what changed

| Part | Finding | Severity | State |
|---|---|---|---|
| A | A PNG/JPEG labelled `application/pdf` crashed `pdftoppm` → 500 | should-fix (§3) | Fixed; owner approved in principle |
| B | Teacher multi-file sync upload: a later bad file returned a request-level 400 after earlier files were extracted, charged and saved; a retry re-charged and duplicated them | **blocker** (§3/§5) | Fixed, pending gate |
| C | `upload_answers_engine_async` retried a deterministic bad file 3 more times | should-fix (§6) | Fixed, pending gate |
| D | Extraction ran outside any refund scope: a failed save or rejected response kept its charge | billing (owner decision) | Fixed, pending gate |
| — | `ocr_processor` app is empty boilerplate | cleanup | **Not touched** (owner: separate item) |

### Owner decisions (2026-09-14)

1. **Retry guard:** a per-course SHA-256 file fingerprint; no frontend change.
2. **Response:** keep today's shapes — 201 all succeeded, 207 mixed
   (`successful` / `failed` / `summary`), 400 all failed. Adds
   `already_uploaded` to each successful entry.
3. **Base:** build on Section 7's branch at its gated commit, confirmed with
   that session.
4. **Refunds:** a file that ends as failed is never charged.

### Design as built

- `AssignmentUploadFingerprint` (additive migration `0039`): unique on
  `(course, sha256)`. A row is:
  - claimed before any AI call;
  - completed in the same transaction that saves the assignment;
  - deleted if the upload fails;
  - deleted with its assignment (CASCADE), so a deleted assignment can be
    uploaded again.

  A claim older than 40 minutes is stale and may be taken over; that window
  is longer than `upload_assignment_async`'s 35-minute hard limit. A claim
  token stops a request that lost its claim from completing or releasing
  the newer claimant's.
- `assignments/file_uploads.upload_assignment_file` is the one per-file path,
  used by both the sync view and `upload_assignment_async`. Extraction and the
  save run inside `billing_refund_scope`.
- `InvalidUploadFileError` and `UploadAlreadyInProgressError`
  (`assignments/exceptions.py`) are on the user-facing allowlist.
  `InvalidUploadFileError` is also in `UPLOAD_REFUSALS`, so it is never
  retried. Server-side rasterizer faults (missing poppler, timeout) still
  retry.

**Known limits, not changed:**
- Within a file that finally *succeeds*, a charge from an earlier internal
  extraction attempt that was rejected is kept.
- Cancelling after the save has committed keeps the charge.

Both behave as before this change.

## 2. Commits

Branch `task/section-9-remediation`, on Section 7's `ec28d90`. That commit is
Section 7's `0c1a9c7` merged with beta `30b7b95`, confirmed by the Section 7
session as the commit to build on.

| Commit | Content |
|---|---|
| `d481fd6` | Part A — refuse `is_pdf=False`; poppler read errors → 400 |
| `de0c750` | Parts B–D — per-file outcomes, fingerprint, refund scope, retry policy |
| `e70fde5` | Comment-only fix flagged by flake8-eradicate (E800) |
| `cada6c5` | Real-provider test |

The rebase was conflict-free in code. The only conflict over the session was
the audit task-table rows (keep Section 8's row 8 and this section's row 9).
`UPLOAD_REFUSALS` auto-merged with Section 7's `AssignmentNotOpenError`.

## 3. Composition with other sections

- **Section 5** (`ai_processor/services.py`, now on beta): Part A only edits
  `PDFService.extract`. It keeps what Section 5's tests pin there:
  - the method takes no arguments beyond `self`;
  - it returns one entry per page;
  - the module-level `pdf_service` object still exists.

  Its `UnreadableUploadTest` accepts `(ParseError, PDFPageCountError)`, and
  "image bytes named .pdf" now raises `ParseError`.
- **Section 7** (`assignments/tasks.py`, `students/views.py`): this section's
  exceptions live in a new `assignments/exceptions.py`, so there is no edit to
  `students/exceptions.py`. `UploadAlreadyInProgressError` covers only the
  teacher assignment-file path; Section 7's
  `SubmissionProcessingInProgressError` stays the only guard on student
  upload-async.

## 4. Real-provider verification — PASSED

`RUN_REAL_AI=1 python manage.py test assignments.tests_real_upload_billing`,
2026-09-14 20:26:45–20:27:07 (+01:00), on `60f28c3` + the then-uncommitted
test file (committed unchanged as `cada6c5`).

The run used a real wallet, subscription and ledger on PostgreSQL, with one
real billed extraction.

| Step | Result |
|---|---|
| Batch: `quiz.png` (legible worksheet) + `broken.pdf` (not a PDF) | **207**; `quiz.png` created, `already_uploaded: false`; `broken.pdf` failed |
| Charges after first request | kept `[15545]`, refunded `[]` — the good file charged once, the bad file never reached the AI |
| Identical replay | **207**; same assignment id, `already_uploaded: true`; `broken.pdf` failed again |
| Charges after replay | kept `[15545]`, refunded `[]` — **no new charge**; still 1 assignment |

Exit 0, 1 test OK.

## 5. Earlier runs whose raw logs were lost — NOT counted

A session restart on 2026-09-14 wiped the scratchpad. The runs below did
happen, but their complete output no longer exists. Under this project's
evidence rule they are **not** counted. Every one is re-run on the committed
tree (§7–§8).

- Part A alone, pre-rebase: 3 mutants killed; full suite 3866 OK (12 skipped),
  run with `--keepdb`. This part of the record did survive, in the previous
  version of this file.
- Parts B–D, pre-rebase working tree: 121 targeted tests OK; 10 mutants
  (M4–M13) killed, restores sha-verified.
- On the rebased `6f5014d`: 138 targeted tests OK (1 skipped: an unrelated
  Redis-cache test), `check` clean, `makemigrations --check` clean, migration
  safety "additive only". Pre-commit then flagged E800, fixed in `e70fde5`.

## 6. Pre-commit on the current tip

`pre-commit run --files <all 14 Section 9 files>` on `cada6c5`: exit 0, tree
clean.

## 7. Mutation re-run on the committed tree

_Pending — waits for the Section 7 gate on `ec28d90` so the shared PostgreSQL
is not loaded during it._

## 8. Strict final gate

_Pending._ It follows the owner's procedure:
- a detached worktree at the exact commit, fingerprinted before and after;
- a fresh uniquely named test database, no `--keepdb`;
- unfiltered output kept, run under `systemd-inhibit`;
- `pre-commit --all-files`, migration safety `--base beta`,
  `makemigrations --check` and the full suite must all exit 0;
- the database is confirmed dropped afterwards.
