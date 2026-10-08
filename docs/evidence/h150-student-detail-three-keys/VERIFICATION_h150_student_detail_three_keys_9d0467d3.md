# Verification: H-150, a student's submission detail sends the three details it declares (d5)

- **Branch:** task/student-detail-three-keys at **9d0467d3**, on beta d7143538 (batch 11 as pushed). Code, tests and runner last changed at 6f6a56ca; 9d0467d3 adds only docs/evidence/h150-student-detail-three-keys/ (v2's own name-only diff: 20 files, none outside that folder). d5's gates ran on 6f6a56ca; v2's run on 9d0467d3.
- **Severity:** LOW. Found by v2 while reading for H-130's follow-up; the Senior Manager ruled the three are sent.
- **The change:** in `StudentSubmissionDetailStudentVersionSerializer` three read-only fields had sources written with two underscores, which name no attribute, so no student was ever sent them. Now `assignment_title` reads the assignment's title, `assignment_due_date` its due date in the standard date form, `course_title` the course's name. One production file, three lines. No migration, no model change, no setting. A payload changes (rule 20): three keys are added to one answer, `GET /api/v1/submissions/<id>` as a student.
- **Verifier:** v2 (independent), 2026-10-07. One slot from 0b, 15:45:35 to 15:45:59 WAT, 1-minute load 3.52 to 3.09, nothing else of the team's running; nothing timed.
- **Verdict:** **VERIFIED-WITH-NOTES.** The three keys are sent in the forms stated, a teacher's answer does not gain them, and a saved change of each reaches the student's next read, released or not. The notes are limits of the cache, all bounded by the key's five minutes and none about a grade.

## Read before any run
- **One place sends this answer:** the retrieve route of `StudentSubmissionViewSet`, under one cache key that hangs on the reader's own counter. A student's queryset has no DRAFT or UNPUBLISHED assignment and no enrolment condition. The foreign keys on the way to the course's name are not null, by the view's own note; v2 did not read the models for it.
- **Why the key needs nothing more for the three:** an assignment's save bumps every student holding an enrolment row in its course (`assignments/signals.py`, `_bump_assignment_scopes`), and a course's save does the same (`classrooms/signals.py`, `_course_scopes`). v2 read all six handlers of an Assignment save; no other one bumps a student.
- **The date's form:** the project's time zone is UTC, so the standard form ends in `Z`, as d5's test writes it.

## v2's probe, each expectation written first
`tests_vf2_h150_probe.py`: d5's fixture and the real Redis cache, on d5's base class and NOT on d5's test class, so none of d5's tests runs a second time. (The base class brings one test of its own, "no wildcard sweep", so the probe's runs say "Ran 4"; it passed each time.)

| Probe | What it holds |
|---|---|
| Q1 | d5's "reaches the next read" tests for the due date and the course's name are on an unreleased paper. On a RELEASED paper: a due date the teacher saves and a course the teacher renames each move the student's counter and reach the next read; the stored document beside them and every other key stay as they were |
| Q2 | a limit as a fact: after a queryset update of the due date and of the course's name the student's counter has not moved and the next read still has the old ones; with the cache emptied the new ones are there |
| Q3 | a second limit as a fact: an assignment moved to another course by a save (what the PATCH route does) does not move the counter of a student who answered it and is not in the new course; the next read still has the old course's name; with the cache emptied, the new one |

Baseline (d5's module and the probe): **Ran 28 tests in 10.442s, OK.**

| Mutant (rule 19), both in `students/views.py` | Written before the run, every line of Q1 to Q3 read against it | As run |
|---|---|---|
| V1 the student's detail is never served from the cache | Q2 and Q3 fail at their "still the old one" lines; Q1 passes | Ran 4, FAILED (failures=2): Q2 (`'2026-10-21T09:00:00Z' != '2026-10-14T09:00:00Z'`), Q3 (`'Other Course' != 'Original Course'`) |
| V2 the key hangs on a counter nothing moves | Q1 fails at its first "reaches" line; Q2 and Q3 pass | Ran 4, FAILED (failures=1): Q1 (`'2026-10-14T09:00:00Z' != '2026-10-21T09:00:00Z'`) |

Both sets exactly as written; each restore matched the commit's file by sha256, `__pycache__` cleared, the tree clean at 9d0467d3 afterwards. Each of Q1 to Q3 was seen red. d5's tests were not run under V1 and V2 (rule 15).

## d5's gates, read by v2 from the raw logs (not repeated, rule 15)
v2 unpacked the committed logs and checked each against the checksum EVIDENCE.md gives: all match (the red step, (a), the battery log, the expected file, the comparison's output, the regression's raw and stamped logs).

| Gate | The raw log |
|---|---|
| (r) the module at e12c1845, tests before the fix | Ran 24, FAILED (failures=2, errors=6): the eight written tests |
| (a) the module and the guard list, 6f6a56ca | Ran 492 tests in 154.803s, OK |
| (b) 5 mutants, 6f6a56ca | baseline green; 5 of 5 killed with verified restore; every failing set the one written before the run (6, 2, 2, 3, 1) |
| (c) AutoGrader, students, assignments, `--parallel 2`, 15:27:54 to 15:34:58, in a quiet window | Ran 1646 tests in 387.231s, OK (skipped=16); exit=0; stalled=0; no `FAIL:` or `ERROR:` line; load 3.37 and 5.74 |

- **Tests that READ the changed answer outside the regression's three apps** (today's lesson from score printing: look for tests that exercise the changed route, not only tests that pin the old answer). One `git grep` on 9d0467d3 over every test file, on 0b's grant, for the route's name and its path: 26 lines. Outside the three apps the route is named in two billing test modules and one ai_processor module; v2 read each hit: all are PATCH requests refused or billed before any answer is built. None reads the student's answer.
- **d5's three asks, tested by reading:** (1) the frontend lines are right: the route, text or null for the title, the standard date form or null for the due date, the course's name; no key removed or renamed; d5's exact-keys test holds the list. (2) the limit "a released paper shows today's title beside a stored document that prints the old one" is real and held by d5's released-student test. (3) under T5 only the course-rename test of this class failed (T5.log), and the older course-list test stays green because that key also hangs on the global counter, which a course's save still moves.

## Notes
1. **An assignment moved to another course** (Q3; in the evidence in d5's words). A student of the old course who is not in the new one reads the old course's name, and an old title or due date changed in the same save, for at most five minutes. For the unreleased document's title and due date this is older than this row; for the course's name it is new with it. The same family as H-149. Named, not repaired.
2. **A write that fires no signal is not seen until the key runs out** (Q2). v2 searched the assignments and classrooms apps at 6f6a56ca (production files) for `.update(` and `bulk_update(`: the only such write of one of the three columns is the title-repair command, and the signals module says the four repair commands bump in bulk afterwards. No queryset write of a due date or of a course's name in those two apps. Other apps were not searched; a text search, not a proof.
3. **H-149's gap** (a deleted enrolment row) applies to the three as to the document; shown for the document in H-130's follow-up, not shown again here.
4. **v2 told 0b the probe has three tests; its runs say four** (the base class's own test). The failing sets are exact all the same.
5. **Not run against the frontend or a browser.** Whether a screen already reads these three keys, and what it expects of `course_title`, is the frontend's to say.

## Files
In `~/Documents/Projects/GAP-v2-handover/`: this record; `tests_vf2_h150_probe.py` (9ff4b0e49820d409), `vf_h150_mutants.py` (28920130ea3c10f0), `vf_h150_run.sh` (963a4ef0aad5db1a); `runs/h150_9d0467d3.status` (ebd71daa0918728d), `runs/h150_9d0467d3_script.out` (8a1173c7ba42cdde), `runs/h150_9d0467d3_baseline.log` (1865483843eefb24), `runs/h150_9d0467d3_mutants.log` (867c49ab5f07893f), `runs/h150_9d0467d3_v2_mutant_logs.tar.gz` (cc03c59ea140275b: the baseline and two judgement files), `runs/h150_9d0467d3_route_tests_search.txt` (256c5108fbf47288).
