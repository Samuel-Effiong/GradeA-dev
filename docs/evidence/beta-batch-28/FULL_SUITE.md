# Batch 28: one strict full run, green

Recorded by the Builder (a0), 2026-10-26. The Senior Manager built the batch and granted the run. The Builder
ran the hooks, started the run and read its result. Gate 1 (the package read), the merge to beta and the push are the
Senior Manager's.

**What batch 28 is:** beta `b737f61c` (batch 27) plus one `--no-ff` merge, clean:

- `6db6c4b6`: Item 6 "small rows" (H-217, H-226, H-228, H-229; H-195 closed, cause unknown) at its gate tip
  `6cc3b19f`.
- The only file both batch 27 and Item 6 touched is `docs/HARDENING_BACKLOG.md` (different rows).

## The run on `6db6c4b6`: green

- Against beta `b737f61c`, 45 files changed (+1525, −11).
  - The code change is two production files:
    - `.gitignore`: `.mypy_cache/`;
    - `scripts/check_migration_safety.py`: `AddConstraint` / `RemoveConstraint` reported (H-217). This is a dev tool
      outside the Django apps, not app code. The suite reaches its seam only through
      `AutoGrader/tests_migration_safety_check.py`.
  - The tests:
    - `AutoGrader/tests_migration_safety_check.py`: 19 to 24 tests (H-217, `AConstraintChangedInOneStep`);
    - `users/tests_rate_limited_envelope.py`: 11 to 12 tests (H-226, a stand-in for `requests.post` in the
      `google_auth` throttle probe, and `test_the_google_throttle_probe_never_calls_google`);
    - `users/tests_credit_balance_permission.py`: 20 tests, no count change; one assertion tightened from
      `InsufficientCreditsError` to `StudentEmptyWalletError` (H-228).
  - Every other file is under `docs/`. H-229 tightens the row-chain runner (`GAP-58-runs/row_chain.py`, outside this
    repository) and H-195 is a note; neither changes anything the suite runs.
  - `git diff --name-only b737f61c..6db6c4b6`, outside `docs/`, gives exactly these 5 files. They are Item 6's own
    `seam_changed_files.txt` (outside `docs/`).
- No migration. `makemigrations --check --dry-run` on the merge tip: "No changes detected". This is the script's own
  pre-check; mypy passed too.
- Item 6's own gate is recorded in `docs/evidence/item6-small-rows/` (NOTES.md, "The gate on 73bf9bbb"): reds 0a to 0d
  as written, Ran 350 OK, all 8 mutants killed, `--h120` 13/13. `73bf9bbb` is an ancestor of `6cc3b19f`, and the one
  commit after it changes `docs/` only.

**Hooks on the merge tip `6db6c4b6`:** run by the Builder (16:04 to 16:07). `pre-commit run --all-files` exited 0.
The table is in `hook_table_6db6c4b6.txt`: 26 hooks, 25 Passed, 1 Skipped ("check for broken symlinks", no files to
check), none Failed. `git status --porcelain` was empty before and after; the tree was unchanged.

**Command:** `strict_bundle.sh 6db6c4b6 b28 Grade-Automator-Plus-beta-batch-28 task/beta-batch-28`.
- A copy is beside this file (sha256 `2b64776f…`). It is batch 27's script (sha256 `51c0dd08…`), with only its log
  folder changed (`b27` to `b28`); `diff` of the two shows that one line (line 13) and nothing else.
- Everything else is the same: rules 12, 13, 16, 17 and 18, the shared lock, the 3600 s timeout, the 300 s stall
  watchdog, the load check after the pre-checks, the cap of available memory − 2 GB, and the 1.5 GB guard.
- No drill settings. No `PLAYWRIGHT_*` variable was set for the run. `~/.cache/ms-playwright` still held batch 27's
  reinstall (`chromium-1208`, `chromium_headless_shell-1208`, `ffmpeg-1011`), checked before the start.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `6db6c4b6` | 2026-10-26 16:09:28 to 16:19:31 (564 s of tests; 603 s wall) | **Ran 8376 tests, OK (skipped=30)**, exit 0 |

- FAIL/ERROR headers across the whole log: 0. "... FAIL" and "... ERROR" result lines: 0.
- No suspend. The stall watchdog never fired, and the memory guard never acted.

**Tests per app** (they sum to 8376): ai_processor 1420, assignments 774, audit 377, AutoGrader 774, billing 2217,
classrooms 640, dashboard 430, students 867, users 877.
- Against batch 27 (8370), that is +6, as counted before the start (by syntax tree, `b737f61c` to `6db6c4b6`) and in
  the log by each test's own line:
  - `AutoGrader.tests_migration_safety_check` +5 (24 test lines; the 5 new are `AConstraintChangedInOneStep`'s);
  - `users.tests_rate_limited_envelope` +1 (12 test lines; the new one is
    `test_the_google_throttle_probe_never_calls_google`).
- None of the 6 failed. `users.tests_credit_balance_permission` ran its 20, as before, none failed. For the tests with
  a docstring, the "ok" is on the next line.
- Every other app is unchanged. No test in batch 27's log is missing from this one.

**Skips:** 30, the same set as batch 27, test for test (opt-in real AI 12, load tests 9, network or Redis opt-in 5, the
two in-worker fork tests, the two benchmarks). The 47 renderer tests all ran.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran: 24 tests, counted by their own test lines, and none failed.

**The cross-row risk, named before the run: as expected.**
- Written down before the run, in `GAP-a0-runs/b28/cross_row_risk_named_before_run.txt`: H-226 puts a stand-in for
  Google's token exchange into the `google_auth` throttle probe, so `blocked_outbound` should drop from 4 to 0. If a
  raise remained, it would be from a `users` module that posts to the Google route without a stand-in.
- Result: `blocked_outbound=0`. The log has 0 `BlockedNetworkCallError` raises and names `oauth2.googleapis.com` 0
  times (batch 27: 1 raise, 4 lines).
- `users/views.py`'s "Google OAuth token exchange failed" ERROR line appears 4 times, down from 5. All 4 are failures
  the tests raise on purpose through their own stand-ins: `ConnectionError("dns")` (`users.tests_auth_audit_doors`),
  and `ConnectionError("dns failure")`, `HTTPError` "400 invalid_grant" and `Timeout("slow")`
  (`users.tests_google_auth`). Batch 27's fifth was the blocked call. A fifth match in this log is the new test's
  docstring, not a log line.
- `scripts/check_migration_safety.py` is outside the Django apps. Its 5 new tests are the only place the suite meets it.

**Load and memory:**
- Load 2.24 3.32 3.85 at the pre-start check (16:09:16). The script's own check after its pre-checks read 2.35 3.31
  3.85, under 8, so the start did not wait.
- The load at the end was 6.67 8.14 6.23. Builder 4d and db were not running tests, per the Senior Manager.
- 13,497 MB available at the start, so an 11G cap. The lowest available during the run was 9,930 MB.

**The log:** `strict_b28.log.xz` (packed with `xz -9e`; 476,924 bytes, under the hook's 500 KB limit of 512,000
bytes).
- 5,833,924 bytes unpacked, sha256 `3867bc0400438cb719976b129bc7e5f1ef66e9ddc2311f29caf145ecf8092361`.
- Unpacking it was checked to give the identical bytes.
- `strict_b28.summary.txt` is the script's summary.
- The log's own timestamps (`[2026-10-26 15:1x:…]`) are in UTC, one hour behind WAT.

## A correction to batch 27's disclosure: the test module

Batch 27's record says its one blocked call was "in `users.tests_google_auth`". The call site it names
(`users/views.py` `_google_auth`) is right. The test module is wrong: the call came from
`users.tests_rate_limited_envelope`, `NamedThrottleEnvelopeTests.test_google_auth_throttle`, H-226's own probe.
- Under `--parallel`, Django prints a subsuite's result lines only after that subsuite ends, while worker log lines are
  written as they happen. So a worker's log lines come before its own results, not after them. In batch 27's log the
  raise (line 35312, ForkPoolWorker-2) is followed by `users.tests_rate_limited_envelope`'s results, with
  `test_google_auth_throttle ... ok` at line 35390. Batch 27's record read it against the `users.tests_google_auth`
  results printed just before it.
- Builder 4d's red 0a (`docs/evidence/item6-small-rows/gate_73bf9bbb/r0a_h226_at_58cdfdb6.log.gz`, the module alone)
  shows the raise inside `test_google_auth_throttle`.
- `users/tests_google_auth.py` patches `requests.post` around every post to the Google route.
- This batch confirms it: with only the probe given a stand-in, the raise is gone.

**Not a measure: the count of refusal lines.** "API request refused" (H-227's WARNING) appears on 1,319 lines here and
on 2,131 in batch 27's run 2. That is not this batch's doing: on batch 27's single tip, run 1 had 1,438 and run 2 had
2,131. How many of these worker lines reach the log varies by hundreds from run to run. The same goes for batch 27's
"8 to 2,131", which was one sample. The "API Exception" (14) and "Free trial plan not found" (6,991) counts are about
the same as batch 27's. Tracebacks are 561, down 4 from 565: the blocked call's chained traceback.

**Not shown by this run:**
- the migration-safety check run on a real pull request (H-217): the suite runs `scripts/check_migration_safety.py`
  only through its own tests;
- H-229's row-chain runner (outside this repository; Item 6's gate ran its tests, 1d);
- real Google sign-in, real AI calls and the load tests (opt-in, skipped as in batch 27);
- the PDF renderer on the production image: the laptop's Chromium is Playwright's unofficial ubuntu24.04 fallback
  build on Ubuntu 26.04, while the image installs its own (`Dockerfile:51`, `--with-deps`);
- anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
