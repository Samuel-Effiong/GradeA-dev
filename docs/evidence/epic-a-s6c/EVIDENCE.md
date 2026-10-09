# Epic A S6c: the identity codes #1/#2 (FR-A-06, 08a §5; F2/F3). Author's evidence

**Author:** 1a (grade-automator-plus-c2), assigned by the SM on 2026-09-30. **Verifier:** v2. The author does not verify this.
**Branch:** `task/epic-a-s6c`, created from phase2/epic-a `be1147a` (S6b merged) with `scripts/task-worktree.sh new epic-a-s6c phase2/epic-a` (the base-ref fix). The change is `280571c`, plus `7c7832f` (two fixes the first regression found; see Runs).

## What changed
When a teacher's batch item can't be attributed to a student, it now fails with its own code. Before, one uncoded `CannotAssociateStudentError` covered three situations, and a real student who wasn't enrolled read the same as an unknown name.

| # | Code | Raised for | `params` |
|---|---|---|---|
| 1 | `MISSING_STUDENT_NAME` (422, USER) | No name on the paper; a name that matches nobody on the roster; a name that matches more than one enrolled student | `file_name`; `name_state`, one of: "no name was found on the paper", `the name "…" doesn't match anyone on the roster`, `the name "…" matches more than one student`. The quoted text is the **paper's own**, whitespace-collapsed |
| 2 | `STUDENT_NOT_ON_ROSTER` (422, USER) | The name is uniquely one of the **uploading teacher's own** students outside this course's roster: pending or withdrawn here, or in another of their courses | `file_name`; `student_display` ("First Last" as stored) |

**The lookup** (`students/services.py`):
1. The ENROLLED roster of this course, exact then substring, unique only (unchanged). One match is returned; two or more are ambiguous (#1).
2. Only when nobody matches: `_teachers_own_students(teacher)`, meaning students enrolled **in any status** in courses the uploading teacher owns and can still reach (`teacher_course_access_q`, H-38). Exactly one match gives #2. None, or several, gives #1 "doesn't match anyone", because naming one of several would be a guess.

**Tenancy (SM ruling, 2026-09-30):** the second lookup is **never** the school. A colleague's student in the same school, or another school's student, answers #1, and nothing stored about them reaches the message or params: not their name as stored, email, email local part or id. The paper's own text is quoted; the tests make its casing differ from the stored name, to prove that nothing comes from the database. A unique enrolled match in this course still wins over an off-roster namesake.

**Types** (`students/exceptions.py`): `StudentNameUnmatchedError` and `StudentNotOnRosterError` are `CodedError` + `CannotAssociateStudentError`. So they stay in `UPLOAD_REFUSALS` (never retried) and are user-facing. Their `status_code` matches their spec (422).

**The file name** reaches the message through `upload_answers_engine(file_name=…)` from the batch task (`file_name`, or the upload's own name). A direct service caller with none gets "the paper".

**F3 / `replaced_existing`:** `DUPLICATE_SUBMISSION` is **defined and never raised**; a static test scans every production module. The silent overwrite of an ungraded submission is unchanged. The SM's informational flag is written where an item-result dict **already exists on this path**: the tracked task's `meta` (`replaced_existing: true|false` on every successful answer upload), which the task-status endpoint (`GET tasks/status/{task_id}`) already serves. **Session-results does not show it yet.** Its per-item entry is built from the task's fields, not its meta, so lifting the flag into that shape is **S7a** (08a §4.3).

**F2:** there is no assign-student resolve and no kept extracted answers; the teacher re-uploads. `resolutions` is not emitted.

## Existing tests changed (asserting the coded answer, never weaker)
| Test | Was | Now |
|---|---|---|
| `tests_proxy_upload_attribution.test_student_who_is_not_enrolled_is_not_matched` (a PENDING student) | "not among the enrolled" | raises `StudentNotOnRosterError` (still a `CannotAssociateStudentError`), "isn't enrolled in this course": more precise, still refused, never matched |
| `…test_missing_name_is_reported` | "cannot be found" | raises `StudentNameUnmatchedError`, "no name was found" |

`tests_task_tracking` and `tests_error_messages` build the base `CannotAssociateStudentError` directly and are unaffected.

## Coverage: each touched production file, and the modules that test it
| Production file | Covering modules |
|---|---|
| `students/services.py` (`_match_enrolled_student`, `_name_matches`, `_teachers_own_students`, `upload_answers_engine`'s `file_name`/`upload_outcome`) | `students.tests_identity_reason_codes`, `students.tests_proxy_upload_attribution`; the `students` regression |
| `students/exceptions.py` | `students.tests_identity_reason_codes`, `students.tests_proxy_upload_attribution`, `students.tests_task_tracking`, `AutoGrader.tests_error_messages` |
| `assignments/tasks.py` (`file_name` passed, `replaced_existing` in the success meta) | `students.tests_identity_reason_codes` (through the real task), `assignments.tests_upload_task_retry_policy`, `assignments.tests_file_reason_codes`, `assignments.tests_upload_batch_billing` |
| Endpoints that serve the result (unchanged code) | `users.tests_task_status_scoping`, `users.tests_task_viewset`, `users.tests_remaining_branches`; `classrooms.tests_student_summary_tracking` |

## Runs (rule 15; each under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, its own test DB, `EXEMPT_EMAIL_DOMAINS` empty)
| Run | Tree | Result | Log |
|---|---|---|---|
| Reproduce first: the final new module on the **unchanged** epic tip | `be1147a` | **14 of 15 fail** (12 failures, 2 errors). The one that passes is the static `DUPLICATE_SUBMISSION` check, true on the base too. On the base, every case raised the one uncoded `CannotAssociateStudentError` | `repro_be1147a.txt` |
| Changed modules outside `students` (the coverage table) | `280571c` | **Ran 144, OK**, 278 MB peak | `changed_modules.txt` |
| ONE owning-app regression: `students` | `280571c` | **FAILED 2**, both caused by this slice: (a) my `replaced_existing` test gave its task a Celery id with dots and spaces, which the status route rejects (NoReverseMatch); (b) `students.tests_upload_pipeline` hands the task a bare `object()` as the rebuilt file, and my change read `uploaded_file.name` from it, so the task raised and retried | `regression_students_280571c_FAILED.txt` |
| Fix `7c7832f`: `getattr(uploaded_file, "name", None)` (the service then says "the paper"); a uuid Celery id in the test | | | |
| `students` regression again | `7c7832f` | **Ran 274, OK** (skipped 1), 254 MB peak | `regression_students.txt` |
| `assignments.tests_upload_task_retry_policy`: the one changed module outside `students` that the one-line task fix touches (the task path; behaviour is identical for any upload with a name) | `7c7832f` | **Ran 8, OK** | `SUMMARY.txt` |

`SUMMARY.txt` is both run sequences. The logs are trimmed to the test ids that ran plus the summaries, with emails redacted.

## Mutants (author's)
`mutate.py`; results in `mutants.txt` (at `7c7832f`). Each is restored with a sha256 check. **7 of 7 killed, each by the tests aimed at it.**

| Mutant | Killed by |
|---|---|
| C1 the off-roster lookup removed | the 3 NOT_ON_ROSTER tests, the pending-student proxy test and the batch-isolation test |
| C2 **tenancy widened** to every student | `test_a_colleagues_student_in_the_same_school_is_never_named`, `test_another_schools_student_is_never_named` |
| C3 an ambiguous enrolled match is guessed | the ambiguity test and 3 proxy-attribution tests |
| C4 two off-roster namesakes: one named anyway | `test_two_out_of_roster_matches_are_not_a_guess` |
| C5 the task drops the file name | 9 tests (each message names the file) |
| C6 `replaced_existing` never set | `test_a_second_upload_for_the_same_student_replaced_the_first` |
| C7 the roster widened to any enrolment status | the pending, withdrawn and ambiguity tests, the proxy test and batch isolation |

`mutants_280571c_superseded.txt` is the first battery, kept for honesty. It ran before the fix, when the `replaced_existing` test errored on the route for every mutant. So its kills by that one test were not real, and C6's was only that. The battery above is the one that counts.
