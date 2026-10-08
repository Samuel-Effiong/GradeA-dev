# Verification: beta-batch-10, Gate 1 (batch level) @ 78c447d6

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Base:** beta = origin/beta `8bbf44f9` (batch 9). **Tip:** `78c447d6`; the code tip is `88c88611` (the H-127 merge). `fd28682c` adds only the backlog changes and a dated correction to batch 9's Gate 1 record, and `78c447d6` only 0b's full-run record with the run's log, summary and script (both docs). **Date:** 2026-10-06.

**Verdict: VERIFIED.** Batch 10 is exactly its one verified item, merged cleanly, and its one strict full run passed on the tree it pushes. Nothing is required before the push. The package should carry the four points of N1: they say what this batch changes for students and for the frontend, and what it does not fix.

I ran no test for this gate (rule 15). The checks are git and record checks, my own run of 0b's masked credential scan in 0b's slot, and my own reading of the raw full-run log.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | origin/beta `8bbf44f9` is an ancestor of `78c447d6`: a fast-forward, 31 commits, 12 non-docs files. |
| Evil merges | The one first-parent merge in `8bbf44f9..78c447d6`, `88c88611`, is **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree) and holds nothing that is on neither parent. |
| Direct commits | Two, both docs only: `fd28682c` and `78c447d6`. |
| The item is its verified commit plus docs only | See the table below. The verified commit is an ancestor of what was merged, with **0** non-docs commits after it on the item's own line. |
| My records in the tree, byte for byte | H-127 and H-128: the record, the probe, the two mutants, the two notes written before the runs and the five logs match my copies. Batch 9's Gate 1 record matches as pushed. |
| The dated correction to batch 9's Gate 1 record | `docs/evidence/beta-batch-9/VERIFICATION_corrected_2026-10-06.md` is byte-identical to my corrected file (line 99 only: H-129 is closed, not open), with a note beside it. The pushed record is left as it was, by the SM's ruling. |
| Migrations, models | **None.** The run reports `makemigrations`: no changes detected. |
| Settings, beat schedule, requirements | None changed. No management command added or changed. |
| Production files | **Eight:** `assignments/serializers.py`, `dashboard/views.py`, `students/serializers.py`, `students/views.py`, `students/services.py`, `students/second_opinion_serializers.py` (a docstring), `ai_processor/services.py`, and the new `students/feedback_projection.py`. The other four non-docs files are the item's test modules. |
| The docs commit `fd28682c` | Three files, all under `docs/`: the backlog (H-127 and H-128 marked done at `88c88611`; H-120, H-121, H-130 and H-133 updated; rows H-134 to H-137 added), the corrected record and its note. Rows H-127 to H-137 are each there once. |
| The record commit `78c447d6` | Four files, all in `docs/evidence/beta-batch-10/`, **0** outside `docs/`. |
| The run's tree and the push tip | The run was on `fd28682c`. `fd28682c..78c447d6` differs in **0** non-docs files, and `88c88611..78c447d6` likewise. So the strict run's tree equals the push tip's code. |

## The item
| Item | Verifier, verdict | Verified at | Merged tip | Batch merge |
|---|---|---|---|---|
| H-127 what a student is sent of a saved grading result (MEDIUM), with H-128 a failed second opinion saves a code (LOW) | 1a, VERIFIED-WITH-NOTES | `d027ac91` (first runs at `f43e0f23`; two tests added between them, covered by my second runs at `1d6824e7`) | `a2e5ddc7` | `88c88611` |

After my verified tip the item's line holds one commit, docs only: my record with its ten files (`a2e5ddc7`).

## The mutation battery is against its module's last test change (rule 17's addendum)
| Item | Last change to its tests | Battery |
|---|---|---|
| H-127, H-128 | The guard module `a7216ae3`; the formatter and error-code test modules before it; the routes module `5ca8f909` and then `1d6824e7` (two tests added) | 32 of 32 at `bbd2b53d`, which holds `a7216ae3`. After `5ca8f909` the nine mutants on the projection module ran again at `8610d16e` (two of them new); the other 25 are on files that commit pair does not change. The two tests of `1d6824e7` are aimed at by my mutant Y14, run at `1d6824e7` and killed by exactly those two. |

## The credential pattern scan (widened form; every value masked)
I ran 0b's tool myself on `fd28682c` and on `8bbf44f9`, the same version on both (it opens `.xz` since today, row H-136), listing every row, one after the other in 0b's slot, and compared the two. The four files `78c447d6` adds I checked one by one.
- **URLs with a password part:** none added, none removed.
- **One value written two ways** (plain and percent-encoded): 0 groups at the tip.
- **Assignment forms, new against the base:**
  - Four literal lines in two of the item's test modules: the password of the test accounts they create. By a salted digest, without printing it, it is the same string as the repository's standard fixture password, which is in 185 test files at the base.
  - Two lines in the item's gzipped regression log: a test's made-up host name in a "blocked unsafe fetch" warning, as in every full-run log.
- **The run's log.** The tool skips lines longer than 4000 characters without saying so (row H-137). The raw log has none, and I also scanned it with my own masked scan, percent-decoded as well: **0** URL-form matches; the assignment-form matches are traceback code, test descriptions and the two warnings above, the same names and counts as in the earlier batches' logs.
- **0b's record, the summary and the script copy:** no URL form and no assignment form.
- **This record** holds no URL with anything in the password position and no assignment form.

**Limits.** The scan is by pattern: it finds the shapes above and nothing else, and it reads the tree at the tip, not the history.

## Gate 10 (0b's strict full run; one per batch, rule 15, not repeated)
| Tip | Result |
|---|---|
| `fd28682c` (the final code tree) | **Ran 5812 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR. 12G, `--parallel 4`, `--verbosity 2`; whole-repo mypy and `makemigrations --check` clean first (0b's `docs/evidence/beta-batch-10/FULL_SUITE.md`, `78c447d6`) |

I read the raw log myself (`~/Documents/Projects/GAP-0b-runs/strict_b10.log`, 8,032,907 bytes; its sha256 prefix `674586d28004c8ca` matches the record, and the committed `strict_b10.log.xz` unpacks to the same):
- **The result line** is `Ran 5812 tests in 395.574s`, `OK (skipped=28)`. The log holds exactly one "Ran" line and one result line; after them come only a blank line and the five "Destroying test database" lines. No line ends in FAIL or ERROR, there is no FAIL or ERROR header, and no expected failure or unexpected success. No `BlockingIOError`, no `OutputNotRead`, no fatal interpreter error.
- **The count checks out exactly, by test id:** batch 9 ran 5755. This log has 5812 distinct test ids; every one of batch 9's is among them, and **57** are new. 5755 + 57 = **5812**.
- **Where the 57 are:** the item's four test modules, each in full: `students.tests_student_feedback_routes` 31, `AutoGrader.tests_student_feedback_guard` 14, `students.tests_formatter_input` 6, `ai_processor.tests_second_opinion_error_code` 6.
- **Per-app counts** (my own count from the ids) match 0b's record: ai_processor 823, assignments 663, AutoGrader 587, billing 2109, classrooms 401, dashboard 270, students 305, users 654. They sum to 5812.
- **The skips** are 28, by the reasons printed: 12 real AI calls, 9 load tests, 4 network, 1 Redis, and 2 tests that cannot fork inside a parallel worker; the same as batch 9's. None of the batch's new tests is skipped, and no test was skipped for want of Chromium.
- **What this run adds to the item's own runs.** It is the first run of the whole of billing, users and classrooms on the item's code, and the first with the guard in a full parallel run. The item's own regression covered assignments, dashboard, students and ai_processor.
- **Rule 18 form.** The script copy shows the run's output going straight to the log file with stdin from `/dev/null`, nothing piped; rules 12, 13 and 16, the lock and the bytecode setting are in the command. The summary reports the watchdog never fired, exit 0, no suspend during the run, and the load at the suite's start (2.94) and end (4.57).
- **It was the only strict run of batch 10, on the final code tree, and it passed.**

**Not done by me:**
- I did not match each test's result line one by one. The result line and the absence of any FAIL or ERROR are what I rely on.
- I did not run the commit hooks over the batch range again. I ran them over the item's range, twice; the full run's whole-repo mypy passed.
- I did not read the new backlog rows' wording against their subjects, beyond that the rows are there once each.

## Notes (not blocking)
**N1 (four points for the package, in plain words; from my H-127 record).**
1. **What students no longer receive.** A student's assignment page, dashboard list and submission list no longer carry what is written for the teacher: the second grader's marks and reasons, the review flags, the notes on the level chosen, the advice to the teacher. The "formatted grade" on the student's own submission page now shows only its sections written for the student. A failed second opinion no longer saves the error's own text, which could hold a teacher's credit balance.
2. **What a student can still read.** A sentence written to the student can still restate a review flag, because the AI that words the result is still given the flags and told to surface them (row H-131, a product decision). Papers worded before this change may restate the second opinion in such a sentence; old rows are not rewritten. And the rule that a student must not be able to tell a grade exists before its release is **not** met by this batch (rows H-133 and H-130).
3. **What the frontend and operators should expect.**
   - Answers cached before the deployment are still served for up to 15 minutes (the student dashboard) or 5 (the submission pages), unless the deployment clears the cache.
   - A student page that asks the submission list for the review-queue filters or ordering is now refused (403). Nobody has read the frontend for such a request.
   - A stored formatted grade that is not in the expected form is now shown to a student as nothing, where it used to be shown as stored. Whether any stored paper is like that is not known: nobody has looked at stored papers.
   - A teacher now sees a short code in place of a failed second opinion's error text.
4. **One student route still answers in the teacher's shape.** The answer upload responds with the teacher's form of the paper. It is safe today only because an upload is refused once a paper is graded, so the paper it returns is always ungraded. It was not changed, because that would change what the frontend receives.

**N2 (nothing was observed on a live or staging service).** Everything about this item is from the code and from test runs on one machine, by the author and by me.

**N3 (the run's log is `.xz`).** A reader needs `xz -dc`. It unpacks to the listed checksum.

**N4 (rollback to beta 8bbf44f9, code only; rule 11).**
- No migration and no new column: **the rollback section needs no SET DEFAULT statement.** No schema, stored data, Beat entry or Redis key to undo.
- After a rollback the three student routes and the formatted grade return what they returned before, and a failed second opinion saves its text again. A second opinion that failed while this batch was live keeps its code.
- This is my reading of the code, not a tested rollback.

**N5 (open rows named in this record; none blocks).** I checked each against the backlog at the tip: H-126 open, H-130 in progress, H-131 waits for a decision, H-133 decided and not started, H-134 waits for a decision, H-135 not scheduled, H-137 not started. H-129 is closed by founder decision and H-136 is fixed in the tool.

Working files: `~/Documents/Projects/GAP-1a-scratch/gate1-b10/` (`check.sh`, `check_fd28682c.txt`, `check_78c447d6.txt`, `scan_all_fd28682c.txt`, `scan_all_8bbf44f9.txt`, `b10u`); `gate1-b9/b9u`; `gate1-b8/rawscan.py`; `gate1-b7/same_value.py`.
