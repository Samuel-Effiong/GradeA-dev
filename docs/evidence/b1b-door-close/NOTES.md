# B1b door-close: an UPLOAD door job closes at its last item

SM brief 2026-11-12 (found by the Fixer's hands-on B1b test). Branch `task/b1b-door-close` from origin/staging 43216577d. Phase 2, to staging. Venv `~/Documents/Virtualenvs/epic_b1c_env` (same requirements as staging 43216577d). Runs: flock, `--noinput`, RUN_LOAD_TESTS / CI_REQUIRE_REDIS / CI_REQUIRE_NETWORK unset. No log files committed (owner rule 2026-11-11).

## The bug

A door job (teacher batch upload, student upload-async, assignment upload-async) stayed RESERVED after every item had settled or failed. The last-item helper (`batch_queue.decide_after_item`, through `holds._after_batch_item` and `batch_queue.after_batch_item`) acted for BATCH_GRADING only, and the backstop's call to it was a no-op for UPLOAD. So `billing_holds_drain` never reported DRAINED.

## The fix

- UPLOAD jobs use the batch job's last-item rule (SM ruling C4): when no item is held or waiting and every held item has ended, the job ends SETTLED if anything settled, else FAILED, and returns what is left of its hold.
- **A second problem, found while fixing the first.** The teacher batch and assignment doors launch each file as soon as it is held, so an early file can end before the last file is attached. A door job therefore closes only when it holds at least its `total_items`. Each door request then calls `seal_door_job` (through `DoorHold.seal()`) after its loop. Seal sets `total_items` to what the job actually holds, since a file the door could not estimate, or whose hold lost the race (D-12), never joined the job. If every file has already ended, seal closes the job then.
  - The student single upload holds its one file inside its own transaction, so it needs no seal.
- The backstop seals a stalled UPLOAD job instead of calling the no-op hook. That closes a door job whose request died before sealing, and one left open by the old code (the Fixer's shape).
- **A choice to confirm.** The brief said "settles" for any mix of outcomes. If every file failed, the job ends FAILED rather than SETTLED (the C4 rule, as for batch jobs). Either way the hold is returned and nothing is reserved.

## Evidence

- R1 red: tests first (`test_holds_door_close`, 9 new tests). The edited `test_holds_door` granted-hold test had asserted the job stays open, which was the bug. Result: Ran 20 FAILED (failures=9, errors=1).
- R2 green, with the door tests (`test_holds_door_close`, `test_holds_door`, `test_holds_door_commit`, `test_holds_late_door_student`): Ran 35 OK.
- Plants (on `test_holds_door_close`; all three killed, files restored):
  - P1, hook back to BATCH_GRADING only: 6 FAIL, killed.
  - P2, no wait for the request to attach every file: 2 FAIL, killed.
  - P3, backstop does not seal: 1 FAIL, killed.
- N1 near, final tree, billing + students + users + guards67 (there is no `submissions` app; submissions live in `students`). Expected Ran 5916 (billing 2945, students 1140, users 1137, guards67 1151, overlap removed), 11 static skips.
  - Holds unset: Ran 5916 OK (skipped=17).
  - Holds set: Ran 5916 OK (skipped=17).
