# H-180: gates on `90b5c763` and the stops on the way (written 2026-10-09 from the files in `gate_files/`)

Tip gated: `90b5c763` (tests-only fixes `cb2eb573`, `90b5c763` over the first tip `657e2d72`). Base beta `035e0a07`; it needs a clean base update onto current beta before it merges (Release Engineer's note).

| Step | Result |
|---|---|
| (r) the module on base `035e0a07` | 11:40:25 to 11:40:44 Oct 9: 11 failing as expected, each for its written reason (rule 22) |
| (a) 930 tests | 11:40:44 to 11:44:52: Ran 930 tests in 222.544s, OK, 0 FAIL/ERROR |
| (b) 18 mutants | 11:44:52 to 11:46:47: 18/18 killed, restore verified; 17 failing sets as written, M16 killed by 3 tests (corrected set in `CORRECTED_SET_M16_made_after_the_regression.md`) |
| (c) `c_h180.sh`, seven apps, parallel 2 | 11:59:53 to 12:13:05: Ran 5880 tests in 746.685s, OK (skipped=26), exit 0, stalled 0 |

## Stops, each with its one-sentence cause (all mine, tests only; the code of the change was frozen at `846124c3`)
1. First run on `657e2d72` (`first_stop_a_657e2d72/`): step (a) failed 2 tests: my new no-wallet test assumed a teacher without a wallet while `users/signals.py:325` gives every new user an empty one (the door rightly refused a wallet at 0); and an old test of the student upload task still expected the generic credit text where a student now reads the fixed sentence.
2. Second run on `cb2eb573` (`second_stop_cb2eb573/`): step (a) failed 2 tests because my fix of the old test landed on the wrong one (the edit task's instead of the upload task's), found by reading the failure; the diff of the corrected fix was read back before the commit.
3. Third run on `90b5c763`: green except the M16 set, ruled to stand.

## What the no-wallet branch is
Which real accounts can have NO wallet (the "left to the permission" branch): the signal's wallet creation is in a try/except that continues on failure; accounts created before the signal (commit `da3a1afd`, 2026-06-30) have one only if a backfill ran (none found); bulk-created users skip signals (a scale harness only); no non-test code deletes a wallet. Reachability in production is **not checked** (needs the database; a read-only count query for the founder is in `READONLY_QUERY_NO_WALLET_FOR_THE_USER.sql.txt`, NOT run by us). The branch is defensive.

## Who reads the stored failure text of a student's upload
Only the student who started it, on the polled status route (`users/views.py`, scoped to `requested_by`), and the teacher's own batch status route for a teacher's batch (a student's task has no batch session). No admin page registers the tracked task and no email reads its error text. The teacher sees nothing about the student's failed upload (no teacher notification, as designed).

## Not cured here, named for the package
- A credit refusal of a student's answer EDIT stored the generic text: H-211 (stacked on this row).
- The window between the door and the task (another job can spend the balance in between).

## Limits named by Verifier 1 (record in `verification_1a/`)
- **Cumulative batch (N1):** the door compares EACH file with the WHOLE balance, so files that are each affordable but not together are all queued, and the later ones fail in the task with the generic message. The door's docstring does not list it (its code is frozen at the gated shape); it is named here for the package. Epic B's run-level hold cures it.
- **Not shown (N3):** nobody has read the student site's handling of the new 402 body (`{"error": <student sentence>, "code": "insufficient_credits"}`).
- **Worktrees (rule 21):** Grade-Automator-Plus-h180-student-upload-credit-door, and -h211-student-edit-credit-sentence which is stacked on this row.
