# H-282 NOTES

The design is in `DESIGN.md`. The base is `162a43e66`. The test DB is
`test_AutoGrader_h282`, with `settings_worktree.py` (untracked).

B33 is running its full run, so only small serial runs are allowed now, with
at least 4000 MB available. The near run and the planted-bug sweep are held
until the SM says B33 is done.

## Red tests: `students.tests_h282_upload_door_superadmin` (12)

| Class | Tests | Before the fix |
|---|---|---|
| `WhoIsARealSuperAdmin` | 4 | error: `CustomUser` has no `is_real_super_admin` |
| `TheSingleDoor` | a1, a2 | fail: the door returns `None` |
| | b1 (control) | pass |
| `TheBatchDoor` | a3, a4 | fail: the door returns `{}` |
| | b2 (control) | pass |
| `TheRoutesRefuseASuperAdminFirst` | r1, r2 (characterization) | pass (403, nothing queued) |

## E1: red run at the red commit (expectations, written before the run)

Serial, label `students.tests_h282_upload_door_superadmin`. Expect `Ran 12`,
`FAILED (failures=4, errors=4)`:
- the 4 errors are the 4 `WhoIsARealSuperAdmin` tests (`AttributeError`);
- the 4 failures are a1, a2, a3 and a4;
- b1, b2, r1 and r2 pass.

## E2: green run after the fix (expectations, written before the run)

Serial, the same label. Expect `Ran 12`, `OK`.

## E3: small serial check (expectations, written before the run)

Serial, labels `students.tests_upload_credit_door` and
`billing.tests_h281_individual_credit_wording` together with the H-282 module.
Expect `OK`, with no change to any existing test.

Count (from the test loader, before any run): 17 + 44 + 12, so expect
**`Ran 73`, `OK`**.

## E1 result (dff9b2d7a): met

`Ran 12`, `FAILED (failures=4, errors=4)`, exactly as predicted. The 4 errors
are `AttributeError: 'CustomUser' object has no attribute
'is_real_super_admin'`. The failures are a1 and a2 (`unexpectedly None`) and
a3 and a4 (`[] != [0]`). The log is `logs/red_run_dff9b2d7.txt`.

## Fix commit (4906b192f)

- `users/models.py`: `CustomUser.is_real_super_admin()`, next to
  `is_teacher()`.
- `assignments/upload_door.py`: lines 87 and 121 become
  `if request_user.is_real_super_admin():`.

Nothing else changes. There is no new wording and no migration.

## E2 result (037afed7e): met

`Ran 12`, `OK`. The log is `logs/green_run_037afed7.txt`.

## E3 result (037afed7e): met

`Ran 73`, `OK`. No existing test changed. The log is
`logs/small_check_037afed7.txt`.

## E4: planted bugs (expectations, written before the sweep; held until B33 is done)

**Baseline.** At this commit, the E3 labels (73 tests), serial. Expect
`Ran 73`, `OK`.

**Sweep.** One planted bug at a time, in a disposable detached worktree at
this commit, DB `test_AutoGrader_h282_mut`, the same 73 tests, serial. Each
file is restored and checked by sha256 after each bug. Every bug must be
**killed**. "At least" means other tests may also fail; any extra killer is
listed in the results.

| Bug | Change | Expected killers (at least) |
|---|---|---|
| M1 | predicate: drop `self.is_superuser and` | `test_a_half_set_up_super_admin_is_not`, a1, a2, a3, a4 |
| M2 | predicate: drop `and self.user_type == UserTypes.SUPER_ADMIN` | `test_a_teacher_with_is_superuser_ticked_is_not` |
| M3 | single door (line 87) back to `request_user.user_type == UserTypes.SUPER_ADMIN` | a1, a2 |
| M4 | batch door (line 121) back to `user_type` only | a3, a4 |
| M5 | single door: early return removed (`if False:`) | b1, `tests_upload_credit_door.TheDoorFunctionTest` |
| M6 | batch door: early return removed (`if False:`) | b2 |

## E5: near run (expectations, written before the run; held until B33 is done)

The labels are in `logs/near_labels.txt`: 19 modules, **220 tests** (from the
test loader). They cover:
- the upload-door and upload modules in `students/` and `assignments/`;
- the H-281 and H-282 modules;
- the both-flags modules (`ai_processor.tests_real_superadmin_unmetered`,
  `users.tests_models_and_admin`, `classrooms.tests_my_students_course_scope`,
  `assignments.tests_course_ownership_concurrency`).

DB `test_AutoGrader_h282`. It runs serially, or with `--parallel` only if the
SM allows it after B33. There is no migration in the branch diff, so
`AutoGrader.tests_migration_rollback_defaults` is not required. Expect
`Ran 220`, `OK` (skips are allowed if they are listed).
