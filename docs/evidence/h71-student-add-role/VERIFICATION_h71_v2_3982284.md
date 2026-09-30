# Verification: H-71 (student-add role disclosure) @ 3982284, beta line

**Verifier:** Verification Engineer 2 (v2), at the SM's request. **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/h71-student-add-role @ **3982284** (off beta abeda10): 5a5e4c3 the change, 03bebb3 the penetration test, plus the harness and evidence. The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot. Beta guard set: AutoGrader repo-wide guards + the classrooms sweeps (beta has no audit guards).

**Verdict: VERIFIED.**

## Static
- All four sites now raise `NOT_A_STUDENT_MESSAGE` = "This email can't be added as a student.": the single-add serializer, the direct-add serializer, and the enrolment service used by bulk import.
- **Direct add** refuses every non-student role at validation (it used to check teachers only, so an admin address passed and failed later as a **500**, and the status code itself told the roles apart).
- **The log:** `Refused to enrol non-student account %s (%s) in course %s` with the account **pk**, its user_type and the course pk. No email.
- **The changed penetration test** (`test_bulk_import_cannot_hijack_an_existing_teacher_account`) is **not weakened**. It still asserts the teacher keeps its type, isn't enrolled, and `failure_count == 1`; the only change pins the exact neutral message instead of `"teacher" in error` (stricter).

## Evidence
| Check | Result |
|---|---|
| v2 probes (`tests_vf2_h71_probe.py`) + `classrooms.tests_h71_student_add_role` + `classrooms.tests_security_penetration` + AutoGrader guards + the classrooms sweeps | **110 OK** |
| V1: per route, (status, body) for a TEACHER / SCHOOL_ADMIN / SUPER_ADMIN address | **byte-identical across the roles**: single add 400, bulk import 200 (per-row failure), direct add 400; no role word (`teacher/admin/super/staff/…`); **no 500** |
| V2: an UPPER-CASE, whitespace-padded variant of each staff address | the same neutral answers; no enrolment, no 500 |
| V3: a DEACTIVATED school admin | refused the same way (400/200/400); stays SCHOOL_ADMIN, never enrolled |
| V4: every log record (all loggers, INFO+) across every route and role | 9 records, **0 contain a staff email** |
| V5: control, a student address via single add | 200, enrolled |
| v2 mutants (`vf_h71_mutants.py`) | **2/2 KILLED**, by ed's tests too: direct add + single add narrowed back to TEACHER-only (the admin roles diverge); the refusal log carrying the email |
| ed's gates | the prefix, mutation and classrooms regression recorded in EVIDENCE (the first gated run stopped on the role-word test, since fixed) |

Logs: `runs/h71_run1.log`, `runs/h71_run2_mutants.log`.
