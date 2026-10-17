# beta-batch-3 (bundle 3, with batch-2b folded in): full suite (Gate 10)

Recorded by Integration & Release (0b), 2026-09-30. Team brief rules 13 (memory cap) and 15 (one strict full run per bundle; after a test-only fix, only the touched modules re-run).

## Contents (on beta 755aa27)
Each item was merged with its verification record:
- H-43 `/auth/otp` single reply (`3af18a1`; record `7b2f816`), which also carries 1a's batch-2a Gate 1 refresh record `974aa59`.
- Student-tiles partition (`f2510cc`; record `0da2166`).
- H-58/H-59 licence seat counting (`39fee13`; final record `28c4b03`).
- H-53 `/auth/verify` per-address budget, rebased on 755aa27 (`d883ce5`; record `c45950e`).
- H-56 DB-level defaults for rollback safety (`1f52219`, record `e4f3932`), plus its pdf_cache fix (`7c28742` / `2247007`, record `6eca384`).
- **Batch-2b** (SM decision to combine): H-1 stage 3 Design A + step 4 wildcard removal, closing H-4 (`24d5ec0`; record `534c36e`).
- Raw-cache-write guard allow-list for H-53 (`f9f3d94`).
- Backlog docs: H-61, H-28/H-62, H-63 (`8205361`); H-64 (`926a43b`); status refresh (`60a8d0b`).

Migrations (all H-56, `db_default` only): `users 0040`, `assignments 0040`, `billing 0070`, `dashboard 0004`, each `_db_defaults_for_rollback`. The migration graph is linear (`makemigrations --check` clean).

## Strict full runs
Command, with every `RACE_COST_*` variable unset (the capped code defaults):
`systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput`. Whole-repo mypy passed before each run.

| Tip | Start–end (WAT) | Result | Cause | Resolution |
|---|---|---|---|---|
| `f9ad0dc` | 14:40–14:47 | Ran 5014, **FAILED (errors=1)**, 0 blocked outbound | `assignments.tests_pdf_cache…test_unsaved_assignment_gets_a_never_matching_key`: H-56 gave `Assignment.updated_at` a `db_default` with no Python default, so an unsaved instance carries a `DatabaseDefault` sentinel and `pdf_cache.build_cache_key` called `.isoformat()` on it. | d5's fix `7c28742` (a non-datetime `updated_at` is treated as unsaved), verified by 1a. Batch-2b was folded in before the re-run. |
| `6212ce9` | 15:18–15:24 | Ran 5094, **FAILED (failures=1)**, 0 blocked outbound | `AutoGrader.tests_cache_invalidation_coverage…test_nothing_is_cached_under_a_raw_key`: batch-2b's guard allows 2 raw writes in `users/throttling.py`, but H-53 adds 3 (its budget counter and lock, lines 190/194/215). Not a response cache. | d5's test-only allow-list fix `f9f3d94` (2 → 5, with the reason), verified by 1a. |

Both failures were integration gaps between items verified separately; neither is a flake.

## Final: after the test-only fix (rule 15)
- Tip `0c95d4e` = `6212ce9` + merge of `f9f3d94`. Its tree is identical to `f9f3d94`'s.
- Re-run of the only failing module, under the 6G cap: `AutoGrader.tests_cache_invalidation_coverage` **Ran 10, OK**.
- The 6212ce9 full run had no other failure. Everything after 0c95d4e is docs only.

## Rollback
- **Target:** beta `755aa27`.
- The H-56 migrations only **add** DB-level defaults, and they can stay in place on a code-only rollback. Older code omits these columns from INSERTs, and the DB default then fills them. That is H-56's purpose: once they're applied, the rule-11 NOT NULL rollback caveat no longer applies to these 14 columns.
- No other migrations.
