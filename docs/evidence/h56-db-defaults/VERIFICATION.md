# Verification: H-56 database defaults for rollback @ 2e78950

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Hardening (d5).
**Base:** beta 463e222 (next bundle). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** The 14 NOT NULL columns added since production (9c21bee) now carry database defaults, so a code-only rollback to production's code can INSERT again. The guard's narrower scope, which d5 flagged as a deviation, is correct, and I recommend the SM accept it. Nothing is required before merge.

## What I checked
My own detached checkout with its own test DB, under `systemd-run` MemoryMax=6G, nice and timeout.

| Check | Result |
|---|---|
| Scope | 4 AlterField migrations (users 0040, billing 0070, assignments 0040, dashboard 0004), the matching `db_default=` in the 4 models, the guard and INSERT test file, and evidence. |
| **Defaults in the migrated schema** (my probe reads `pg_attribute`/`pg_attrdef` in the migrated test DB) | All 14 are NOT NULL **with** a default: 0 or false for the integer and boolean columns, `'{}'::jsonb` / `'[]'::jsonb` for the two JSON columns, `''` for `previous_local_product`, and `statement_timestamp()` for `assignments_assignment.updated_at`. The 9 stop-gap columns match `GAP-rollback-set-defaults.sql` exactly, apart from `now()` becoming `statement_timestamp()`: for a single-statement INSERT the result is the same, and only a long multi-statement transaction would differ. |
| `PRODUCTION_HEADS` | Identical to the latest migration per app at origin/main 9c21bee (users 0035, billing 0058, assignments 0037, classrooms 0016, students 0025, dashboard 0002). |
| `makemigrations --check --dry-run` | No changes detected |
| Guard + INSERT tests + my probes | **7 OK** |
| Reproduce-first | d5's run on beta: 9 INSERT subtests each raise NotNullViolation on their own column. Taken from the author's log; not re-run. |

## Guard scope (d5's deviation from "AddField/AlterField, empty allow-list")
The guard flags a NOT NULL column without a `db_default` **only if its table exists at production's heads and the column doesn't**. That is exactly the INSERT that older code sends while omitting the column.
- Columns production already has are skipped. A `db_default` wouldn't help with those anyway: old code names them in its INSERT.
- My probe checked the one real hazard in that class, a column that was nullable in production and has since been tightened to NOT NULL. There are **none**.
- Tables production lacks are skipped for the same reason as CreateModel: old code never inserts into them.

The allow-list is still empty. **I agree with the narrower scope.**

## My mutants (4)
| Mutant | Result |
|---|---|
| H1: `token_epoch` loses its `db_default` in users 0040 | killed by 3 tests. The guard names `users.customuser.token_epoch`; the old-code INSERT fails; my schema probe fails. |
| H2: the guard's production-column check replaced by an unconditional skip | killed: `test_the_guard_flags_a_not_null_field_without_a_db_default` |
| H3: a new migration adds a NOT NULL `BooleanField` without `db_default` to `users.CustomUser` | killed. The guard names `users.customuser.vf_new_flag`. |
| H4: the users production head moved to 0039, so `token_epoch` looks as if production had it | killed: the guard's self-test |

All restores were sha-checked, and the added migration file was deleted.

## Notes (not blocking)
**N1 (checked, no action).** My probe first flagged `billing.creditledger.user` as a production column missing from the final state. That's a state-only change: billing 0059 reinterprets the FK `user` as a plain `user_id` UUID through `SeparateDatabaseAndState`, and the database column `user_id` is kept (now nullable). Old code still finds its column, so there's no rollback hazard.

**N2 (process).** Once this is live on a deployment, the rollback sections of later packages can drop the stop-gap SET DEFAULT SQL for these 9 columns (see the rollback caveat). Until then the caveat stands. When production moves, `PRODUCTION_HEADS` has to move with it, as the test file's own comment says.

**N3.** billing 0070 is kept by the SM's ruling; H-28 and H-62 renumber when revived.

## Re-verification: guard rule (a)+(b) @ b029f5c. Verification Engineer 1a, 2026-09-30
**Verdict for b029f5c: VERIFIED-WITH-NOTES, with R1 REQUIRED (tests only).**

The SM declined the table-age scope and approved rules (a) and (b), which are independent of the production cutoff:
- (a) an AddField of a NOT NULL column without `db_default`, on any table;
- (b) an AlterField that turns a column from nullable to NOT NULL, judged against the migration state before that migration.

The code implements both (`migrations_since_production`, `rollback_candidates`, `broken_fields`).

**Runs:** guard tests plus my probes, **9 OK**.

| Mutant | Result |
|---|---|
| H1: `token_epoch` loses its `db_default` | killed (3 tests) |
| H2: rule (a) dropped | killed |
| H3: a new NOT NULL AddField without `db_default` (a real migration file) | killed; the guard names `users.customuser.vf_new_flag` |
| H4: production head too new | killed |
| H5: rule (b) disabled in `rollback_candidates` | killed: `test_rule_b_fires_only_when_an_alter_makes_a_column_not_null` |
| **H6: `fields_a_rollback_would_break` passes the FINAL state as "state before"** | **SURVIVED** |

**R1 (REQUIRED, tests only).**
- The rule (b) self-test injects its own `state_before`, and the real graph has no tightening today, so nothing exercises the production wiring.
- Under H6, `previous_field.null` reads the final NOT NULL, and rule (b) silently never fires on real migrations.
- Pin the wiring: for example, run `fields_a_rollback_would_break` over the real loader plus a synthetic tightening migration and assert it's flagged, or assert that the state the lambda returns has a known real AlterField's pre-migration nullability.

**N4 (wording).** The guard's failure message says old code "omits them from INSERT (or inserts NULL)". A `db_default` fixes only the omitted case; an explicit NULL insert into a tightened column still fails. There's none today.

## Re-verification: R1 @ 1f52219. Verification Engineer 1a, 2026-09-30
**Verdict for the tip 1f52219: VERIFIED-WITH-NOTES.** Nothing is required; R1 is closed.

b029f5c..1f52219 is test-only. Rule (b)'s wiring is now a named `state_before(loader)`, used by `fields_a_rollback_would_break` and the guard-on-guard, and two tests pin it:
- `test_rule_b_reads_the_state_before_each_migration`: before billing 0070 the wallet column has no `db_default`, and in the final state it has one;
- `test_the_guard_uses_the_state_before_each_migration`: a wraps-spy asserts the guard calls `state_before` exactly once.

**Runs** (`systemd-run` 6G): guard module **9 OK**. My **H6a** (the guard inlines a final-state lambda) is killed by the spy test. My **H6b** (`state_before` returns the final state) is killed by the state test. H5 is still killed. Per rule 15, the author's run is relied on for the rest. N4 (the failure message's "or inserts NULL") stands as a wording note.

## Correction (Verification Engineer 1a, 2026-09-30): a regression missed at 1f52219
Bundle 3's strict full run (0b, f9ad0dc) failed on `assignments.tests_pdf_cache…test_unsaved_assignment_gets_a_never_matching_key`.
- **Cause:** `Assignment.updated_at` got `db_default=Now()` and has no Python `default=` (it can't: `auto_now` excludes `default`). So an **unsaved** instance now holds Django's `DatabaseDefault` sentinel instead of `None`. `assignments/pdf_cache.build_cache_key` tests `updated_at` for truth; the sentinel is truthy, so `.isoformat()` raises.
- **Scope, checked against the 4 migrations:** the other 13 fields all have a Python `default=`, so unsaved instances get real values. `bulk_create` paths (e.g. `classrooms/scale_my_students.py`) get `updated_at` from `auto_now`'s `pre_save`. The assignment serializers exposing `updated_at` serialize saved rows. `pdf_cache` is the only affected reader found.
- **Why I missed it:** I ran the guard module and my probes only. H-56 changed models in 4 apps, and the author's evidence had no owning-app regression. Under rule 15 a missing author regression must be flagged and run, and I didn't do that. The earlier VERIFIED-WITH-NOTES at 1f52219 is **withdrawn** until the fix is verified.
- **Required:** fix the reader (not the field), with a test that pins it, and commit the owning-app regressions for assignments, users, billing and dashboard.

## Re-verification: the pdf_cache regression fix @ 2247007 (code 7c28742). Verification Engineer 1a, 2026-09-30
**Verdict for the tip 2247007: VERIFIED-WITH-NOTES.** Nothing is required. The correction above is closed.

- **Fix.** `assignments/pdf_cache.build_cache_key` now keys on `updated_at.isoformat()` only when `updated_at` is a real `datetime`, and on the never-matching `"unsaved"` otherwise (`None`, or H-56's `DatabaseDefault` placeholder). The `db_default` stays, as the rollback needs it.
- **New pins** (`UnsavedInstanceTests`):
  - an `apps.get_models()` scan asserts that `Assignment.updated_at` is the **only** field in the project with a `db_default` and no Python default;
  - an unsaved assignment's key ends `:unsaved`;
  - one with a real timestamp keys on its isoformat.
- **Runs** (`systemd-run` 6G): the guard module + my probes + `assignments.tests_pdf_cache`, **50 OK**.
- **My mutants:**
  - P1 (the truth test restored) is killed by the new test and the original `test_unsaved_assignment_gets_a_never_matching_key`;
  - P2 (`_state.adding` instead of the type check) is killed by the real-timestamp test, which pins the type-based rule;
  - P3 (a second `db_default`-only field added to `dashboard.StudentRiskAlertState`) is killed by the scan test, so the next such field can't slip through.
- **Regression evidence (rule 15 addendum).**
  - The author's assignments run: 613 OK (`docs/evidence/h56-db-defaults/pdf_cache_fix_7c28742/`).
  - users, billing and dashboard are covered by 0b's bundle-3 strict run at f9ad0dc. That run had H-56 in and ran 5014 tests, and its only error was this pdf_cache one.
  - Bundle 3's re-run will confirm the whole combination.
- **Audit of other readers:** matches mine. `scale_my_students`' `bulk_create` sets `updated_at` via `auto_now`, and the serializers exposing it see saved rows only.
