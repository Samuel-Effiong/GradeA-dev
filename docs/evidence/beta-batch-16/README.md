# Beta batch 16: module gate (rule 23)

Rows merged, in this order, onto beta `b4fda750` (batch 15a): H-211 (`fc35d036`), H-208 (`5b9e9956`), H-203 (`5fe68114`), H-209 (`a0c5af33`), then H-209's own evidence commit (`7d328eac`, docs only). Every merge was conflict-free. `billing/tests/test_refusal_handling.py` is touched by two rows (H-211, H-208); it is in the gate below.

Gate run on the merge tip `72d6a157` (the code of all four rows), 2026-10-09 19:09:21 to 19:13:46 WAT (clock read in the script), one inhibit around the whole script (`gate_b16.sh.txt`):

- `pre-commit run --all-files`: exit 0, 25 hooks, 24 Passed, 1 Skipped (check for broken symlinks: no files), none failed. The table is `hook_table_72d6a157.txt`. No tracked file changed.
- `makemigrations --check`: no changes detected.
- 14 test modules (the rows' own, H-164/H-202's, the Google auth tests, `AutoGrader.tests_cache_bespoke_1114`, `billing.tests.test_refusal_handling`): Ran 253 tests, OK (skipped=3), 0 FAIL/ERROR headers. Console: `module_gate_console.txt`; the raw log is `module_gate_modules_72d6a157.log.gz`.

This gate is not the full run. The batch's one full run and its record follow in the next commit.
