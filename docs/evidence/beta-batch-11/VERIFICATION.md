# Verification: beta-batch-11, Gate 1 (batch level) @ 4b0ccfa9

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Base:** beta = origin/beta `3457df56` (batch 10). **Tip:** `4b0ccfa9`; the code tip is `a716c862` (the merge of H-130's tests-only follow-up). `76f713e3` adds only backlog changes, and `4b0ccfa9` only 0b's record of the two full runs with their logs, summaries and script (both docs). **Date:** 2026-10-07.

**Verdict: VERIFIED.** Batch 11 is exactly its three verified items and one verified tests-only follow-up, merged cleanly. Its first strict full run was red on one test; that is told below and kept in the record. The second strict full run passed on the tree the batch pushes. Nothing is required before the push. I found five points of wording in the package draft (N2); none changes a fact about the code.

I ran no test for this gate (rule 15). The checks are git and record checks, my own run of 0b's masked credential scan in 0b's slot, and my own reading of both raw full-run logs.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | origin/beta `3457df56` is an ancestor of `4b0ccfa9`: a fast-forward, 56 commits, 209 files, 19 of them outside `docs/`. |
| Evil merges | All **4** first-parent merges in `3457df56..4b0ccfa9` are **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree) and hold nothing that is on neither parent: `7944259e`, `be05f953`, `baf58b24`, `a716c862`. |
| Direct commits | Four, all docs only: `0f5fb51b`, `079ae209`, `76f713e3`, `4b0ccfa9`. |
| Each item is its verified commit plus docs only | See the table below. Each verified commit is an ancestor of what was merged, with **0** non-docs commits after it on the item's own line. |
| My records in the tree, byte for byte | H-133: the record, the two probe files, the mutants, the two notes written before the runs and the eight logs match my copies (14 files). Batch 10's Gate 1 record matches as pushed. |
| v2's records | Present in the tree for H-130, H-120 and the follow-up. I did not byte-compare them. |
| Migrations, models | **None.** Both runs report `makemigrations`: no changes detected. |
| Settings, beat schedule, requirements | None changed. No management command added or changed. |
| Production files | **Nine:** `assignments/serializers.py`, `assignments/tasks.py`, `classrooms/final_grade.py` (new), `classrooms/serializers.py`, `classrooms/signals.py`, `students/exceptions.py`, `students/serializers.py`, `students/services.py`, `students/views.py`. **One tooling file:** `scripts/check_migration_safety.py` (it names `AddField` and `db_default` because it judges migrations; it is not one). The other nine non-docs files are test modules. |
| The docs commits | `0f5fb51b`, `079ae209` and `76f713e3` each change `docs/HARDENING_BACKLOG.md` only. |
| The record commit `4b0ccfa9` | Six files, all in `docs/evidence/beta-batch-11/`, **0** outside it. |
| The second run's tree and the push tip | The second run was on `76f713e3`. `76f713e3..4b0ccfa9` differs in **0** non-docs files, and `a716c862..4b0ccfa9` likewise. So the passing run's tree equals the push tip's code. |
| Between the two runs | `079ae209..76f713e3` differs outside `docs/` in one file: `AutoGrader/tests_cache_bespoke_1114.py`. |

## The items
| Item | Verifier, verdict | Verified at | Merged tip | Batch merge |
|---|---|---|---|---|
| H-130 a student's final grade and answer document from released work only (MEDIUM) | v2, VERIFIED-WITH-NOTES | `7d0eff4c` | `b151d846` | `7944259e` |
| H-120 the migration safety check judges a changed column by what it was before (LOW; tooling) | v2, VERIFIED-WITH-NOTES | `5c48475a` | `e25c1579` | `be05f953` |
| H-133 before release a student is told and shown nothing that names grading, as far as its row says (MEDIUM) | 1a, VERIFIED-WITH-NOTES | `b4a3b70e` | `d08f98d9` | `baf58b24` |
| H-130's follow-up: the submission-detail cache tests say what H-130 made true (tests only) | v2, VERIFIED-WITH-NOTES | `5c48c505` | `572b536b` | `a716c862` |

After my verified tip, H-133's line holds one commit, docs only: my record with its fourteen files (`d08f98d9`).

## Each mutation battery is against its module's last test change (rule 17's addendum)
| Item | Last change to its tests | Battery |
|---|---|---|
| H-130 | `9a9d6cb2` (one comment), after `5d195a9e` and `c20e9a4a` | v2's record: the battery is on the final code and tests, nothing but evidence after `9a9d6cb2`. I checked the commit order, not the battery. **One later change, in another item:** H-133's `2233be8a` changed one assertion of an H-130 test module; no H-130 mutant ran after it. The changed test ran in 0b's cross-side run, in H-133's gates and regression and in both full runs. |
| H-120 | `ea5d45bf` (a second table) | The 13 mutants again at `ea5d45bf`, the second chain (v2's record; the commit order checked by me). |
| H-133 | `a9ca796b` (the new module); the guard and the two older modules at `846e7a6e` | The nine mutants on `students/serializers.py` at `a9ca796b`; twelve at `e85e2ae0` and the rest at `6fc161e2`, on files unchanged since, after which the module only gained tests. My six ran at the final tip `b4a3b70e`. |
| H-130's follow-up | `79850d19` | Three mutants at `d87a56f6`, after it (v2's record; the commit order checked by me). |

## The credential pattern scan (widened form; every value masked)
I ran 0b's tool myself on the frozen tip `4b0ccfa9` and on `3457df56`, the same version on both, listing every row, one after the other in 0b's slot (12:50:22 to 12:51:33), and compared the two. The tip's scan covers the record commit's six files, the two `.xz` logs opened.
- **URLs with a password part:** none added, none removed (the same five rows on both, placeholders in example and workflow files).
- **One value written two ways** (plain and percent-encoded): 0 groups at the tip.
- **Literal assignment lines in test files:** 629 at the base and 629 at the tip. The batch's test modules add none.
- **Assignment forms new against the base, literal in shape:** I read each with its value masked.
  - Two lines in each of the two full-run logs: a test's made-up host name in a "blocked unsafe fetch" warning, as in every full-run log.
  - Three matches in H-120's evidence: a sentence that quotes the names the pattern itself catches.
  - One in v2's mutant file for the follow-up: the words "predicted to pass" in a comment.
- **The other new rows** are not literal in shape (code, variables, placeholder words) and sit in the items' evidence files, their gzipped regression logs and the two full-run logs.
- **The raw logs, by my own masked scan as well,** percent-decoded too: **0** URL-form matches in either; the assignment-form matches are traceback code, test descriptions and the two warnings above, the same names and counts in both logs. Neither log has a line over 4000 characters, which the tool would skip without saying so (row H-137).
- **This record** holds no URL with anything in the password position and no assignment form.

**Limits.** The scan is by pattern: it finds the shapes above and nothing else, and it reads the tree at the tip, not the history. The tool counts names that hold only the word "key" (cache keys, primary keys) without listing them: 1012 such matches at the base, 1055 at the tip. I did not read those.

## Gate 10 (0b's strict full runs; rule 15, not repeated by me)
| Run | Tip | Result |
|---|---|---|
| First | `079ae209` | **Ran 5913 tests, FAILED (failures=1, skipped=28)**, exit 1. Not run again. |
| Second | `76f713e3` (the final code tree) | **Ran 5915 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR. |

Both: 12G, `--parallel 4`, `--verbosity 2`; whole-repo mypy and `makemigrations --check` clean first (0b's `docs/evidence/beta-batch-11/FULL_SUITE.md`, `4b0ccfa9`).

**The red run, as I read it** (`~/Documents/Projects/GAP-0b-runs/strict_b11.log`, 8,062,239 bytes; sha256 prefix `8dc6f1db9a0c60f3`, and the committed `.xz` unpacks to the same):
- One "Ran" line, one result line, one FAIL header and one line ending in FAIL: `AutoGrader.tests_cache_bespoke_1114.SubmissionDetailFreshnessTests.test_a_teachers_assignment_edit_does_NOT_change_this_payload`. No ERROR.
- The two answers the test compared differ in the assignment's title inside the student's answer document. That is H-130's change: until release the document is built from the assignment as it is now. The test pinned the behaviour H-130 replaced. Nothing of H-133's or H-120's is in the failure.
- **Why no gate saw it before:** H-130's regression ran classrooms, students and assignments; this module is in the AutoGrader app. Team rule 20, written the same day, now puts that module in the regression of any change to what a serializer or a cached route returns, and keeps a red full run in the record.
- **The cure is tests only:** three tests replace the one (`79850d19`), verified by v2, with a regression of AutoGrader, students and assignments (1639 OK, skipped=16, cited). No production line changed between the two runs.

**The green run, as I read it** (`strict_b11b.log`, 8,065,545 bytes; sha256 prefix `0ca845c931d0c92c`, and the committed `.xz` unpacks to the same):
- **The result line** is `Ran 5915 tests in 403.158s`, `OK (skipped=28)`. The log holds exactly one "Ran" line and one result line; after them come only the five "Destroying test database" lines. No line ends in FAIL or ERROR, there is no FAIL or ERROR header, and no expected failure or unexpected success. No `BlockingIOError`, no `OutputNotRead`, no fatal interpreter error.
- **The count checks out exactly, by test id:** batch 10 ran 5812. This log has 5915 distinct test ids. **One** of batch 10's is gone, the cache test that was replaced, and **104** are new. 5812 - 1 + 104 = **5915**.
- **Where the 104 are:** `students.tests_no_grade_tell_before_release` 37 and one more in `AutoGrader.tests_student_feedback_guard` (H-133's 38); `students.tests_answer_document_before_release` 25 and `classrooms.tests_student_final_grade_released_only` 19 (H-130's 44); `AutoGrader.tests_migration_safety_check` 19 (H-120); `AutoGrader.tests_cache_bespoke_1114` 3 (the follow-up).
- **Against the red run, by id:** one id is only in the red run (the replaced test) and three only in the green (its replacements). The other 5912 are the same.
- **Per-app counts** (my own count from the ids) match 0b's record for both runs. Green: ai_processor 823, assignments 663, AutoGrader 609, billing 2109, classrooms 420, dashboard 270, students 367, users 654; they sum to 5915. Red: the same but AutoGrader 607.
- **The skips** are 28, by the reasons printed: 12 real AI calls, 9 load tests, 4 network, 1 Redis, and 2 tests that cannot fork inside a parallel worker; the same as batch 10's. None of the batch's new tests is skipped, and no test was skipped for want of Chromium.
- **Rule 18 form.** The script copy is byte-identical to the file 0b ran and differs from batch 10's in the branch name only (three lines). Output went straight to the log file with stdin from `/dev/null`, nothing piped. The summaries report the watchdog never fired, no suspend, and the load at the suite's start and end (2.88 and 6.31; 2.64 and 4.14).
- **The second was the only strict run of the final code tree, and it passed.**

**Not done by me:**
- I did not match each test's result line one by one. The result line and the absence of any FAIL or ERROR are what I rely on.
- I did not run the commit hooks over the batch range. I ran them over H-133's range; both full runs' whole-repo mypy passed.
- I did not check v2's three records beyond that they are in the tree and say what the tables above cite, nor 0b's cross-side counts in the package.
- I did not read the new backlog rows' wording against their subjects, except H-133's row and the part of H-130's row that tells the red run.

## Notes (not blocking)
**N1 (H-133's row and the package say what was closed, not "nothing").** By the SM's ruling on my H-133 note N1. I read both at the tip.
- **The row** (`docs/HARDENING_BACKLOG.md`): "on the student's list the grading state reads IDLE until release and the three scheduling fields are empty (the student's detail page carries none of the four); on the list and the detail page `max_points` is the assignment's total as on a submitted paper". That is the corrected sentence (`079ae209`; the first version put the four fields on the detail page too). It names the answer to an accepted upload or edit on an ungraded paper as open until H-141, and the remaining-attempts tell.
- **The package draft** (as it stood at 12:50:13) carries the same sentence in its second opening point, says in words that this does NOT mean a student is shown nothing, and names both open parts.
- **The merge title of `baf58b24`** still says "list, detail"; a merge title cannot be changed and the row is what a reader is sent to.

**N2 (five points of wording in the package draft; told to 0b).**
1. "No field is added, removed or renamed" is not exact: every refusal of an upload or edit gains `code`, for a teacher as well as a student (the draft's next line says so).
2. Under "What changes for users", the list's maximum "is the assignment's total" lacks "until release".
3. "22 of 22 mutants ... and 9 of 9 on the `max_points` delta" reads as 31; six of the nine are six of the 22 run again on the final file. 25 different mutants.
4. The open answer is "read in the code by the Security Engineer, not run": I read it too, and did not run it either.
5. "Teachers: nothing changes in what they read or are told" is true of the words; their refusal bodies gain `code` (point 1).

**N3 (what the batch does not close, for the package).**
- **H-141, open:** the answer to an accepted upload or edit on a paper that is not graded can show a student the teacher's schedule and a failed or stale grading state. Read, not run.
- **`remaining_attempts`** drops to 0 at grading, by the founder's choice.
- **H-149:** a student whose enrolment row was deleted could read an old title for up to five minutes; no production code deletes such a row (v2's and d5's reading, cited).
- **Not covered by any test:** a second AI grading of a paper already graded (from my H-133 record). **Nobody has read the frontend.**

**N4 (the machine during the full runs; from 0b's record).** The other project's single-process run overlapped the second run's first two minutes. Load at the suite's start 2.64. Nothing failed and the result is the one written beforehand, so I see no effect.

**N5 (nothing was observed on a live or staging service).** Everything about this batch is from the code and from test runs on one machine.

**N6 (the runs' logs are `.xz`).** A reader needs `xz -dc`. Each unpacks to the listed checksum.

**N7 (rollback to beta 3457df56, code only; rule 11).**
- No migration and no new column: **the rollback section needs no SET DEFAULT statement.** No schema, stored data, Beat entry or Redis key to undo.
- After a rollback a student reads the stored final grade and the stored answer document again, is told the old refusal sentences without codes, and the student's list shows the real grading state and schedule. A task row refused while this batch was live keeps its neutral sentence and its code.
- This is my reading of the code, not a tested rollback.

**N8 (open rows named in this record; none blocks).** I checked each against the backlog at the tip: H-141 in progress (batch 12); H-149 not scheduled; H-137 not started; H-134 and H-131 wait for a decision.

Working files: `~/Documents/Projects/GAP-1a-scratch/gate1-b11/` (`check.sh`, `check_76f713e3.txt`, `check_4b0ccfa9.txt`, `scan_all_4b0ccfa9.txt`, `scan_all_3457df56.txt`, `ids.sh`, `b11u`, `b11red_u`, `b10s`, `new.ids`); `gate1-b8/rawscan.py`; `gate1-b7/masked_lines.py`.
