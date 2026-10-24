# H-130: a student must not learn of a grade before the teacher releases it

**Author:** d5. **Branch:** `task/h130-student-final-grade-released-only`,
begun on `task/beta-batch-9` `9fb6d4fe`, now on `task/beta-batch-10` `78c447d6`
(H-127/H-128 merged). For batch 11. **Verifier:** v2. Production code in three apps
(classrooms, students, assignments), two new test modules, one changed
test, a mutation runner. No migration, no model field, no settings change.

Source: Verifier 1's report (the course final grade counts unreleased
grades), my reading for the SM, v2's pre-reads. The rule, from the
founder's side on 2026-10-06: **"a student should not know a grade exists
before the teacher releases it."**

**State of the gates at this commit:** all have run on the code tip
`9a9d6cb2`, each on 0b's grant on 2026-10-06, and all are as written
beforehand: both red proofs, the module step, 18 of 18 mutants, the
three-app regression. **Two earlier chains stopped themselves** on results
that differed from the written ones; both times the cause was a fault in
my tests of part B, not in the production change. They are disclosed
below with their logs ("What went wrong on the way").

## The two defects
**A. The course final grade.** `StudentCourse.final_grade` is recalculated
on every save or delete of a submission, from every GRADED submission,
released or not (`classrooms/signals.py`, `compute_final_grade`).
`/student-course` (list and detail) returned that stored number and its
letter to the student. So grading moved a number the student could read
before anything was released, and with one unreleased item its exact score
could be worked out from the rest.

**B. The answer document.** `raw_input` is the document built by
`student_submission_to_html`; its header prints "Graded At" and "Score".
Grading rebuilds the document with both filled in and stores it. The
student's two serializers returned the stored text, while withholding
every other grade-bearing field until release.

## The change
**A (5 points)**
1. For a reader who is not a teacher, `final_grade` and `grade_letter` on
   `/student-course` come from the RELEASED submissions only
   (`classrooms/final_grade.py`, `released_final_grade`).
2. One arithmetic. `final_grade_from` holds the formula (points-weighted
   average, clamped to 0 to 100, two decimal places, half up). The stored
   figure and the student's figure both go through it; they differ only in
   which rows they are given.
3. The student's figure is worked out from the submissions the viewset
   already prefetches. No query per enrolment: the student's list stays
   flat (`classrooms.tests_query_budget`).
4. A reader the serializer cannot identify (no request, no user type) is
   treated as a student. The route itself needs a login.
5. Unchanged: `StudentCourse.final_grade` itself, the receivers that
   write it, what teachers read on the same route, every staff dashboard,
   the `recalculate_final_grades` command. **Nothing stored needs
   recomputing and there is no production action.**

**B (4 points)**
1. `students.services.answer_document_for_student` is the one place a
   student-facing reader gets the document from. Until release it ALWAYS
   returns the document rebuilt from the row in its ungraded form; after
   release, the stored document.
2. The ungraded form is the header exactly as a newly submitted row has
   it: no grading time, and the score column's own default, printed by the
   same lines. A new row's score is zero by default and the header prints
   an empty "Score:" line for it; the rebuilt form prints the same.
3. There is no "is this row graded?" decision. The first version of this
   fix had one (`564a903c`) and v2's pre-read showed it wrong for rows made
   the way production makes them: see "What went wrong on the way".
4. A row with no stored document is served as it is, so a submission that
   is still being processed looks as it did. Nothing stored is rewritten.
   Staff read the stored document as before.

## Behaviour changes to know (for the package and the frontend)
- **A student's final grade and letter can now differ from the
  teacher's** for the same enrolment, while something is graded and not
  released. They are equal again once everything graded is released.
- **A final grade a teacher sets by hand** (PATCH on the enrolment) is
  never read by a student after this row. It cannot be told apart from the
  computed one (same column, no flag, no history), and it did not last
  before either: the next save or delete of one of that student's
  submissions overwrites it. The SM has logged a separate row for the
  product question.
- **Before release, the document's header follows today's values:** the
  assignment's current title and due date and the student's current name,
  not those at upload time. A rename shows to the student when it happens.
  After release the student reads the stored document, whose header is
  from grading time.
- **A regrade after release moves the student's number and document at
  once.** Intended: the work is released.

## What it does not cover
- **Per-submission signals** that change at grading time (remaining
  attempts, the "already been graded" refusal, grading state, list
  filters): the Security Engineer's H-127 and a separate row of theirs.
- **Serializers returned to a student from write routes** (the upload and
  edit responses) still carry the stored column. A graded row refuses
  those writes before a response is built. They are named in the guard
  test with that reason.
- **One conversion of the document per uncached read** of an unreleased
  submission by its student. Both student routes cache per reader.
- **A document stored by an older version of the builder** is read by the
  student in today's form until release, and in its stored form after it
  (v2's point).
- **Staff figures still count unreleased work.** That is the SM's ruling:
  staff have seen those scores.
- **Admins do not reach `/student-course`** (the viewset serves teachers
  and students). If a later route gave them this serializer they would
  read the released-only figure: the safe side.
- **Production (`origin/main` `9c21bee8`, 2026-08-24), by reading only:**
  main has the same code shape for both defects. Whether a real student
  was shown such a value cannot be told from the code.

## What the tests of part B rely on
`PART_B_TESTS.md`, in this folder: for the fixture, the read helpers and
every test of part B, the existing behaviour it relies on, the file and
line where I read it at the tip, and which run first showed it red and
green. Written on the SM's order after the second stopped chain; v2 read
the test file against it line by line, found one more wrong statement
(the fourth, below), and read the correction.

**The upload route's own answer.** The student's upload route answers
with the staff serializer, stored document included. That is safe only
because the upload refuses a graded or being-graded row first. Changing
that answer is a separate row for the Security Engineer (SM's ruling).
Until it lands, the safety of the upload answer rests on that refusal,
and `TheUploadRoutesOwnAnswer` pins it.

**Does the "7.0" / "7.00" printing touch the student's form?** (the SM's
question; the full answer is at the end of `PART_B_TESTS.md`.) No. The
ungraded form never prints the row's score; it prints the column's
default, and the header prints nothing for a zero, float or decimal.
Tests read a grade of zero on both student routes and compare the
document stored at upload with the one rebuilt on a read. The difference
shows only in a document that prints a non-zero score, which a student
receives only after release (row H-139, existing, teachers' documents).

## What went wrong on the way (kept, because the record should show it)
- **The first fix of B (`564a903c`) was wrong, and its first tests could
  not see it.** It rebuilt the document only for a "graded" row, decided
  by "has a grading time or a score", and printed the score as "Not
  graded yet". But `StudentSubmission.score` has a default of zero: every
  real row has a score, so every real row counted as graded, and no real
  submitted document says "Score: Not graded yet". The tests compared the
  rebuilt form with itself. v2 found it reading the red commit, before any
  run. The tests were remade on a row from the upload engine
  (`1c5313ff`, red against the first fix) and the fix was replaced
  (`d6299358`). Mutant B6 is that first version.
- **So the red proof of defect B is not the first red commit.** (r2) runs
  the tip's tests on the code of the first fix. The defect itself, the
  stored document served before release, is mutant B3.
- **Four faults in part B's TESTS, three found by runs and one by v2's
  reading.** None is a fault of the production change.
  1. *The student was PENDING* (first early chain, 15:37, tip `e8315419`:
     stopped at (r2), 12 tests failing where 8 were written). The fixture
     enrolled by e-mail, which leaves the enrolment pending, and a pending
     student may not read a course's assignments: every read of the
     student's assignment route answered 404. **Part B on the assignment
     route was first exercised only at the corrected tip.** A test now
     pins that refusal.
  2. *A guard test broke the source it parsed* (same chain):
     `inspect.cleandoc` before `ast.parse`.
  3. *An assertion about two renderings of a graded document* (second
     chain, 16:44, tip `c20e9a4a`: both red proofs as written, then 1 red
     of 460 in the module step). I asserted that the document the read
     route rebuilds for an empty column equals the one grading stored.
     Grading prints the score from a float ("7.0"), the rebuild from the
     database ("7.00"). Existing behaviour, teacher's document only; row
     H-139.
  4. *The guard's labels* (v2's line-by-line read of `5d195a9e`): the
     dictionary called the staff serializer "staff only" and two others
     "returned from a write route". The student's upload route answers
     with the staff serializer; no route builds the other two.
- **Both stopped chains are in this folder as they ended:**
  `first_early_chain_e8315419.tar.gz` and `second_chain_c20e9a4a.tar.gz`
  (raw logs, the expected-set outputs, chain status).
- **Why (r1) still stands with the old fixture.** (r1) runs part A's test
  file as it was at `5e08d9ca`, where the student was still PENDING. The
  enrolment routes serve a pending student (they filter by student
  only), each helper asserts 200 and finds the row, so those requests
  reached the serializer; v2 confirmed it by reading.

## Commits
| Commit | What |
|---|---|
| `5e08d9ca` | Red tests first, with two seams, behaviour unchanged |
| `fe865332` | Fix A: a student's final grade and letter from released work only |
| `564a903c` | Fix B, first version (wrong for rows made the way production makes them) |
| `e200dad5` | Part A: more tests, two conditions removed, before any run |
| `1c5313ff` | Part B's tests remade on a row from the upload engine (red against `564a903c`) |
| `d6299358` | Fix B corrected: always rebuild until release |
| `5a50a220` | The mutation runner |
| `e8315419` | One more test on the assignment route (v2's second pre-read). The first early chain ran here and stopped |
| `f4d7ed37`, `3bd84f89` | Tests: the student ENROLLED; the guard's parse fixed; a PENDING student pinned as refused |
| `3a9e8a60` | Base update onto `task/beta-batch-10` `78c447d6` (H-127 in; no conflict) |
| `c20e9a4a` | Tests: part A's reader ENROLLED too. The second chain ran here and stopped |
| `5d195a9e` | Tests: the wrong assertion corrected; a grade of zero on both routes |
| `9a9d6cb2` | Tests after v2's line-by-line read (labels, the upload route's answer, a saved rename, a due date); one comment in `students/views.py`. **The gates ran on this tip** |

Against `78c447d6` the branch changes: `classrooms/final_grade.py` (new),
`classrooms/signals.py`, `classrooms/serializers.py`,
`students/services.py`, `students/serializers.py`,
`assignments/serializers.py`, `students/views.py` (comment lines only),
two new test modules, `classrooms/tests_final_grade_zero_score.py`, and
this folder.

## Gates
Each run on 0b's grant on 2026-10-06, output straight to a file. Before
them 0b itself ran the two cross-side guards (H-124's and H-127's) on the
merged tree at `c20e9a4a`: Ran 36 tests, OK
(`0b_crossside_h124_h127_guards_on_h130_c20e9a4a.log`, 0b's run, not
mine); both guards ran again in (a) on the final files.

| Step | Result, from the raw log | Load (1 min) start / end |
|---|---|---|
| (r1) part A's test module at the first red commit `5e08d9ca`, in a disposable worktree | Ran 14 tests in 2.967s, FAILED (failures=9), exit=1 | 2.95 / 3.10 |
| (r2) part B's test module as at the tip, on the code of the first fix `564a903c`, in a disposable worktree | Ran 25 tests in 7.866s, FAILED (failures=12), exit=1 | 3.10 / 4.00 |
| (a) the three changed test modules, five modules nearest the change, H-127's guard and its two test modules, the repo-wide guard list; 33 labels, serial | Ran 464 tests in 170.279s, OK, exit=0 | 4.00 / 5.44 |
| (b) 18 mutants | baseline Ran 57 tests, OK; 18 of 18 KILLED; runner exit=0, 193 s | 5.44 / 7.52 |
| (c) classrooms, students and assignments, `--parallel 2`, under the lock, watchdog | Ran 1413 tests in 170.395s, OK (skipped=14), exit=0; stalled=0 | 3.54 / 4.59 |

- **The chain** (`chain.sh 9a9d6cb2 5e08d9ca 564a903c`): GRANT 17:01:38,
  17:01:57 to 17:09:19, chain exit 0. No wall-clock assertion in it.
- **(r1):** the failing tests are exactly the 9 named beforehand
  (`expected_r1_5e08d9ca.txt`).
- **(r2):** the failing tests are exactly the 10 named beforehand
  (`expected_r2_564a903c.txt`). The tip's test file is written over that
  commit's in the disposable worktree, and `chain.status` says so.
- **(c):** GRANT 17:10:04 with a quiet window; a waiter read the load
  every 20 s (`c_wait.log`) and started the run at 17:11:30, the first
  reading at 4.00 or under; it ended at 17:14:41. Tests skipped for want
  of Chromium: 0. The 14 skips are opt-in tests: 9 load tests
  (`RUN_LOAD_TESTS`) and 5 real-AI tests (`RUN_REAL_AI`).
- **This is the gate that counts,** on the batch 10 base with H-127 in it.
  The first early chain ran on the batch 9 base.

## Mutants
The expected failing tests were written by class and method before any
run (`expected_kills.py.txt`; first written at 14:25, last changed at
16:58:29 for the tests added after v2's reads, each addition marked in
the file with its reason; 0b read it before the grant, sha256 prefix
`e0a6d7e538b0c65b`; the run started at 17:01:57). Every mutant's failing
set equals its written set (`expected_kills_9a9d6cb2.txt`).

| Mutant | What is broken | Failing tests |
|---|---|---|
| A1 | the student's figure counts unreleased work | 9 |
| A2 | a student is read as staff | 11 |
| A3 | the letter comes from the stored number | 9 |
| A4 | the number comes from the stored number | 11 |
| A5 | no fallback to the assignment's points | 3 |
| A6 | another course's work is counted | 1 |
| A7 | a row with no grading time is counted | 2 |
| A8 | a teacher reads the released-only figure | 3 |
| A9 | a reader that cannot be identified reads the stored figure | 1 |
| B1 | a released row is rebuilt too (the student never gets the stored document) | 3 |
| B2 | a row with no stored document gets one made up | 1 |
| B3 | **the defect itself:** before release the student reads the stored document | 12 |
| B4 | the ungraded form shows the grading date | 11 |
| B5 | the ungraded form shows the row's score | 10 |
| B6 | the ungraded form prints "Not graded yet" for the score (my first version) | 9 |
| B7 | the student's submission serializer returns the stored column | 10 |
| B8 | the student's assignment serializer returns the stored column | 5 |
| B9 | the submission serializer carries the plain column again | 11 |

- **Baseline:** Ran 57 tests in 9.115s, OK (the three test modules the
  mutants are judged by).
- **Every one of the 19 raw outputs** holds its own "Ran" line and a
  result line; no BROKEN, no exit 124 or 137. Every restore equals the
  commit's blob by sha256.
- **Rules 17 and 18:** `PYTHONDONTWRITEBYTECODE=1` on every run;
  `__pycache__` of the mutated modules' packages deleted before the
  baseline, before each mutant and after each restore; each inner run
  writes to its own file with stdin from the null device. The runner is
  H-124's below the mutant table (0b diffed it).
- **A database is left behind, checked by looking:** listed read-only
  through Django's connection after the chain, `test_h130_mut` exists
  (the battery passes `--keepdb`); `test_h130_repro` does not. 0b drops
  the first.
- **The battery is on the final code and tests:** nothing but this folder
  changes after `9a9d6cb2`.

## The credential pattern
This folder, archives opened: 0 URLs with anything in the password
position, 0 encoded ones. Three files hold assignment-form lines whose
names contain "pass" or "token": `expected_kills.py.txt` (the words
"expected but passed:") and the two copies of the (c) log, where the
lines are source code quoted in a logged traceback
(`renew_student_activation(token=serializer.validated_data[...])`) and
the word "bypass:" in a log message. No value is in any of them.

## Files
- Raw logs of the gates: `r1_repro_5e08d9ca.log.gz`,
  `r2_repro_564a903c.log.gz`, `a_modules_9a9d6cb2.log.gz`,
  `b_mutation_battery_9a9d6cb2.log`,
  `c_three_apps_p2_9a9d6cb2.raw.log.gz` (the evidence) with its stamped
  copy `c_three_apps_p2_9a9d6cb2.log.gz` (a convenience),
  `c_three_apps_p2_9a9d6cb2.load.txt`, `c_wait.log`, `iso.status`.
- `battery_9a9d6cb2.tar.gz`: `results.tsv`, the short log per mutant, and
  `logs/raw/*.out`, each inner run's whole output.
- `chain.status` (this chain's lines), `expected_r1_5e08d9ca.txt`,
  `expected_r2_564a903c.txt`, `expected_kills_9a9d6cb2.txt`.
- The two stopped chains, as they ended:
  `first_early_chain_e8315419.tar.gz`, `second_chain_c20e9a4a.tar.gz`.
- 0b's cross-side run: `0b_crossside_h124_h127_guards_on_h130_c20e9a4a.log`.
- `PART_B_TESTS.md`: what each test of part B relies on, with the lines
  read. `FRONTEND_NOTE.md`: what changes for the frontend.
- Scripts as run: `chain.sh.txt`, `c_h130.sh.txt` with `iso_file.sh.txt`
  and its starter `wait_c.sh.txt`, `expected_kills.py.txt`.
