# Post-merge gate on `beta` after the Section 5 merge

This gate covers the merge commit `30b7b95d912c3ed33ee8b0ae8695b3299cd7ad92`: the owner-approved merge of
Section 5 into `beta`. **It is the gate for that `beta` tree. It is not the
Section 5 production gate,** which passed on `eb6f3a0` and is recorded in
`../ANSWER_EXTRACTION_BENCHMARK_EVIDENCE.md` and `../s5_final_gate/`.

| Item | Value |
|---|---|
| Merge commit | `30b7b95d912c3ed33ee8b0ae8695b3299cd7ad92` |
| Parents | `544424a` (`beta` before the merge) and `aedade3` (Section 5: `eb6f3a0` plus docs-only evidence and closure commits) |
| Checkout | `../Grade-Automator-Plus-beta-post-s5-gate`, a detached worktree at the merge commit, locked for the whole gate, used by no other session. Only the gitignored `.env` link and `settings_worktree.py` were added. |
| Infrastructure | postgres: PostgreSQL 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1) on x86_64-pc-linux-gnu; redis: 8.0.5; python: 3.12.10  django: 5.2.6 |

## A lost first run, and why this one exists

The first post-merge full suite on this same commit also passed: 4,146 tests,
OK, 20 skipped, 17:08-17:47Z on 2026-09-14. A session restart then wiped the
scratchpad holding its raw logs, before they were committed. That run is not
used as evidence here. Every step below was re-run on the same commit, with
the complete output written straight into this directory.

## Merge integrity

Checked with exact path sets before `beta` was moved:
- The merge changes 105 paths against `544424a`, all of them Section 5 paths.
  `assignments/services.py` was already identical on `beta`.
- It differs from the Section 5 tip only in the 53 paths `beta` changed after
  `084d0e4`.
- In `docs/CODEBASE_AUDIT_SECTIONS.md`, only the Section 5 row differs from
  `beta`.
- There were no conflicts. `beta` was moved by compare-and-swap from
  `544424a`.

## Fingerprint

| Field | Value |
|---|---|
| HEAD | `30b7b95d912c3ed33ee8b0ae8695b3299cd7ad92` |
| `sha256(git ls-files -s)` | `d6fca188b57a6fcd70e44a17082a53d6275e5e4dd8de7c4d7565a2a51dde4f77` |
| tracked content sha256 | `66db147e75abc1820fcd48f24ed04b3c4d7ab36ba8740f75c8562a6ffc490ad3` |
| `git status --porcelain` | 0 lines |

The fingerprint was identical before the checks, after the static checks,
after the full suite and at the end (`01_`, `01b_`, `06_`, `11_`). `beta` was
still `30b7b95` at each of those points.

## Static, system and migration checks

| Check | Result |
|---|---|
| `manage.py check` | exit 0; System check identified no issues (0 silenced). |
| `check --deploy --fail-level ERROR` | exit 0; System check identified 62 issues (0 silenced). |
| `makemigrations --check --dry-run` | exit 0; No changes detected |
| `scripts/check_migration_safety.py --base 544424a` | exit 0; No new migration files in this diff. |
| `pre-commit run --from-ref 544424a --to-ref 30b7b95` | exit 0; no failed hooks |
| `pre-commit run --all-files` (a throwaway detached checkout of the same commit, removed afterwards) | exit 0; 0 failed hooks; 0 files modified |

The 62 deployment warnings are the development-environment set:
48 (drf_spectacular.W001); 8 (drf_spectacular.W002); 1 (security.W004); 1 (security.W008); 1 (security.W009); 1 (security.W012); 1 (security.W016); 1 (security.W018). That is 3 fewer `drf_spectacular.W001` than on `eb6f3a0`; the
difference comes from the Sections 7 and 8 changes already on `beta`.

## Full repository suite

| Item | Value |
|---|---|
| Command | `systemd-inhibit --what=sleep:idle python manage.py test --noinput -v 2 --settings=settings_worktree`, with `RUN_REAL_AI` unset and **no `--keepdb`** |
| Test database | `test_beta_post_s5_30b7b95`; `pg_database` had 0 rows for it beforehand |
| Result | **Ran 4146 tests in 1683.553s - OK (skipped=20)**, **exit 0**, 0 `FAIL:`/`ERROR:` lines |
| Time | 2026-09-14T20:07:10Z to 2026-09-14T20:35:29Z (1699 s) |
| Complete log | `full_suite.log.gz`, 60248 lines, raw sha256 `4403fa42c6140818d701d07f55235cb94d0ae682f1073e224cdd96679ae309a2` |
| Teardown | "Destroying test database for alias 'default' ('test_beta_post_s5_30b7b95')..." present; **0 "other sessions using the database" lines**; afterwards **0 `pg_database` rows** and **0 `pg_stat_activity` connections** |

The 20 skips are the opt-in real-AI tests. The suite ran alone: the targeted
live check below ended before it started, and the H-1 session held its
database load until it finished.

## Targeted real-provider check (billed)

After `084d0e4`, `beta` changed the code that calls answer extraction
(`assignments/tasks.py`, `students/services.py` and `students/views.py`). So
the merged tree was checked against the real model, not only with mocks.

| Item | Value |
|---|---|
| Where | a `git archive` export of the merge commit, content identical to the checkout (`07_exports.txt`), with its own fresh database `test_beta_post_s5_live_30b7b95` |
| Command | `RUN_REAL_AI=1 ANSWER_LIVE_ONLY=AE-905,AE-916 manage.py test ai_processor.tests_answer_benchmark_live assignments.tests_real_extraction.RealAssignmentExtractionTest.test_a_real_extraction_from_a_PDF_upload` |
| Result | **Ran 7 tests in 302.767s - OK**, exit 0; resolved per test: 7 ok, 0 skipped, 0 failed |
| Time | 2026-09-14T20:00:35Z to 2026-09-14T20:06:15Z |
| Teardown | 0 `pg_database` rows and 0 connections afterwards |

Live extraction:

| ID | Pages | Chunks | Retries | Tokens | Credits = tokens | Discrepancies |
|---|---|---|---|---|---|---|
| AE-905 | 5 | 2 | 0 | 28,088 | yes | none |
| AE-916 | 5 | 2 | 0 | 41,479 | yes | none |

Live extraction, then real grading:

| ID | Extraction discrepancies | Flagged for review | Score | Credits = tokens |
|---|---|---|---|---|
| AE-905 | none | none | 20/20 | yes |
| AE-916 | none | Q9 | 25/35 | yes |

- **Split answers:** Q3 came back whole in both documents: "The major
  component is" and "Nitrogen", in order.
- **Model variation, within the contract:** on AE-905 the model also
  transcribed the page's "(continued overleaf)" marker.
- **Recorded billed tokens:** 177,527. The real PDF-upload test also makes
  a billed call but does not record its tokens.

## What this gate establishes

- **It establishes** that the `beta` tree at `30b7b95d912c3ed33ee8b0ae8695b3299cd7ad92` passes all of the
  following, and that the checkout under test did not change during the run:
  - the full repository suite on a fresh database, with clean teardown
  - the static, system and migration checks, and `pre-commit --all-files`
  - a targeted real-provider check of answer extraction and grading
- **It does not** re-run the Section 5 mutation testing (35/35 on `eb6f3a0`).
  Every Section 5 code file on this commit is byte-identical to the Section 5
  branch.
- **It does not** cover any commit after `30b7b95d912c3ed33ee8b0ae8695b3299cd7ad92`. Recording it added only
  docs-only commits.

## Files

| File | Contents |
|---|---|
| `00_identity.txt` | commits and parents |
| `01_`, `01b_`, `06_`, `11_` | fingerprints |
| `02_infra.txt` | infrastructure |
| `03_static_results.txt`, `04_precommit_all.txt` | check results |
| `05_full_suite.txt` | full-suite summary and teardown |
| `07_exports.txt`, `08_live.txt` | the live check |
| `*.log.gz` | complete raw logs (raw sha256 in `RAW_LOG_SHA256SUMS.txt`) |
| `live_run_postmerge.json`, `live_grading_run_postmerge.json` | live records |
| `SHA256SUMS.txt` | checksums of every other file in this directory |
