# Verification: beta-batch-8, Gate 1 (batch level) @ 5fc8a456

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Base:** beta = origin/beta `63c3da22` (bundle 7). **Tip:** `5fc8a456`; the code tip is `e578e3db` (the H-118 merge). `5fc8a456` adds only the backlog rows, 0b's full-run record and the strict run's evidence files (all under `docs/`). **Date:** 2026-10-06.

**Verdict: VERIFIED.** Batch 8 is exactly its three verified items, merged cleanly, and the strict full run passed on the exact final code tree, twice. Nothing is required before the push. The run itself did not follow the team's form (N1); I checked each departure and agree with the SM's ruling that the run stands.

I ran no test for this gate (rule 15). The checks are git and record checks, my own run of 0b's masked credential scan in 0b's slot, and my own reading of the two raw full-run logs.

The batch closed without H-110; it moves to the next batch.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | origin/beta `63c3da22` is an ancestor of `5fc8a456`: a fast-forward, 20 commits, 6 non-docs files. |
| Evil merges | All **3** first-parent merges in `63c3da22..5fc8a456` are **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree) and hold nothing that is on neither parent: `4df1b61a`, `5236b7dd`, `e578e3db`. |
| Direct commits | One, docs only: `5fc8a456`. |
| Each item is its verified commit plus docs only | See the table below. Each verified commit is an ancestor of what was merged, with **0** non-docs commits after it on the item's own line, and no base-update merge inside any item. |
| My records in the tree, byte for byte | H-98: the record, the probe, the mutant, the note written before the run and both logs match my copies. H-119 has no record file (a static read, reported by message). |
| v2's record | Present in the tree for H-118 (`VERIFICATION_h118_playwright_probe_own_stderr_7d534ed9.md`). I did not byte-compare it. |
| Migrations, models | No new migration, no model change. The one change under a `migrations/` path is H-119's four comment lines in `students/migrations/0026`: no operation. The strict run reports `makemigrations`: no changes detected. |
| Settings, beat schedule, requirements | None changed. No management command added or changed. |
| Production files | Two: `billing/license_service.py` and `billing/refresh_timing.py` (H-98). The other four non-docs files are tests and the commented migration. |
| The docs commit `5fc8a456` | 29 files, all under `docs/`, **0** outside: the backlog (rows H-118 to H-125 added, H-98 marked, a note on rule 18 and three earlier batteries), `docs/evidence/beta-batch-8/FULL_SUITE.md`, and 27 files in `docs/evidence/beta-batch-8-strict/`. Its commit hooks ran clean (0b's log: 8 hooks, none failed). |
| After the run tip | `e578e3db..5fc8a456` differs in **0** non-docs files. So the strict run's tree equals the push tip's code. |

## The three items
| Item | Verifier, verdict | Verified at | Merged tip | Batch merge |
|---|---|---|---|---|
| H-119 the acknowledgement on migration 0026 (comment only) | 1a, static read OK | `0cfde2f0` | `0cfde2f0` | `4df1b61a` |
| H-98 an unserved refresh in a cycle's last week is reported | 1a, VERIFIED-WITH-NOTES; the docs-only delta checked by reading | `7ab45f1e`; delta `d78ef385` | `5c570868` | `5236b7dd` |
| H-118 the renderer tests' Chromium check in a child process (test only) | v2, VERIFIED-WITH-NOTES | `7d534ed9` | `dc5d94b3` | `e578e3db` |

## Each mutation battery is against its module's last test change (rule 17's addendum)
| Item | Last change to its tests | Battery |
|---|---|---|
| H-98 | `ff2edee7` (`billing/tests/test_allocation_anchor.py`) | 11 of 11 at `9e211ca4`, the second battery, in file form; my mutant at `7ab45f1e`. |
| H-118 | `48cdcbe8` (both test modules) | 11 of 11 at the frozen tip `7c2a55f3`, after `48cdcbe8` (v2's record says the same; I checked the commit order, not the battery). |
| H-119 | none | No test and no battery: a comment. |

## The credential pattern scan (widened form; every value masked)
I ran 0b's tool myself on `5fc8a456` and on `63c3da22`, listing every row, one after the other in 0b's slot, and compared the two.
- **URLs with a password part:** none added, none removed.
- **One value written two ways** (plain and percent-encoded): 0 groups at the tip.
- **Assignment forms:** three new lines, each the ordinary word "passed" before a colon, in the strict run's generated `EVIDENCE.md` and in the two expected-kills scripts of H-98 and H-118. None is a value.
- **The two suite logs are stored as `.xz`, which the tool does not open.** I checked that each committed `.xz` unpacks to the raw log (below) and scanned the two raw logs with my own masked scan, percent-decoded as well: **0** URL-form matches in each. The assignment-form matches are lines of traceback code, test descriptions and two "blocked unsafe fetch" warnings that name a test's made-up host; I read each with its value reduced to its shape. They are the same names and counts as in bundle 7's log.
- **This record** holds no URL with anything in the password position and no assignment form.

**Limits.** The scan is by pattern: it finds the shapes above and nothing else, and it reads the tree at the tip, not the history.

## Gate 10 (the strict full run; rule 15, not repeated)
| Tip | Result |
|---|---|
| `e578e3db` (the final code tree), run 1 | **Ran 5709 tests in 2043.246s, OK (skipped=26)**, exit 0 |
| `e578e3db`, run 2 | **Ran 5709 tests in 1965.852s, OK (skipped=26)**, exit 0 |

Both were serial (`--parallel 1`, `-v 2`), each on its own test database, after `pre-commit run --all-files`, the migration safety script, `manage.py check` and `makemigrations --check`, all clean in both runs (0b's `docs/evidence/beta-batch-8/FULL_SUITE.md`).

I read both raw logs myself (`~/Documents/Projects/Grade-Automator-Plus-gate-logs/beta-batch-8-strict/`, `r1-5-full-suite.log` 7,686,795 bytes, sha256 prefix `706ced205ac56bbb`; `r2-5-full-suite.log` 7,663,129 bytes, `2fffc3b27a4c71f4`):
- **The result lines.** Each log has one "Ran" line and its "OK (skipped=26)" line, followed only by a blank line and the "Destroying test database" line. No line ends in FAIL or ERROR, there is no FAIL or ERROR header, no expected failure or unexpected success, no `BlockingIOError`, no `OutputNotRead` and no fatal interpreter error.
- **The logs are of this tree.** The test database names and the sleep inhibitor's reason carry the commit's short id, and the run's summary file names the full commit, tree `7d916790` and 3171 tracked files; I computed the same tree id and file count from `e578e3db`. Beyond what the files say of themselves: the set of test ids is exactly this tree's (next point), and a tree with anything else in it would show other ids.
- **The count checks out exactly, by test id.** Bundle 7 ran 5686. Each log has 5709 distinct test ids, the same set in both; every one of bundle 7's is among them, and **23** are new: 11 in `AutoGrader.tests_no_playwright_at_import` (H-118) and 12 in `billing.tests.test_allocation_anchor` (H-98). 5686 + 23 = **5709**.
- **Per-app counts** (my own count from the ids) match 0b's record: ai_processor 817, assignments 628, AutoGrader 562, billing 2109, classrooms 401, dashboard 270, students 268, users 654. They sum to 5709.
- **The skips.** 26 in each run, by the reasons printed: 12 real AI calls, 9 load tests, 4 network, 1 Redis; all opt-in. Bundle 7's run skipped 28: the other two cannot fork inside a parallel worker, and ran here because the run was serial. None of the batch's new tests is skipped.
- **The committed copies.** All 24 files of the evidence folder (22 `.gz`, 2 `.xz`) unpack to the checksum in `RAW_LOG_SHA256SUMS.txt` and to the raw file on disk; I unpacked and compared each.
- **Rule 18 form.** The script wrote each step's output to its own file; the raw logs end with their own result lines, which is what I rely on.

**Not done by me:**
- I did not match each test's result line one by one; other output sits inside many test lines in a serial verbose log. The result lines and the absence of any FAIL or ERROR are what I rely on.
- I did not run the commit hooks over the batch range myself. The strict run ran every hook on all files at `e578e3db`, twice, and 0b's docs commit passed them.
- I did not read the content of the new backlog rows against their items, beyond that they are the rows named.

## Notes (not blocking)
**N1 (the strict run departed from the team's form; the SM ruled it stands; I agree).** 0b's record lists six departures. I checked each against the files:
- **Two serial runs, not one parallel run.** True (`gate.log`). A serial run covers no fewer tests; it ran two that a parallel run skips. It does not exercise the parallel runner, which no item of this batch changes.
- **No memory cap, no machine lock, no load average.** True: the script's own log shows none, and `inhibitor.txt` shows the sleep inhibitor only. These are breaches of the machine rules. They cannot make a failing suite pass. What they cost is that the timings are no reference, and that H-118's real-browser tests with wall-clock limits passed twice at an unknown load.
- **No grant, and nobody knows who started it.** I cannot add to that. My own session ended at about 00:21 and started nothing; the run began at 00:42. The SM is asking the user. It is a question of process, not of what the logs show.
- **The per-test table is not evidence.** True: 5706 rows, three of them not test ids. I did not use it; I built the id lists from the raw logs.
- **The generated `EVIDENCE.md` is a template.** True: its "NOT SUPPLIED" lines are unfilled placeholders, not findings.
- My reason for agreeing: the result is shown by the raw logs themselves, which I tied to the commit by its test ids, and it was the same in two independent runs.

**N2 (the two suite logs are `.xz`).** Each `.gz` is over the repository's 500 KB limit for an added file. 0b compressed them again from the raw logs; they unpack to the listed checksums. A reader needs `xz -dc`.

**N3 (rollback to beta 63c3da22, code only; rule 11).**
- No migration and no new column: **the rollback section needs no SET DEFAULT statement.** No schema, stored data, Beat entry or Redis key to undo.
- After a rollback the ERROR line for an unserved refresh in a cycle's last week is no longer logged (H-98). Lines already written stay.
- This is my reading of the code, not a tested rollback.

**N4 (operations).** One thing an operator sees: one more ERROR log line at a licence's renewal (H-98). An "owed 1" line for a licence whose cycle ended just after 03:05 UTC, with Beat healthy, means the last daily run started late, not that there was an outage (my H-98 record, N1). Nothing a user sees changes.

**N5 (what the batch does not cover).**
- **H-110** (MEDIUM, the renderer's driver on the service's stderr) is not in it. Its verification is open: one test added after my runs has no logged run yet.
- **H-119 only acknowledges one file.** The migration safety check on the GradeA-dev repository stays red on the other old files (rows H-119 and H-120).
- **H-118's source rule misses four shapes** (row H-124, LOW).

**N6 (open rows; none blocks).** H-110, H-120, H-121, H-123 (frozen, not yet run), H-124, H-125, and the rows still open from bundle 7.

Working files: `~/Documents/Projects/GAP-1a-scratch/gate1-b8/` (`check.sh`, `check_5fc8a456.txt`, `scan_all_5fc8a456.txt`, `scan_all_63c3da22.txt`, `rawscan.py`, `mine_r1.ids`, `mine_r2.ids`).
