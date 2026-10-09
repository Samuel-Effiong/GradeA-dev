# Verification: H-109, the Redis hygiene URL-shape test builds its URLs from parts (d5)

- **Branch:** task/h109-redis-hygiene-test-urls at **b43ea36e**, on task/beta-batch-7 085adecd. Bundle 7. The code tip is d58a65d3; b43ea36e adds only docs/evidence/h109-redis-hygiene-test-urls/.
- **The delta:** test-only, one file, AutoGrader/tests_redis_hygiene_databases.py (+21/-14), all inside `test_the_database_is_set_whatever_the_url_says`. A delta on H-97, which v2 verified at 1e407f75.
- **Verifier:** v2 (independent), 2026-10-05. **No test run and no slot:** the proof below is by evaluating the two tables, which is stronger for this change than a repeat of d5's run.
- **Verdict:** **VERIFIED-WITH-NOTES**

## The change, as read
- **Before:** eight of the test's nineteen URL shapes were written in the source as whole URLs with the test's made-up password in the password position.
- **After:** a helper inside the test joins scheme, user, password and the rest; the eight shapes call it. No line of the file is a URL with a password.

## The proof that the test feeds the same strings (v2's own program, no test run)
v2 parsed the test function at 085adecd and at d58a65d3, executed only the statements up to and including the `shapes` table in each, and compared the results.

| Check | Result |
|---|---|
| Entries | 19 and 19 |
| Labels, in order | Identical |
| Strings, in order | Identical |
| Entries that carry a password part, once evaluated | 8 and 8 |
| The made-up password | Unchanged |
| Every statement after the table (the loop and the assertions), compared as syntax trees | Identical |
| The docstring | Identical |
| Hunks of the diff | Six, all inside the function (lines 224 to 286) |

## The credential pattern (SM rulings of 2026-10-05)
| Where | A URL with anything in the password position | An assignment to a PASS-like name |
|---|---|---|
| The module at 085adecd | 8 lines | 1 line |
| The module at d58a65d3 | **0 lines** | 1 line: the made-up password, in a test file, marked for the secrets hook |
| d5's twelve battery logs | 0 | not scanned for this form |
| EVIDENCE.md and BASELINE_EXPECTED.md | 0 | not scanned for this form |

## d5's gates (read, not repeated: rule 15.4)
| Gate | Tip | Result |
|---|---|---|
| The touched module | d58a65d3 | 9 tests OK |
| H-97's battery, D1 to D5 | d58a65d3 | 5 of 5 killed; restores verified |
| The same battery on the unedited module (0b's extra grant) | 085adecd | 5 of 5 killed, with the same counts |
| Redis afterwards, H-97's read-only listing | | 0 test keys of dead or live pids in all 16 databases |

- **v2 compared the two batteries' logs line by line:** per mutant they differ only in the commit line and the elapsed time. The FAIL and ERROR header lines are identical, mutant for mutant (52, 16, 8, 15 and 12 lines).
- **Rule 17:** EVIDENCE.md states `PYTHONDONTWRITEBYTECODE=1` and the `__pycache__` deletion before the baseline, before each mutant and after each restore.
- **H-97's committed logs and results are untouched:** d58a65d3..b43ea36e adds files only under the H-109 evidence folder.

## Notes (none blocks)
1. **H-97's record under-reports two mutants against its final module** (d5's finding, confirmed on the read). H-97's D1 to D4 were recorded at a4f379be; the commit that answered v2's own finding (f1e0e9d7) then gave this test a subtest per shape and database, and only D5 was recorded there. Today D1 fails 43 tests with 9 errors and D4 gives 15 errors; H-97's results say 20 and 3, and 1. The mutants are killed either way. v2's H-97 record states "4 of 4 killed" at a4f379be and "D5 killed" at f1e0e9d7 and quotes no counts for D1 to D4, so it needs no correction; v2 did not notice at the time that D1 to D4 had not been re-run on the final module.
2. **The made-up password stays in the file as one assignment.** That is the form the SM's ruling allows in a test file; it is counted, not hidden.
3. **No run by v2.** If a run of the module is wanted for the record, it is 9 tests and needs 0b's grant; v2 judged it adds nothing to the table comparison and d5's green run.
