# H-232: all-students scoping with the foreign course in the requested session (tests only)

Found by the Verifier by reading the user's commits fafed57f..913f248f (T1); the SM made it a tests-only row for the small batch after batch 18. Branch task/h232-all-students-scoping-tests from beta 8a64c35b.

## The gap (read on 8a64c35b)
all-students builds its courses as `reachable_courses(teacher).filter(session_id=...)` (dashboard/views.py, `all_students`). The existing tests put every foreign course in ANOTHER teacher's own session while the request names the requester's session, so the session filter alone hid it: by reading, no test failed if `reachable_courses(teacher)` became `Course.objects`. The code itself is correct; this row pins it.

## Change
- 6f42e9fe tests only: dashboard.tests_h232_all_students_scoping, one school with two teachers and a SCHOOL session holding a course of each. (a) a teacher names another teacher's own INDIVIDUAL session: set(); (b) a colleague's course in the same school session is not mine; (c) a teacher removed from the school (school=None) loses the school session (H-38); (d) a colleague's course_id in the shared session is a 404. Each test first shows the hidden student IS visible to the right teacher, so an empty answer is empty for the scoping reason.

## Mutant (GAP-58-runs/h232/config.py, copied as `chain_6f42e9fe/config.py.txt`)
Q1 (the Verifier's): `reachable_courses(teacher)` -> `Course.objects` in all_students. Written before any run: a, b, c fail with "Items in the first set but not the second:" (b and c at their first assertion), d with "200 != 404"; the existing tests_teacher_all_students does not move.

## The chain on 6f42e9fe (a6's GRANT 15:04 WAT; logs gzipped in chain_6f42e9fe/)

Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e; config `config.py.txt` sha256 5aaf2351dfc7b18e33bc96109f556a6736314b532b63162c4ac4c5b7eea0630e (Verifier pre-read: clear). One systemd-inhibit around the whole chain. Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) | none: tests only, green on the base by design |
| (a) 13 labels | "Ran 182 tests in 53.377s", OK |
| (b) baseline (2 modules) | "Ran 22 tests in 2.906s", OK |
| Q1 reachable_courses -> Course.objects | "Ran 22 tests in 2.469s", FAILED (failures=4): a, b, c, d; "4 failing, as expected" |

"(b) every mutant killed, each failing set and reason as written"; "h232 CHAIN END: green as written" at 15:09:03. Rule 24: the row's regression is its batch's full run.
