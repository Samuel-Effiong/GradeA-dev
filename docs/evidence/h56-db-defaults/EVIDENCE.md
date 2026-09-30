# H-56: database defaults so a code-only rollback does not break inserts

Branch `task/h56-db-defaults` on beta `463e222`. Commits: `3758b03` (the defaults, migrations and tests), `5c86555` (guard scope narrowed). Backlog: `docs/HARDENING_BACKLOG.md` H-56.

## The risk

Code older than a migration omits that migration's new column from INSERT. Nine NOT NULL columns added after production (origin/main `9c21bee`) had only a Django-side `default`. After a code-only rollback to production's code, every insert into their tables would fail with a NOT NULL violation. That covers user creation, wallet `get_or_create`, every Stripe webhook, assignment creation and the daily risk task.

## The change

- `db_default` on all 14 NOT NULL fields added since `9c21bee`, each with the same value as its Python default. That is the nine, plus five on tables created in the same commit (`df305cc`), per the SM's scope.
  - `Assignment.updated_at` gets `db_default=Now()`.
  - The two JSON fields get a typed `models.Value`.
- Four `*_db_defaults_for_rollback` migrations: users 0040, billing 0070, assignments 0040, dashboard 0004.
  - For the nine columns the tested stop-gap `GAP-rollback-set-defaults.sql` (kept outside the repo) covers, their `sqlmigrate` output matches it statement for statement. The stop-gap has no statements for the other five.
  - One exception: `Now()` renders as `STATEMENT_TIMESTAMP()` where the stop-gap used `now()`.
- `makemigrations --check`: clean.

## The tests (`AutoGrader/tests_migration_rollback_defaults.py`)

- **Guard.**
  - It flags a NOT NULL column with no `db_default` when an AddField or AlterField after production's heads touches it, its table exists at production's heads, and the column does not. That is exactly the column an older INSERT omits. The allow-list is empty.
  - Out of scope, for the same reason: tables production does not have (older code never inserts into them; this is how CreateModel is excluded), and AlterFields on columns production already has (older code lists those in its INSERT).
  - The first version flagged every touched field. It reported 8 such fields that no rollback can break, so `5c86555` narrowed it.
  - In scope today: exactly the nine rollback columns.
- **Guard on the guard.**
  - The nine are in its scope.
  - The production heads exist.
  - On a synthetic state with `token_epoch`'s `db_default` dropped, it flags that field alone.
- **Old-code INSERTs** (real PostgreSQL). For each of the nine columns, a raw INSERT listing every other column (as older code's INSERT does) succeeds, and the row gets the default.

## Runs (rule 13: `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`)

| Tree | Result |
|---|---|
| beta `463e222`, before the change, with this test file copied in | **FAILED as required** (`run_before.log`): the guard and its synthetic check fail, and all nine INSERT subtests raise `NotNullViolation`, one per column. |
| `5c86555`, with the change | **5 OK** (`run_after.log`) |

## For 0b

Billing 0070 is also used by the unmerged `task/overage-lock` (H-62) and `task/p1b-divergence` (H-28). Whichever lands second needs a merge migration.
