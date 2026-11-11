# H-80: reaching 100% also sets has_hit_80_percent (beta bug fix, owner go 2026-11-11)

Branch `task/h80-signal` from origin/beta 4a20bea4b. Venv: `~/Documents/Virtualenvs/h80_env` (from this branch's requirements.txt + requirements-dev.txt; deleted when H-80 is done). Runs: flock, `--noinput`, BILLING_HOLDS_ENABLED / RUN_LOAD_TESTS / CI_REQUIRE_REDIS / CI_REQUIRE_NETWORK unset.

- R1 red (tests only, `billing.tests.test_h80_hit_80_percent`): expected 8 run, 5 red. Got Ran 8, FAILED (failures=2, errors=3): the jump to 100%/110% gives (False, True); the 3 backfill tests: ModuleNotFoundError (no migration yet). 85%, 79% and zero allocation pass (guards).
