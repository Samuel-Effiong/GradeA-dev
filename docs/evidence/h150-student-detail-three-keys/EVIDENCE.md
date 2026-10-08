# H-150: a student's submission detail sends the three details it declares

**Severity:** LOW. **Author:** d5. **Branch:**
`task/student-detail-three-keys`, on beta `d7143538` (batch 11 as
pushed). **Verifier:** v2. **Batch:** 12.
One production file, one test module, a mutation runner. No migration,
no model change, no setting. **A payload changes** (rule 20): three keys
are added to one answer. One line for the frontend, below.

## The fault
Found by v2 in its pre-read of the H-130 follow-up (its N2). The
student's serializer for one submission
(`StudentSubmissionDetailStudentVersionSerializer`,
`students/serializers.py`) declared `assignment_title`,
`assignment_due_date` and `course_title` with sources written with two
underscores (`assignment__title`, and so on). That is the spelling of a
database lookup, not of an attribute, so the sources named nothing; the
fields being read-only, they were skipped, and **no student was ever
sent the three keys**.

## Rulings (Senior Manager, 2026-10-07)
- The three are sent. `course_title` keeps the declared key and sends the
  course's **name** (a course has a name and no title).
- `assignment_due_date` in the standard date form, as `submission_date`
  beside it; `null` when the assignment has no due date.
- H-149's known gap (a student whose enrolment row was deleted can read
  an older value for at most the cache's five minutes) applies to these
  three as to the document; it is **named, not repaired** here.

## The change
`students/serializers.py`, three lines:

```python
assignment_title = serializers.CharField(source="assignment.title", read_only=True)
assignment_due_date = serializers.DateTimeField(source="assignment.due_date", read_only=True)
course_title = serializers.CharField(source="assignment.course.name", read_only=True)
```

They are read at the time of the read, released or not. Nothing of a
grade is in them. The cache key is unchanged (the student's own counter):
an assignment's save and a course's save each bump every student holding
an enrolment row in the course, which the tests hold (T4, T5).

`AutoGrader/tests_cache_bespoke_1114.py`, class
`SubmissionDetailFreshnessTests`: eight tests added or widened (the three
details and their form; null when absent; the exact keys of a student's
answer; a teacher's answer does not gain them; a saved due date and a
course rename reach the next read; grading changes none of the three).
Two of H-130's follow-up tests are widened, as their own note foretold:
the title beside the document now follows a retitle.

## Commits
| Commit | What |
|---|---|
| `e12c1845` | tests (red) |
| `a50c6aae` | the three sources |
| `6f6a56ca` | mutation runner (T1 to T5); the tip the gates ran on |

## The whole-tree search for tests that pin the old behaviour
One `git grep` on `6f6a56ca` over every test file (`'*test*.py'`), on
the Release Engineer's grant, 13:40:22 (`tree_search_6f6a56ca.txt`).
Terms: `assignment_due_date`, `course_title`, `assignment__title`,
`assignment__due_date`, `assignment__course`.
23 hit lines: 14 in this row's own module; 9 elsewhere, each read: eight
are database lookups (`assignment__course...`) in
`classrooms/tests_recalculate_final_grades.py`,
`classrooms/tests_teacher_access_sweep.py`,
`dashboard/tests_dashboard_remediation.py`,
`dashboard/tests_h14_at_risk_equivalence.py`, `students/tests.py` and
`students/tests_submission_list_queries.py` (which tests the LIST
serializer, not this one), and one is a migration's name in
`AutoGrader/tests_migration_rollback_defaults.py`. **No test pins the
three keys' absence.**

## Gates
All on the Release Engineer's grants; 6G cap, sleep inhibited, timeout
1800, output to files, own databases, rules 17 and 18. Expected failing
sets were written before any run (`expected_kills.py`,
`b8126220061cfa32`) and, on the Senior Manager's instruction of
2026-10-07, every one of the module's 24 tests was read against every
mutant before the grant; no set changed.

### The chain, 14:57:03 to 15:02:49, on `6f6a56ca`
| Step | What | Result |
|---|---|---|
| (r) | the module at `e12c1845`, tests before the fix | Ran 24 in 17.158s, FAILED (failures=2, errors=6): the 8 written tests (`9fa2d98eac973027`) |
| (a) | the module and the guard list | Ran 492 in 154.803s, OK (`3187eab74426ec86`) |
| (b) | 5 mutants, each restored and verified | baseline green; 5/5 killed; 112 s (`bfa70eb16bd82467`) |

Load 5.2 to 6.9 (another granted run beside it). No kill looks like a
timeout: each mutant's run took 14 to 17 s and ended with a FAILED line.
Every mutant's failing set was the written one
(`expected_kills_6f6a56ca.txt`, `a2aa62cf6904bc1e`).

| | Mutation | Failing tests (written beforehand, and as run) |
|---|---|---|
| T1 | the title's source names nothing again | 6: every test that reads the title |
| T2 | the due date as plain text, not the standard form | 2 |
| T3 | the course's id where its name should be | 2 |
| T4 | an assignment's save no longer bumps its course's students | 3 |
| T5 | a course's save no longer bumps its students | 1 |

T5 leaves the older test "a TEACHER's course rename reaches the student"
green, and rightly: the course list's key also hangs on the global
counter, which T5 leaves.

State looked at after the chain (15:03:02): worktree at `6f6a56ca`,
clean; no test process of this row; the database `test_h150_mut` exists
(kept by `--keepdb`).

### The regression
`c_h150.sh 6f6a56ca`, in a quiet window, 15:27:54 to 15:34:58:
AutoGrader (for the cache tests, rule 20), students and assignments,
`--parallel 2`. **Ran 1646 tests in 387.231s, OK (skipped=16)**; exit=0;
stalled=0; no `FAIL:` or `ERROR:` line in the raw log; none skipped for
want of Chromium. Load 3.37 at the start, 5.74 at the end. Raw log
`c_three_apps_p2_6f6a56ca.raw.log`, sha256 begins `4e79274253ab3999`
(stamped copy `3ac51b1b8700607c`); here gzipped.

## For the frontend
A student's answer for one submission (`GET /api/v1/submissions/<id>` as
a student) gains three keys: `assignment_title` (text or null),
`assignment_due_date` (the standard date form, or null),
`course_title` (the course's name). No key is removed or renamed. A
teacher's answer is unchanged.

## Limits, stated
- H-149's gap, as above: named, not repaired.
- The three follow the assignment and the course as they are NOW, also
  on a released paper, whose document beside them is the stored one. A
  retitled assignment therefore shows its new title beside a released
  document that prints the old one. Held by a test; said here so that
  nobody is surprised.
- **An assignment moved to another course** (v2's pre-read, tested by
  my reading of `assignments/signals.py` `_bump_assignment_scopes` and
  the edit route): the bump after the save goes to the students of the
  course the assignment is in AFTER the save. A student who answered it
  and is not in the new course is not bumped, so their cached answer
  keeps the old course's name (and an old title or due date changed in
  the same save) for at most the key's five minutes. The same family as
  H-149, bounded the same way, nothing of a grade. For the unreleased
  document's title and due date this staleness is older than this row;
  for the course's name it is new with it. Named, not repaired.
- **A write that fires no signal moves nothing** (v2's pre-read): the
  three are fresh because a SAVE moves the student's counter. The four
  repair commands bump in bulk after their `bulk_update`. H-130's
  follow-up searched the application code for other writes of an
  assignment's title or due date that skip save and found none (a text
  search, not a proof); a course's name was not part of that search,
  and I have not searched for it here.
- Not run against a real browser or the frontend.

## The credential pattern
`credcheck.sh -v` on this folder alone, 2026-10-07 15:43, archives opened,
masked output: no URL with a password, no encoded URL, no bare made-up
password. Assignment-form names in six files, each judged: `KeyError:`
in the red step's log and the mutants' logs (the tests' own errors);
`PASS:` in the two regression logs (an older probe's line); `passed:`
in the expected file (the comparison's wording); `keys:` in this file's
prose. None is a credential.

## Files
Logs are gzipped where they are large or carry trailing spaces; the
checksums above are of the files before gzip.
- `run_mutants.py`: the runner (T1 to T5). `chain.sh.txt`,
  `run_chain.sh.txt`, `c_h150.sh.txt`, `iso_file.sh.txt`: the scripts as
  run. `expected_kills.py.txt`: the expected sets.
- `chain.status`, `chain_wait.log`, `r_repro_e12c1845.log.gz`,
  `expected_r_e12c1845.txt`, `a_modules_6f6a56ca.log.gz`,
  `b_mutation_battery_6f6a56ca.log`, `battery_6f6a56ca.tar.gz`,
  `expected_kills_6f6a56ca.txt`: the chain.
- `c_three_apps_p2_6f6a56ca.raw.log.gz`, `.log.gz`, `.load.txt`,
  `iso.status`, `c_h150.out.txt`: the regression.
- `tree_search_6f6a56ca.txt`: the search's hits.
