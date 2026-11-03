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
Expect `OK`, with no change to any existing test. The exact count is written
into the evidence, from `Found N test(s)`, before the result is read.
