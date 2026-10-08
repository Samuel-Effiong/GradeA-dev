# Batch 12a: the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-07. 0b started the run and read its result.

**What batch 12a is:** batch 12 WITHOUT H-148, prepared on the Senior Manager's order of 2026-10-07 so that either answer of the user can be pushed at once. H-148 makes adding a student by email need a first and a last name, which breaks the current page until the frontend sends them; the user is deciding whether it goes out now. If the six-row batch 12 is pushed, this branch is not used.

**Scope:** `task/beta-batch-12a`: beta `d7143538` (batch 11 as pushed) plus five rows, each merged as its verified commit plus docs only: H-154 (`a60657c5`), H-137 (`ce1d641f`), H-147 (`e036e367`), H-141 (`42b71ffe`), H-150 (`220f9cd6`), and one docs commit (`2adc1bb3`: the backlog). The branch is cut at `220f9cd6`, so these are the same five merge commits as in batch 12; H-148's merge (`a522c69f` there) is the next commit of that branch and is not in this one. 21 files outside `docs/` differ from beta; 9 of them are production files.

**Why it has its own full run:** rule 15 gives a bundle ONE full run. Batch 12's run (6086 OK, at `00d0b699`) ran with H-148 in the tree; the five rows without it are a different bundle and had not been run together.

**Not in the batch:** H-148, H-152 and H-153 (batch 12b, together); the score-printing rows H-139, H-140, H-142 and H-144, H-145, H-146 (batch 13).

**One file was merged by git from two rows:** `students/serializers.py` (H-141 and H-150), exactly as in batch 12, where Gate 1 read it.

**Command:** the strict full run under rules 12, 13, 16, 17 and 18, by `strict_b12a.sh 2adc1bb3 b12a` (a copy is beside this file as `strict_b12a.sh.txt`; it is batch 11's script with the worktree and branch names changed): sleep inhibited, a 12G memory cap with no swap, the machine's full-suite lock, a 3600 s timeout, four workers, output straight to a file, with the stall watchdog. Before it, in the same script: mypy over all files (Passed) and `makemigrations --check` ("No changes detected").

| Tip | Start–end of the suite (WAT) | Result |
|---|---|---|
| `2adc1bb3` | 2026-10-07 17:09:36–17:18:18 (522 s wall, 492 s tests) | **Ran 6047 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired |

**Written beforehand** (0b's run log): OK, skipped=28, about 6045 tests (the six-row run's 6086 less H-148's own new tests, about 40). It came out 6047: Verifier 1 had written exactly 6047 beforehand (6086 less the 39 tests of H-148's three new modules).

**Tests per app** (counted from the run's per-test lines; they sum to 6047):

| App | This run | Batch 12's six-row run (6086) | Batch 11's green run (5915) |
|---|---|---|---|
| ai_processor | 852 | 852 | 823 |
| assignments | 663 | 663 | 663 |
| AutoGrader | 669 | 669 | 609 |
| billing | 2109 | 2109 | 2109 |
| classrooms | 434 | 461 | 420 |
| dashboard | 270 | 270 | 270 |
| students | 396 | 396 | 367 |
| users | 654 | 666 | 654 |

The difference from the six-row run is in classrooms (27) and users (12): 39, H-148's.

**Skipped: 28**, the same as batch 12's and batch 11's runs, by the reasons printed in the log: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`, and 2 tests that cannot fork inside a parallel worker. No test was skipped for want of Chromium (0 lines "Headless Chromium not available").

**Load average (1 minute):** 3.51 when the suite started, 10.72 when it ended.

**The machine, and what ran beside the suite (all disclosed by those who ran it; the run is green, so none of it is offered as an excuse for anything):**
- The other project on this laptop ran a TypeScript build for some minutes in the second half of the run; the load reached 16. Its manager had agreed the quiet stretch, called the build its own error and stopped further ones.
- The Security Engineer's commit hooks on three Python files, 17:08:10 to 17:08:30: before the suite began (during this script's type-check step).
- The Hardening Engineer's credential pattern check of one evidence folder, 17:07:51 to 17:11:17: over the suite's first minute and a half.
- Verifier 2's one `git grep` of one commit's Python files at about 17:10 (git objects only, under a second).
- 0b itself ran one process listing during the run to see what was loading the machine.
- No other test run of ours went beside it. No suspend.

**The log:** `strict_b12a.log.xz` (8,018,157 bytes unpacked, sha256 `3cb3163810743d8de6aa591e49a5cc3df3dff320b23c261bb85effb50fc95b6e`); unpack with `xz -dc`. `strict_b12a.summary.txt` is the script's own summary (sha256 begins `a930aa576378f0fc`).

**Credential pattern check (0b, on the three files added with this record, values masked):** done with `scripts/credscan.py` at this tip, its scan function called on these files. No address with a password part; no archive it could not read. Two lines are listed as literal assignments outside test files, both in the log and both the same known line (a warning that a fetch of a test host's address was blocked; the "value" is the test host's name, 13 characters; the log holds that warning line twice). Not a credential. Counts for the three files: 6 literal rows (the two above, and 4 under names that hold only the word KEY, which the tool counts and does not list), 12 code expressions, 5 placeholder words, 9 variables or masks. These are the same counts as for batch 12's log.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
