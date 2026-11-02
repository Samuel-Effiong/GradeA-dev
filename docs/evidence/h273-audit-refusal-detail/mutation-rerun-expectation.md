# H-273 mutation re-run: expectation (committed before the run)

Full battery on ff2cba75 (log ~/Documents/Projects/GAP-a0-runs/h273/mutation_ff2cba75.log):
25/27 KILLED, 2 SURVIVED, all 27 restores sha256 OK. Per doctrine a survivor = STOP.

Survivors were real test gaps, not equivalent mutants:
- M-H273-16 (drop "Assignment" from _TARGET_LABEL): the old coverage test only
  checked the labels were non-empty strings. New test scans production code for
  every `target_type="X"` literal plus every history REGISTRY model and requires a
  label for each.
- M-H273-18 (resolve school names for the first event only): the query-count tests
  used 5 events on ONE school. New test: two schools, one query, both names.

Re-run: `python mutants.py 16 18` on the commit carrying the new tests.
Expected: both KILLED, restores sha256 OK.
