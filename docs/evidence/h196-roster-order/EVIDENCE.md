# H-196: the lists of enrolments have one defined order

Written 2026-10-09 by d5 from the files in `gate_files/` (raw, gzipped without change; scripts as `.txt`). Branch `task/h196-defined-roster-order`, base beta `5e37ac2b`, gated tip `c388dfd9`; commits after it are docs only.

## The fault, in plain words
The list of students in a course (the course detail and the course list) and a student's list of courses (the teacher's my-students list) were built from enrolments with **no order**. PostgreSQL returned them in whatever order its plan gave. Two reads of the same data could therefore list the same students in a different order. beta's CI went red once on this (`AutoGrader.tests_cache_commit_race ... settle_fresh_every_round`, run 37810517413: the cached answer and the fresh answer differed in the first student's id).

## What is SHOWN, what is READ, what is NOT shown
- **Shown by a run (the probe, 2026-10-08 18:13, `gate_files/probe/`):** on the unchanged code the order of `students` in the course detail depends on the query plan. With the same 12 students, under the sequential-scan plan the row that had just been updated moved to the END of the list; under the merge-join plan the list came out in ascending order of the ids shown (the student ids; the enrolment ids were not printed); under the default plan, before and after the update and on the list route, it was the same. The probe failed (red) exactly as it is built to when an order differs.
- **Read, not shown:** that the CI failure of run 37810517413 was this fault. The log's message is shortened by unittest, so the differing field cannot be recovered; the equal length of the two answers and the first student's id differing fit "same students, other order". It was never reproduced.
- **Order in practice before:** only what the probe saw (above). I did NOT compare it with the insertion order, so I do not say "insertion order mostly".
- **Not checked:** whether the frontend depends on any particular order; which of the other unordered lists are cached (see the follow-up row below).

## The change
One definition in `classrooms/models.py`: `ENROLLMENT_LIST_ORDER = ("created_at", "id")` (oldest enrolment first, the id as tie-break), the queryset method `StudentCourse.objects.in_list_order()`, and `enrollment_list_key()` for rows already in memory. Used at: the course prefetch (`classrooms/views.py`, course detail and list), the serializer's fallback query (`classrooms/serializers.py`), and `StudentListSerializer._enrollments` (a Python sort of what it reads, so a student's courses in my-students come oldest enrolment first). The Senior Manager ruled the order (created_at ascending, then id) so that the behaviour teachers have is dependable and not a new one; the order is a product choice and is one line to change (the constant) with its tests.
- The neighbouring route `StudentCourseViewSet` sorts **newest first** (`-created_at`, `id`). The course detail keeps **oldest first** because that is the nearest thing to what it showed.
- `classrooms/views.py` my-students prefetch is **deliberately left unordered** (a comment says so): the order is applied where the enrolments are read, and a second order there could not be told apart from it by any test.
- `CourseSerializer._own_entry` (a student's own entry) carries the call too but holds at most one row (unique student/course): untestable, said in the test module.

## Gates on `c388dfd9` (all on the Release Engineer's grants; rule 22 reasons written beforehand)
| Step | Result |
|---|---|
| (r) the module on base `5e37ac2b` | 10:21:40 to 10:22:00 Oct 9: 8 failing as expected, **each for its written reason** ("Lists differ" at its own comparison; AttributeError on `ENROLLMENT_LIST_ORDER` for the definition test) |
| (a) 61 labels | Ran 939 tests in 449.665s, OK, 0 FAIL/ERROR lines (includes `AutoGrader.tests_cache_bespoke_1114`, the roster cache modules, the changed routes' modules, the guard list) |
| (b) 7 mutants N1 to N7 | 7/7 killed with verified restore; every failing set the expected one (N1 3, N2 2, N3 2, N4 3, N5 4, N6 2, N7 4) |
| (c) `c_h196.sh`: AutoGrader, classrooms, students, dashboard, parallel 2 | 10:31:40 to 10:38:21: Ran 1887 tests, OK (skipped=5), exit 0, stalled 0 |

**The first chain run (10:02) stopped at (a) with no test run:** the new worktree had no `settings_worktree.py` (my set-up omission); the stopped logs are in `gate_files/first_stop_nosettings/`.

## Limits of the tests, stated
- The tests make the defined order the REVERSE of the order the rows were inserted in, so a database that returns insertion order fails them on the old code. N7 (the queryset orders by id only) is expected to fail the created_at tests only because random uuid4 ids are in the reverse of the created order with probability 719/720 per test.
- A mutant on the my-students prefetch does not exist, by design (see above).
- No browser or frontend was involved.

## Follow-up row (LOW, number from the Release Engineer, H-197): lists ordered by a non-unique key
Add a unique tie-break to: `StudentSubmission` (`-submission_date`), `Topic` (`name`), `Assignment` (`title`), `Course` (`name`), `AssignmentGenerationMessage` (`created_at`), `PlanFeatureInclusion` (`plan`, `display_order`), `SubscriptionPlan` (`category`, `tier`). The unordered `submissions` prefetches (`classrooms/views.py` students list) belong to the same row. **Which of these routes are cached: not checked.** No work done on it here.

## What a frontend note can say
"The order of students in the course detail and the course list, and of a student's courses in my-students, is now defined: by the time the enrolment was made, oldest first (then by the enrolment's id). It used to be undefined." (Not checked against the frontend.)
