# H-38 part 2 — reproduce: what a removed teacher can still do

Base: beta `4b902fc`. Module: `billing/tests/test_h38_part2_removed_teacher_routes.py`
(33 tests, **23 FAIL on beta** = 23 open holes; the other 10 pass).
Log: `01_reproduce_on_beta.log` (one `H38P2` line per probe, sha256 of the full run recorded).

Setup is the part-1 scaffold: school, license, teacher, session, course and student
created through real endpoints with real JWTs, teacher removed through
`remove_teachers`. The assignment, submission and topic are inserted through the ORM.
"Changed" is measured on the row (or, for reads, on the response body), never on the
status code alone. Re-run unchanged against `task/teacher-removal` afterwards.

| Site (route) | Kind | Status | Row changed / data leaked |
| --- | --- | --- | --- |
| `assignments/views.py:310` DELETE /assignments/{id} | DELETE | 204 | row deleted |
| same, PATCH /assignments/{id} | WRITE | 200 | title changed |
| same, GET list / GET {id} | READ | 200 | school assignment returned |
| `assignments/views.py` PATCH associate-topic | WRITE | 200 | topic attached |
| `assignments/views.py` POST publish-all-grades | WRITE | 200 | submission published |
| `assignments/views.py:776` POST upload | WRITE | 400 | course lookup PASSED (error is "no files", after it) |
| `students/views.py:358` DELETE /submissions/{id} | DELETE | 204 | row deleted |
| same, GET list / GET {id} | READ | 200 | submission returned |
| `students/views.py` POST publish | WRITE | 200 | `is_published` set |
| `classrooms/views.py:2392` POST/PATCH/DELETE /topics | WRITE/DELETE | 201/200/204 | topic created / renamed / deleted |
| `classrooms/views.py:1723` POST /course/{id}/topics | WRITE | 201 | topic created |
| `classrooms/views.py:2099` PATCH / DELETE /student-course/{id} | WRITE/DELETE | 200/204 | enrollment status changed / row deleted |
| `classrooms/views.py:2041-2099` GET list, my-students | READ | 200 | school student returned |
| `dashboard/views.py` GET teacher courses/{id}, students/{course}, assignments/{id} | READ | 200 | school stats / student / assignment returned |

Closed already (probe passes on beta):

| Route | Status | Why |
| --- | --- | --- |
| paid AI: `assignments/generate`, `grade-all`, `upload-async`; `submissions/{id}/grade`, `update-grade`, `teacher_feedback`; teacher `custom-ai-prompt` | 402 / 403 | HasCreditBalance / no active subscription. **The credit gate does stop paid AI** for a removed teacher with expired buckets. |
| `students/views.py:158` submission upload against a school assignment | 403 | closed |
| `/school-admin/dashboard/*` | 403 | role check |
| teacher `overview/{session}` | 404 | closed for the school-owned session |

Not probed: `students/views.py:1383` (the file ends at line 1365) and
`dashboard/views.py` 2831/2944/3148/3283 (SchoolAdmin actions and inner helpers, not
teacher-scoped routes; the school-admin surface is covered as a negative control).
`associate_topic` also looks the topic up by bare id (`get_object_or_404(Topic, id=...)`,
no course scope) — noted, not part of this hole.

Common cause: every open route filters by `course__teacher=user`, and removal
leaves `course.teacher` pointing at the removed teacher.
