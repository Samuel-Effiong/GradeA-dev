# H-234: every my-students number follows the selected session, never the course

Branch task/h234-grade-follows-filters, base 486687ed (beta after batch 19). All times are WAT on 2026-10-14.

Authorship: fix drafted by bd while briefly Builder; adopted unchanged by Builder 13; verified by 28.

## The rule (the user's final decision, through the SM)
On my-students, `grade`, `at_risk` and the three counts (`total_assignments_in_course`, `total_assignments_submitted`, `percentage_of_submission`) are all computed over ONE basis. That basis is the student's non-withdrawn enrolments (H-230) in this teacher's reachable courses, within the selected session (`?enrollments__course__session=`).
- The course filter (`?enrollments__course=`) changes none of these numbers. It only narrows which students appear.
- With no session given, the basis is the whole history.
- `min_grade`, `max_grade`, `at_risk` and `ordering` filter and sort on the same figures.
- "Total" keeps today's meaning: every assignment of the counted courses. Whether drafts count is H-235.

## Superseded commits
2b50899c (tests) and 38563bd9 (the fix) encoded the EARLIER rule: the grade and at_risk also followed the course filter, and the counts stayed on the one "relevant" course. The user replaced that rule. These two commits stay in the branch history, but are superseded by a5fc2125 and 0dbe61e9, which rewrite both files against the final rule. Nothing below was run on 38563bd9. The earlier config is kept outside the repo as GAP-58-runs/h234/config_51c734ab_superseded.py.txt.

## Change
- **a5fc2125, tests only:** classrooms/tests_h234_grade_follows_filters.py, 15 tests.
  - The fixture: the student Wren is in C1 (90%) and C3 (60%) in Term 1, and C2 (30%) in Term 2. That gives 75.00 for Term 1, 30.00 for Term 2 and 60.00 with no session.
  - s1–s3: the session scopes the grade and the counts, including an upper-case session id.
  - k1, k2: a course filter, with or without the session, changes none of the five numbers.
  - w1, w2: a withdrawn course is out of the grade and the counts.
  - n1, n2: with no session, the whole history.
  - r1, r2: at_risk and its filter.
  - g1–g3: min_grade, max_grade and ordering.
  - a1 (control): a one-course student agrees with all-students. All-students has one row per course, so only such a student can be compared with it.
- **0dbe61e9, the fix:**
  - `classrooms.models.grade_basis_enrollments(enrollments, *, session_id=None)` drops WITHDRAWN and keeps only the session's courses. It has no course argument.
  - `StudentListSerializer._counted_enrollments` passes only the session filter.
  - `get_total_assignments_in_course` sums the assignments of the counted courses.
  - `get_total_assignments_submitted` counts the student's submissions in those courses.
  - The percent follows from the two counts. `teacher` still describes the one relevant course.

## Mutants (GAP-58-runs/h234/config.py, copied as `chain_0dbe61e9/config.py.txt`)
| Mutant | What it does | Expected failing set |
|---|---|---|
| J1 | the course leaks into the grade's submissions | {k1} |
| J2 | the course leaks into the total | {k1, k2} |
| J3 | the session is ignored | {s1, s2, s3, w1, r1, r2, g1, g2, g3} |
| J4 | withdrawn courses are kept | {w1, w2} |
| J5 | the counts go back on the relevant course | {s2, k1, k2, w2, n2} |
| J6 | no normalisation of the session id | {s3} |
| J7 | the no-session basis is the first enrolment only | {n1, n2, w2} |

Each written reason was checked against the real assertion's message form and operand types before the run (Tuples differ, Decimal != Decimal, set messages, "First differing element 0:", "True is not false").

## The chain on 0dbe61e9 (logs gzipped in `chain_0dbe61e9/`)
- The Verifier (28) pre-read it and cleared it for its slot. Release Engineer bd granted it at 23:18:52.
- It ran 23:19:10–23:23:22 under one systemd-inhibit, in scope gap-h234-chain with MemoryMax=19G. bd's memory guard recorded no stops.
- Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e. Config sha256 2e56919b02869232e46202195de3c82917cf464ecd1b1f018f0e5ee967d2fb0b.

Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the test file from the tip, on 486687ed | "Ran 15 tests in 1.534s", FAILED (failures=13); "13 failing, as expected"; "the red set is the expected one, each for its written reason" |
| (a) 27 labels | "Ran 438 tests in 96.896s", OK |
| (b) baseline | "Ran 15 tests in 1.616s", OK |
| J1 | FAILED (failures=1); "1 failing, as expected" |
| J2 | FAILED (failures=2); "2 failing, as expected" |
| J3 | FAILED (failures=9); "9 failing, as expected" |
| J4 | FAILED (failures=2); "2 failing, as expected" |
| J5 | FAILED (failures=5); "5 failing, as expected" |
| J6 | FAILED (failures=1); "1 failing, as expected" |
| J7 | FAILED (failures=3); "3 failing, as expected" |

"(b) every mutant killed, each failing set and reason as written"; "h234 CHAIN END: green as written" at 23:23:22. Rule 24: the row's regression is its batch's full run.

## Open, outside this repo
The frontend is not in this repository. Someone who owns it must confirm that the my-students request sends exactly `enrollments__course__session` and `enrollments__course`.

## Files
Every gzip was checked to decompress to its raw original. The logs have no trailing whitespace and contain no credential URLs or passwords.
