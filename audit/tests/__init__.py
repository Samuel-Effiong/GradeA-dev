"""Test tooling for the audit app that is NOT part of the suite: benchmarks
and harnesses (test_bench_*.py). They skip in the suite and run only by
explicit label with AUDIT_BENCH=1. Kept under tests/ so the repo-wide guards treat them as test
code, never production (v2's N1 on S4; Gate 10 at 3d6575c)."""
