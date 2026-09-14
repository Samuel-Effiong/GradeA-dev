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

The run started 2026-09-14 21:47:48 (+01:00) on committed `6d21d63` (clean
tree, `dirty_lines=0`). That commit's code is identical to `cada6c5`; the
difference is docs only.

- Each mutant is one exact-string replacement that must match exactly once.
- After each mutant, the file is restored from a uniquely named copy and its
  sha256 is verified.
- **KILLED** counts only real `FAIL:` / `ERROR: test_…` lines, never an
  import or collection error.
- An unmutated baseline of every targeted class ran first: **30 tests, OK**.

About 4 minutes in, the run was deliberately stopped with SIGINT so the
Section 8 strict gate, which had started 3 minutes earlier, ran on a quiet
host. The interrupt restored the file under test. Afterwards
`git status --porcelain` was empty and `git diff HEAD` was empty.

| Mutant | Break | Result |
|---|---|---|
| M1 | PDF: remove the `is_pdf` refusal | **KILLED** — 6 failures |
| M2 | PDF: stop catching poppler read errors | **KILLED** — 2 errors |
| M3 | PDF: catch everything (blames server faults on the file) | **KILLED** — 2 errors |
| M4 | View: a per-file refusal escapes the loop again | **KILLED** — 4 failures, 1 error (bad file first/middle/last, all-invalid, replay) |
| M5 | Claim: an already-uploaded file is extracted again | **KILLED** — 3 failures (both replays, task path) |
| M6 | No refund scope around extraction and save | **KILLED** — 3 failures (failed save, unusable response ×2) |
| M7 | A failed upload never releases its claim | **KILLED** — 5 failures |
| M8 | A live claim held by another request is ignored | **KILLED** — 3 failures (concurrent retry, racing batches, live claim) |
| M9 | A stale claim is never taken over | **KILLED** — 1 error |
| M10 | A lost claim's save is kept | **KILLED** — 1 failure |
| M11 | Bad files retried again (removed from `UPLOAD_REFUSALS`) | **KILLED** — 1 failure, 2 errors (includes the real Celery worker on Redis) |
| M12 | Answer task no longer wraps the file refusal | _interrupted — re-run pending_ |
| M13 | The file's own message is replaced by the fallback | _not reached — re-run pending_ |

**11/11 run killed; all restores sha-verified. M12–M13 still to run.**

> **Caveat: this run is INTERIM and is not counted as the mutation gate.**
> Every mutant above (20:47:48–~20:51Z) overlapped the first minutes of the
> Section 8 strict gate, which Section 8 then aborted for that reason.
> Concurrent test processes share one Redis server, and 11 test modules
> flush fixed Redis databases (the H-1 session is fixing that). So a
> contended run's failures cannot be proven to come from the mutant alone.
> All 13 mutants are re-run on a quiet host, after the Section 8 gate and
> before the Section 9 strict gate, and that re-run is the one counted.

### 7a. Counted run: quiet host — 13/13 KILLED

- **When:** 2026-09-14 23:12:48–23:16:10 (+01:00).
- **What:** committed `d59add7`, whose code is identical to `6d21d63`, on a
  clean tree.
- **Quiet host:** the Section 8 gate process had ended at 22:44. Immediately
  before launch, `pgrep -af "manage.py test"` returned nothing. The H-1 and
  Section 8 sessions confirmed they were holding all DB and Redis test
  activity.
- **Baseline:** the unmutated run of every targeted class passed, 30 tests
  OK.

| Mutant | Result (failing tests) |
|---|---|
| M1 remove `is_pdf` refusal | **KILLED** — 6 failures |
| M2 stop catching poppler read errors | **KILLED** — 2 errors |
| M3 catch everything | **KILLED** — 2 errors |
| M4 per-file refusal escapes the loop | **KILLED** — 4 failures, 1 error |
| M5 already-uploaded file extracted again | **KILLED** — 3 failures |
| M6 no refund scope | **KILLED** — 3 failures |
| M7 failed upload never releases its claim | **KILLED** — 5 failures |
| M8 live claim ignored | **KILLED** — 3 failures (concurrency tests, 94 s) |
| M9 stale claim never taken over | **KILLED** — 1 error |
| M10 lost claim's save kept | **KILLED** — 1 failure |
| M11 bad files retried again | **KILLED** — 1 failure, 2 errors (includes real Celery worker on Redis) |
| M12 answer task no longer wraps the refusal | **KILLED** — 1 failure, 2 errors (includes real Celery worker on Redis) |
| M13 file's own message replaced by fallback | **KILLED** — 3 failures |

Every restore was sha256-verified, and the tree had `dirty_lines_after=0`.
Script exit 0.

## 8. Strict final gate — PASSED on `d59add7`

The gate ran on the owner's procedure, on commit
`d59add730bff61a220a180e5d99b99dcca87a402` (branch
`task/section-9-remediation`, base Section 7's gated `ec28d90`). Its code is
identical to `6d21d63` and `cada6c5`; the difference is docs only.

**Setup and host**

- Dedicated detached worktree `../Grade-Automator-Plus-s9-final-gate-d59add7`,
  used by no other session.
- Fresh test DB `test_s9_final_gate_d59add7`, no `--keepdb`, `--parallel 1`.
- Run under `systemd-inhibit --what=sleep:idle`.
- The host was quiet. The mutation run just before it and the gate itself
  were serialised with the Section 8 and H-1 sessions, which held all DB and
  Redis test activity (§7a).

| Step | Result |
|---|---|
| Test DB before | `pg_database` rows **0**, connections **0** |
| Fingerprint before | HEAD `d59add7…`, index sha256 `a77e6c41…f45a8`, content sha256 `a300634e…677f`, status lines **0** |
| `pre-commit run --all-files` | **exit 0** |
| `scripts/check_migration_safety.py --base beta` | **exit 0**; `0039` additive |
| `manage.py check` | **exit 0** |
| `makemigrations --check --dry-run` | **exit 0** |
| Full suite (23:17:34 → 23:48:40, +01:00) | **4194 tests, OK (skipped=22), exit 0**; `FAIL`/`ERROR` lines **0** |
| Teardown | `Destroying test database` present; "other sessions" lines **0** |
| Test DB after | `pg_database` rows **0**, connections **0** |
| Fingerprint after | **identical** to before (all four values) |
| Files newer than start | 1990, **all** under `.mypy_cache/` (gitignored, written by pre-commit's mypy hook); no tracked file changed |

- **Full, unfiltered output:** 55,929 lines, sha256
  `5d4815b27395b6aeaa1466f2641d646f0d78eeefd108b8108f21029c2df90abc`.
- **Skips:** the 22 skipped are Section 7's gate's 21 plus this section's
  opt-in real-provider test (`RUN_REAL_AI`). That is consistent with the
  counts, but individual skip reasons were not printed at this verbosity.

**Where the raw output lives:** `docs/evidence/section_9_final_gate/`, with
the precedent set by Section 5's post-merge gate:
- `full_suite.log.gz`, `precommit_all_files.log.gz`, the counted
  `mutations_quiet_host.log.gz`, `chain.log.gz`, and
  `real_provider_batch_upload.log.gz`;
- `gate_report.txt` and both fingerprints;
- `RAW_LOG_SHA256SUMS.txt`, the sha256 of each log before compression;
- `RAW_LOG_LINE_COUNTS.txt`;
- `SHA256SUMS.txt`, covering the stored files.

**What this gate does and does not prove.** It proves Section 9 on top of
Section 7's `ec28d90`. `beta` has since moved to `91f752b`: it now carries
Section 8 (`fb9b29c`) but still not Section 7. A read-only trial merge of
`d59add7` with beta `71d4175` merges code cleanly, and `assignments/tasks.py`
and `AutoGrader/error_messages.py` both auto-merge. It conflicts only in
`docs/CODEBASE_AUDIT_SECTIONS.md` and `docs/HARDENING_BACKLOG.md`.

**Merging into `beta` is the owner's decision.** The integrated tip that
finally lands needs its own gate, per the Section 7 / Section 8 practice.
