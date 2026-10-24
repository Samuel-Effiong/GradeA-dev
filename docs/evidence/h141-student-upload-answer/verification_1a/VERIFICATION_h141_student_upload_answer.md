# Verification: H-141: a student's upload answer shows nothing of grading @ 44a23f98

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-07.
**Branch:** `task/h141-student-upload-answer` @ **44a23f98** (code and test tip `e8b75198`, carried by 0b's base update `c7ad6482` onto beta `d7143538`, batch 11 as pushed). For batch 12, its own row. No model, no migration. No file outside `docs/` differs between `c7ad6482` and `44a23f98`.

Commits: `f32b316f` red tests; `e8b75198` the change; `09829938` docs (rule 20's cache modules added to the gate, before any run); `c7ad6482` base update; `2148c8ae` and `44a23f98` docs only. The evidence is in `docs/evidence/h141-student-upload-answer/`.

**I ran at 44a23f98** (2026-10-07, 15:17:21 to 15:19:23 WAT; hooks ended 15:20:33) on 0b's GRANT of 15:16:46, from my own detached scratch checkout, serial, each run once, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, `--settings=settings_worktree` (mutants: `settings_worktree_mut`), every run's output straight to its own file with stdin from `/dev/null`. The machine was busy (the Security Engineer's H-152 gate and two runs of the other project); the one-minute load at each start is in each log (9.40 to 14.19). None of my runs has a wall-clock assertion. No timeout, no kill.

Under rule 15 I cite ed's gate (at `c7ad6482`) and ed's regression (students and assignments, serial, at `2148c8ae`) and repeat neither. Under rule 20 I cite ed's gate, which ran the AutoGrader cache modules.

**Verdict: VERIFIED-WITH-NOTES.** What the evidence lists under "What changes" is true on everything I read and drove. I found no defect. The notes are about the backlog row's wording, an old behaviour beside this row, and what the guard does not see; none asks for a change before the merge.

## What changes (checked by reading the code at the tip)
- **The student's upload route** (`upload_answers`, students only) answers with `StudentUploadAnswerSerializer`, not with the teacher's serializer. Thirty keys in the teacher's order. Fourteen are the student's own facts. Sixteen staff keys are constants (`SentAs`), which do not read the row: no score, no grading state but IDLE, no review field, no feedback, no scheduled run.
- **`max_points`** is the assignment's total; **`raw_input`** is the student's document (H-130's function).
- **Every serializer of a submission declares who it is for** (`audience`), and a new guard, `AutoGrader/tests_submission_audience_guard.py`, holds which action of the submissions view builds which, who can call it, that a student's action builds a staff serializer only in the else of a test for a student, and that every serializer a student's action builds is given the context.

## What I checked by reading
- **The fourteen keys that still read the row.** `is_published` is the one that could tell of grading. Both places that set it (`publish_grade` in `students/views.py`, the bulk release in `assignments/views.py`) require a grading time and a score, and an upload on a paper with a grading time is refused (`_check_submission_open`), so on every accepted upload it is false. `remaining_attempts` is H-133's function: it drops to 0 at grading (the founder's choice), which an accepted upload cannot show. `answers`, `submission_date` and the document are the upload's own.
- **The other actions a student can call.** `create` answers nothing (see N2). The queued upload and the queued edit answer with task ids; a search of `assignments/tasks.py` for every `return {` and every serializer call found ids, a status, a message and a code, and no serialized submission (I did not read the file line by line). The edit route (`partial_update`) answers with the list serializer and the request (probe pb drives it). `retrieve` gives a student the student's page serializer.
- **The other places that build the teacher's serializer** (`grade`, `update_grade`, `publish_grade`, `mark_reviewed`): each is teacher-only in `get_permissions` or on the action, as the guard's table says; the guard's `who_can_call` test holds the table against the view.
- **ed's raw logs, read by me from the commit.** `prefix_base_production_failing_c7ad6482.txt.gz`: one Ran line, `Ran 48 tests`, `FAILED (failures=31, errors=1)`. `modules_and_guards_c7ad6482.txt.gz`: one Ran line, `Ran 538 tests`, `OK`, 17 lines of `AutoGrader.tests_cache_bespoke_1114`. `regression_2148c8ae.txt.gz`: one Ran line, `Ran 1045 tests`, `OK (skipped=14)`. `mutation_results_c7ad6482.json`: 32 entries, 32 KILLED; 32 mutant logs.
- **The one difference ed reports** (a guard test written as red on the base that was green there, then seen red under two mutants; the Senior Manager ruled the gate stands): the evidence says it as it is, beside the untouched expectation. I add nothing to it.

## What I checked by running (at 44a23f98)

The author's 15 route tests and 32 mutants cover the upload answer key by key. My two probes look beside them; four further mutants are aimed at the guard's own scanner and census, whose tests the author named as never seen red. The failing method set of each mutant was written beforehand (`h141_expected_kills.txt`, written 15:15:16, before any run). Rule 19: each of my probes was seen green on the tip and red under its mutant; the failing method set of every mutant run is exactly the one written.

| Run | What it shows | Result |
|---|---|---|
| Baseline: my probe module (2), `students.tests_student_upload_answer` (15), `AutoGrader.tests_submission_audience_guard` (17) | green at the tip | `Ran 34 tests in 2.824s`, `OK` |
| W1: the constant field hands out its own value, not a copy | **pa**: one serializer answering twice does not hand out the same list; what a caller adds to the first answer's `question_breakdown` is not in the second | `Ran 2`, `FAILED (failures=1)`: pa |
| W2: the edit route builds its answer without the request | **pb**: a student's EDIT (PATCH), accepted through the route on a paper that is not graded but has a FAILED grading run and a scheduled one on its row, answers with state IDLE, no scheduled time, no task name; the row still holds them | `Ran 2`, `FAILED (failures=1)`: pb |
| G1: the guard's scanner says every serializer was given a context (the guard module alone) | four `ScannerSelfTest` tests can fail | `Ran 17`, `FAILED (failures=4)`: `test_a_serializer_built_without_a_context_is_seen`, `test_the_body_of_that_test_is_not_the_staff_branch`, `test_the_else_of_another_test_is_not_the_staff_branch`, `test_a_test_for_a_teacher_does_not_count` |
| G2: the scanner does not take the else of a test for a student as the staff branch | a fifth self-test can fail, and rule 3 notices | `Ran 17`, `FAILED (failures=2)`: `test_the_else_of_a_test_for_a_student_is_the_staff_branch`, `test_rule_3_a_student_action_builds_staff_only_in_the_staff_branch` |
| G5: the scanner says no serializer was given a context | the sixth self-test can fail, and rule 3 notices | `Ran 17`, `FAILED (failures=6)`, three methods: `test_a_context_keyword_is_seen`, `test_the_else_of_a_test_for_a_student_is_the_staff_branch`, `test_rule_3_a_student_action_gives_every_serializer_the_context` (four sub-tests) |
| G6: the census leaves out the teacher's detail serializer | the census test can fail | `Ran 17`, `FAILED (failures=7)`, three methods: `test_the_census_found_the_serializers`, `test_rule_1_the_serializers_are_for_whom_this_guard_was_told`, `test_rule_2_each_action_builds_the_serializers_named_and_no_other` (five sub-tests) |

G1, G2, G5 and G6 change the guard module itself (a test file), not production code: they show that its self-tests and its census test fail when the scanner or the census is wrong. With them the six `ScannerSelfTest` tests and `test_the_census_found_the_serializers`, the seven the author listed as never seen red, have each been seen red by name.

Every mutant log has "applied", "mutated sha differs: True", "restored_sha256_matches_commit_blob: True" and 0 tracked changes after the restore. Each log holds exactly one Ran line.

**Commit hooks** over `d7143538..44a23f98`: exit 0, 18 passed, 0 failed, 7 skipped (no files to check).

## Notes
- **N1. The backlog row's opening words are wider than the defect.** The row at the tip (`docs/HARDENING_BACKLOG.md`, H-141) says "the student's own upload and edit routes answer the student with the STAFF serializer, built without the request". That was true of the upload route only. The edit route answers with the list serializer and the request (the code's own comment dates that to H-127); the evidence says so, and my probe pb drives it on a paper with a failed and a scheduled run. When 0b closes the row it should say: the upload route; the edit route was already safe and is now tested for it.
- **N2. `create` on the submissions view raises `NotImplementedError`.** A student's POST to the collection is therefore answered with a server error, not a refusal. It is old, it sends nothing of a submission, and it is not this row's; the guard's table calls it "refuses everything". It deserves a line on the backlog (a 405 or a 400 would be the honest answer).
- **N3. What the guard does not see**, as its docstring says and as I read it: a response built by hand, a serializer built inside a helper the action calls, other views, and whether the `context=` given holds the request. Its tables are kept by hand.
- **N4. The copy in `SentAs` matters only where one serializer answers more than once** (a `many=True` answer, or a re-used instance): DRF gives each serializer instance its own copy of a declared field. The upload route builds one serializer per request. pa holds the property for the day that changes.
- **N5. The frontend.** The upload answer's `score` is null where it was a zero; the other staff keys are constants. Neither the author nor I can read the frontend. The evidence carries the line for whoever can.
- **N6. The teacher's serializer keeps its old student branches** (the evidence says so and why). No student route builds it, and the guard now holds that.
- **N7. Not observed on a live or staging service**, by the author or by me.

## Credential check
My probe, mutant file, expected note and seven logs, searched for a URL with anything in the password position and for assignment forms whose name contains PASS, PWD, SECRET, TOKEN or KEY, masked output only: 0 lines in each; no line over 4000 characters. No archive among them.

## Files (in `~/Documents/Projects/GAP-1a-records/`, sha256 prefixes)
- `h141_probe_tests_vf1a_h141_probe.py` 3c6d83a1cdca41dc
- `h141_mutants_W.py` 30cd29b3a72dcf5c
- `h141_expected_kills.txt` 333004ea7aebe950
- `runs/h141_44a23f98.log` 673e97db1d7953ef
- `runs/h141_mutant_W1_44a23f98.log` 361c820cb9b1f3e6
- `runs/h141_mutant_W2_44a23f98.log` bb8fd9a514e392ed
- `runs/h141_mutant_G1_44a23f98.log` 6cfe4ed1207aabee
- `runs/h141_mutant_G2_44a23f98.log` 12cb8abe58a1c447
- `runs/h141_mutant_G5_44a23f98.log` b73acc5b8d50bbfa
- `runs/h141_mutant_G6_44a23f98.log` 91aeb7cade2e9518

The logs of G2, G5 and G6 each have one line with trailing whitespace (unittest's sub-test output): they are committed gzipped so the whitespace hook does not rewrite them, and compared after `zcat`.

The runner (`~/Documents/Projects/GAP-1a-scratch/h141_run.sh`, 17dd1b7f9fa5b38c) and the comparison helper (`h141_expect.sh`, ca6589b60f1a3832) were read by 0b before the grant.
