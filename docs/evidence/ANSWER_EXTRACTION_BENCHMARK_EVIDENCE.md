# Answer-extraction benchmark - verification evidence

Section 5 (`ai_processor`). Built against the 43-section "Answer Extraction -
Comprehensive Benchmark & Verification Specification". Last updated
2026-09-14.

**SECTION 5 - CLOSED / PRODUCTION GATE PASSED.** Closure approved by the
owner on 2026-09-14.

- The production gate passed on the exact commit
  `eb6f3a06b9b864a462540276f7f5cb403256c4b7`. See "Final gate on the
  committed tree" below.
- The split-answer and question-number defects were validated against the
  real provider.
- The docs-only evidence corrections `2a05dc5` and `516fa39` are part of the
  evidence trail.
- The owner approved merging into the current `beta`. A separate post-merge
  gate establishes the state of the resulting `beta` tree. The Section 5 gate
  above belongs to `eb6f3a0` only, and is not claimed for any later `beta`.
- Closing Section 5 does **not** close H-11, H-1, H-13 or Section 9.

Permanent requirement, set by the owner on 2026-09-14:
> A student's answer must never be silently truncated because it crosses a
> chunk boundary.

## Tree

| Item | Value |
|---|---|
| Base | `beta` at `084d0e4`. The commits between `1373eae` and `084d0e4` are the H-1 cache session's and touch none of these files. |
| Committed as | `eb6f3a06b9b864a462540276f7f5cb403256c4b7` (tree `fc787681f599f80fa00b8ed972ac98eee07205ea`) on `task/section-5-answer-extraction`, parent `beta` `084d0e4`. The commit contains only the 79 Section 5 paths. Merged into `beta` as `30b7b95` on 2026-09-14 (owner-approved). |
| Changed by this work (tracked) | `ai_processor/services.py`, `ai_processor/tests_answer_extraction_gate.py` |
| Created by this work | `ai_processor/benchmark/answers/**`, `ai_processor/tests_answer_benchmark_{scenarios,failures,inputs,concurrency,grading,live}.py`, `ai_processor/tests_answer_chunk_merge.py`, `ai_processor/management/commands/answer_extraction_benchmark.py`, this file |

## What was built

- `ai_processor/benchmark/answers/`:
  - deterministic PDF generator
  - scenario catalogue: 55 scenarios with stable `AE-NNN` ids
  - deterministic fake provider, with failure injection and model quirks
  - harness running the REAL pipeline
  - live-provider corpus and call recorder, including live grading
  - report generator
  - mutation runner
  - README
- Seven test modules, listed under "Reproduce".
- `manage.py answer_extraction_benchmark` writes
  `reports/BENCHMARK_REPORT.md` and `reports/benchmark_report.json`.

## Defects found by the benchmark, and fixed

IDs match the regression mapping in the README and the generated report.
R1-R8 are the defects the spec named up front; N1-N9 were found by this
work.

| ID | Defect | Consequence | Fix | Guarded by |
|---|---|---|---|---|
| N1 | The chunk merge kept an empty entry unless a later one had text. A BLANK is empty, so the chunk that never saw the page beat the chunk that did. | Every deliberate blank on page 4 or later was reported NOT_FOUND, flooding the review queue. | Status precedence. A BLANK counts only when its `source_page` lies in its chunk; ties between empty entries go to NOT_FOUND. | AE-600, 601, 800, 801, 802; M15, M16, M17 |
| N2 | An answer written across the chunk seam kept only its first half. | The student was graded on the opening of their answer. | Parts returned by different chunks are joined in page order; the joined answer stays ILLEGIBLE if either part was. | AE-205, 206; M18 |
| N3 | From chunk 2 onward the note listed EVERY question as already extracted. | An obedient model skips every answer on its own pages. | Only questions with a real transcription are listed. | AE-804; M19 |
| N4 | The single-call path wrapped credit, access and cancellation errors in a bare Exception. | An out-of-credits teacher was retried 3 times and shown a generic error; cancellations were retried. | Re-raised with type intact. Exhausted retries and failed chunks now also keep their cause. | error-type and persistent-failure tests; M23-M27 |
| N5 | An empty or `None` submission still made a billed call and returned a success. | Credits spent on nothing. | Refused before any call. | `EmptySubmissionTest`; M28 |
| N6 | `_split_into_chunks` with a negative size returned `[]`. | A misconfiguration would silently extract nothing. | Sizes below 1, and non-integers, are rejected. | `ChunkSizeContractTest`; M12 |
| N7 | Harness defect: generated PDFs were not byte-deterministic. | The determinism claim was false. | `save(..., no_new_id=True)`. | `DeterminismTest`; M32 |
| **N8** | **The chunk note told later chunks an already-found question "does NOT need to be extracted again". The real model obeyed and dropped the continuation of a split answer.** | **Confirmed live on AE-905: "The major component is… Nitrogen" was graded as "The major component is".** | **The note asks for the continuation, under the same question number, and lists found questions plainly (it used to print `QQ1`).** | **AE-803, 805, 806, 807; live AE-905, 915, 916; `AlreadyFoundNoteTest`; M33** |
| **N9** | **The merge keyed answers on the model's raw question label. The model switches between `1` and `Q1` from chunk to chunk.** | **One question became two, so the gate re-read and re-billed the whole script (live AE-912: 12 calls, 189,526 tokens). On a single call, the final attempt dropped real answers as "numbering drift".** | **Answers are relabelled with the assignment's own label before the merge and before the gate. `3`, `"3"`, `"Q3"` and `"Question 3"` are one question; an assignment declaring both `1` and `Q1` is left alone.** | **`QuestionLabelTest`, `QuestionNumberStyleTest`; live AE-912, 921, 905; M34, M35** |

N9 surfaced during the broad live validation of the N8 fix. N8's plain
listing of found questions made the model copy chunk 1's number style, which
exposed N9. Both fixes were validated together.

One pre-existing test was also changed. `tests_answer_extraction_gate.py`
passed `content=[]` as a placeholder, which the N5 guard now rejects. It now
passes a one-block placeholder; none of its assertions changed.

## Results

### Deterministic benchmark (free)

`manage.py answer_extraction_benchmark`, 2026-09-14:

```text
Total scenarios 55 | Passed 55 | Failed 0 | Known gaps 0 | Skipped 0
Chunked 42 | Real-provider documents 9 | Mutations 35, killed 35
```

The benchmark and related unit suites pass after both fixes: 219 tests,
including the grading and objective pipelines. The 2 skipped tests are the
billed live classes.

### Mutation testing (35/35 killed)

- Ran in a scratchpad copy of the tree (no `.git`) against its own database,
  `test_mut_303c0ac6`.
- Unmutated baseline: exit 0.
- Every mutant was killed by a failing assertion, none by a crash.
- Every file was restored and verified by sha256, with 0 restore failures.
- **M33** restores the old note wording and is killed by the split-answer
  scenarios.
- **M34** (merge) and **M35** (single-call gate) each remove one of the two
  label relabels, and are killed by `QuestionNumberStyleTest`.

The copy was synced after the last code change. Since then, only the report
generator's regression list and `README.md` have changed (metadata and
documentation).

### Real provider - before and after (billed, `x-ai/grok-4.3`)

Staged as the owner required: the targeted failing document first, then the
broad corpus.

| Stage | What ran | Result | Tokens |
|---|---|---|---|
| Original run (before any fix) | 7 documents | 6 correct; **AE-905 lost the second half of Q3** | 425,593 |
| Targeted: note fix | AE-905 only | **Q3 complete**, no retries | 27,804 |
| Broad: note fix only | 9 documents + grading 3 | all correct, **but** AE-912 and AE-921 re-read (N9) | 863,701 + 172,042 |
| Targeted: label fix | AE-912, AE-905 | no retries; AE-912 back to 62,811 tokens | 90,762 |
| **Broad: both fixes (final)** | **9 documents + real grading on 3** | **all correct, zero retries, credits = tokens everywhere** | **485,133 + 171,716** |

Total billed across this validation: 1,811,158 tokens.

Final broad extraction run (2026-09-14T13:19:47Z):

| ID | Pages | Chunks | Retries | Tokens (final) | Tokens (original) | Tokens (note fix only) |
|---|---|---|---|---|---|---|
| AE-903 | 3 | 1 | 0 | 22,747 | 22,778 | 22,700 |
| AE-906 | 6 | 2 | 0 | 46,063 | 46,046 | 46,634 |
| AE-907 | 7 | 3 | 0 | 59,681 | 58,886 | 59,469 |
| AE-909 | 9 | 3 | 0 | 69,947 | 69,391 | 69,854 |
| AE-912 | 12 | 4 | 0 | 62,630 | 63,524 | 189,526 |
| AE-921 | 21 | 7 | 0 | 111,412 | 108,880 | 334,568 |
| AE-905 | 5 | 2 | 0 | 28,038 | 56,088 | 56,019 |
| AE-915 | 8 | 3 | 0 | 42,911 | - | 43,060 |
| AE-916 | 5 | 2 | 0 | 41,704 | - | 41,871 |

On every document:
- all statuses were exact and there were no discrepancies
- every page was sent exactly once, and every claimed range matched
- the schema was sent on every call
- the credits moved by exactly the reported tokens

The split answers came back whole:
- AE-905 and AE-916: "The major component is / Nitrogen"
- AE-915, over three chunks: "The main gas in the air / makes up about four
  fifths of it and / that gas is Nitrogen"

**Live extraction then real grading** (2026-09-14T13:26:26Z). In all three
documents:
- the graded statuses equal the extracted ones
- only NOT_FOUND questions are flagged for review
- totals equal the per-question sums
- blank and absent answers score 0, and real answers score above 0
- the grader saw every part of each split answer
- the second opinion ran, and credits equalled tokens

| ID | Graded statuses | Flagged for review | Score |
|---|---|---|---|
| AE-905 | Q1-Q4 ANSWERED | none | 20/20 (split Q3 scored 5/5) |
| AE-906 | Q3 BLANK, Q9 NOT_FOUND, rest ANSWERED | Q9 | 20/30 |
| AE-916 | Q5 BLANK, Q9 NOT_FOUND, rest ANSWERED | Q9 | 25/35 (split Q3 scored 5/5) |

The raw records are in `ai_processor/benchmark/answers/reports/`:

| File | Contents |
|---|---|
| `live_run_before_prompt_change.json` | original run |
| `live_run_ae905_targeted.json` | targeted note fix |
| `live_run_after_note_before_labels.json` | broad run, note fix only (extraction) |
| `live_grading_run_after_note_before_labels.json` | broad run, note fix only (grading) |
| `live_run_labels_targeted.json` | targeted label fix |
| `live_run.json` | final broad run (extraction) |
| `live_grading_run.json` | final broad run (grading) |

## Repository gates

| Gate | Tree | Result | Status |
|---|---|---|---|
| Full `manage.py test`, before the split-answer work (2026-09-14 08:57-09:36Z) | shared working tree, `--keepdb`, HEAD `1373eae` throughout (reflog: `beta` did not move between 2026-09-13 18:15Z and 2026-09-14 13:31Z) | 3,961 tests OK, 0 failures, 17 skipped | interim |
| Full `manage.py test`, after both fixes (2026-09-14 13:29:57Z, 1,305 s) | shared working tree, `--keepdb`; started at HEAD `1373eae` plus this work | **3,974 tests OK, 0 failures, 18 skipped**, exit 0 | interim, with a caveat below |
| **Owner's final gate** | **exact commit `eb6f3a0`, dedicated locked detached worktree, fresh databases without `--keepdb`, unfiltered logs** | **PASSED - 4,003 tests OK; static, migration, real-provider and mutation checks all pass (see "Final gate on the committed tree")** | **final** |

**Caveat on the second run: the fingerprint could not detect the change
that happened.** The H-1 session committed `bfb6d8a` and `f593be1` to `beta`
at 13:31:37Z and 13:33:06Z, during the run. The shared checkout's working
tree was rewritten to match at 13:31:37Z. Those commits touched
`users/signals.py`, `users/admin.py` and three `AutoGrader/tests_cache_*`
files; none of them are this work's files.

The "code fingerprint" used here hashes the working tree's diff against
HEAD. A commit that moves HEAD and the working tree together leaves it
unchanged, so "identical before and after" did not prove that no code
changed.

What the run actually covered is the tree as imported when it started:
`1373eae` plus this work. The new `AutoGrader/tests_cache_user_fanout.py`
did not exist at test discovery and was not run. This limitation is exactly
why the owner's final gate requires a dedicated detached worktree at a fixed
commit.
## Final gate on the committed tree

All artifacts are in `docs/evidence/s5_final_gate/`:
- a summary per step (`00`-`11`)
- the complete raw logs, gzipped, with their sha256 in
  `RAW_LOG_SHA256SUMS.txt`
- the mutation report
- the live extraction and grading records from this run
- `SHA256SUMS.txt` over every artifact

### 1. Identity and isolation

| Item | Value |
|---|---|
| Commit | `eb6f3a06b9b864a462540276f7f5cb403256c4b7` |
| Tree | `fc787681f599f80fa00b8ed972ac98eee07205ea` |
| Parent | `084d0e4fe68ca56682343294cb17daf914785008` (`beta` when committed) |
| Checkout | `../Grade-Automator-Plus-s5-final-gate`, a detached worktree at the SHA, **locked** for the whole gate, used by no other session. Only the gitignored `.env` symlink and `settings_worktree.py` were added. |

The fingerprint was taken at four points: before, after the static checks,
after the full suite, and at the end. It was identical every time:

| Fingerprint field | Value |
|---|---|
| HEAD | `eb6f3a0…` |
| `sha256(git ls-files -s)` | `99d76ffaa14ad4eb3fd0a727f3e067a57abc18c0335c7ae208288c4f8f23e0ed` |
| sha256 over every tracked file's content | `794298e66d78de1ead33ad4736b8818a5670bc4ebd57796efdb45086df39087b` |
| `git status --porcelain` | 0 lines |
| `task/section-5-answer-extraction` | still `eb6f3a0` |

This fingerprint compares the checkout with its own fixed SHA, not with a
moving branch. So, unlike the earlier interim runs, a commit by another
session to `beta` or any other branch cannot hide a change here. During the
gate the H-1 session committed its Sections 7 and 8 integration merge on its
own branch, and the fingerprint did not move.

The real-provider and mutation runs used `git archive` exports of the same
SHA. Both exports' content hashes equal the checkout's: `794298e6…`.

### 2. Infrastructure

Real PostgreSQL 18.6, real Redis 8.0.5, Python 3.12.10, Django 5.2.6.

### 3. Static, system and migration checks (exact commit)

| Check | Result |
|---|---|
| `pre-commit run --from-ref 084d0e4 --to-ref eb6f3a0` | exit 0, no failed hooks |
| `pre-commit run --all-files` | **exit 0**, every hook Passed or Skipped with no files to check, **0 files modified**. Run in a throwaway detached worktree at the same SHA, removed afterwards. |
| `manage.py check` | 0 issues, exit 0 |
| `manage.py check --deploy --fail-level ERROR` | exit 0, 65 warnings (below) |
| `makemigrations --check --dry-run` | "No changes detected", exit 0 |
| `scripts/check_migration_safety.py --base beta` | "No new migration files in this diff.", exit 0 |

The 65 deployment warnings are exactly the set the H-1/H-2 release gate
traced to the development `.env` and to existing schema code:
- 51 `drf_spectacular.W001`
- 8 `drf_spectacular.W002`
- one each of `security.W004`, `W008`, `W009`, `W012`, `W016` and `W018`

### 4. Full repository suite (exact commit)

| Item | Value |
|---|---|
| Command | `systemd-inhibit --what=sleep:idle python manage.py test --noinput -v 2 --settings=settings_worktree`, with `RUN_REAL_AI` unset and **no `--keepdb`** |
| Test database | `test_s5_final_gate_eb6f3a0`; `pg_database` had 0 rows for it beforehand |
| Result | **Ran 4003 tests in 1331.9s - OK (skipped=18)**, **exit 0**, 0 `FAIL:`/`ERROR:` lines |
| Time | 2026-09-14T15:13:46Z to 15:36:13Z (1,347 s) |
| Complete log | 59,267 lines, sha256 `20321cc3b1af19ff0e85451f345c9d07381044ef0ca0ded7899c6d3069d56bbc` |
| Teardown | "Destroying test database for alias 'default' ('test_s5_final_gate_eb6f3a0')..." present; **0 "other sessions using the database" lines**; afterwards **0 `pg_database` rows** and **0 `pg_stat_activity` connections** |

The 18 skips are the opt-in real-AI tests, which run separately in section 5.

### 5. Real-provider checks (exact commit, billed)

- **Command:** `RUN_REAL_AI=1 systemd-inhibit … manage.py test --noinput -v 2`
  on `ai_processor.tests_answer_benchmark_live`,
  `ai_processor.tests_real_chunked_extraction` and
  `assignments.tests_real_extraction`.
- **Database:** fresh `test_s5_live_eb6f3a0`, without `--keepdb`. It had 0
  rows before, and 0 rows and 0 connections after.
- **Result: Ran 11 tests - OK, exit 0.** Each test's outcome was resolved
  individually: 11 ok, 0 skipped, 0 failed.
  - `LiveAnswerExtractionBenchmarkTest.test_live_corpus`
  - `LiveEndToEndGradingTest.test_extraction_through_real_grading`
  - `RealChunkedAnswerExtractionSafetyTest.test_blank_and_not_found_do_not_collapse_into_one_silent_zero`
  - `RealChunkedAssignmentExtractionTest.test_a_multipage_paper_extracts_every_question_exactly_once`
  - `RealAssignmentExtractionTest`: `test_a_real_extraction_from_a_PDF_upload`,
    `test_a_real_extraction_returns_a_usable_assignment`,
    `test_the_extracted_document_survives_the_prosemirror_round_trip`
  - the four `LiveCorpusShapeTest` checks
- **Time and log:** 2026-09-14T15:42:52Z to 16:07:00Z. 1,220 lines, sha256
  `c0148ffd90bf0200efccb66966cd79d702130d356189a4aad0cecca5669c61f0`.

Live corpus results (model `x-ai/grok-4.3`):

| ID | Pages | Chunks | Retries | Tokens = credits |
|---|---|---|---|---|
| AE-903 | 3 | 1 | 0 | 22,907 |
| AE-906 | 6 | 2 | 0 | 46,766 |
| AE-907 | 7 | 3 | 0 | 59,133 |
| AE-909 | 9 | 3 | 0 | 69,731 |
| AE-912 | 12 | 4 | 0 | 63,284 |
| AE-921 | 21 | 7 | 0 | 111,751 |
| AE-905 | 5 | 2 | 0 | 28,181 |
| AE-915 | 8 | 3 | 0 | 43,028 |
| AE-916 | 5 | 2 | 0 | 42,399 |

- **Extraction:** 0 discrepancies on every document, and credits equalled
  reported tokens on every document. Extraction total: **487,180 tokens**.
- **Extraction then real grading** (AE-905, AE-906, AE-916): **171,772
  tokens**.
  - Review flags: AE-905 none, AE-906 Q9, AE-916 Q9.
  - Totals: 20/20, 20/30 and 25/35.
  - The credit check held on all three.
- **Recorded total:** 658,952 tokens.
- **Not measured:** the other two real-AI modules
  (`tests_real_chunked_extraction`, `tests_real_extraction`) make billed calls
  but do not record their tokens.

### 6. Mutation testing (exact commit)

| Item | Value |
|---|---|
| Where | a `git archive` export of `eb6f3a0`, with its own database `test_s5_mut_eb6f3a0` |
| Result | **35 of 35 KILLED**, baseline exit 0, **0 restore failures**; the export's content hash after every restore still equals the commit's (`794298e6…`) |
| Time and log | 2026-09-14T15:42:56Z to 16:05:40Z; sha256 `c0b2425a0c666988b363d15c56b39b883da88eb3319ab3b2d06cae189b68b41c` |
| Database cleanup | the runner reuses its database between mutants, so it was dropped by hand after confirming 0 connections. No `test_s5_*` database remains. |

### 7. What this gate does and does not establish

- **It establishes** that the exact committed Section 5 tree, `eb6f3a0`,
  passes all of the following, and that the checkout under test did not
  change during the run:
  - the full repository suite on a fresh database, with clean teardown
  - all static, system and migration checks
  - the real-provider checks
  - mutation testing
- **It does not merge anything into `beta`.** Merging is the owner's decision.
  When it happens, `assignments/services.py` and
  `docs/CODEBASE_AUDIT_SECTIONS.md` will need an ordinary three-way merge
  against the Sections 7 and 8 integration.
- **Correction recorded in this file.** An evidence-table edit made before
  the gate left a duplicate "Owner's final gate … NOT RUN" row in the copy
  committed in `eb6f3a0`. It is removed in the docs-only follow-up commit
  that records this gate. No code or test file was involved.

### 8. Closure

Approved by the owner on 2026-09-14: **SECTION 5 - CLOSED / PRODUCTION GATE
PASSED**, based on the gate on `eb6f3a0` recorded above.

What remains open:
- The follow-up items below are retained as independent work.
- H-11, H-1, H-13 and Section 9 are separate items and remain open.

## Post-merge gate on `beta` (30b7b95)

The merge into `beta` has its own gate, recorded in
`docs/evidence/beta_post_s5_gate/README.md`. It is the gate for the `beta`
tree at `30b7b95d912c3ed33ee8b0ae8695b3299cd7ad92`, not a re-statement of the
Section 5 gate above.

Result, in the same locked-checkout, fresh-database, unfiltered-log form:
- full suite: 4,146 tests, OK, 20 skipped, exit 0, clean teardown
- static and migration checks and `pre-commit --all-files`: pass
- targeted real-provider check (AE-905, AE-916, real PDF upload): 7/7

## Follow-up items (retained, not fixed here)

- **Retry policy re-bills successful chunks.** One persistently failing chunk
  on a 6-page script costs 12 calls, 3 of them re-reading chunk 1. This is
  pinned by `PersistentChunkFailureTest`; changing it is a billing decision.
- **Question-number mismatch in grading.** Answer extraction now normalises
  labels (N9). Grading's own join, `_question_number_key` in
  `_pair_question_with_answers` and `_stamp_answer_provenance`, still does not
  equate `"Q3"` with `3`. It is untouched because it is grading logic. It is
  not reached by extracted answers any more, which now carry the
  assignment's own labels.
- **Retry paths that lose the original error cause:** the assignment chunker,
  the grading batches and the grading summary.
- **Remaining cancellation retry:** `extract_grade_with_retry` does not
  re-raise `TaskCancelledError`.
- **Handwriting testing is simulated.** It uses an oblique face with per-glyph
  jitter, not real scanned student scripts.
- **Last-chunk note wording.** It still says "mark any question that was not
  found in any chunk as genuinely skipped". The merge defends against a model
  that reads this as BLANK (AE-800), but the wording itself is unchanged
  prompt text.
- **Not injected or measured:** a database or Redis failure mid-extraction,
  and memory use on large documents.

## Reproduce

```bash
python manage.py test ai_processor.tests_answer_benchmark_scenarios \
  ai_processor.tests_answer_benchmark_failures ai_processor.tests_answer_benchmark_inputs \
  ai_processor.tests_answer_benchmark_concurrency ai_processor.tests_answer_benchmark_grading \
  ai_processor.tests_answer_chunk_merge
python manage.py answer_extraction_benchmark
RUN_REAL_AI=1 python manage.py test ai_processor.tests_answer_benchmark_live   # billed
```

Mutation testing: see `ai_processor/benchmark/answers/README.md`. It must run
in a copy of the tree, with its own test database.
