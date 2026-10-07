# H-130 follow-up: the submission detail cache tests after H-130

**Author:** d5. **Branch:** `task/h130-cache-test-contract`, on
`task/beta-batch-11` `079ae209`. **Verifier:** v2. **Batch:** 11.
One test module changed, a mutation runner added. **No production file
changed.** No migration, no settings change, nothing for the frontend.

## What happened
Batch 11's one full run at `079ae209` (the Release Engineer, 2026-10-07
11:28:04 to 11:35:25) ended **Ran 5913, FAILED (failures=1, skipped=28)**.
The log is `GAP-0b-runs/strict_b11.log` (sha256 begins `8dc6f1db9a0c60f3`);
that red run is kept in the batch's record. The one failing test:

`AutoGrader.tests_cache_bespoke_1114.SubmissionDetailFreshnessTests.
test_a_teachers_assignment_edit_does_NOT_change_this_payload`

It read a submission as the **student**, had the teacher retitle the
assignment, read again, and demanded the two answers be equal, because
"`raw_input` is a snapshot materialised once". H-130 part B changed that
on purpose for one reader: until the grade is released, a student's
document is rebuilt from the row on every read, so it follows the
assignment's current title and due date and the student's current name
(H-130's evidence, "what is given up").

**Why H-130's gates did not see it (my miss):** H-130's regression ran
classrooms, students and assignments. This module is in the AutoGrader
app and is not on the guard list. Rule 15 asks for every app that reads
the changed thing; I did not search the whole test tree for tests pinning
the stored-once behaviour.

## The reading (2026-10-07, before any code; nothing run)
Asked by the Senior Manager: can a student be served a stale document?

- **The answer is cached** per reader: key
  `studentsubmissions:user_id__<reader>:instance_id__<id>`, versioned by
  the reader's own counter (`usr`) alone, lifetime 5 minutes
  (`students/views.py` `retrieve`; `CACHE_TTL`).
- **A teacher's change of title or due date** is an `Assignment` save.
  Its `post_save` receiver bumps the counter of every student holding an
  enrolment row in the course, whatever the row's status
  (`assignments/signals.py` `_bump_assignment_scopes`). The next read is
  rebuilt. The red test itself showed it: the second read carried the new
  title.
- **A student's rename** is a user save, which bumps that user's counter
  (`users/signals.py` `clear_user_cache`).
- **Writes that skip save:** none found for an assignment's title or due
  date, or for a user's name, in assignments, classrooms, students,
  dashboard and users code. A text search, not a proof.
- **No flipping between forms:** one key per reader; grading and release
  are submission saves, which bump the student (H-130's tests of the
  cached response).
- **Other cached student answers that depend on live assignment fields,**
  each family checked: the assignment list and retrieve as a student
  (reader's counter; already rendered the assignment live before H-130;
  same bump); the submission list (no document in it); my_courses,
  profile, settings (no document, no assignment field in the last two).
  Staff answers read the stored snapshot, unchanged.

So the cache was right and the test's contract was out of date.

## The ruling (Senior Manager, 2026-10-07)
No production change. The test is **replaced, not deleted**, by three;
the family's notes are brought up to date; the narrow case below is left
as a known limit, written in the module and logged as a backlog row.

## The change
`AutoGrader/tests_cache_bespoke_1114.py`, class
`SubmissionDetailFreshnessTests`:

1. `test_a_teachers_retitle_reaches_the_students_unreleased_document`:
   the student's document carries the new title on the next read, with no
   cache clear, and nothing else in the payload changes.
2. `test_a_teachers_retitle_does_NOT_change_the_teachers_payload`: the
   old test's contract, kept for the reader it is still true for. It is
   the reason the key has no `global` scope.
3. `test_a_teachers_retitle_does_NOT_change_a_released_students_document`:
   once released, the student reads the stored snapshot.

The module docstring's table row and notes for family 12 say the same,
and carry the known limit and its cure.

## Limits, stated
- **KNOWN LIMIT, backlog H-149 (LOW).** The assignment's bump goes to
  students with an enrolment row; a student reads their own submission
  with no enrolment check. A student whose enrolment row has been
  DELETED can read the old title or due date in an unreleased document
  for at most the key's 5 minutes. Leaving a course keeps the row (as
  withdrawn); no production code was found that deletes one. No grade is
  involved. Cure, if wanted: the course's counter (`crs`) in the
  student's key, with a test seen red first.
- **Tests 2 and 3 demand an equal payload, which a stale cache also
  gives** (v2's pre-read, N1). They mean something only because the
  retitle bumps the reader; nothing in those two tests shows that it did.
  X1 leaves both green. v2's probe P1 covers it.
- **Why "nothing else in the payload changes" holds today** (v2's
  pre-read, N2; a fact older than this change): the student's serializer
  declares `assignment_title`, `assignment_due_date` and `course_title`
  with sources written with two underscores, which name no attribute, so
  the three keys are absent from a student's answer. If those sources are
  repaired, test 1 goes red on its last line, and rightly: it must then
  be widened. Raised with the Senior Manager by v2.
- **Nothing in the module fails when a key is invalidated too often**
  (v2's pre-read, N3): adding `global` to the key leaves every test
  green by reading.

## Commits
- `79850d19` the tests (one module).
- `d87a56f6` the mutation runner. **The tip the gates ran on.**
- This folder's commit: results only, no test or code line.

## Gates
All on the Release Engineer's grants, 2026-10-07, at `d87a56f6`, tree clean.
There is no red-commit step: the change is tests only. The old test's red
is the full run named above; the mutants show each new test can fail.

- **(a) the changed module, H-130's three modules and the guard list,**
  serial, 11:44:08 to 11:48:27: **Ran 487 tests in 205.967s, OK**
  (`a_modules_d87a56f6.log.gz`).
- **(b) the battery,** 11:48:28 to 11:49:43: baseline Ran 17 OK; 3 of 3
  killed with verified restore (`b_mutation_battery_d87a56f6.log`,
  `battery_d87a56f6.tar.gz`).
- Load during (a) and (b): 9.9 to 12.4 (another project's serial battery
  ran beside it, by the grant). No wall-clock assertion is in these runs.

## Mutants
Each is one way the three tests could have passed whatever the code
does. Expected failing sets were written before any run
(`expected_kills.py`, file clock 2026-10-07 11:42).

| | Mutation | Fails (as written beforehand, and as run) |
|---|---|---|
| X1 | an assignment's save no longer bumps enrolled students (`assignments/signals.py`) | test 1 only |
| X2 | the stored document is re-made on every read (`students/views.py`) | tests 2 and 3 |
| X3 | a released student gets the rebuilt document (`students/services.py`) | test 3 only |

As run: X1 Ran 17, FAILED (failures=1); X2 Ran 17, FAILED (failures=2);
X3 Ran 17, FAILED (failures=1). `expected_kills_d87a56f6.txt`: each
failing set is the expected one and nothing else.

## The regression
ONE run of AutoGrader, students and assignments at `--parallel 2`
(`c_h130c.sh d87a56f6`), 12:01:42 to 12:06:58: **Ran 1639 tests in
289.356s, OK (skipped=16)**, exit 0, stalled=0
(`c_three_apps_p2_d87a56f6.raw.log.gz`, `iso.status`). Expected before
the start (`c_expected_d87a56f6.txt`): OK, none skipped for want of
Chromium; so it was (count 0). The 16 skips: 9 load tests and 5 real AI
calls, all opt-in, and 2 that cannot fork inside a parallel worker.

Load: 2.97 at the start, 3.34 at the end.

**Beside this run, the Release Engineer's own disclosure:** about 23
seconds of one core at full load (a cost check of its own tool in plain
Python, ending 12:02:37) and a few sub-second calls, inside the quiet
window it had ordered. Nothing turned red.

The AutoGrader app is in this regression because the changed module
lives there and reads students and assignments code (rule 15; rule 20).

## The search for other references
The Release Engineer, one `git grep` on `d87a56f6` (2026-10-07): the old
test's name stands only in four old evidence logs, each the record of a
past run (`docs/evidence/batch-2b-candidate-per-module-8b1c0cf`,
`cache_commit_race`, `h1-stage3-per-module-cc14bb0`,
`h1-step4-per-module-8063c44`). No code, no other test and no document
refers to it. Nothing edited.

## The credential pattern
`credcheck.sh -v` on this folder, 2026-10-07 12:07, before the commit: no
URL, encoded URL or made-up password string. Three name-and-value hits,
judged: "PASS:" in the two copies of the regression log is a test's own
printed verdict ("[H-1 Stage 3 failure-mode probe] PASS: ..."), and
"passed:" is a word in `expected_kills.py.txt`. Output masked.

## Files
- `run_mutants.py`: the runner; its body below the mutant table is
  byte-identical to H-130's.
- `chain.sh.txt`, `run_chain.sh.txt`, `c_h130c.sh.txt`,
  `expected_kills.py.txt`, `iso_file.sh.txt` (the same file as H-130's
  copy): the scripts as run.
- `chain.status`, `chain_wait.log`, `c_wait.log`, `iso.status`,
  `c_expected_d87a56f6.txt`, `c_three_apps_p2_d87a56f6.load.txt`.
- `a_modules_d87a56f6.log.gz`, `b_mutation_battery_d87a56f6.log`,
  `battery_d87a56f6.tar.gz`, `expected_kills_d87a56f6.txt`.
- `c_three_apps_p2_d87a56f6.raw.log.gz` (the raw log: the evidence) and
  `c_three_apps_p2_d87a56f6.log.gz` (the same with a time on each line).
- One test database is left on the local server: `test_h130c_mut`
  (looked at 12:07).
