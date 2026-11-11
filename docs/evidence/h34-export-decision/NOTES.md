# H-34 follow-up: export decision for `admin_locked_at` (staging only)

Brief: SM -16, 2026-11-11. H-34 added `CustomUser.admin_locked_at` (users 0042).
Staging's student data export guard (NFR-CMP-04) has no decision for it, so
`students.tests_data_export.FieldListGuardTests.test_every_field_is_decided_exactly_once`
fails on origin/staging 1b842050f. Beta has no `students/data_export.py`.

Fix: `"admin_locked_at": _SECURITY` in `EXPORT_OMITTED["users.CustomUser"]`,
next to `locked_until`. No new wording, no migration.

Base: origin/staging 1b842050f69fa3ac38d5d953d09973edfa1a6b71.
Environment: venv ~/Documents/Virtualenvs/h284_deps_env,
`--settings=settings_worktree` (test DB test_h34_export_decision), `--keepdb`,
under `flock ~/.machine-fullsuite.lock`.

Other lists of omitted fields: `students/student_data_map.py` has none (it
lists only `profile_image` for CustomUser). The one other list is the
historical record `docs/evidence/nfr-cmp-04-student-data/NOTES.md:389`; it
records the fields as of that work and is left unchanged.

## Counts (build_suite().countTestCases())

- students.tests_data_export: 46
- students.tests_data_export.FieldListGuardTests: 6
- near (students + users + guards67): 2981

## Expectations, declared before each run

R1 red (unfixed, 1b842050f): students.tests_data_export: Ran 46, FAILED
(failures=1), errors=0. The one failure is
FieldListGuardTests.test_every_field_is_decided_exactly_once, naming
`users.CustomUser.admin_locked_at` as undecided.

R2 green (with the fix): students.tests_data_export: Ran 46, OK.

N1 near (with the fix): students + users + guards67: Ran 2981, OK,
failures=0, errors=0. Skip count not predicted; recorded as seen.

## Results

R1 red: as predicted. Ran 46, FAILED (failures=1); the only failure is
test_every_field_is_decided_exactly_once (label='users.CustomUser'),
"Items in the second set but not the first: 'admin_locked_at'". Log 01_red.log.

R2 green (fix da51ec7e6): as predicted. students.tests_data_export: Ran 46, OK. Log 02_green.log.

N1 near (fix da51ec7e6): students + users + the 67 standing guard labels,
venv h284_deps_env, settings_worktree, --keepdb, --parallel 1, under flock.
Ran 2981, OK (skipped=6). Matches the declared count, failures 0, errors 0;
I did not predict a skip count. Log 03_near_n1.log.gz.

No migration: the change is one dict entry in students/data_export.py.
