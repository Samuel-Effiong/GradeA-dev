# H-179: gates on `021c8f7e` (written 2026-10-09 from the files in `gate_files/`)

Row: a reply the model cut off at its length limit (`finish_reason == "length"`) is logged once from `execute_graded_task` with its task type and model, and nothing else (option A of the Senior Manager's ruling: log only; the "existing metrics module" exists only on the next-stage line). The unmetered super-admin branch logs nothing. Base beta `035e0a07`; it needs a clean base update onto current beta before it merges. Code gated: `51c759ed`; `021c8f7e` adds the runner and notes. No stop on any chain.

| Step | Result |
|---|---|
| (r) the module on base `035e0a07` (rule 22 reasons written beforehand) | 11:31:03 to 11:31:22 Oct 9: 3 failing as expected, each for its written reason ("0 != 1" at `len(records)`; "not enough values to unpack" at the two unpack lines) |
| (a) 942 tests | 11:31:22 to 11:36:05: Ran 942 tests in 256.355s, OK, 0 FAIL/ERROR |
| (b) 7 mutants K1 to K7 | 11:36:05 to 11:36:52: 7/7 killed, restore verified, every failing set as written (K1 3, K2 to K7 1 each) |
| (c) `c_h179.sh`, eight apps, parallel 2 | 12:21:10 to 12:34:48: Ran 6142 tests in 780.592s, OK (skipped=28), exit 0, stalled 0 |

## What is NOT shown
- That the log line reaches the error-reporting service or any dashboard: the row only writes a WARNING line; its count is read from the log.
- Nothing about production volumes: how often replies are cut off is exactly what this line will tell once it is in the logs.
- The raw log of the regression is split in two parts (large-file hook): see `gate_files/c_eight_apps_p2_021c8f7e.raw.log.README.txt`.
