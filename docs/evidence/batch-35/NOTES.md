# Batch 35 (H-284 + H-285 + H-286 B), beta only

Owner: fix the dependency audit (it blocks Railway QA deploys from beta); remove djoser; H-286 do both. No main, not even a hotfix (2026-11-03).

The full run uses the NEW dependency set, venv h284_deps_env (strict_bundle copy in GAP-a0-runs/b35 points there instead of h271_static_files_env).

Near run at 4ea2073f: Ran 997, OK (skipped=4). An earlier near run at f352897f was red on 1 test, AutoGrader.tests_migration_safety_check's H-120 snapshot that asserted 0027 unacknowledged. That was fixed on the H-286 branch at 7fd181a5.

Count: DiscoverRunner(...).countTestCases() = 8911. That is B34 8906, + 4 (H-285), + 1 (H-286).

- B35-E1 (committed before the run): full run Ran 8911, OK (skipped=30).
