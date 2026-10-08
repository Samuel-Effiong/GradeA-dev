# Verification: H-130, a student does not learn of a grade before release: the course final grade and the answer document (d5)

- **Branch:** task/h130-student-final-grade-released-only at **7d0eff4c**, on task/beta-batch-10 78c447d6 (H-127 merged). The code and tests tip is 9a9d6cb2; 7d0eff4c adds only docs/evidence/h130-student-final-grade-released-only/.
- **The change:** production code in six files (`classrooms/final_grade.py` new, `classrooms/serializers.py`, `classrooms/signals.py`, `students/services.py`, `students/serializers.py`, `assignments/serializers.py`; `students/views.py` comment lines only), two new test modules, one changed test module, a mutation runner. No migration, no settings change.
- **The rule it is tested against (founder, 2026-10-06):** a student should not know a grade exists before the teacher releases it.
- **Verifier:** v2 (independent), 2026-10-06. Three pre-reads, one line-by-line read of part B's tests ordered by the SM, and two slots from 0b: 17:22:30 to 17:24:22 WAT (load 4.49 at the start, 10.21 at the end) and a repeat for evidence, 17:27:47 to 17:29:07 (see "v2's runs"). Nothing in them is timed.
- **Verdict:** **VERIFIED-WITH-NOTES**

## The founder's rule, measured
v2 read the four student routes that carry the answer document or the course figure (the student's submission, the assignment, the course enrolment, the course list), whole answers, before grading and again after grading with nothing released, and compared the whole answers key by key (probe D1; the student is enrolled and the submission was made by the upload engine).

| What the student reads | Keys that differ after grading |
|---|---|
| The student's own submission | **one: `remaining_attempts`** |
| The assignment, with the student's document | none |
| The course enrolment (detail) | none |
| The course list | none |

- **The answer document, the course final grade and its letter are the same before and after grading on all four.** That is this row.
- **The one key that moves is not this row's:** remaining attempts is a per-submission signal and belongs to the Security Engineer's rows (N3).
- Not read by this comparison: the student's lists of submissions and of assignments, whose serializers carry neither the document nor the course figure (their per-submission signals are the Security Engineer's rows), and the task-status routes, which v2 did not read at all.
- The same holds for a student made the production way (D8), after a second attempt (D2), and the upload route gives back no grade (D9).

## The two defects and the change, as read
- **A, the course final grade.** The stored `StudentCourse.final_grade` counts every graded submission, released or not, and `/student-course` gave it and its letter to the student. Now a reader who is not a teacher gets the figure that released submissions alone imply, worked out in Python from the rows the view already loads, through the same arithmetic as the stored figure (`final_grade_from`). The stored value stays the staff figure.
- **B, the answer document.** Grading rebuilds the stored document with "Graded At" and "Score" filled in, and the student's two read routes returned the stored text. Now, until release, a student always reads the document rebuilt from the row in its ungraded form; after release, the stored one. Nothing stored is rewritten and staff read what they read before.
- **Why "always":** the first version rebuilt only once the row was graded and printed the score as "Not graded yet". A real submitted row has the score column's default, zero, which the builder prints as an empty value, so a real student's document would have changed at grading. v2's first pre-read found this by reading. With the rebuild before grading as well as after, grading changes nothing the student reads.
- **The rows the student's figure counts** are the stored figure's (a grading time, a score, points above zero from the stored maximum or else the assignment's) plus "released". A stored maximum of zero is not replaced, as in the stored figure.
- **A student cannot sort or filter the enrolment list by the stored figure:** the viewset sets no filter backend and the settings name no default one.
- **Rule 14:** the mocks in the new tests return real values (a dict for the extraction, a string for the prepared content).

## What v2's reads found, and where each point stands at 7d0eff4c
| Point | At 7d0eff4c |
|---|---|
| Pre-read 1, P1: the fixture's submitted row had no score; production's has the default zero, so byte-identity could be green in tests and false in production | **Fixed by a change of design** (always rebuild; the ungraded form prints the column's default). The fixture's row is made by the upload engine |
| P2: a rebuild shows the assignment's current title, due date and the student's current name | **A stated cost, pinned by tests.** No longer a grading signal: a rename shows when it happens, before grading as after |
| P3: the cached paths after "release all" | **Tests added** (the document on both routes; the course list) |
| P4: one arithmetic needs one choice of rows | **A table test added,** through the student route |
| P5: a final grade set by hand on the enrolment never reaches the student | **A stated behaviour change** (it cannot be told apart from the computed value); the SM logged a separate row |
| The line-by-line read of part B's tests (SM's order, at 5d195a9e): the guard's dictionary called the staff serializer "staff only", but the student's own upload route answers with it | **Labels corrected; two tests through the upload route added.** The route itself is not changed in this row (SM's ruling; a new row for the Security Engineer). See N1 |
| Two suggestions: a comment on the read route's cache key said the opposite of the code after this row; the fixture's assignment had no due date | **Both taken;** a test now holds the link between a saved rename and the cache refresh |

## Four faults in part B's tests, three found by runs
The evidence records them; v2 confirms each from the kept logs and the commits.
1. The fixture's student was PENDING, and the assignment route answers 404 to a pending student, so those tests reached no code. Found by d5's first early chain.
2. A guard test passed the class source through `inspect.cleandoc` before parsing it. Found by the same chain.
3. An assertion took a document rebuilt on a read to equal the one grading stored; the score prints as "7.0" from memory and "7.00" from the database. Found by d5's second chain. Existing behaviour, the teacher's document only (row H-139).
4. The guard's audience labels. Found by v2's line-by-line read.

**v2's part in this:** v2's three pre-reads did not see faults 1 to 3. v2's own probe reused the PENDING fixture and would have failed the same way. After the SM's order v2 read every test against the code, rebuilt its probe (status and key checked before content on every read; a student made the production way), and found fault 4.

## d5's gates, read by v2 from the committed raw logs (not repeated, rule 15)
| Gate at 9a9d6cb2 | The raw log |
|---|---|
| (r1) part A's module at the red commit 5e08d9ca | Ran 14 tests, FAILED: the 9 named tests |
| (r2) the tip's part B tests on the first fix 564a903c | Ran 25 tests, FAILED: the 10 named tests |
| (a) 33 labels: the changed modules, H-124's and H-127's guards, H-127's two test modules, the repo-wide guard list | Ran 464 tests in 170.279s, OK |
| (b) 18 mutants | baseline OK; 18 of 18 killed; every failing set equals the list written beforehand |
| (c) classrooms, students, assignments, `--parallel 2` | Ran 1413 tests in 170.395s, OK (skipped=14, all opt-in) |

- **Nothing but evidence after 9a9d6cb2.** The battery and (c) are on the final code and tests.
- **H-127 edits the same two student serializers.** v2 read its hunks: none touches the document or the figure.
- **Credential shapes** in the branch's files, archives opened (87 texts): no URL with anything in the password position, plain or encoded.

## v2's runs at 7d0eff4c (17:22:30 to 17:24:22, and a repeat for evidence 17:27:47 to 17:29:07)
Expectations were written in the runner and the probe before any run of H-130; 0b read the script, the runner and the probe before the grant.

| Step | Result |
|---|---|
| Baseline: d5's two modules, the changed zero-score module, v2's nine probes | Ran 66 tests in 31.241s, OK |
| V1 the first design back in the helper, against d5's tests | **KILLED.** Ran 25 tests, FAILED (failures=4): the two named tests (the rename test and the half-graded test), and with them d5's saved-rename test and the rename read on the assignment route |
| V1 against v2's probe | **KILLED.** Ran 9 tests, FAILED (failures=2): exactly D3 and D4 |
| V2 a stored maximum of zero replaced, against d5's part A | **KILLED.** Ran 19 tests, FAILED (failures=1): exactly `test_the_student_route_chooses_rows_as_the_stored_figure_does` |
| V3 the assignment route rebuilds only once graded, against d5's tests | **KILLED.** Ran 25 tests, FAILED (failures=2): exactly the source guard and `test_the_same_on_the_students_view_of_the_assignment` |
| V3 against v2's probe | **KILLED.** Ran 9 tests, FAILED (failures=2): exactly D3 and D4 |

What v2's probes read, each through the real route, status and key first:
- **D1, the whole payload** of the four student reads before and after grading, nothing released. On the student's submission page exactly one key differs, `remaining_attempts` (a per-submission signal; N3). On the assignment page, the course detail and the course list nothing differs. The document, the final grade and its letter are the same before and after on all four.
- **D2, a second attempt, then grading:** the student reads exactly what the second upload stored, before and after.
- **D3, D4:** a rename, a new due date and a new name show on both routes when they happen and not when grading happens.
- **D5, the teacher** before release: the stored document, the stored final grade and its letter, on the list and the detail.
- **D6, the student's course list:** 6 queries with one enrolment and 6 with four enrolments and nine more released submissions. No query per enrolment.
- **D7, a student's raw-text edit** on a graded, unreleased row: refused, 409, the body's only key is "error".
- **D8, a student made the production way** (invited, then activated by the function every login calls, then an upload through the engine, the assignment with a due date): grading changes nothing on either student route and the course figure stays empty.
- **D9, the upload route:** two attempts each answer 201 with the stored document and nothing in the grade keys; after grading a third is refused 409 with only "error", and both student reads are what they were. The answer has 30 keys, the teacher's field set (`score`, `score_percentage`, `formatted_grade`, `grade_status`, the review fields among them), all empty for the ungraded row (N1).

**A fault in v2's own evidence, and the repeat.** The first runner wrote both judgements of a mutant to one file name, so for V1 and V3 the raw output of the judgement against d5's tests was overwritten by the probe judgement's. Neither v2 nor 0b saw it when reading the runner. What the first run keeps for those two is the runner's summary line (its "Ran" line, its result line and the failing test names), in `runs/h130_7d0eff4c_mutants.log`; the table above is from those lines and from the kept files. v2 reported it, and the SM ruled a repeat of V1 and V3 only, both judgements, with one line of the runner changed so that each judgement keeps its own file. It is a repeat for evidence, not a second attempt at a result: the expected failing sets were written to a file before the grant, as the first run's exact sets.

| Repeat (17:27:47 to 17:29:07) | Result |
|---|---|
| V1 against d5's tests | **KILLED.** Ran 25 tests in 9.584s, FAILED (failures=4): the same four tests as the first run |
| V1 against v2's probe | **KILLED.** Ran 9 tests in 4.899s, FAILED (failures=2): D3 and D4 |
| V3 against d5's tests | **KILLED.** Ran 25 tests in 6.830s, FAILED (failures=2): the source guard and the rename read on the assignment route |
| V3 against v2's probe | **KILLED.** Ran 9 tests in 3.762s, FAILED (failures=2): D3 and D4 |

- All four sets equal the first run's and the file written beforehand. Each judgement now has its own raw file with its "Ran" line, its result line and its failure blocks; v2 read all four. Load 3.39 at the start and 9.19 at the end.
- One correction: v2's release message for the first run said V1 failed three of d5's tests. It failed four, as the table says.

- **Rules 16, 13, 12** wrap both steps. **Rule 17:** `PYTHONDONTWRITEBYTECODE=1` and `python -B`; `__pycache__` of the three mutated packages deleted before the baseline, before each mutant and after each restore; every restore equals the commit's blob by sha256. **Rule 18:** every inner run wrote straight to its own file with stdin from the null device.
- **Load:** 1-minute load 4.49 at the baseline's start, 12.62 at its end and the mutants' start, 10.21 at the end. Nothing in the run is judged by the clock. No other test run of the team's ran beside it.
- **Left behind:** the database `test_vf2_s1` (`--keepdb`), dropped by 0b after each run. The probe copy was removed and the checkout is the frozen tip again.

## Notes
- **N1 (the upload answer).** The student's own upload route answers with the staff serializer, stored document included. No grade reaches a student by it: the upload refuses a row that is graded or being graded, under a row lock, and d5's new test and v2's D9 hold that. The safety rests on that refusal, not on the serializer, until the Security Engineer's row changes the route.
- **N2 (what a student sees differently, for the package and the frontend).** Before release the document's header follows the assignment's current title and due date and the student's current name. A document stored by an older version of the builder is read in today's form until release. A final grade set by hand on the enrolment is never shown to the student; the student's figure is the one released work implies, and it is empty until something is released.
- **N3 (not this row's).** Per-submission signals (remaining attempts, grading state, the "already been graded" refusal, list filters) are H-127's and the Security Engineer's rows. A released grade of zero prints an empty Score line in the document (existing). The "7.0" and "7.00" printing is H-139.
- **N3a (a later row will change one assertion).** The Security Engineer's H-133 adds a stable code beside the refusal sentence, so the 409 body of a refused upload gains a second key. d5's test and v2's D9 both require the single key "error" at 7d0eff4c, which is right for this tip; H-133 changes d5's assertion in its own branch after the merge (agreed by d5 and the SM, told to v2 by the Security Engineer on 2026-10-06).
- **N4 (the guard's reach).** The scan of serializers reads four modules and explicit field lists. All seven serializers of a submission are in one of them with explicit lists today. A serializer with `fields = "__all__"`, or in another module, would not be seen.
- **N5 (production).** d5's sentence about `origin/main` is by reading only; v2 did not check it.

Files: `~/Documents/Projects/GAP-v2-handover/` `tests_vf2_h130_probe.py`; first run: `vf_h130_mutants.py`, `vf_h130_run.sh`, `runs/h130_7d0eff4c_baseline.log`, `runs/h130_7d0eff4c_mutants.log`, `runs/h130_7d0eff4c.status`, `runs/h130_7d0eff4c_mutant_logs.tar.gz` (the baseline's whole output and the three kept inner outputs); repeat: `vf_h130_mutants_r2.py`, `vf_h130_run_r2.sh`, `runs/h130_7d0eff4c_r2_expected.txt`, `runs/h130_7d0eff4c_r2_mutants.log`, `runs/h130_7d0eff4c_r2.status`, `runs/h130_7d0eff4c_r2_mutant_logs.tar.gz` (four inner outputs, one per judgement).
