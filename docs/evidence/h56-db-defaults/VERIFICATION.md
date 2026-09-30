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
