# Bench harnesses moved under a test path (Gate 10 red at 3d6575c)

Branch `task/epic-a-bench-move`, cut from phase2/epic-a `3d6575c`. Test tooling and doc strings only; no app behaviour changes. The verifier is v2 (narrow check).

## Why
Gate 10 at `3d6575c` was 5586 run, **1 failure**: `AutoGrader.tests_no_wildcard_invalidation.test_no_production_code_calls_a_wildcard_cache_operation` → `{'audit/bench_volume.py: clear': [70]}`.
- S8's volume harness called `cache.clear()`, and, living as `audit/bench_volume.py`, it counted as production code for the H-1 guard.
- S8's gates ran the audit app, not that AutoGrader guard. Rule 15 addendum 2 now requires every repo-wide guard in a slice's changed set.
- It is the same root cause as v2's N1 on S4: test tooling living as an importable app module.

## The change
- `audit/bench_volume.py` → **`audit/tests/test_bench_volume.py`**, and `audit/bench_history.py` → **`audit/tests/test_bench_history.py`**, in a new `audit/tests/` package whose docstring says what it's for. The repo's `name-tests-test` hook requires `test*.py` names under `tests/`, so each class is `@skipUnless(AUDIT_BENCH == "1")`: a skip in the suite, and a run only by label, e.g. `AUDIT_BENCH=1 python manage.py test audit.tests.test_bench_volume`.
  - The repo-wide guards skip `/tests/` paths.
  - Nothing in the app imports them.
- `audit/tests_history_guard.py`: the benchmark's `SUPPRESSION_ALLOWED` entry is removed; its stale-entry test would now fail on it. The allow-list is exactly the **two production sites**: `record_bulk` and `_populate_and_save_grade`. This closes v2's N1 on S4.
- The doc strings in `audit/volume.py`, the `audit_volume_report` command and the two moved files name the new paths. Earlier evidence files keep their paths as a historical record.

A static pre-check with the guards' own scanners, before the run:
- the wildcard scan shows only its documented, allow-listed `assignments/pdf_cache.py` exception;
- S4's bulk and suppression scans show nothing unlisted and nothing stale.

## Gates (rule 15 after a full run + addendum 2: the touched modules + ALL repo-wide guards; no second full run)
_pending_
