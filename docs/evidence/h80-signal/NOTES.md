# H-80: reaching 100% also sets has_hit_80_percent (beta bug fix, owner go 2026-11-11)

Branch `task/h80-signal` from origin/beta 4a20bea4b. Venv: `~/Documents/Virtualenvs/h80_env` (from this branch's requirements.txt + requirements-dev.txt; deleted when H-80 is done). Runs: flock, `--noinput`, BILLING_HOLDS_ENABLED / RUN_LOAD_TESTS / CI_REQUIRE_REDIS / CI_REQUIRE_NETWORK unset.

- R1 red (tests only, `billing.tests.test_h80_hit_80_percent`): expected 8 run, 5 red. Got Ran 8, FAILED (failures=2, errors=3): the jump to 100%/110% gives (False, True); the 3 backfill tests: ModuleNotFoundError (no migration yet). 85%, 79% and zero allocation pass (guards).
- R2 green (`test_h80_hit_80_percent` + `test_analytics_and_audit_log`): Ran 19 OK. `makemigrations --check`: no changes. `check_migration_safety` vs origin/staging and origin/main: both exit 0, 0074 "additive only".
- Mutants (`mutants.log`): M1 elif comes back -> 2 FAIL, killed. M2 backfill drops its has_hit_cap filter -> 1 FAIL, killed. Both restored, sha256 OK.
- N1 near (billing + users + guards60, final tree 62849ee7e, `--parallel 4`): expected Ran 4423 (billing 2419 + users 1119 + guards60 1087, minus 202 guard tests already inside billing/users), 1 static skip. Got Ran 4423 OK (skipped=7) in 389 s (`N1_near.log.gz`). Count matches; the 6 runtime skips beyond the static one are not attributed (default verbosity does not list reasons).
- Landing note: staging already has billing 0074-0084 (B1a/B1b); merging beta into staging needs an empty billing merge migration joining 0084 and 0074_h80 (precedent students/0040_merge.py, 6074b2c95). The backfill also raises the nightly conversion score (+30) for cap-only teachers - intended.
