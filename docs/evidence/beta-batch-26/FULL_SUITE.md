# Batch 26: one strict full run, green

Recorded by the Builder (a0), 2026-10-25. The Senior Manager built the batch and granted the run. The Builder
started it and read its result. Gate 1 (the package read), the merge to beta and the push are the Senior Manager's.

**What batch 26 is:** beta `7286717e` (batch 25) plus three `--no-ff` merges, all clean:

- `9eb5b2b9`: H-221 (the two reset-step refusals are audited) at its frozen tip `bcc7a22a`;
- `8733e152`: H-210 (the school-admin activation door shares `/auth/verify`'s per-address budget) at its frozen tip
  `80f80a4d`;
- `5eb50bf1`: H-198 (`POST /auth/otp` answers the two leaking states with the neutral 202) at its frozen tip
  `727ef958`.

## The run on `5eb50bf1`: green

- Against beta `7286717e`, 81 files changed (+3145, −52).
  - The code change is `users/views.py` only, in three methods: `reset_password` (H-221), `register_school_admin`
    with one import (H-210), and `otp` (H-198).
  - The tests:
    - new: `users/tests_h221_reset_audit.py`, `users/tests_h210_register_budget.py`, `users/tests_h198_otp_neutral.py`;
    - changed by H-198, with no count change: `users/tests_reset_for_an_invited_student.py`,
      `users/tests_switched_off_means_out.py`, `users/tests_auth_endpoints.py`,
      `classrooms/tests_school_admin_active_invite.py`.
  - Every other file is under `docs/`.
- No migration. `makemigrations --check --dry-run` on the merge tip: "No changes detected". This is the script's own
  pre-check; mypy passed too.
- Each row's own gate is recorded in its evidence folder:
  - `docs/evidence/h221-reset-audit/`: the gate on `da069c2b` (red 4 blocks, Ran 772 OK, 9/9 killed);
  - `docs/evidence/h210-register-budget/`: the gate on `1021f147` (red 8, Ran 664 OK, 9/9 killed);
  - `docs/evidence/h198-otp-neutral/`: the red gate on `039c6135`, kept as the record, then the gate on `e5de4d07`
    (red 17, Ran 733 OK, 8/8 killed).

**Hooks on the merge tip `5eb50bf1`:** run by the Senior Manager. `pre-commit run --all-files` exited 0. The table is
in `hook_table_5eb50bf1.txt`: 26 hooks, 25 Passed, 1 Skipped, none Failed. The tree was unchanged.

**Command:** `strict_bundle.sh 5eb50bf1 b26 Grade-Automator-Plus-beta-batch-26 task/beta-batch-26`.
- A copy is beside this file (sha256 `43ad78b6…`). It is batch 25's script, with only its log folder changed (`b25`
  to `b26`).
- Everything else is the same: rules 12, 13, 16, 17 and 18, the shared lock, the 3600 s timeout, the 300 s stall
  watchdog, the load check after the pre-checks, the cap of available memory − 2 GB, and the 1.5 GB guard.
- No drill settings.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `5eb50bf1` | 2026-10-25 15:14:58 to 15:31:36 (922 s of tests; 998 s wall) | **Ran 8348 tests, OK (skipped=30)**, exit 0 |

- "Found 8348 test(s)."
- FAIL/ERROR headers across the whole log: 0. "... FAIL" and "... ERROR" result lines: 0.
- No suspend. The stall watchdog never fired, and the memory guard never acted.

**Tests per app** (they sum to 8348): ai_processor 1420, assignments 767, audit 377, AutoGrader 762, billing 2216,
classrooms 640, dashboard 430, students 867, users 869.
- Against batch 25 (8323), that is +25, all in users, as counted before the start (by syntax tree, 7286717e to
  5eb50bf1) and in the log by each test's own line:
  - `tests_h198_otp_neutral` +8;
  - `tests_h210_register_budget` +11;
  - `tests_h221_reset_audit` +6.
- None of the 25 failed. For the tests with a docstring, the "ok" is on the next line.
- Every other app is unchanged. The H-198 edits to four existing modules added no tests.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran: 24 tests, counted by their own test lines, and none failed.

**The cross-row risk, named before the run: not seen.**
- All three rows change `users/views.py`.
- H-210 shares `/auth/verify`'s per-address budget, kept in a cache the test runner does not clear between tests.
- No users or audit test failed. Every module that calls `/auth/verify`, `/auth/register/school-admin`,
  `/auth/reset-password` or `/auth/otp` ran in this one suite, beside the three rows' own tests.

**Load and memory:**
- Load 4.75 8.58 6.79 at the pre-start check (15:14:17). The script's own check after its pre-checks read 7.60 8.76
  6.93, under 8, so the start did **not** wait (the Senior Manager had expected one, from 10.1 at 15:12).
- The load at the end was 8.87 11.53 9.92. Other teams' work was sharing the laptop; no other GAP test run was going.
- 9,289 MB available at the start, so a 7G cap. The lowest available during the run was 7,793 MB.

**The log:** `strict_b26.log.xz` (packed with `xz -9e`; 507,868 bytes, under the hook's 500 KB limit of 512,000
bytes).
- 9,454,751 bytes unpacked, sha256 `efe919b907b3bf9d798d903e3e1acb3cad59e44c0c6bd70e920518659ecf0c3e`.
- Unpacking it was checked to give the identical bytes.
- `strict_b26.summary.txt` is the script's summary.

**Disclosed: blocked outbound.** The summary says `blocked_outbound=8`. That figure counts log lines holding the
message, and each blocked call's traceback repeats the message four times.
- The log has 2 `BlockedNetworkCallError` raises. Both are to `oauth2.googleapis.com` (`64.233.176.95`, port 443,
  `/token`), from `users/views.py` `_google_auth` (H-226), the same two as in batch 25.
- No batch 26 row changes `_google_auth` (each row's seam check limits its change to its own method). Every test
  passed.

**Not shown by this run:**
- what the frontend does with H-198's neutral 202 for the two changed states (the frontend was not read, by the
  user's ruling);
- real mail delivery (the mail task is replaced in the tests);
- behaviour under a real Redis outage (H-210 and H-53's budget fail open; that path is unchanged);
- anything about live data;
- **H-161 row (b)** described the old "Email not verified." answer that H-198 has now changed. The row lives in
  `docs/HARDENING_BACKLOG.md`, so this same commit appends "(answer changed by H-198, 25 Oct)" to it.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
