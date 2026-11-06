# STOP record — H-273 near-run mismatch

Commit: cba462cf
Branch: task/h273-audit-refusal-detail
Date: 2026-10-30
Command: `python manage.py test audit users classrooms billing students assignments --settings=AutoGrader.settings --verbosity=1 --noinput`

## Result

| Metric   | Baseline (committed log) | Run         | Delta |
|----------|--------------------------|-------------|-------|
| Total    | 5832                     | 5852        | +20   |
| Skipped  | 16                       | 20          | +4    |
| Ran      | 5816                     | 5832        | +16   |
| Failures | 0                        | 1           | +1    |
| Errors   | 0                        | 1           | +1    |

## (1) ID-list diff

### Baseline integrity problem

Two different baselines exist:

- **Committed** `baseline_id_list.log` (at cba462cf): Total 5832, Skipped 16, Ran 5816.
  Full IDs trimmed to SHA-256 hash (pre-commit 500 KB limit); no full ID list from this run
  was archived.
- **Archived** `~/Documents/Projects/GAP-a0-runs/h273/baseline_id_list_full.log`:
  Total 5457, Skipped 5, Ran 5452. Contains 430 `dashboard.*` tests and **zero**
  `assignments.*` tests. Also contains 3 `dashboard.*` skips not in the committed list.

The archive was collected with the wrong label set (`audit billing classrooms dashboard
students users`), not the set recorded in NOTES line 295 and the committed log
(`audit users classrooms billing students assignments`). The committed log's
Total/Skipped/Ran header is from a correct-set run, but the full IDs from that run were
never saved — only the SHA-256 of the omitted section.

**The baseline command and settings module are not recorded anywhere.** The committed log
does not record the exact command, --tag/--exclude-tag flags, settings module, or env vars
used. I cannot confirm whether the baseline was collected with the same `--settings=AutoGrader.settings`
or the same venv. The archive's label set proves it was a different run.

### Diff against archive (wrong-set baseline)

Since no full ID list exists for the correct-set baseline, the diff below is against the
archive (wrong-set, Total 5457):

- **Added** (826): 805 `assignments.*` + 21 `billing.tests.test_billing_period_anchoring.*`
- **Removed** (431): 430 `dashboard.*` + 1 `unittest.loader._FailedTest.billing.tests.test_billing_period_anchoring`

The 805 assignments IDs are expected: the archive used dashboard instead of assignments.

The 21 billing period anchoring tests exist at f5d2d577 (the baseline commit). They were
always in the billing app. The archive collected them as 1 `_FailedTest` entry because
`billing/tests/test_billing_period_anchoring.py` imports `pytest`, which was missing from
the venv used for the archive collection. With pytest missing, the module fails to import,
so Django's loader records 1 `_FailedTest` instead of 21 real tests. This accounts for
+20 in the Total delta.

### H-273 test changes

H-273 (e198e8eb + cba462cf) added **zero new test IDs**. The only test change is updating
one exact metadata assertion in `audit/tests_admin_action.py` line 107-110:

```python
# Before
{"source": "DummySuccessView"}
# After
{"source": "DummySuccessView", "method": "GET", "http_status": 200}
```

The "refusal path" and "email-keyed ValidationError" tests (from NOTES §4 mutant targets)
are planned for Step 3 and have not been written yet.

## (2) VENV — missing packages

`billing/tests/test_license_service.py` fails with `ModuleNotFoundError: No module named 'pytest'`.

**h273 venv** (`~/Documents/Virtualenvs/AutoGrader_env_h273`): does NOT contain `pytest`.

**requirements-dev.txt** lists `pytest==9.1.1`. The h273 venv was created from
`requirements.txt` only, not `requirements-dev.txt`.

Packages in `requirements-dev.txt` but NOT in the h273 venv (54 packages):

```
bandit, black, celery-types, cfgv, codespell, coverage, detect-secrets,
distlib, django-browser-reload, djangorestframework-stubs, django-stubs,
django-stubs-ext, docutils, docutils-stubs, dparse, eradicate, flake8,
flake8-bugbear, flake8-comprehensions, flake8-docstrings, flake8-eradicate,
flake8_import_order, flake8-print, flake8-rst-docstrings, flower, honcho,
identify, imagesize, iniconfig, isort, mccabe, mypy, mypy_extensions,
nodeenv, pathspec, pluggy, pre_commit, pycodestyle, py-cpuinfo, pydocstyle,
pyflakes, pytest, restructuredtext_lint, safety, safety-schemas,
snowballstemmer, stevedore, tblib, tomlkit, types-bleach, types-docutils,
types-pytz, types-pyyaml, types-requests, virtualenv
```

All packages in `requirements.txt` (the production deps) ARE present in the h273 venv.

**Impact**: Without `pytest`, `billing/tests/test_billing_period_anchoring.py` and
`billing/tests/test_license_service.py` both fail to import. Django's test loader records
each as 1 `_FailedTest` instead of their real test counts. `test_billing_period_anchoring`
has 21 tests and `test_license_service` has an unknown count — the `_FailedTest` entry
suppresses the real count and inflates `errors` by 1 per module.

**Was the baseline collected in the same venv?** Unknown. The archive contains
`unittest.loader._FailedTest.billing.tests.test_billing_period_anchoring` and
`unittest.loader._FailedTest.billing.tests.test_license_service` — both `_FailedTest`
entries, identical to the current run. This means the archive was ALSO collected without
pytest. The committed baseline log (Total 5832) is 395 tests MORE than the archive (5457),
which is roughly consistent with swapping dashboard (430 tests) for assignments (805 tests)
and gaining the 21 real period-anchoring tests that were masked as 1 `_FailedTest`.
However, 5457 - 430 + 805 + 20 = 5852, not 5832, suggesting the committed log's run also
had pytest missing (test_billing_period_anchoring counted as 1, not 21). This is
consistent: **neither run had pytest installed**, but the committed run used the correct
label set.

Recalculation with committed baseline also missing pytest:
- Archive: 5457 IDs (dashboard set, no pytest)
- Committed: 5832 IDs (assignments set, no pytest)
- Current:  5852 IDs (assignments set, no pytest, same code)

If the committed run was at f5d2d577 with no pytest:
- Remove dashboard: 5457 - 430 = 5027
- Add assignments: 5027 + 805 = 5832 ✓

Current run at cba462cf:
- Same label set as committed, same venv (no pytest), same code for assignments/dashboard
- Should be 5832 if no tests were added

Current = 5852 = committed + 20. The 20 extra tests: `test_billing_period_anchoring` now
resolves to 21 real tests instead of 1 `_FailedTest` = net +20. This means **freezegun was
installed** in the h273 venv (it was — I installed it to fix a prior run error), and
`test_billing_period_anchoring` imports `freezegun` as well as `pytest`. Let me verify:

Verified: `test_billing_period_anchoring` imports `freezegun` (line 41), NOT `pytest`.
`test_license_service` imports `pytest` (line 10).

- **Baseline** (no freezegun, no pytest): both modules fail to import → 2 `_FailedTest`
  entries (counted as 2 tests, 2 errors).
- **Current run** (freezegun installed, no pytest): `test_billing_period_anchoring` loads
  successfully (21 real tests), `test_license_service` still fails (1 `_FailedTest`,
  1 error). Net: +20 tests (21 real minus 1 fake), errors 2→1.

This exactly accounts for the +20 Total delta (5832 → 5852) and the single error.

## (3) ENV — BETA_CREDITS mismatch

**Failure**: `billing.tests.test_free_plan_activation_security.ConcurrentActivationTests.test_racing_requests_that_all_pass_the_checks_yield_one_grant`

```
AssertionError: 15000000 != 10000000
```

**Source of values**:
- Test constant `BETA_CREDITS = 10_000_000` (line 79) — hardcoded in test file
- Model default `initial_beta_credits = models.PositiveIntegerField(default=20_000_000)` (billing/models.py:1701)
- `live_balance()` (line 273) sums all unexpired `CreditBucket.remaining_credits`

**Root cause**: `users/signals.py` auto-activates a free trial (5,000,000 raw credits) for
every new teacher user (line 336-388). The test's `setUp` calls `self.make_user(UserTypes.TEACHER)`,
which triggers the signal. The signal creates a Trial subscription with a 5M credit bucket.
Then the concurrent test assigns a Beta plan (10M credits). Result: 10M + 5M = 15M.

**Why it passed in batch 31**: The batch-31 full run (f5d2d577, Ran 8587 OK) used the
shared `AutoGrader_env` venv, which likely has a different `.env` or the test DB state
differs. The `.env` in the h273 worktree has `USE_BETA_PLAN_ON_SIGNUP=False`, but the
trial activation signal at `users/signals.py:336` checks `is_beta_eligible` (user type),
not `USE_BETA_PLAN_ON_SIGNUP`. The signal runs for ALL teacher user creations regardless
of that flag.

This is likely a **flaky concurrency test** that depends on timing — if the beta activation
completes before the trial signal fires, the signal may detect an existing subscription and
skip. In the h273 worktree the timing differs. No H-273 code touches billing, signals, or
the concurrent test.

**No env var, .env value, or settings module difference explains the 15M vs 10M.** The
value is 10M (test constant) + 5M (trial signal). The trial signal is in the codebase at
f5d2d577. This is a pre-existing race condition.

## (4) Extra skipped tests

The committed baseline has 16 skipped. The current run has 20 skipped (+4).

Without verbosity=2 output from the current run, the 4 extra skip IDs cannot be named from
this run's output. However, the committed baseline's 16 skips are all `assignments.*`,
`audit.*` bench tests, and 1 `students.*` test — all skipped by `@skipUnless`/`@skip`
decorators for expensive/real-provider tests.

The +4 skips likely come from:
- Tests that conditionally skip based on env (e.g., `CI_REQUIRE_NETWORK`, missing API keys)
- The h273 worktree .env may lack keys present in the batch-31 environment

I cannot name them without a verbosity=2 collection-only pass (Django does not support
`--collect-only`), and the SM directive prohibits additional test runs beyond the collection
in item (1).

## Summary of root causes

| Issue | Root cause | H-273 related? |
|-------|-----------|----------------|
| +20 Total/Ran | `test_billing_period_anchoring` resolves to 21 real tests (freezegun now installed) instead of 1 `_FailedTest` | No — freezegun installed to fix prior run |
| 1 failure | Trial signal + concurrent beta grant = 15M vs expected 10M | No — pre-existing signal race |
| 1 error | `test_license_service` imports `pytest`, not in h273 venv | No — requirements-dev.txt not installed |
| +4 skipped | Likely env-conditional skips (missing API keys or CI flags) | No — worktree env differences |
| Baseline mismatch | Archive collected with wrong label set; committed log has correct counts but no full IDs | Process error in baseline recording |

**The baseline archive is unusable for comparison.** A new baseline must be collected with
the correct label set, full IDs archived, and the same venv/settings used for subsequent runs.
