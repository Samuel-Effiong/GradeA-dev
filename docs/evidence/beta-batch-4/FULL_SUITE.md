# beta-batch-4 (bundle 4): full suite (Gate 10)

Recorded by Integration & Release (0b), 2026-09-30. Team brief rules 13 (memory cap) and 15 (one strict full run per bundle).

## Contents (on beta `abeda10`)
Each item was merged with its verification record:
- **expire_bucket race fix:** `0a8ff68`, 1a VERIFIED; merged at `ede7101`.
- **H-62 overage lock:** `f3002bc`, 1a VERIFIED-WITH-NOTES; merged at `98b0ddf`. Its N3 replay-path lock tests are `1448f14` (1a VERIFIED; `1afebe4`), and 1a's final record is `c1458ba` (`bd29d1f`). Gate 4 red team: PASS.
- **task-worktree.sh optional base ref:** `cd4e8ae`, v2 VERIFIED; merged at `86022c2`, with v2's record at `2ea9b92`.
- **H-28 licence Stripe divergence, Change 1:** `1109ffd`, 1a VERIFIED-WITH-NOTES after REJECTED R1/R2; merged at `957cc15`, with the record `1113bec` at `2f77394`.

**Migrations:**
- `billing 0071`: two AddFields, NOT NULL with `db_default`, plus a CHECK.
- `billing 0072`: a CreateModel, `LicenseStripeMutationIntent`.

The migration graph has one leaf, and `makemigrations --check` is clean.

## Strict full run
**Command**, with every `RACE_COST_*` variable unset:
`systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`.
Whole-repo mypy passed first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `2f77394` | 18:49:14–18:56:02 (408 s) | **Ran 5303 tests, OK (skipped=28)**, 0 blocked outbound |

The first strict run on the final code tip passed, so no re-run was needed.

## Strict full run after the F6 fold-in (founder decision b2)
Four live-bug fixes (F6) were folded into bundle 4 after the first run, each merged gated on its dry run:
- **Item 1, the add_teachers school-name leak:** `82e724a` (1a VERIFIED-WITH-NOTES); merged at `3b94aa8`.
- **Item 2, the H-38 tasks namespace and auto-grade:** `d755926` (1a VERIFIED); merged at `7faaedf`.
- **Item 3, the mid-cycle grant and trial-expiry re-checks:** `2bfa2e8` (1a VERIFIED-WITH-NOTES); merged at `e190f06`.
- **Item 4, the monthly rollover lost to the 05:00 cleanup (round 2):** `4da21c3` (1a VERIFIED, after round 1 was REJECTED); merged at `9627f7d`, with the record `52dbdc8` at `4e629d4`.

There are no migrations or settings changes in `2f77394..4e629d4`, and `makemigrations --check` is clean.

**The command** is the same as above, with every `RACE_COST_*` variable and `AUDIT_BENCH` unset. Whole-repo mypy passed first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `4e629d4` | 22:40:34–23:32:10 (3096 s wall; tests measured 514 s) | **Ran 5382 tests, OK (skipped=28)**, 0 blocked outbound |

**The wall clock includes a system suspend.** The machine suspended at 22:48:01 and resumed at 23:30:31 (`journalctl`: "The system will suspend now!" / "PM: suspend entry (s2idle)"). The run was frozen in the middle, not hung, and it resumed and passed. Django's time (514 s) doesn't count the suspend, because it uses a monotonic clock. No test failed or errored.

The first strict run on the final code tip passed, so no re-run was needed.
