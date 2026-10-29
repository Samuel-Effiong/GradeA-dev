# Batch 27: one strict full run, green

Recorded by the Builder (a0), 2026-10-26. The Senior Manager built the batch and granted the run. The Builder
ran the hooks, started the run and read its result. Gate 1 (the package read), the merge to beta and the push are the
Senior Manager's.

**What batch 27 is:** beta `b4af529f` (batch 26) plus four `--no-ff` merges, all clean:

- `421dc058`: H-197 (pin of the webhook mirror's `canceled_at` reading) at its frozen tip `f005ef09`;
- `52f692b0`: H-227 (refusals logged at WARNING, class and status only, no traceback) at its frozen tip `471bce25`;
- `a09cedaa`: H-219 (the last task sites keep no error text or traceback) at its frozen tip `49e2eae6`;
- `a505c1cd`: H-218 (a filter on `celery.app.trace` names the error by class only) at its frozen tip `95145833`.

## The run on `a505c1cd`: green (run 2)

- Against beta `b4af529f`, 80 files changed (+3636, −36).
  - The code change is four production files:
    - `users/exceptions.py`, `custom_exception_handler`'s logging only (H-227);
    - `assignments/tasks.py`, the task sites only (H-219);
    - `AutoGrader/celery.py` and `AutoGrader/safe_logging.py`, the filter, its constants, imports and its install on
      `celery.app.trace` (H-218).
  - The tests:
    - new: `AutoGrader/tests_h218_celery_trace_filter.py`, `assignments/tests_h219_task_error_text.py`,
      `users/tests_h227_refusal_logging.py`;
    - changed by H-197: `billing/tests/test_date_scheduled_cancellation.py` (10 to 11 tests);
    - changed by H-227, with no count change: `users/tests_exception_handler.py` (6 tests).
  - Every other file is under `docs/`.
  - `git diff --name-only beta..HEAD`, outside `docs/`, gives exactly these 9 files. They are the union of the four
    rows' own `seam_changed_files.txt` (outside `docs/`), each row's list being its own files only.
- No migration. `makemigrations --check --dry-run` on the merge tip: "No changes detected". This is the script's own
  pre-check; mypy passed too.
- Each row's own gate is recorded in its evidence folder. Each gate sha is an ancestor of its frozen tip, and the
  commits after it change `docs/` only:
  - `docs/evidence/h197-mirror-pin/`: the gate on `9e38391d` (no red run, by rule 19's mutant route; Ran 422 OK, 1/1
    killed);
  - `docs/evidence/h227-refusal-log-level/`: the gate on `b4567a16` (red 6, Ran 522 OK, 6/6 killed);
  - `docs/evidence/h219-task-error-text/`: the gate on `5f3bf64a` (red 6, Ran 632 OK, 7/7 killed);
  - `docs/evidence/h218-celery-trace-filter/`: the gate on `c9e07439` (red 5, Ran 522 OK, 9/9 killed).

**Hooks on the merge tip `a505c1cd`:** run by the Builder (15:16 to 15:19). `pre-commit run --all-files` exited 0.
The table is in `hook_table_a505c1cd.txt`: 26 hooks, 25 Passed, 1 Skipped ("check for broken symlinks", no files to
check), none Failed. `git status --porcelain` was empty before and after; the tree was unchanged.
- An interrupted hook run by the SM at 15:12 left a partial file; overwritten by this run.
- The table opens with 12 `[INFO]` lines: pre-commit finishing the environments that interrupted run had half-installed
  (in pre-commit's cache, not the tree).
- The hooks were not re-run for run 2: the tree is unchanged since this hook run.

**Command:** `strict_bundle.sh a505c1cd b27 Grade-Automator-Plus-beta-batch-27 task/beta-batch-27`.
- A copy is beside this file (sha256 `51c0dd08…`). It is batch 26's script (sha256 `43ad78b6…`), with only its log
  folder changed (`b26` to `b27`); `diff` of the two shows that one line (line 13) and nothing else.
- Everything else is the same: rules 12, 13, 16, 17 and 18, the shared lock, the 3600 s timeout, the 300 s stall
  watchdog, the load check after the pre-checks, the cap of available memory − 2 GB, and the 1.5 GB guard.
- No drill settings. No `PLAYWRIGHT_*` variable was set for the run (see run 1).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `a505c1cd` (run 2) | 2026-10-26 15:39:27 to 15:52:31 (719 s of tests; 784 s wall) | **Ran 8370 tests, OK (skipped=30)**, exit 0 |

- FAIL/ERROR headers across the whole log: 0. "... FAIL" and "... ERROR" result lines: 0.
- No suspend. The stall watchdog never fired, and the memory guard never acted.

**Tests per app** (they sum to 8370): ai_processor 1420, assignments 774, audit 377, AutoGrader 769, billing 2217,
classrooms 640, dashboard 430, students 867, users 876.
- Against batch 26 (8348), that is +22, as counted before the start (by syntax tree, `b4af529f` to `a505c1cd`) and in
  the log by each test's own line:
  - `AutoGrader.tests_h218_celery_trace_filter` +7;
  - `assignments.tests_h219_task_error_text` +7;
  - `billing.tests.test_date_scheduled_cancellation` +1 (11 test lines);
  - `users.tests_h227_refusal_logging` +7.
- None of the 22 failed. `users.tests_exception_handler` ran its 6, as before. For the tests with a docstring, the
  "ok" is on the next line.
- Every other app is unchanged.

**Skips:** 30, the same as batch 26: the same tests, for the same reasons (opt-in real AI 12, load tests 9, network or
Redis opt-in 5, the two in-worker fork tests, the two benchmarks).
- The 47 renderer tests skipped in run 1 all show "ok" by their own lines: `assignments.tests_pdf_renderer` 34,
  `assignments.tests_renderer_crash` 8, `assignments.tests_pdf_renderer_driver_stderr` 4,
  `assignments.tests_download_pdf` 1.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran: 24 tests, counted by their own test lines, and none failed.

**The cross-row risk, named before the run: not seen.**
- Written down before run 1, in `GAP-a0-runs/b27/cross_row_risk_named_before_run.txt`: all four rows touch logging.
  H-218's filter on `celery.app.trace`, H-219's task sites and H-227's handler run in the same suite under the same
  logging configuration, and `AutoGrader/safe_logging.py` is shared.
- What was looked for: any `assertLogs` / `assertNoLogs` failure, or any FAIL/ERROR, in assignments, AutoGrader, users
  or billing. There were none, in either run. Each row's own new tests ran and passed inside the whole suite.

**Load and memory (run 2):**
- Load 1.31 3.27 4.46 at the pre-start check (15:39:13). The script's own check after its pre-checks read 1.24 3.16
  4.40, under 8, so the start did not wait.
- The load at the end was 4.92 6.23 5.67. Nothing of Builder 4d's was running, per the Senior Manager.
- 11,985 MB available at the start, so a 9G cap. The lowest available during the run was 9,799 MB.

**The log:** `strict_b27.log.xz` (packed with `xz -9e`; 478,724 bytes, under the hook's 500 KB limit of 512,000
bytes).
- 5,877,496 bytes unpacked, sha256 `d3ddec809110cb198108b7373d060d102b8c2fce3429bf9f8a96a3e5662ebb36`.
- Unpacking it was checked to give the identical bytes.
- `strict_b27.summary.txt` is the script's summary.

**Disclosed: blocked outbound.** The summary says `blocked_outbound=4`. That figure counts log lines holding the
message, and each blocked call's traceback repeats the message four times.
- The log has 1 `BlockedNetworkCallError` raise. It is to `oauth2.googleapis.com` (`64.233.176.95`, port 443,
  `/token`), from `users/views.py` `_google_auth` (H-226), in `users.tests_google_auth`, the same call as in batches 25
  and 26.
- No batch 27 row changes `_google_auth`. Every test passed.

## A correction to batch 26's disclosure: one call, printed twice

Batch 26's record says its log "has 2 `BlockedNetworkCallError` raises". It was **one** blocked call, printed twice.
- Both of batch 26's tracebacks name the same connection object (`HTTPSConnection object at 0x722241280560`, all 6
  mentions).
- The second copy came from `custom_exception_handler`'s `logger.error("API Exception", exc_info=exc)`, which then
  logged every exception it handled, with its traceback, before DRF answered it.
- H-227 moved that ERROR line to the unhandled-500 path only and logs a refusal at WARNING, with no traceback. So
  batch 27 prints the one call once (3 mentions of one connection object), and `blocked_outbound` halves from 8 to 4.
- The number of blocked calls did not change: one in each batch. Only the duplicate print went away.

## Why the log is smaller

Batch 26's log was 9,454,751 bytes; run 2's is 5,877,496 (−3,577,255), with 22 more tests and the same skips. The
cause is the same H-227 change:
- "API Exception" lines: 2,883 in batch 26, 14 in run 2. "API request refused" lines (H-227's WARNING): 8 to 2,131.
- "Traceback (most recent call last)" lines: 4,233 to 565.
- The skips are not the cause: run 1 (47 renderer tests skipped) was 5,836,297 bytes, about the same as run 2.

## Run 1, not signed

Run 1 on the same tip, same script and same arguments: 2026-10-26 15:20:34 to 15:33:13 (715 s of tests; 759 s wall).
**Ran 8370 tests, OK (skipped=77)**, exit 0, FAIL/ERROR headers 0. The start waited a few seconds on load (8.89 just
after the hooks), then started at 6.71. Cap 10G (12,933 MB available), lowest available 9,878 MB. Watchdog, memory
guard and suspends all 0. Per-app counts and the +22 were the same as run 2's.
- It is not signed, by the Senior Manager's ruling: a batch that changes `assignments/tasks.py` is not signed green
  with 47 renderer tests skipped.
- The 47 extra skips were all "Headless Chromium (is) not available": `assignments.tests_pdf_renderer` 34,
  `assignments.tests_renderer_crash` 8, `assignments.tests_pdf_renderer_driver_stderr` 4,
  `assignments.tests_download_pdf` 1. All 47 were "ok" in batch 26.
- The cause, as the Senior Manager reported it: `~/.cache/ms-playwright` was removed by the user's cache clean-up after
  batch 26; reinstalled for run 2 with Playwright's ubuntu24.04-x64 fallback build (chromium-1208, Chrome for Testing
  145.0.7632.6) because Playwright 1.58.0 has no build for Ubuntu 26.04; the override was used at install time only.
- The reinstall, in `GAP-a0-runs/b27/playwright_install.txt`:
  - no `PLAYWRIGHT_BROWSERS_PATH` is set in the environment or `.env`; the renderer's launch
    (`assignments/pdf_renderer.py:503`, `playwright.chromium.launch(args=["--no-sandbox"])`) reads no path and nothing
    from the override, so it uses the default cache;
  - `playwright install chromium` alone failed: "Playwright does not support chromium on ubuntu26.04-x64";
  - `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 playwright install chromium` installed `chromium-1208`,
    `chromium_headless_shell-1208` and `ffmpeg-1011` (622M, 649,989,481 bytes). No `--with-deps`, no system packages;
  - a plain launch probe with no `PLAYWRIGHT_*` variable set printed `145.0.7632.6`; `python -m playwright --version`
    is 1.58.0;
  - run 2 was started with the override unset (`env -u PLAYWRIGHT_HOST_PLATFORM_OVERRIDE`).
- Run 1's files are kept as `strict_b27_run1.*` in `GAP-a0-runs/b27/` (log sha256 `6708faab…`); they are not in this
  folder.

**Not shown by this run:**
- a real Celery worker process's logging setup (H-218 read `celery/app/log.py`; the suite runs Celery's in-process test
  worker only), and the other Celery outputs H-218 names and leaves alone (the result backend's copy, Sentry's Celery
  event, `celery.worker.request`'s line, the `task-failed` event);
- that Sentry receives nothing for a refusal now logged at WARNING (H-227): the suite asserts the level, not Sentry;
- the PDF renderer on the production image: the laptop's Chromium is Playwright's unofficial ubuntu24.04 fallback
  build on Ubuntu 26.04, while the image installs its own (`Dockerfile:51`, `--with-deps`);
- real Google sign-in, real AI calls and the load tests (opt-in, skipped as in batch 26);
- anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
