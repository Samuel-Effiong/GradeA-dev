# H-288: teacher-summary is scoped by the session (backend only)

Owner-approved 2026-11-05 ("enforce it on the backend so that the frontend
won't have to do anything"). SM does it. Branch
`task/h288-teacher-summary-scope` from beta a3ca097d8; worktree
`Grade-Automator-Plus-h288-teacher-summary-scope`; test DB
`test_h288_teacher_summary_scope` (only this worktree's settings name it).
Venv `~/Documents/Virtualenvs/h284_deps_env` (the one Batch 35 ran on).
NO full suite (owner rule); near runs only.

## Problem

`GET /api/v1/schools/teacher-summary` (Super Admin only) took its teacher
list from `school_id` alone; `session_id` only scoped the counts. The school
page sends only `session_id`, so it got every teacher on the platform, with
other schools' teachers at zero.

## Design

| Rule | Design |
|---|---|
| a. session_id decides whose teachers are listed | look the session up; SCHOOL -> `teachers.filter(school_id=session.school_id)`; INDIVIDUAL -> `teachers.filter(pk=session.teacher_id)` |
| b. school_id still narrows | unchanged filter, applied too; another school's school_id gives an empty page |
| c. unknown session | empty page (list-filter convention already pinned for school_id; no error) |
| d. "teachers of the school" = current roster | SM recommendation, given to the owner: matches the school page's teacher count; a teacher who left is not listed for an old session |
| e. no session_id | unchanged |
| f. no new user-facing wording | none: no new error; OpenAPI parameter text is developer-facing |

## Red tests and expectations (written before the red run, on beta + tests)

Tests: `classrooms.test_views.TeacherSummarySessionScopeTest` (7 new) and
`TeacherSummaryEndpointTest` (10; one renamed and changed:
`..._nonexistent_session_id_returns_empty_page`).

Before the fix (E1), on this commit:
- FAIL (5): `test_school_session_alone_lists_only_that_schools_teachers`,
  `test_teacher_who_left_the_school_is_not_listed`,
  `test_individual_session_lists_only_its_teacher`,
  `test_school_id_from_another_school_gives_empty_page`,
  `test_well_formed_but_nonexistent_session_id_returns_empty_page`.
- PASS (12): the other 3 new tests (guards that the fix keeps today's
  behavior: zero-count teacher listed, matching school_id, no session_id)
  and the other 9 `TeacherSummaryEndpointTest` tests.
- So: Ran 17, FAILED (failures=5), no errors.

After the fix (E2): Ran 17, OK.

Near run (E3) after the fix: `classrooms` +
`AutoGrader.tests_migration_rollback_defaults` not needed (no migration).
Expected: OK, count recorded before the run.

Mutant (E4): remove the INDIVIDUAL branch's filter -> at least
`test_individual_session_lists_only_its_teacher` fails.

## Results

- E1 (red, 92974d0c): **the 5 named tests FAILED, the rest passed** - met.
  Count miss (my arithmetic): `TeacherSummaryEndpointTest` has 9 tests, not
  10, so it was Ran 16, FAILED (failures=5), not 17. Log `logs/red_run_92974d0c.txt`.
- E2 (green, fix 1d7c9f6a): Ran 16, OK - met (count corrected to 16). Log `logs/green_run.txt`.
- E4 mutants, each restored by sha256 (all killed):
  - M-h288.1 INDIVIDUAL filter removed -> 1 failure (individual session).
  - M-h288.2 SCHOOL filter removed -> 3 failures (school-alone, other school_id, teacher who left).
  - M-h288.3 `none()` removed -> 1 failure (unknown session).
- E3 near run (expectation written before the run): `python manage.py test classrooms`
  on 1d7c9f6a: **OK, no failures or errors** (count is recorded from the run).
  Result on 8927e6c1 (same code as 1d7c9f6a): **Ran 694, OK** - met. Summary `logs/near_8927e6c1_summary.txt`; full log GAP-a0-runs/h288_near.log (too large to commit).
