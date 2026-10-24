# Verification by Verifier 2 (v2): H-196, the lists of enrolments have one defined order

Tip verified: `2f9dfc1d` (H-196 on beta `8567a7a0`; code `2c6db873` plus the comment `c388dfd9`, then the merge of beta; the rest is evidence). Author: d5.
Run: Release Engineer's slot 2026-10-09 12:44:58 to 12:45:53 (load 3.21 at start). Probe `d0bd7ba2fe34ee62`, runner `eacf0d689eab0032`, script
`016307e80d7058d6`, written before the run. The author's chain (939 OK, 7 of 7 mutants) and `c_h196` (Ran 1887 OK) were NOT repeated (rule 15).

## Word: VERIFIED-WITH-NOTES

## What I checked
1. **Reading.** The order is defined once: `ENROLLMENT_LIST_ORDER = ("created_at", "id")`, `StudentCourseQuerySet.in_list_order()` and `enrollment_list_key()` in
   `classrooms/models.py`; used at the course prefetch (`classrooms/views.py`), the two serializer fallbacks (`classrooms/serializers.py`) and
   `StudentListSerializer._enrollments` (`students/serializers.py`, sorted in memory). The two reasons the author gives for leaving things as they are hold:
   the my-students prefetch (`classrooms/views.py` ~2113) is left unordered because EVERY reader of a student's enrolments in `StudentListSerializer` goes through
   `_enrollments` (lines 825, 847, 863), which sorts whether or not a prefetch exists; and `CourseSerializer._own_entry` holds at most one row because
   `StudentCourse` has `UniqueConstraint(student, course)`. UUIDs order the same way in Postgres and in Python.
2. **Rule 22.** The author's per-test failure fragments (`expected_kills.py`, written 8 Oct 19:23, before the red run of 9 Oct 10:22; the file before rule 22 is kept) are looked for
   inside each test's own block. In the raw red log I checked them myself: the seven order tests fail at their final list comparison ("Lists differ"), the definition test with an AttributeError.
3. **Seven tests of mine, baseline Ran 7 OK on the tip:** c1 course detail `students`, c2 course list `students`, c3 a created_at tie broken by id, c4 the serializer's fallback with no
   prefetch, c5 my-students `enrolled_courses`, c6 the course a my-students row is about (the oldest enrolment's), c7 a characterisation of the student-course list. The order is made
   observable as in the author's tests (the defined order is the REVERSE of insertion); my ids ascend with insertion so an order by id is also not the defined one.
4. **Nine faults, failing set equal to the written one each time (restores 10 of 10):** Hv course prefetch unordered {c1,c2,c3}; Sf serializer fallback unordered {c4}; Ko order by id
   only {c1,c2,c4}; Kd newest first {c1,c2,c4}; Kt tie broken by descending id {c3}; Kk in-memory key by id {c5,c6}; Kr students serializer unsorted {c5,c6}; Mv student-course list
   oldest first {c7}. **Kq SURVIVED as predicted:** an order added at the my-students prefetch (the other direction) changes nothing visible, which proves the author's reason that the
   serializer sorts what it reads.

## Roster routes other than the ones the author tested (the Senior Manager asked for every one)
- **Ordered by the row:** course detail and list `students`; the serializer fallback; my-students `enrolled_courses` and the course a row is about.
- **NOT ordered (finding f1, seen red):** the teacher's assignment detail `student_submissions` (`assignments/serializers.py` 480-483: one entry per enrolled student, from
  `StudentCourse.objects.filter(course=...)` with no order). On the tip my test fails at its final list comparison: got `Student0..Student5` (insertion order), expected
  `Student5..Student0`. A mutant that adds `.in_list_order()` there (Fx) makes it pass. By the Senior Manager's ruling this is row **H-213** (owner d5, tests first, the ruled order through
  the same single definition; batch 15's package names it under "not cured"). Whether withdrawn rows are meant to appear in that list: by reading, nothing in this repository reads it except
  the serializer that serves it and an OpenAPI example (`assignments/views.py` 215); the reader is the teacher's client, outside this repository, so the backend cannot say. The list was built on
  2026-03-31 (`a96dc416`) from a query with no status filter, so withdrawn and pending students are included; `CourseSerializer.students` excludes WITHDRAWN; I did not read whether the
  dashboard's course-performance list (`dashboard/views.py` ~3611) drops withdrawn rows later. Intent unknown; the order fix must not change it.
- **Newest first, by decision:** the student-course LIST route orders `("-created_at", "id")` with its own comment about pagination: deterministic, deliberate, opposite to the ruled direction.
  The Senior Manager ruled it stays (the ruling was for a class's roster). My c7 records it as it is.
- **Name order, by decision:** the dashboard's course-performance student list is sorted by name; it stays.

## Notes
- N1: the order tests rely on made-up created_at values and on the database returning rows in insertion order for a small table; Postgres can return other orders, but the defined order is the
  reverse of insertion, so any plan that matches it would have to be by accident (six rows, a one-in-720 chance for a random plan).
- N2: the cache is patched out in every probe test, so no cached answer stands in for the order (the CI red that led to H-196 was a cached answer against a fresh one).

## Files
`probe`, `runner`, `script`, driver and mutant logs (gzipped) in `logs/`.
