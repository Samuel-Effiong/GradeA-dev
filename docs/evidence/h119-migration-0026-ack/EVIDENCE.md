# H-119: migration 0026 acknowledged for the "Migration safety" check; the check stays red, and why

**Author:** d5. **Branch:** `task/h119-migration-0026-ack`, off beta
`63c3da22`. Tooling. A comment only: no operation, no SQL, no new
migration, no change to any workflow.

## The change
`6256dd15` adds four comment lines at the top of
`students/migrations/0026_alter_backgroundprocessingtask_task_type.py`:
the marker `docs/MIGRATIONS.md` (section 4) asks for, and the reason.

- 0026 only adds one choice, "student_summary", to
  `BackgroundProcessingTask.task_type`. The column was created NOT NULL
  with `max_length=64` in 0017, and 0026 keeps both. Nothing changes in
  the database.
- `scripts/check_migration_safety.py` flags every `AlterField` whose
  field is NOT NULL with no default, whatever the field was before, with
  the message "AlterField making a column NOT NULL without a default".
  For 0026 that message is not true. (The check's rule is its own row,
  H-120.)
- The word is "contract" (SM): `billing/migrations/0058`, the same kind
  of choices-only change, carries the same word. The docs say the word
  is not checked; it records that a human looked.

## What this does NOT do: the check stays red
The row said the check fails on 0026 alone. That was wrong.

- The check runs only on pull requests. On GradeA-dev the only one is
  the founder's pull request 2, beta into dev. GitHub's dev is
  `4cae3265`, dated 2026-05-07 (SM, read on GitHub, 2026-10-05); the
  local `origin/dev` ref is the same commit.
- Against that base the pull request adds 107 migration files. In CI on
  `63c3da22` (SM): 83 OK, 10 acknowledged, 14 FAIL.
- The same command here on `6256dd15`: 83 OK, 11 acknowledged, 13 FAIL,
  exit 1. So 0026 moved from FAIL to acknowledged, and nothing else
  changed.
- The 13 that still fail, none touched by this branch:
  `assignments/0038_assignment_updated_at`,
  `assignments/0040_db_defaults_for_rollback`, and `billing/0009`,
  `0011`, `0013`, `0014`, `0016`, `0017`, `0021`, `0022`, `0023`, `0025`,
  `0059_append_only_audit_tables`. Their findings are of four kinds:
  RemoveField, RenameField, AddField without null or a default, and the
  same over-broad AlterField message as 0026.
- **Nobody acknowledges those 13 now** (SM). They are applied
  migrations compared with a dev branch from May. Whether pull request 2
  should exist is the founder's decision, and the SM has asked. If it
  stays, the one-time backlog acknowledgement that `docs/MIGRATIONS.md`
  describes becomes a new row then. Two of the 13 are recent work
  (`assignments/0038`, `0040`) and deserve a real look, not a blanket
  marker.
- **The check guards nothing in the team's real flow today:** it runs on
  pull requests only, and the team pushes to beta directly. The workflow
  is not changed here.
- Nobody closes, merges or edits pull request 2; it is the founder's.

## The 13 other files, read only (asked for by the SM; no markers, no code)
For the founder or the SM to decide from later. "Before" and "after" are
each field's state in Django's own migration graph, read without a
database (`read_13.py.txt`, output `read_13.out`). "Safe as applied" is
my judgement from reading the operations, for a deploy where workers of
the previous release are still running; all 13 are already applied on
beta, and I looked at no database. "H-120 clears it" means: the file
would stop failing if the check flagged an AlterField only when the
column actually becomes NOT NULL.

| File (added) | What the flagged operation does | Safe as applied? | H-120 clears it? |
|---|---|---|---|
| `assignments/0038_assignment_updated_at` (2026-08-24) | Adds `Assignment.updated_at`, `auto_now`, NOT NULL, no default. | Forward: yes; Django fills existing rows with the migration's time for an `auto_now` field. **Rollback: no.** After it, the column is NOT NULL with no database default, so the previous release, which does not know the column, cannot insert an assignment. This is the case H-56 was raised for. The flag is right. | No (AddField) |
| `assignments/0040_db_defaults_for_rollback` (2026-09-30) | Gives the same column a database default (`Now()`). The column was already NOT NULL. | Yes: `SET DEFAULT` only. It is H-56's repair of 0038: with it, the previous release can insert again. The flag is a false alarm. | **Yes** |
| `billing/0009` (2026-05-07) | Flagged: `CreditBucket.bucket_type` gets a fourth choice; already NOT NULL. (Also adds `BetaProfile.uuid` with a default: not flagged.) | Yes: choices only, nothing in the database. | **Yes** |
| `billing/0011` (2026-05-07) | Drops `BetaProfile.uuid`; changes `BetaProfile.id` from a text primary key to an auto number. | No, by the house rule: a dropped column and a primary key's type change in one step. Part of the BetaProfile key change of May (0009 to 0014). The flag is right. The check's AlterField message is wrong here too, and it does not notice the type change at all. | No (RemoveField) |
| `billing/0013` (2026-05-07) | Drops `BetaProfile.id`; makes `uid` NOT NULL and the primary key (0012 fills it first). | No, by the house rule: a dropped primary key column. The flag is right. | No (RemoveField; and `uid` really does become NOT NULL) |
| `billing/0014` (2026-05-07) | Renames `BetaProfile.uid` to `id`. | No, by the house rule: a rename in one step. The flag is right. | No (RenameField) |
| `billing/0016` (2026-06-04) | Drops `SubscriptionPlan.overage_block_price`; adds `tagline` (text, NOT NULL, no default, blank allowed) and the `features` many-to-many. | The drop: no, by the house rule. `tagline`: forward yes (Django fills existing rows with an empty string), rollback no (the same shape as `assignments/0038`). `features`: a false alarm; a many-to-many adds no column to the table. | No (RemoveField, AddField) |
| `billing/0017` (2026-06-04) | Drops `SubscriptionPlan.overage_credit_price_cents`, which 0016 had added. | No, by the house rule: a dropped column. The flag is right. | No (RemoveField) |
| `billing/0021` (2026-06-10) | One more choice each on `CreditBucket.bucket_type`, `CreditLedger.ledger_type` and `SubscriptionPlan.name`; all already NOT NULL. | Yes: choices only. | **Yes** |
| `billing/0022` (2026-06-11) | Flagged: `CreditBucket.bucket_type` altered with no change to null, default, length or number of choices. (Its two AddFields are safe and not flagged.) | Yes. | **Yes** |
| `billing/0023` (2026-06-16) | `SubscriptionPlan.name`: 5 choices to 8; already NOT NULL. | Yes: choices only. | **Yes** |
| `billing/0025` (2026-06-19) | `SubscriptionPlan.name`: 8 choices to 13 and `max_length` 20 to 100; already NOT NULL. | Yes: choices, and a column made wider. | **Yes** |
| `billing/0059_append_only_audit_tables` (2026-09-03) | Flagged: `CreditUsageLog.wallet` and `.bucket`, foreign keys already NOT NULL, altered with no change to null. | The flagged operations: yes. The file's real weight is elsewhere and the check does not look there: it makes `CreditLedger.user` nullable and swaps the relation for a plain column inside a hand-written `SeparateDatabaseAndState`, explained at length in the file's own docstring. I have not judged that part. | **Yes** (for what the check flags) |

Count: H-120's narrower rule would clear **7 of the 13** (`assignments/0040`;
`billing/0009`, `0021`, `0022`, `0023`, `0025`, `0059`). The other **6**
(`assignments/0038`; `billing/0011`, `0013`, `0014`, `0016`, `0017`) are
flagged for an operation that really is non-additive and would still
need a marker or a decision. Five of those six are the May and June
billing history; `assignments/0038` is the one with a consequence still
worth knowing, and 0040 has already repaired it.

Three things the reading showed about the check itself, for H-120:
it says "making a column NOT NULL" for alterations that do not;
it flags a many-to-many AddField, which adds no column; and it does not
see a column's type change or anything inside `SeparateDatabaseAndState`.

## The run
| What | Result | Files |
|---|---|---|
| `python scripts/check_migration_safety.py --base origin/dev` on `6256dd15`, as the workflow runs it; 2026-10-05, under 0b's word | exit 1: OK 83, ACK 11 (0026 among them), FAIL 13 | `check_after_6256dd15.log`, `check.status`, `check.sh.txt` |
| `read_13.py`: each of the 13 files' operations and field states from Django's migration graph, no database | read only; the table above | `read_13.py.txt`, `read_13.out` |

It is not a test run: the script imports the migration modules and opens
no database, so it ran under `timeout 300` with its output straight to a
file, without the test wrapper (0b agreed). The local `origin/dev` ref
was used as it was; nothing was fetched. The tree was clean after it.

No Django test was run for this change: it adds comments to one file.

## For the reader (static read; 0b proposes 1a)
- Check the claim against the two files: `task_type` in
  `students/migrations/0017_backgroundprocessingtask.py` (NOT NULL,
  `max_length=64`, eight choices) and in 0026 (the same, nine choices).
- `git diff 63c3da22 6256dd15` is four added comment lines.
- In the log, 0026 reads `ACK` and is followed by the check's own
  finding line, which is the over-broad message described above.
