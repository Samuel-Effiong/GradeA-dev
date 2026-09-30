# Verification: Epic A S6c @ 262d1f6

**Verifier:** Verification Engineer 2 (v2). **Author:** 1a. **Date:** 2026-09-30.
**Branch:** task/epic-a-s6c @ 262d1f6 (code 280571c + fix 7c7832f, on epic be1147a). Design: 08a §1 #1/#2, §2.2 rows 1–2, §5 S6c, F2, F3; the SM's tenancy condition.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at 262d1f6. Rule 15: v2's probes, mutants and the changed modules. 1a's students regression (274 OK at 7c7832f) and the modules outside students (144 OK) are cited.

**Verdict: VERIFIED-WITH-NOTES** (N1 is cosmetic).

## Static
- **Lookup scope (tenancy):** `_teachers_own_students(teacher)` = students with an enrolment (any status) in a course where `course.teacher == teacher` **and** the course is still reachable (H-38 `teacher_course_access_q`, in ONE filter call). It is never `teacher.school`, never global.
- **Order:** the ENROLLED roster first (unique; ≥2 → MISSING "matches more than one student"). Only if nobody matches, the teacher's own students: exactly one → STUDENT_NOT_ON_ROSTER, `student_display` = first + last name only; none or several → MISSING "doesn't match anyone on the roster" (nobody named). Only the paper's own text is quoted in `name_state`.
- Both types are `CodedError` + `CannotAssociateStudentError` (so they stay in UPLOAD_REFUSALS, never retried), 422.
- F3: DUPLICATE_SUBMISSION is defined, with no raise site. `replaced_existing` goes in the tracked task's meta; lifting it into session results is S7a (stated in EVIDENCE). F2: no `resolutions`.

## Evidence
| Check | Result |
|---|---|
| v2 probes (`tests_vf2_s6c_probe.py`) + `students.tests_identity_reason_codes`, `tests_proxy_upload_attribution`, `AutoGrader.tests_reason_codes`, `assignments.tests_upload_task_retry_policy` | **75 OK** |
| C1 no name / C2 unmatched / C3 ambiguous on the roster | MISSING_STUDENT_NAME with the matching `name_state`; C2/C3 quote the name read |
| C4 the teacher's student PENDING in this course / C5 enrolled in the teacher's other course | STUDENT_NOT_ON_ROSTER, `student_display` "Pending Pupil" / "Other Course" |
| C6 unique enrolled + an off-roster namesake of the same teacher | the enrolled student wins |
| **T1** a colleague's student, same school, exact name / **T2** another school's student | **MISSING_STUDENT_NAME**; their email, local part and id appear nowhere in the message or params |
| T3 | no submission is created on any refusal |
| D1 | `DUPLICATE_SUBMISSION` ∈ ReasonCode; no raise site in non-test code; a second proxy upload for the same student overwrites the same ungraded submission (F3) |
| v2 mutants (`vf_s6c_mutants.py`, 4) | **4/4 KILLED** by 1a's own tests (and v2's): Z1 the lookup widened to `teacher.school`; Z2 widened to every student; Z3 the off-roster lookup ENROLLED-only (PENDING/withdrawn become MISSING); Z4 `student_display` including the email |
| 1a's gates | reproduce-first 14/15 fail on be1147a; students 274 OK; outside 144 OK; 7/7 mutants (committed) |

## Notes
- **N1 (cosmetic).** When no file name is known (`UNNAMED_PAPER`), the #2 message starts in lower case: "the paper belongs to Pending Pupil, who isn't enrolled in this course." The template opens with `{file_name}`. Real proxy uploads pass the file name (7c7832f), so this only shows in that fallback. It could be fixed by capitalising the first character when rendering, or by a QA catalogue tweak.

Logs: `runs/s6c_run1.log`, `runs/s6c_run2_mutants.log`.
