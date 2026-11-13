# B1b cancel-hold: a door item that ends before its metered unit returns its hold

SM brief 2026-11-12 (the Fixer's hands-on test; a money bug). Branch `task/b1b-cancel-hold` from origin/staging ef1080075. Phase 2, to staging. Venv `~/Documents/Virtualenvs/epic_b1c_env`. Runs: flock, `--noinput`, RUN_LOAD_TESTS / CI_REQUIRE_REDIS / CI_REQUIRE_NETWORK unset. No log files committed.

## The bug

Cancelling an UPLOAD door item that had not started left it HELD: the row was CANCELLED, the job stayed RESERVED, and the wallet stayed reserved by the item's estimate. Only the 36 h backstop would ever return it.

**Root cause.** The D6-c release (`batch_queue.release_unused_item_hold`) acted for BATCH_GRADING papers only. It is called from every place an item ends: `cancel_processing_task`, `mark_processing_task_cancelled`, `mark_processing_task_failure` and `mark_processing_task_success`. So an UPLOAD door item that ended before a metered unit settled it kept its hold. That covers:

- cancelled before it started (from the session, or by the worker seeing the cancel);
- failed before any AI call (for example, an unreadable file);
- finished without an AI call.

## The fix

The release now covers the job types that end at their last item (`LAST_ITEM_JOB_TYPES`: BATCH_GRADING and UPLOAD). The item's hold is returned once (`fail_item`), and the item counts as finished. The door job then closes under the door-close last-item rule: SETTLED if anything settled, else FAILED, with nothing left reserved.

## Other unstarted-HELD states checked (SM ask)

- **Cancelled, failed or finished before the metered unit.** Same root cause; fixed here.
- **Door launch failure on commit, or broker down.** Already handled: `_DoorLaunchOnCommit._settle_failure` calls `fail_item` directly, whatever the job type.
- **Late-door refusal.** No unstarted HELD state. A door item whose hold was returned runs under its own successor UPLOAD job, opened with no up-front hold. A refused call fails that unit (`fail_root`), and the successor job, which the unit owns, is finished FAILED.
- **A row deleted while held.** Handled by `release_open_holds_on_delete` (pre_delete).
- **A file whose hold lost the race (D-12), or that had no estimate.** Never attached and never held.
- **The worker never runs the message** (for example, it is lost in the broker). This is by design: the item sweep handles started items only (review r4 R4-1), so only the 36 h backstop (`backstop_unstarted`) returns it. Not changed here.

## Evidence

- R1 red, `billing.tests.test_holds_door_cancel` (6 tests): Ran 6 FAILED (failures=6). Every failure was `HELD != RETURNED`.
- R2 green, with the door tests (`test_holds_door_cancel`, `test_holds_door_close`, `test_holds_door`, `test_holds_door_commit`, `test_holds_late_door_student`): Ran 41 OK.
- Plants (all killed, files restored):
  - P1, release back to BATCH_GRADING only: 6 FAIL.
  - P2, UPLOAD released only on failure, not on cancel: 5 FAIL.
  - P3, the session cancel does not release: 4 FAIL.
- N1 near, final tree, billing + students + users + guards67. Expected Ran 5922, 11 static skips.
  - Holds unset: Ran 5922 OK (skipped=17).
  - Holds set: Ran 5922 OK (skipped=17).
