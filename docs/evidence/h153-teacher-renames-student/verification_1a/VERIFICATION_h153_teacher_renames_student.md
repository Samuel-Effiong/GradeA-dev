# Verification: H-153: a teacher renames a student @ 05c83b0d

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-07.
**Branch:** `task/h153-teacher-renames-student` @ **05c83b0d**, the final tip. Stacked on H-152 (its final tip `8b23502b`, carried in by 0b's merge `02ab42c6`); the two travel together as batch 12b. H-152 is Verifier 2's and is not verified here. No model, no migration.

H-153's own change against H-152's tip is three files: `users/views.py` (the action `student_name`), `users/serializers.py` (`StudentNameSerializer`) and `users/tests_teacher_renames_student.py` (28 tests). They are unchanged from `5db450d2`, where the fresh gate ran, to `05c83b0d`. The evidence is in `docs/evidence/h153-teacher-renames-student/`.

**I ran at 05c83b0d** (2026-10-07, 16:54:05 to 16:55:44 WAT; hooks ended 16:56:52) on 0b's GRANT of 16:53:44, from my own detached scratch checkout, serial, each run once, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, `--settings=settings_worktree` (mutants: `settings_worktree_mut`), every run's output straight to its own file with stdin from `/dev/null`. One step of the Hardening Engineer's chain ran beside mine; the one-minute load at each start is in each log (2.72 to 3.37). No timeout, no kill.

Under rule 15 I cite ed's fresh gate (at `5db450d2`) and ed's regression (users and classrooms, serial, again at `02ab42c6` after the last base update) and repeat neither. Under rule 20 I cite ed's gate, which ran the AutoGrader cache modules; my own probes are on the cache.

**Verdict: VERIFIED-WITH-NOTES.** What the evidence lists under "What changes" is true on everything I read and drove. I found no defect. The author's first gate was red through a fault in the route; it is told plainly in the evidence and the hand-over, its logs are kept, and the fix is what the fresh gate and my runs ran on. The notes are what the row leaves and two things for the Senior Manager to know; none asks for a change before the merge.

## What changes (checked by reading the code at the tip)
- **A new route,** `PATCH users/<student id>/student-name`, with `first_name`, `last_name` (at least two letters each) and an optional `middle_name`. Nothing else can be sent through it.
- **Who may:** a teacher who can reach a course the student is currently in (enrolled or pending), and a super admin. Not a school admin, not the student, not a teacher whose student has withdrawn or completed.
- **What the answers tell:** an account the caller cannot already read is answered 404 whether it exists or not; 403 is said only about an account the caller can read anyway. The permission checks come before the form is read.
- **One exact name per course,** in every course the student has a place in: a clash in a course the caller teaches is refused and quotes the name; a clash in another course is refused with a sentence that quotes nothing.
- **A log line per rename,** with ids only.
- **The lock** is on the student's enrolment rows only (`select_for_update(of=("self",))`), with only the course joined.

## The first gate, as the author tells it and as I read it
- At `17febb89` the gate's modules step was red. From the committed log `modules_and_guards_RED_GATE_17febb89.txt.gz`, read by me: one Ran line, `Ran 522 tests`, `FAILED (failures=15)`. The route locked the enrolment rows through a join that may be empty (a course's session), which PostgreSQL refuses; every rename that passed the permission checks answered 500. The author's tests had been written before the route existed, so their first red had proved nothing about it.
- The fix is `5db450d2`; two tests for a course with no session came first (`c2c39b72`). The author's mutant M25 puts the first version's statement back and fails 17 named tests.
- **Said by the author and true as I read it:** no test isolates `of=("self",)` on its own (with only the course joined the lock is accepted without it); row H-166 is to be the guard. No test runs two renames at once.

## What I checked by reading, beside that
- **The order of the answers** in `student_name`: `get_object` (404 for what the caller cannot read), the caller's role (403), the account being a student's (400), a current place of the student with this teacher (403), then the form (400), then the clash check and the save inside one transaction.
- **What a rename refreshes.** `first_name`, `middle_name` and `last_name` are among the fields `users/signals.py` treats as seen by other users, so the save moves the student's own cache generation, each of the student's teachers' and their school admins'. The route saves through the model (`student.save(update_fields=...)`), which is what makes that happen. My probes ra and rb drive it.
- **Rule 20, the whole test tree.** I searched every test file for the users view, the new route's names, and for tests that take a census of routes or actions. The matches outside users and classrooms are the unrelated `student_name` field of submissions; I found no census that a new action of the users view would trip, and none outside the author's runs that names this route.
- **ed's raw logs, read by me from the commit.** `prefix_base_production_failing_5db450d2.txt.gz`: one Ran line, `Ran 28 tests`, `FAILED (errors=34)` (the route does not exist on the base's files; as the author says, it proves nothing test by test). `modules_and_guards_5db450d2.txt.gz`: one Ran line, `Ran 524 tests`, `OK`, 17 lines of `AutoGrader.tests_cache_bespoke_1114`. `regression_9d68c080.txt.gz`: `Ran 1150 tests`, `OK (skipped=4)`. `regression_02ab42c6.txt.gz`: one Ran line, `Ran 1160 tests`, `OK (skipped=4)`. `mutation_results_5db450d2.json`: 25 entries, 25 KILLED; 25 mutant logs.

## What I checked by running (at 05c83b0d)

The author's 28 tests and 25 mutants cover who may rename, what each refusal says, the clash rule and the log line. The author says plainly that what follows the account's save was not read further; the one test that reads the new name as another teacher clears the cache first. My probes read WITHOUT clearing it. The failing method set of each mutant was written beforehand (`h153_expected_kills.txt`, written 16:51:52, before any run). Rule 19: each probe was seen green on the tip and red under its mutant, and each mutant failed exactly the set written. Answers are read from `response.data`.

| Run | What it shows | Result |
|---|---|---|
| Baseline: my probe module (3), `users.tests_teacher_renames_student` (28) | green at the tip | `Ran 31 tests in 3.678s`, `OK` |
| Y1: the rename writes with a queryset update, so no save signal fires | **ra** and **rb** both depend on the save's signal | `Ran 3`, `FAILED (failures=3)`: ra (two sub-tests) and rb |
| Y2: a user's change does not move the generations of the user's teachers | **ra**: another teacher of the same student, whose course page and whose read of the student's account are already cached (a second read is shown to cost fewer queries), sees the new name on both at once | `Ran 3`, `FAILED (failures=2)`: ra, its two sub-tests, and no other |
| Y3: a user's change does not move the user's own generation | **rb**: the student's own cached course page (their own roster entry, H-147's) shows the new name at once | `Ran 3`, `FAILED (failures=1)`: rb |
| Y4: the rename keeps the stored middle name whatever is sent | **rc**: a middle name alone can be changed; it is stored and answered | `Ran 3`, `FAILED (failures=1)`: rc |

Every mutant log has "applied", "mutated sha differs: True", "restored_sha256_matches_commit_blob: True" and 0 tracked changes after the restore. Each log holds exactly one Ran line.

**Commit hooks** over `8b23502b..05c83b0d`: exit 0, 18 passed, 0 failed, 7 skipped (no files to check).

## Notes
- **N1. The neutral refusal is still a yes or no.** A teacher whose rename is refused with "This name cannot be used for this student." learns that someone of exactly that name has a place in another course their student is in. The sentence quotes nothing and the author's evidence names the cost. A teacher can try names one at a time; each successful try renames the student, so it is not a quiet way to list a class. I name it so that it is a decision.
- **N2. The rename locks the student's enrolment rows, not the courses.** The add-by-email locks the course and not the account (H-168). So a rename to a name and, at the same moment, an add or another rename that brings the same name into the same course are not shut out by either lock: two students of one exact name could end in one course, which the enrolment's own check then refuses on the next save of either row. Read in the code, not run; no test runs two requests at once. It belongs with H-168's cure.
- **N3. Not isolated by a test, as the author says:** `of=("self",)` on its own (H-166), and that the lock serialises two renames.
- **N4. A rename is recorded as a log line only.** Whether and how long the hosting service keeps that log nobody on the team knows. There is no record in the database on this line.
- **N5. Papers and documents:** a paper bearing the old name no longer matches after a rename (matching is unchanged and not tested here); a released or staff copy of an answer document keeps the name it was built with. Both are in the evidence.
- **N6. Who may not, by decision:** a teacher whose student has completed the course, and a school admin. A student with no current teacher can be renamed by a super admin only.
- **N7. The frontend.** The route is new; no page calls it yet. Neither the author nor I can read the frontend. Until a page offers it, a student who is already nameless and already enrolled stays nameless (H-148's note N8).
- **N8. H-152 is under this row** and is not verified by me. My runs were on the tip that holds H-152's final state.
- **N9. Not observed on a live or staging service**, by the author or by me.

## Credential check
My probe, mutant file, expected note and five logs, searched for a URL with anything in the password position and for assignment forms whose name contains PASS, PWD, SECRET, TOKEN or KEY, masked output only: 0 lines in each but one. The probe's line 36 assigns to a variable called `token` the result of a call (the same statement the author's test module uses to sign a test user in): a code expression, no literal value. No line over 4000 characters; no trailing whitespace. No archive among them.

## Files (in `~/Documents/Projects/GAP-1a-records/`, sha256 prefixes)
- `h153_probe_tests_vf1a_h153_probe.py` 4d5f484f17a7a67e
- `h153_mutants_Y.py` 0ea3ff314a72f9af
- `h153_expected_kills.txt` 0c4a6c74e2a3ab38
- `runs/h153_05c83b0d.log` 7c1f9f981df204d3
- `runs/h153_mutant_Y1_05c83b0d.log` 8bd0d81156395990
- `runs/h153_mutant_Y2_05c83b0d.log` 449e152c4eeb83f4
- `runs/h153_mutant_Y3_05c83b0d.log` c6e3bc906bdd7aca
- `runs/h153_mutant_Y4_05c83b0d.log` 23ddf24e38a3a357

The runner (`~/Documents/Projects/GAP-1a-scratch/h153_run.sh`, db1d123b702a05f9) and the comparison helper (`h153_expect.sh`, e22b66be3134b759) were read by 0b before the grant.
