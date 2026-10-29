# Batch 23: the one strict full run, green

Recorded by the Builder (a0), 2026-10-24. The Senior Manager built the batch and granted the run; the Builder started
it and read its result. Gate 1 (the package read), the merge to beta and the push are the Senior Manager's.

**What batch 23 is:** beta `dac16545` (batch 22) plus one `--no-ff` merge, clean:

- `6dc465bf`: H-205 (an image-capable backup for page reading; claude-haiku-5.5 replaces gemini-2.5-flash as the
  second grader's other candidate) at its frozen tip `514242b8`.

On the merge tip `6dc465bf`:

- The tree equals `514242b8` (`git diff 514242b8 6dc465bf` is empty). 44 files changed against beta.
- No migration. `makemigrations --check --dry-run` on the merge tip: "No changes detected".
- The row's own gate is in `docs/evidence/h205-image-fallback-model/` (gate on `662edddf`: 942 OK, 14/14 mutants
  killed, 17 written reds).
- **Effect after deploy:** `IMAGE_FALLBACK_MODELS` joins the grading and the reading config versions, so saved grading
  answers are not reused for one round and new readings carry a new `rcfg:` version (accepted by the Senior Manager;
  named in the H-205 code commit and NOTES.md).

**Hooks on the merge tip `6dc465bf`:** `pre-commit run --all-files`, 13:19:28 to 13:21:29, exit 0. The table is in
`hook_table_6dc465bf.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files), none Failed. The hooks changed
no files.

**Command:** `strict_bundle.sh 6dc465bf b23 Grade-Automator-Plus-beta-batch-23 task/beta-batch-23`. A copy is beside
this file (sha256 `a724c437…`). It is batch 22's script with its logs in the Builder's `b23` folder; everything else
is the same (rules 12, 13, 16, 17, 18, the shared lock, the 3600 s timeout, the 300 s stall watchdog, the load check
after the pre-checks, the cap of available memory − 2 GB with exit 5 under 6G, the 1.5 GB guard).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `6dc465bf` | 2026-10-24 13:08:48 to 13:19:01 (562 s of tests; 613 s wall) | **Ran 8286 tests, OK (skipped=30)**, exit 0. FAIL/ERROR headers: 0 across the whole log; "... FAIL"/"... ERROR" result lines: 0. No suspend; the watchdog never fired; the memory guard never acted. The script's own pre-checks found mypy Passed and makemigrations "No changes detected". |

**Disclosed: a first start that never ran.** The first start (granted 12:35) began at 12:35:54. At 12:38:29, while it
was still in its mypy pre-check, the laptop was powered off on request: systemd-logind logged "The system will power
off now!", with no out-of-memory or crash lines in the journal. No test had run; the summary was empty and there was
no log. That empty summary, and the untracked `.mypy_cache/` the interrupted pre-check left in the worktree, were
moved to the Builder's `b23/stopped_restart/` folder. The Senior Manager re-granted the run after the restart.
`.mypy_cache` is not in `.gitignore`.

**Tests per app** (they sum to 8286): ai_processor 1401, assignments 767, audit 377, AutoGrader 762, billing 2216,
classrooms 640, dashboard 430, students 849, users 844. Against batch 22 (8266), +20, counted by each test's own line,
all H-205:

- ai_processor +19: `tests_h205_image_fallback` 18 (new), `tests_grading_hardening` +1 (image backups never nano).
- audit +1: `tests_metrics` +1 (the image backup counts in `model_fallback_rate`).
- Every other app is unchanged. H-205's edits to `tests_grading_config` (the pinned version, now `cfg:49188dfc9eb4`)
  and `tests_reading_config` changed tests without adding any.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran, and all 24 of its tests are ok, counted by their own test
lines (the same 24 as batches 21 and 22).

**Load and memory.**

- The load was 1.41 2.05 1.26 at the suite's start, measured after the pre-checks, and 8.22 7.04 4.29 at the end
  (the parallel workers finishing). Nothing else of the team's ran beside it.
- Available memory was 21,762 MB at the start, giving a 19G cap. The lowest available memory during the run was
  16,920 MB.

**The log:** `strict_b23.log.xz` (packed with `xz -9e` to stay under the 500 KB hook limit), 9,439,122 bytes unpacked,
sha256 `cf887850b47abd3c44fd9603db266a9ba2208ff4e8e6a15adb7220c4d9faf8db`. Unpacking it was checked to give the
identical bytes. `strict_b23.summary.txt` is the script's summary.

**Disclosed: blocked outbound = 8.** The same 8 blocked attempts to reach Google's token address from
`users/views.py` `_google_auth` inside the Google sign-in tests (H-226), as in batches 17 and 19 to 22. Every test
passed. No batch 23 file is on that path.

**Not shown by this run:** live provider behaviour (H-205's tests replace only the provider call; claude-haiku-5.5 was
measured live in H-205 run1, not here), the second-opinion gap with question images (row H-254, not in this batch),
anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
