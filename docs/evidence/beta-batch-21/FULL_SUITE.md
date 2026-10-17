# Batch 21: the one strict full run, green

Recorded by the Release Engineer (bd), 2026-10-14. bd merged the batch, started the run and read its result.

**What batch 21 is:** beta `84aa98c5` (batches 17–20) plus two merges, with no conflicts:

- `5eafec2b`: H-233 at `22c2839c` (stacked on H-231's `1800ca80`, already on beta);
- `b57b7a92`: H-235 at `7f70305a`, which carries H-234 at `abcc69e5` beneath it.

On the merge tip `b57b7a92`:

- H-233's 18 files (`1800ca80..22c2839c`) equal `22c2839c`.
- H-234 and H-235's 28 files (`486687ed..7f70305a`) equal `7f70305a`.
- The two rows share no file. The 46 files changed against beta are exactly those two sets, and no batch 20 file changed.
- No migrations. `makemigrations --check --dry-run` on the merge tip: "No changes detected".

The Verifier (28) re-read the my-students at-risk path on the merged tip before the run (23:43:23 to 23:43:50), where H-233's yes/no parsing meets H-234's session-based at-risk value: clear for the full run.

**Hooks on the merge tip `b57b7a92`:** `pre-commit run --all-files`, 23:39:24 to 23:43:01, exit 0. The table is in `hook_table_b57b7a92.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files), none Failed. The hooks changed no files.

**Command:** `strict_bundle.sh b57b7a92 b21 Grade-Automator-Plus-beta-batch-21 task/beta-batch-21`. A copy is beside this file (sha256 `93215098…`), the same script as batch 20's: the load is checked after the pre-checks (start only under 8), the memory cap is available memory − 2 GB, and a guard SIGKILLs the run's own scope under 1.5 GB available. It kept rules 12, 13, 16 (one inhibitor around the whole script), 17 and 18, plus the shared lock, the 3600 s timeout and the 300 s stall watchdog.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `b57b7a92` | 2026-10-14 23:44:22 to 23:54:50 (602 s of tests; 628 s wall) | **Ran 7876 tests, OK (skipped=30)**, exit 0. FAIL/ERROR headers: 0 across the whole log. No suspend; the watchdog never fired; the memory guard never acted. The script's own pre-checks found mypy Passed and makemigrations "No changes detected". |

**Tests per app** (they sum to 7876): ai_processor 1231, assignments 767, audit 376, AutoGrader 754, billing 2216, classrooms 589, dashboard 302, students 800, users 841. Against batch 20 (7846), counted by each test's own line:

- classrooms is +22: `tests_h234_grade_follows_filters` 15 and `tests_h235_counts_published_only` 7.
- dashboard is +8: `tests_h233_at_risk_values` 8.
- billing is unchanged at 2216: H-233 rewrote tests inside `billing.tests.test_overage_cap` (6 tests) without adding any.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran, and all 24 of its tests are ok, counted by their own test lines.

**Load and memory.**

- The load was 2.75 3.70 2.75 at the suite's start, measured after the pre-checks, and 5.55 6.22 4.63 at the end.
- Available memory was 21,040 MB at the start, giving an 18G cap. The lowest available memory during the run was 18,560 MB.
- **Disclosed: other work beside the run.** From 23:47:24 to 23:49:35 (times from the output file's stat), the Builder ran `pre-commit run --files` on four H-237 files, mypy included, in another worktree. No tests ran in it. The Builder read the load at 7.22 5.60 3.82 at about 23:49; bd read 6.64 5.85 4.15 at 23:51:50. Every test passed, so no result depends on it.

**The log:** `strict_b21.log.xz`, 9,229,367 bytes unpacked, sha256 `b10ae2539044c5b30d3bdf76ebd454ca0c10d19e43356d6661e22a6240e79575`. Unpacking it was checked to give the identical bytes. `strict_b21.summary.txt` is the script's summary.

**Disclosed: blocked outbound = 8.** These are the two blocked attempts to reach Google's token address from `users/views.py` `_google_auth`, inside the Google sign-in tests (H-226), as in batches 17, 19 and 20. Every test passed. No batch 21 file is on that path.

**Not shown by this run:** that the frontend sends exactly `enrollments__course__session` and `enrollments__course` (H-234; the frontend is not in this repository). Anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
