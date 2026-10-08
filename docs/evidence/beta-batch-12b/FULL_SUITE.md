# Batch 12b: the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-07. 0b started the run and read its result.

**What batch 12b is:** the pushed five-row beta tip `7976a571` (batch 12a) plus three verified rows, each merged as its verified commit plus docs only: H-167 (`e7ed5d8e`, Verifier 1 VERIFIED-WITH-NOTES), H-165 (`fe77a3a9`, Verifier 2 VERIFIED-WITH-NOTES) and H-174 (`f7fd06c9`, Verifier 1 VERIFIED-WITH-NOTES, record `594a4647` read by 0b before the merge); then the docs commit `42a15357` (docs/HARDENING_BACKLOG.md only: the three rows closed, rows H-171 to H-178 added). All three merged without a conflict (`git merge-tree` first). The held group H-148, H-152, H-153 (batch 12s) is NOT in it.

**Command:** the strict full run under rules 12, 13, 16, 17 and 18, by `strict_b12b.sh 42a15357 b12b` (a copy is beside this file as `strict_b12b.sh.txt`; it is batch 12a's script with the worktree and branch names changed).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `42a15357` | lock taken about 20:24:25, ended 2026-10-07 20:32:54 (461 s of tests; the script's 833 s wall includes about five minutes waiting for the machine lock behind a test of the other project) | **Ran 6133 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired |

**Written beforehand:** nothing exact; 0b expected OK, skipped=28, and about 6047 plus the three rows' tests. The count came out 6133, 86 more than batch 12a's 6047.

**Tests per app** (counted from the run's per-test lines; they sum to 6133):

| App | This run | Batch 12a (6047) |
|---|---|---|
| ai_processor | 852 | 852 |
| assignments | 663 | 663 |
| AutoGrader | 692 | 669 |
| billing | 2130 | 2109 |
| classrooms | 434 | 434 |
| dashboard | 270 | 270 |
| students | 438 | 396 |
| users | 654 | 654 |

The differences are in AutoGrader (+23), billing (+21) and students (+42). Billing's 2130 is the same count as the Security Engineer's own billing regression of H-174.

**Skipped: 28**, the same as batch 12a's, batch 12's and batch 11's runs.

**What went wrong at the start, and how it was handled (disclosed):**
- The new worktree had no `settings_worktree.py` and no `.env` link. The script's two pre-check lines in `strict_b12b.summary.txt` ("mypy ... Failed", "makemigrations: ModuleNotFoundError: No module named 'settings_worktree'") are from that. 0b then copied the settings file from 12a's worktree (an untracked, ignored file; only its test database name changed, to `test_beta_batch_12b`) and linked the `.env`, at 20:19:58 to 20:20:30, while the run was still waiting for the lock; the suite's python had not started yet, so the suite ran with the settings. The two checks were re-run by hand at the same tip: `pre-commit run mypy --all-files` Passed (exit 0) and `makemigrations --check --dry-run` "No changes detected" (exit 0). The suite's own result is not affected.
- 0b tried to stop the waiting run with a broad `pkill`; the permission check refused it and nothing was stopped. The run was left to go on.
- The Security Engineer's commit hooks ran at 20:19:13 (one step after the QUIET message arrived), before the suite began; disclosed by that engineer.
- 0b's own mypy and makemigrations re-runs (about 20:20 to 20:21) were before the suite began, while the lock was held by the other project's test.
- No other test run of ours went beside the suite. Load average (1 minute) 3.18 at the script's start, 6.48 at the end.

**The log:** `strict_b12b.log.xz` (8,127,556 bytes unpacked, sha256 `26ee2d4fb9cb18db29388114fed290e3573b8ecf3e3ce68c596aca7c54896200`); unpack with `xz -dc`. `strict_b12b.summary.txt` is the script's own summary.

**After the run tip:** the commit that adds this record changes files under `docs/` only.

**Credential pattern check (0b, on the four files added with this record, values masked):** `scripts/credscan.py`'s scan function called on each file. The record, the script copy and the summary: 0 hits. The packed log (unpacked by the tool): no address with a password part; two lines listed as literal assignments, both the same known warning line from the AI module's test (a fetch of a test host's address; the line holds a 13-character made value, lines 75560 and 75565 of the unpacked log), the same line batch 12a's record names; the rest are variables, code expressions and placeholder words. The tool lists the archive itself as "unreadable" only for its own report call; the unpacked content was scanned.
