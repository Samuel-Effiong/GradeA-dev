# Verification: beta-batch-9, Gate 1 (batch level) @ 792d34b0

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Base:** beta = origin/beta `74065867` (batch 8). **Tip:** `792d34b0`; the code tip is `52d9628c` (the H-124 merge). `dea58a6c` adds only the backlog rows and two dated notes, and `792d34b0` only 0b's full-run record with the run's log, summary and script (both docs). **Date:** 2026-10-06.

**Verdict: VERIFIED.** Batch 9 is exactly its three verified items, merged cleanly, and its one strict full run passed on the tree it pushes. Nothing is required before the push.

I ran no test for this gate (rule 15). The checks are git and record checks, my own run of 0b's masked credential scan in 0b's slot, and my own reading of the raw full-run log.

H-127 and H-128 are not in this batch; they are planned for batch 10.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | origin/beta `74065867` is an ancestor of `792d34b0`: a fast-forward, 44 commits, 5 non-docs files. |
| Evil merges | All **3** first-parent merges in `74065867..792d34b0` are **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree) and hold nothing that is on neither parent: `cc9682b8`, `9fb6d4fe`, `52d9628c`. |
| Direct commits | Two, both docs only: `dea58a6c` and `792d34b0`. |
| Each item is its verified commit plus docs only | See the table below. Each verified commit is an ancestor of what was merged, with **0** non-docs commits after it on the item's own line. |
| The one merge inside an item that is not a plain merge | H-110's base update `45bff75f` (inside the item, before my verified tip): 0b resolved one conflict by hand, in `docs/HARDENING_BACKLOG.md` only (a row renumbered to H-126). It differs from the plain merge in that file alone. In my H-110 record. |
| My records in the tree, byte for byte | H-110: the record, the probe, the two mutants, the two notes written before the runs and the five logs match my copies. Batch 8's Gate 1 record matches too. |
| v2's records | Present in the tree for H-123 and H-124. I did not byte-compare them. |
| Migrations, models | **None.** The run reports `makemigrations`: no changes detected. |
| Settings, beat schedule, requirements | None changed. No management command added or changed. |
| Production files | **One:** `assignments/pdf_renderer.py` (H-110). The other four non-docs files are test modules: H-110's two, `assignments/tests_pdf_renderer.py` (H-123) and `AutoGrader/tests_no_playwright_at_import.py` (H-124). |
| The docs commit `dea58a6c` | Three files, all under `docs/`: the backlog (rows H-127 to H-133 added; H-45, H-110, H-123 and H-124 updated; a note on what a whole log is under rule 18) and two dated notes (who started batch 8's full run; H-123 left no test database behind). Rows H-123 to H-133 are each there once, and the sentence on my H-110 note N2 is in the H-126 row once. |
| The record commit `792d34b0` | Four files, all in `docs/evidence/beta-batch-9/`, **0** outside `docs/`. |
| The run's tree and the push tip | The run was on `dea58a6c`. `dea58a6c..792d34b0` differs in **0** non-docs files, and `52d9628c..792d34b0` likewise. So the strict run's tree equals the push tip's code. |

## The three items
| Item | Verifier, verdict | Verified at | Merged tip | Batch merge |
|---|---|---|---|---|
| H-110 the PDF renderer's driver on its own stderr | 1a, VERIFIED-WITH-NOTES | `ba4c84e1` (first runs at `c10ee47d`; one test added between them, covered by my second runs) | `0da886f0` | `cc9682b8` |
| H-123 the renderer stall test takes its limit from the same run (test only) | v2, VERIFIED-WITH-NOTES | `80c679d6` | `05f21b04` | `9fb6d4fe` |
| H-124 the no-Playwright-at-import rule's misses (test only) | v2, VERIFIED-WITH-NOTES | `f8922f2b` | `13e0231b` | `52d9628c` |

After my verified tip, H-110's line holds two commits, both docs: my record with its ten files (`bc8dcd81`) and one backlog sentence (`0da886f0`), which I checked before the merge.

## Each mutation battery is against its module's last test change (rule 17's addendum)
| Item | Last change to its tests | Battery |
|---|---|---|
| H-110 | `267d7c0c` (the reader test module); `66f76d72` (one test added to the other module) | 19 of 19 at `b8e9200e`, after `267d7c0c`. The added test is aimed at by my mutant Y12, run at `ba4c84e1` and killed by exactly that test. |
| H-123 | `d0beb906` (`assignments/tests_pdf_renderer.py`) | The gates ran at `d9396620`, after it (v2's record; I checked the commit order, not the battery). |
| H-124 | `0dffc395` (`AutoGrader/tests_no_playwright_at_import.py`) | The second round ran at `7134815d`, after it (the same). |

## The credential pattern scan (widened form; every value masked)
I ran 0b's tool myself on `dea58a6c` and on `74065867`, listing every row, one after the other in 0b's slot, and compared the two. The four files `792d34b0` adds I checked one by one.
- **URLs with a password part:** none added, none removed.
- **One value written two ways** (plain and percent-encoded): 0 groups at the tip.
- **Assignment forms:** ten new groups of lines, all in H-123's and H-124's evidence files. I read each line with its value masked. They are a variable named for what a pre-filter lets through, in a probe script and its printed output; the word "passed" or "PASSES" before a colon in sentences and in the expected-kills scripts; and a sort argument in those scripts. None is a value.
- **Two gzipped logs in H-124's evidence**, opened by the tool: one match each, a probe's printed result word. No URL form.
- **The run's log is stored as `.xz`, which the tool does not open.** The committed file unpacks to the raw log (below). I scanned the raw log with my own masked scan, percent-decoded as well: **0** URL-form matches; the assignment-form matches are traceback code, test descriptions and the two "blocked unsafe fetch" warnings that name a test's made-up host, the same names and counts as in batch 7's and batch 8's logs.
- **0b's record, the summary and the script copy:** no URL form and no assignment form.
- **This record** holds no URL with anything in the password position and no assignment form.

**Limits.** The scan is by pattern: it finds the shapes above and nothing else, and it reads the tree at the tip, not the history.

## Gate 10 (0b's strict full run; one per batch, rule 15, not repeated)
| Tip | Result |
|---|---|
| `dea58a6c` (the final code tree) | **Ran 5755 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR. 12G, `--parallel 4`, `--verbosity 2`; whole-repo mypy and `makemigrations --check` clean first (0b's `docs/evidence/beta-batch-9/FULL_SUITE.md`, `792d34b0`) |

I read the raw log myself (`~/Documents/Projects/GAP-0b-runs/strict_b9.log`, 7,595,904 bytes; its sha256 prefix `55482919d031e7e3` matches the record, and the committed `strict_b9.log.xz` unpacks to the same):
- **The result line** is `Ran 5755 tests in 668.975s`, `OK (skipped=28)`. The log holds exactly one "Ran" line and one result line; after them come only a blank line and the five "Destroying test database" lines. No line ends in FAIL or ERROR, there is no FAIL or ERROR header, and no expected failure or unexpected success. No `BlockingIOError`, no `OutputNotRead`, no fatal interpreter error.
- **The count checks out exactly, by test id:** batch 8 ran 5709. This log has 5755 distinct test ids; every one of batch 8's is among them, and **46** are new. 5709 + 46 = **5755**.
- **Where the 46 are:** `assignments.tests_pdf_renderer_driver_stderr_reader` 22 and `assignments.tests_pdf_renderer_driver_stderr` 6 (H-110's 28, the added test among them), `assignments.tests_pdf_renderer` 7 (H-123), `AutoGrader.tests_no_playwright_at_import` 11 (H-124).
- **Per-app counts** (my own count from the ids) match 0b's record: ai_processor 817, assignments 663, AutoGrader 573, billing 2109, classrooms 401, dashboard 270, students 268, users 654. They sum to 5755.
- **The skips** are 28, by the reasons printed: 12 real AI calls, 9 load tests, 4 network, 1 Redis, and 2 tests that cannot fork inside a parallel worker. They are batch 7's 28. Batch 8's run had 26 because it was serial. None of the batch's new tests is skipped, and no test was skipped for want of Chromium, so H-110's fresh-interpreter tests ran with a real browser inside this parallel run.
- **H-110 in this run:** 0 lines of driver output in the log and 0 of the "pipe not taken" error, as in the author's runs.
- **H-123 in this run:** the stall test printed its own limit ("slowest healthy render alone: 1.73 s; hung render's timeout: 6.93 s; limit ... 5.55 s") and passed. The limit stretched beyond the old fixed 4 s, in a full parallel run at a load of 3 to 7: the case the item was made for.
- **Rule 18 form.** The script copy shows the run's output going straight to the log file with stdin from `/dev/null`, nothing piped; rules 12, 13 and 16, the lock and the bytecode setting are in the command. The summary reports the watchdog never fired, exit 0, no suspend, and the load at the suite's start (3.23) and end (6.91).
- **It was the only strict run of batch 9, on the final code tree, and it passed.**

**Not done by me:**
- I did not match each test's result line one by one. The result line and the absence of any FAIL or ERROR are what I rely on.
- I did not run the commit hooks over the batch range. I ran them over H-110's range; the full run's whole-repo mypy passed.
- I did not read the new backlog rows' wording against their items, beyond that the rows are there once each.

## Notes (not blocking)
**N1 (the full run was not on a fully quiet machine at its very start).** Two commits of the Security Engineer's ran their hooks at about 14:41:30 to 14:43, inside the script's opening type check and before the suite started at 14:44:18 (0b's record says so). The suite itself started at a load of 3.23. No test of the suite overlapped them, so I see no effect on the result.

**N2 (the run's log is `.xz`).** A reader needs `xz -dc`. It unpacks to the listed checksum.

**N3 (rollback to beta 74065867, code only; rule 11).**
- No migration and no new column: **the rollback section needs no SET DEFAULT statement.** No schema, stored data, Beat entry or Redis key to undo.
- After a rollback the PDF renderer's driver shares the service's stderr again from the first render on (H-110).
- This is my reading of the code, not a tested rollback.

**N4 (operations; from my H-110 record).**
- The driver's stderr is now in the log as WARNING lines beginning `[PDF] Playwright driver stderr:`. There were none in any run. Playwright's debug variable should not be set in a service: with it there are over a hundred lines a render.
- With a full log pipe a writer waits for the collector again instead of losing lines. That is how main behaves today.
- An ERROR "did not take the renderer's stderr pipe" after a Playwright upgrade means the old behaviour is back.
- Nothing a user sees changes.

**N5 (what the batch does not cover).**
- **H-110:** the reader is not rate-limited (row H-126, LOW); how the hosting platform wires stderr is not measured; the offline benchmark tool still starts a driver on its own stderr.
- **H-127 and H-128** (what a student is sent of a saved grading result; MEDIUM and LOW) are not in this batch. Until they are in beta and promoted, the exposure they describe stays.
- **H-123 and H-124** change tests only.

**N6 (open rows; none blocks).** H-126, H-127, H-128, H-130 to H-133, H-120, H-121, H-125, and the rows still open from earlier batches. (H-129 is closed by founder decision, 2026-10-06.)

Working files: `~/Documents/Projects/GAP-1a-scratch/gate1-b9/` (`check.sh`, `check_dea58a6c.txt`, `scan_all_dea58a6c.txt`, `scan_all_74065867.txt`, `b9u`, `b8u`); `gate1-b8/rawscan.py`.
