# Batch 25: one strict full run, green

Recorded by the Builder (a0), 2026-10-25. The Senior Manager built the batch and granted the run. The Builder
started it and read its result. Gate 1 (the package read), the merge to beta and the push are the Senior Manager's.

**What batch 25 is:** beta `0e3028a8` (batch 24) plus one `--no-ff` merge, clean:

- `268fe5a9`: H-204 (the provider tries the cheapest supplier first, and no fp4 endpoint is used) at its frozen tip
  `4c079554`.

## The run on `268fe5a9`: green

- Against beta `0e3028a8`, 24 files changed (+933, −5). The code change is `ai_processor/services.py` only (the
  provider block and its two constants). The tests are `ai_processor/tests_h204_provider_price_sort.py` (new) and
  `ai_processor/tests_provider_block.py` (H-206's expected blocks gain the two keys). Every other file is under
  `docs/`.
- No migration. `makemigrations --check --dry-run` on the merge tip: "No changes detected". This is the script's own
  pre-check; mypy passed too.
- The row's own gate is recorded in `docs/evidence/h204-provider-price-sort/`: the gate on `1100954f` (33 written
  reds, 772 OK, 8/8 killed, the seam check).

**Hooks on the merge tip `268fe5a9`:** run by the Senior Manager. `pre-commit run --all-files` exited 0. The table is
in `hook_table_268fe5a9.txt`: 26 hooks, 25 Passed, 1 Skipped, none Failed.

**Command:** `strict_bundle.sh 268fe5a9 b25 Grade-Automator-Plus-beta-batch-25 task/beta-batch-25`.
- A copy is beside this file (sha256 `9f86e896…`). It is batch 24's script, with only its log folder changed (`b24`
  to `b25`).
- Everything else is the same: rules 12, 13, 16, 17 and 18, the shared lock, the 3600 s timeout, the 300 s stall
  watchdog, the load check after the pre-checks, the cap of available memory − 2 GB, and the 1.5 GB guard.
- No drill settings.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `268fe5a9` | 2026-10-25 11:39:54 to 11:51:23 (650 s of tests; 689 s wall) | **Ran 8323 tests, OK (skipped=30)**, exit 0 |

- FAIL/ERROR headers across the whole log: 0. "... FAIL" and "... ERROR" result lines: 0.
- No suspend. The stall watchdog never fired, and the memory guard never acted.

**Tests per app** (they sum to 8323): ai_processor 1420, assignments 767, audit 377, AutoGrader 762, billing 2216,
classrooms 640, dashboard 430, students 867, users 844.
- Against batch 24 (8311) that is +12, counted by each test's own line.
- ai_processor +12: `tests_h204_provider_price_sort` (new). All 12 passed. For two of them the "ok" is on the next
  line, after the docstring.
- Every other app is unchanged. The edit to `tests_provider_block` added no tests.

**Near tests:**
- `tests_provider_block` (H-206): 24 tests ran, and none failed.
- `tests_benchmark_golden` (2) and `tests_grading_benchmark` (28) also ran, with no failure. The benchmark replay key
  does not include the provider block.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran: 24 tests, counted by their own test lines, and none failed.

**Load and memory:**
- Load 12.10 at the pre-start check (11:38:34), and 10.15 9.30 6.95 at the script's own check after the pre-checks.
  So the start waited. It began at 7.92 8.86 6.91. The load at the end was 9.11 9.16 8.08. Other teams' work was
  sharing the laptop; no other GAP test run was going.
- 10,780 MB available at the start, so an 8G cap. The lowest available during the run was 8,543 MB.

**The log:** `strict_b25.log.xz` (packed with `xz -9e`; 504,196 bytes, under the 500 KB hook limit).
- 9,455,745 bytes unpacked, sha256 `647f5294772581f57c25405029ab5b53a69c05bd16b99b6b83312e6afe87907d`.
- Unpacking it was checked to give the identical bytes.
- `strict_b25.summary.txt` is the script's summary.

**Disclosed: blocked outbound.** The summary says `blocked_outbound=8`. That figure counts log lines holding the
message, and each blocked call's traceback repeats the message four times.
- The log has 2 `BlockedNetworkCallError` raises. Both are to `oauth2.googleapis.com` (`64.233.176.95`, port 443,
  `/token`), from `users/views.py` `_google_auth` (H-226). The view turns the failure into "Google sign-in failed".
- Every test passed, and no batch 25 file is on that path.

**Not shown by this run:**
- live provider behaviour (the grading tests replace only the provider call). What the provider does with `sort`
  and `quantizations` needs a live call, which is paid; none was made;
- whether grading on beta gets slower with the cheapest supplier first (H-204's stated risk; watched after the
  push);
- anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
