# Verification: H-120, the migration safety check judges a changed column by what it was (d5)

- **Branch:** task/h120-migration-check-before-state at **5c48475a**, on task/beta-batch-11 7944259e (0b's base update, 047da1bd). The check itself last changed at 8258eb02; the test module last changed at ea5d45bf. 5c48475a adds only docs/evidence/h120-migration-check-before-state/ over ea5d45bf.
- **The change:** one CI script, `scripts/check_migration_safety.py`; one new test module, `AutoGrader/tests_migration_safety_check.py`; a mutation runner. No application code, no migration, no settings change. The script runs in the "Migration safety" workflow on pull requests only.
- **Verifier:** v2 (independent), 2026-10-06. One slot from 0b, 17:48:29 to 17:48:51 WAT, load 4.15 at the start and 4.52 at the end (1-minute) (no database, nothing timed).
- **Verdict:** **VERIFIED-WITH-NOTES**

## What the founder should know first: 13 red files become 12, not 6
The check is red today on 13 migration files of pull request 2 (beta into dev). The change clears 7 of them. It also adds a rule the old check did not have, that a change of a column's type is reported, and that rule reports 6 other files of the same pull request for the first time.

| | Files |
|---|---|
| Red today | 13 |
| Cleared by this change | 7 |
| Still red, the check is right about them | 6: assignments/0038; billing/0011, 0013, 0014, 0016, 0017 |
| Red for the first time, each a change of a column's type | 6: billing/0010, 0031, 0034, 0036, 0039; students/0027 |
| Red after this change | **12** |

- **How this was found.** d5's first table listed only the files that fail today, and its summary said 13 become 6. v2 listed every altered column of the project against v2's own statement of the three rules (Django's migration loader from disk; not the check's code) and found the six. d5 then swept all 107 files the pull request adds with the check's own functions, old rule and new, and got the same six; no other file's verdict worsens.
- **The check itself agrees, run as the workflow runs it** (v2's run, below): 107 new migration files against origin/dev; 12 FAIL lines, exactly the twelve v2 had written to a file beforehand; 3 acknowledged; 92 pass; exit code 1.
- **Each of the six is a true report** under the rule the SM approved: a type change can rewrite or lock the table, and a worker on the previous release reads and writes the old type. The SM ruled that the rule stays and the evidence gains a second table.
- All 12 are already applied on beta. "Red" says what the house rule makes of the file, not that something is broken today. Nobody acknowledged any file in this row.

## The two tables, read by v2 against the files
The SM asked v2 to check each line against the migration file itself, to its last operation, not against the check's output. v2 read all 20 files with its own reader (every operation in order; for an altered column the field before and after from Django's own state; both halves of a state-only block; the source for raw SQL, data steps and the marker).

- **The first table (14 files): every line and every verdict holds,** after one correction v2 asked for: billing/0059 adds three columns to the database, not four (the fourth "added field" is the record-only re-declaration of an existing column). Two things the lines lean on were read as well: assignments/0038 is an automatic timestamp with no default, which Django fills at migration time, so it is safe going forward and unsafe to roll back until 0040; billing/0012 does fill the new id before 0013 makes it required and unique.
- **The second table (6 files): all six lines hold.** v2 asked for one addition (billing/0031 also changes a default from 0 to 1 in the same operation; no change in the database) and noted that students/0027's own header already argues that its change is safe as one step. Both are in the evidence's table at 5c48475a (the test module's strings keep the shorter form, and the evidence says so).
- What a type change does to stored values is said in the table from the two types alone. Neither d5 nor v2 looked at data.

## The change, as read
- **Before:** an altered column was judged by the field after the change alone: NOT NULL with no default was reported as "making a column NOT NULL" even when the column was NOT NULL already.
- **After:** it is judged against the field before the migration, taken from the migration files with a loader that has no connection. It is reported when a nullable column becomes NOT NULL with neither a default nor a database default; when the column's type changes; when `max_length`, `max_digits` or `decimal_places` gets smaller; and when its earlier state cannot be found.
- Also: an added many-to-many field is not reported; a database default counts as a default; operations are judged in order; a `SeparateDatabaseAndState` is judged by its database half.
- **d5's reliance on Django, checked.** A nullable column made NOT NULL with a default is not reported, because Django fills the NULL rows. v2 read the installed Django 5.2.6 (`db/backends/base/schema.py`): when the old field is nullable, the new one is not, and the new one has a default or a database default, the schema editor runs an UPDATE that writes the default into the NULL rows before it sets NOT NULL. Read, not run.

## d5's two own points, and the disclosure
- **K12 was killed by one test more than d5 had written.** The extra test is the real-file test, on billing/0059, whose state-only block removes a field in Django's record. v2 read the block: the explanation is right. The expected file is committed as written; the second version (for d5's second chain) is separate.
- **A statement withdrawn:** that billing/0059's work is raw SQL. The file has none. The correction changed description strings and one comment of the test module after the first battery; v2 read that diff (ab4e6979..839a0ec5) and found no assertion or code line in it. The second table then changed the module again (ea5d45bf: one string corrected, a second table and three tests added), and d5 ran the module and all 13 mutants again on that tip, against a second expected file written beforehand: 13 of 13 killed, every set as written. So the battery is on the final test module (rule 17's addendum).
- **The 14:37 run without a grant** (the module once through plain `unittest`): disclosed by d5 at once, used nowhere.

## d5's gates, read by v2 from the committed raw logs (not repeated, rule 15)
| Gate | The raw log |
|---|---|
| (r) the red commit d607ffb6 | Ran 16 tests, FAILED: the 11 named tests |
| (a) the module and the repo-wide guard list, at ab4e6979 | Ran 288 tests in 111.140s, OK |
| (b) 13 mutants, at ab4e6979 | 13 of 13 killed; 12 sets as written, K12 a superset |
| second chain at ea5d45bf: the module | Ran 19 tests in 0.672s, OK |
| second chain at ea5d45bf: the 13 mutants again | 13 of 13 killed; every failing set equals the second expected file |

- No owning-app run: no application code changes (SM's approval).
- **Nothing but evidence after ea5d45bf;** against batch 11 the branch changes the script and the test module and nothing else outside its evidence folder. v2's run at ea5d45bf stands for 5c48475a.
- **Credential shapes** in the branch's files, archives opened (83 texts): no URL with anything in the password position.
- 0b's cross-side run on the merged tree (H-124's guard, H-127's guard and this module, at ea5d45bf): Ran 55 tests in 5.275s, OK. The committed log is the file 0b gave v2 (same sha256). Batch 11 adds no migration file, so the base update changes nothing the check reads.

## v2's run (17:48:29 to 17:48:51, at ea5d45bf)
Expectations were written before any run of the check by v2; 0b read the script, the runner, the probe and the expected file before the grant. Every inner run wrote its own file.

| Step | Result |
|---|---|
| 1. Baseline: d5's module and v2's six probes | Ran 25 tests in 0.997s, OK (d5's 19 and v2's 6; every prediction of the probe held) |
| 2. The check itself, `--base origin/dev`, as the workflow runs it | exit 1; **12 FAIL, 3 ACK, 92 OK**; the FAIL set is exactly the one written at 17:39:00, before any run of the check by v2. ACK: billing/0032, 0033, 0046 |
| 3. M1: `decimal_places` is no longer a limit whose shrinking is reported | **KILLED.** Ran 19 tests, FAILED (failures=1): exactly `test_a_column_made_shorter_is_reported_with_both_numbers` |
| 3. M2: a Python default no longer counts as a default | **KILLED.** Ran 19 tests, FAILED (failures=6): exactly `test_changes_that_are_additive_pass`, `test_columns_every_existing_row_can_get_a_value_for_pass` and `test_each_one` |

What v2's probes hold, each a prediction written first:
- **E1, E2:** the check says of each of the six newly reported files exactly the type change v2 had read, and none is acknowledged; billing/0032 changes a type too and carries a marker.
- **E3, what a pass does not mean:** NOT NULL with a default passes whatever the default is: the same default as before; a default of None, which fills nothing; one computed default on a unique column, which would put the same value in every NULL row (billing/0013 is safe only because 0012 fills first).
- **E4, rule gaps that pass today** (none occurs in today's files): a decimal whose whole part shrinks because only the decimal places grow; `unique` added to an existing column; a rename through `db_column`; a foreign key pointed at another table (d5's stated limit).
- **E5:** a foreign key made one-to-one is named as a type change.
- **E6, never judged, before or after this row:** a table rename, a new index, a new constraint.

- **Rules 16, 13, 12** wrap each step. **Rule 17:** `PYTHONDONTWRITEBYTECODE=1` and `python -B`; `__pycache__` of the script's folder deleted before the baseline, before each mutant and after each restore; every restore equals the commit's blob by sha256. **Rule 18:** every run wrote straight to its own file with stdin from the null device.
- **Load:** 1-minute load 4.15 at the start, 4.52 at the end. One line in the end-to-end log is not the check's: "systemd-run failed with exit status 1." is the wrapper reporting the check's own exit code 1, the expected one.
- **Before the slot** v2 ran its own reading scripts from the same checkout (Django's loader from disk; no database, not the check, not a test); 0b was told in the slot request.

## Notes
- **N1 (limits of the rules, SM's ruling: stated here, and one LOW row).** The three gaps of E4 other than the foreign key: a decimal's whole part, a unique or primary-key constraint added to an existing column, a rename through `db_column`. And the two edges of E3.
- **N2 (what the check never reads).** `RunSQL` and `RunPython` (row H-135), a dropped foreign-key constraint (billing/0059 drops six and passes), indexes and constraints, a table rename.
- **N3 (markers that are no longer needed).** By d5's sweep eight files carry a marker they would not need under the new rule (students/0026; billing/0030, 0048, 0054, 0058, 0063, 0064, 0069). They stay; a marker on a file that passes does no harm.
- **N4 (reach).** The workflow runs on pull requests only and the team pushes to beta directly, so the check guards little of today's flow. Not changed here.
- **N5.** Nobody closed, merged or edited pull request 2, and no file was acknowledged.

Files: `~/Documents/Projects/GAP-v2-handover/` `tests_vf2_h120_probe.py`, `vf_h120_mutants.py`, `vf_h120_run.sh`; v2's reading tools and their outputs `h120_read_14_v2.py`, `h120_read_14_v2.out.txt`, `h120_read_all_new_v2.py`, `h120_read_6_v2.out.txt`, `h120_scan_all_alters_v2.py`, `h120_scan_all_alters_v2.out.txt`, `h120_predict_pr2_v2.py`, `h120_predict_pr2_v2.out.txt`, `h120_pr2_files_origin_dev.txt`; the run: `runs/h120_047da1bd_e2e_expected.txt`, `runs/h120_ea5d45bf_baseline.log`, `runs/h120_ea5d45bf_e2e.log`, `runs/h120_ea5d45bf_e2e_compare.txt`, `runs/h120_ea5d45bf_mutants.log`, `runs/h120_ea5d45bf.status`, `runs/h120_ea5d45bf_mutant_logs.tar.gz` (each inner run's whole output).
