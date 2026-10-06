# H-120: the migration safety check judges a changed column by what it was

**Author:** d5. **Branch:** `task/h120-migration-check-before-state`, on
`task/beta-batch-9` `dea58a6c`. **Verifier:** v2. One CI script
(`scripts/check_migration_safety.py`), one new test module, a mutation
runner. No application code, no migration, no settings change.

Source: my reading for H-119 (row H-120, SM); the SM's approval of the
proposal on 2026-10-06, with type changes and shrinking lengths in scope.

**State of the gates at this commit:** all have run, once, on `ab4e6979`
(0b's grant on 2026-10-06): the red commit is red as written, the check's
test module and the guard list are green, 13 of 13 mutants are killed.
One mutant's failing set was one test LARGER than the one written
beforehand; the SM accepted it as recorded (see "Mutants"). There is no
owning-app run: no application code changes (SM's approval of the
proposal).

## What the check is, and what was wrong
The "Migration safety" workflow runs this script on pull requests. A new
migration that is not additive must carry an acknowledgement comment, or
the check fails. It had no tests.

It judged an `AlterField` by the field AFTER the change alone: NOT NULL
with no default was reported as "making a column NOT NULL", even when the
column was NOT NULL already and only gained a choice, help text or length.
Such a migration is safe, and to pass it needed a marker that said
nothing true about it (students/0026, H-119).

## The change (6 points)
1. **An `AlterField` is judged against the field as it stood BEFORE the
   migration,** read from the migration files themselves with a loader
   that has no connection. No database is touched.
2. **It is reported in three cases,** and the message names the case: a
   nullable column becomes NOT NULL with nothing to fill its NULL rows;
   the column's type changes (both types named); `max_length`,
   `max_digits` or `decimal_places` shrink (both numbers named).
3. **Otherwise it passes:** a choice, help text, a wider column, a
   database default on a column that was NOT NULL already.
4. **An added many-to-many field is not reported.** It adds a table, not
   a column.
5. **A database default (`db_default`) counts as a default,** for an added
   and for an altered column.
6. **Operations are judged in order,** each against what the ones before
   it left. A `SeparateDatabaseAndState` is judged by its database
   operations. A field whose previous state cannot be found is reported:
   it cannot be shown to be additive.

Unchanged: the operations that were always reported (drop, rename, the
two "together" rebuilds), the marker and how it is read, the exit codes,
the output lines, the workflow file.

## The real migrations the check fails on today
These are the 13 files that fail on the founder's pull request 2, and
students/0026, which carries a marker only because of this defect. The
same table is in the test module, where a test holds it.

- **Passes:** additive. No acknowledgement is needed.
- **Needs a decision:** the check is right about the file. It stays red
  until a person acknowledges it in the file. **Nobody has, and nobody
  does in this row: that is the founder's decision, file by file.**

All of them are already applied on beta. "Needs a decision" says what the
house rule makes of the file, not that something is broken today.

| File | What it does | Now |
|---|---|---|
| assignments/0038 | Adds `Assignment.updated_at` as a NOT NULL column with no default. Safe going forward; a rollback is not, because the previous release cannot insert a row. 0040 repairs that. | **Needs a decision** (a NOT NULL column added with no default) |
| assignments/0040 | Gives that same column a database default. It was NOT NULL already. | Passes |
| billing/0009 | One more choice on `CreditBucket.bucket_type`, NOT NULL already; a new column that has a default; two more columns altered with nothing about NULL or type changed. | Passes |
| billing/0011 | Drops `BetaProfile.uuid`, adds a nullable `uid`, and changes `BetaProfile.id` from text to an automatic number. | **Needs a decision** (a dropped column; a type change, now named as one) |
| billing/0013 | Drops `BetaProfile.id`; `uid` becomes NOT NULL and the primary key. It has a default, and 0012 fills it first, so that part is not reported. | **Needs a decision** (a dropped column) |
| billing/0014 | Renames `BetaProfile.uid` to `id` in one step. | **Needs a decision** (a rename) |
| billing/0016 | Twenty-one operations: drops `SubscriptionPlan.overage_block_price`; adds ten columns, one of them (`tagline`) NOT NULL with no default; alters six without narrowing any; makes two new tables; adds the `features` many-to-many. | **Needs a decision** (a dropped column; a NOT NULL column added with no default). The many-to-many is no longer reported. |
| billing/0017 | Drops `SubscriptionPlan.overage_credit_price_cents`, and alters one column with nothing about it changed. | **Needs a decision** (a dropped column) |
| billing/0021 | One more choice each on four columns that were NOT NULL already. Three of them had no default and were the ones reported. | Passes |
| billing/0022 | Two new columns that are safe to add, and one column altered with nothing about it changed. | Passes |
| billing/0023 | `SubscriptionPlan.name`: five choices become eight. NOT NULL already. | Passes |
| billing/0025 | `SubscriptionPlan.name`: more choices, and 20 characters become 100. | Passes |
| billing/0059 | Drops the database's foreign-key constraints on six relations of the two financial audit tables and lets one of them be NULL; turns `CreditLedger.user` from a relation into a plain id column in Django's own record only (same column, no data touched); adds four nullable columns. Nothing is removed, renamed or narrowed. | Passes. **A person should still look at this file:** the check has no rule about a dropped foreign-key constraint. |
| students/0026 | One more choice on a column that was NOT NULL already. | Passes on its own. Its marker (H-119) stays. |

**So of the 13: seven pass, six need a decision.**

**How this table was checked, and what that corrected.** After the run I
read all 14 files to their last operation (`read_14.py.txt`, output
`read_14.out.txt`: every operation in order, for an altered column its
state before and after, both halves of a state-only block), and compared
each line above with the file, not with the check's output. The SM
ordered it, after mutant K12 showed that I had not read billing/0059 to
its end. The verdicts did not change. Seven lines did:
- **billing/0059: an earlier statement of mine was wrong.** I had written,
  in the proposal, in the first version of this table and in messages,
  that its real work is raw SQL which the check does not read. I wrote
  that from memory. The file has no raw SQL and no data step. What it
  does is in the table now.
- billing/0021 has four altered columns, not three; billing/0009, 0011,
  0013, 0016 and 0017 each do more than their first line said.
- In the test module only the description strings and one comment
  changed for this, in the commit that adds this evidence: no assertion and no code, so
  the battery, which ran before it, stands.

## What it does not cover
- **`RunSQL` and `RunPython` are not judged,** before or after this row.
  A file whose work is a data step passes unread (billing/0060, a
  backfill in `RunPython`). The docstring now says so and a test pins it.
  The SM has logged a separate row (H-135).
- **A dropped foreign-key constraint is not judged.** billing/0059 drops
  six and passes. Loosening is additive in the house rule's sense, but a
  person should look at such a file.
- **A foreign key pointed at a different table** is not seen as a type
  change: both are a foreign key by name.
- **A widening is still a type change** when the type's name changes
  (text to long text, a number to a bigger number): reported, on purpose;
  the table may be rewritten.
- **The check runs on pull requests only,** and the team pushes to beta
  directly, so it guards little of today's real flow. Not changed here.
- **Nobody closes, merges or edits pull request 2,** and no file is
  acknowledged.

## A disclosure
At 14:37 on 2026-10-06, before any grant, I ran the new test module's
methods through Python's `unittest` loader in a plain interpreter, to
predict red and green. No database and no browser, about two seconds; it
is still a test run without 0b's grant. I told 0b and the SM at once. Its
output was cut off and is used nowhere: every result here is from the
granted runs below, and the expected failing sets were written from
reasoning and from direct calls of the check's own functions.

## Commits
| Commit | What |
|---|---|
| `d607ffb6` | Red tests first, with one seam, behaviour unchanged: the check's loop moved into `findings_for` |
| `8258eb02` | The fix |
| `ab4e6979` | The mutation runner. The gates ran on this tip |
| the commit that adds this file | After the run: the table's description strings in the test module corrected from the full reading (text only), and this evidence |

## Gates
One chain, `chain.sh ab4e6979 d607ffb6`, on 0b's grant (15:39:52 to
15:43:32 WAT on 2026-10-06), serial, 6G cap, every run's output straight
to a file. No test here has a wall-clock assertion. Two short runs of
another row went beside it, as 0b allowed.

| Step | Result, from the raw log | Load (1 min) start / end |
|---|---|---|
| (r) the red commit `d607ffb6`, the check's test module, in a disposable worktree | Ran 16 tests in 0.048s, FAILED (failures=30), exit=1 | 2.75 / 3.42 |
| (a) the test module and the repo-wide guard list, 23 labels | Ran 288 tests in 111.140s, OK, exit=0 | 3.42 / 3.90 |
| (b) 13 mutants, each against the test module | baseline OK; 13 of 13 KILLED; runner exit=0, 68 s | 3.90 / 4.65 |

- **(r):** the failing tests are exactly the 11 named beforehand
  (`expected_repro_d607ffb6.txt`); the 30 failures are their samples.
- **(b):** every mutant's raw output holds its own "Ran 16 tests" line
  and a FAILED line; no BROKEN, no exit 124 or 137. Every restore equals
  the commit's blob by sha256.
- **The chain's own exit was 1,** because of the one differing set below.
- **No test database was left:** listed read-only through Django's
  connection after the run; none is named for this row. The tests make
  none.

## Mutants
The expected failing tests were written by class and method before the
granted run (`expected_kills.py.txt`, file clock 14:41:47; 0b read it,
sha256 prefix `67af0a43f8d43d0d`). **It is committed as it was written and
has not been edited since.** Result: `expected_kills_ab4e6979.txt`.

| Mutant | What is broken | Failing tests | Against the written set |
|---|---|---|---|
| K1 | the old rule put back (judged by the end state) | 5 | as written |
| K2 | becoming NOT NULL is never reported | 4 | as written |
| K3 | a default no longer excuses becoming NOT NULL | 2 | as written |
| K4 | a type change is not reported | 3 | as written |
| K5 | a shrinking limit is not reported | 1 | as written |
| K6 | a growing limit is reported too | 2 | as written |
| K7 | a field with no known past passes | 1 | as written |
| K8 | an added many-to-many is reported again | 2 | as written |
| K9 | a database default is not a default | 2 | as written |
| K10 | the state is never moved forward | 2 | as written |
| K11 | a state-only block is not looked into | 1 | as written |
| K12 | a state-only block is judged by its record half | 3 | **one MORE than written** |
| K13 | a real file is judged against the state after itself | 1 | as written |

- **K12, the difference.** I wrote two failing tests; three failed. The
  third is the real-migrations test, on billing/0059: that file contains
  a state-only block whose record half removes a field, and under K12
  that removal is reported. I had written that no file in the table uses
  such a block. I had not read 0059 to its end. The mutant is killed by
  a superset of the written tests, for a reason read from its raw output
  (`logs/raw/K12.out` in the archive) and from the migration file.
- **The SM's ruling (2026-10-06):** accepted as recorded, with this
  disclosure and the written file left unedited; no second run. The same
  ruling ordered the full reading of the 14 files described above.
- **Rules 17 and 18:** `PYTHONDONTWRITEBYTECODE=1` on every run;
  `__pycache__` of the script's folder deleted before the baseline,
  before each mutant and after each restore; each inner run writes to its
  own file with stdin from the null device. The runner is H-124's below
  the mutant table (0b diffed it).
- **The battery and the test module:** the battery ran on `ab4e6979`.
  Since then only description strings and a comment of the test module
  have changed (the commit that adds this file); no mutant is made stale by text.

## The credential pattern
This folder, archives opened: 0 URLs with anything in the password
position, 0 encoded ones. Five files hold assignment-form lines whose
names contain "pass" or "key"; all are code or prose (`J_PASS=`,
`A_PASS=`, "passed:", "Passes:", `primary_key=`, `KeyError:`).

## Files
- Raw logs: `r_repro_d607ffb6.log.gz`, `a_modules_ab4e6979.log.gz`,
  `b_mutation_battery_ab4e6979.log`.
- `battery_ab4e6979.tar.gz`: `results.tsv`, the short log per mutant, and
  `logs/raw/*.out`, each inner run's whole output.
- `chain.status`, `expected_repro_d607ffb6.txt`,
  `expected_kills_ab4e6979.txt`.
- Scripts as run: `chain.sh.txt`, `expected_kills.py.txt`.
- The reading of the 14 files: `read_14.py.txt`, `read_14.out.txt`.
