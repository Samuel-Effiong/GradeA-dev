# Integrated gate: Section 8 evidence, E800 register fix and Stripe schedule into `beta`

**Verdict: PASS.** `beta` moved **`1d00b9f` → `fb9b29ccca8e2993c819d5420e500177f31190bf`** by compare-and-swap on 2026-09-14, with the owner's approval.

This gate covers exactly the tree below. It does not carry over to any later `beta` commit: a later material change needs its own gate.

## What moved

| Commit | Change | Same change as the approved commit |
|---|---|---|
| `d52b3e0` | Section 8 evidence record: integration via `2715c64` (docs) | `95e5d52` |
| `5bbb494` | Stale E800 exemption and register row removed for `ai_processor/views.py`, which Section 5 deleted. The register was recounted to 32 files and 347 hits. | `3f985d3` |
| `fb9b29c` | Nightly Stripe plan-price reconciliation schedule plus its beat-watchdog entry (`AutoGrader/settings.py`, `.secrets.baseline`), kept as its own commit | `c2c31f6` |

- **Proof of identity:** `git patch-id --stable` is identical for each pair.
- **Tree check:** the tip tree `ab0b3456b97f828b3d5788e60e1656b1744200a0` equals an independent `merge-tree` of the two approved branches with `1d00b9f`.
- **Stripe schedule:** an earlier Stripe gate on `5c7199b` lost its logs in a session restart, and the owner ruled it **not run**. This integrated gate is the authoritative verification of the schedule.

## Results

| Check | Result |
|---|---|
| Worktree | detached, used only by the gate (`../Grade-Automator-Plus-s8-stripe-gate`) |
| `beta` before and after | `1d00b9f`, unchanged throughout |
| Services | PostgreSQL 18.6, Redis 8.0.5. A real round-trip ran through the Django cache (`django_redis`). |
| Fingerprint before = after | tree `ab0b3456…`; 1,053 tracked files; content sha256 `23b9e2a18d74d4a3…`; 0 diff bytes; 0 untracked files |
| `pre-commit run --all-files` | exit 0. All 24 hooks passed, including black, isort, flake8 (E800 enforced), mypy, bandit, detect-secrets, and the gunicorn/Stripe timeout sync guard. |
| `manage.py check` | no issues |
| `makemigrations --check` | no changes |
| Migration safety against `1d00b9f` | no new migration files |
| Full suite | fresh `test_s8_stripe_integration_gate`, **no `--keepdb`**, `--parallel 1`, under `systemd-inhibit`: **Ran 4146 tests in 1667s, OK (skipped=20), exit 0**, 21:16–21:44Z |
| Teardown | test DB destroyed. 0 "other sessions" lines; afterwards `pg_database` 0, `pg_stat_activity` 0. |
| Sleep | 0 suspend events (journalctl) |
| Coverage | The suite rebuilt by import only has 4,146 tests, equal to the run. It includes the Section 8 modules (remediation 47, audit fixes 23, rigor 35, dashboard 73, real AI chat 2), `billing.tests.test_price_reconciliation` 63, `AutoGrader.test_health` 14 and the H-1 cache suites. |

## Skips (20, all accounted for)

- **18 static:**
  - 9 billed real-AI tests, opt-in via `RUN_REAL_AI=1`, including `dashboard.tests_real_ai_chat` ×2
  - 8 load tests, opt-in via `RUN_LOAD_TESTS=1`
  - 1 live network check, opt-in via `CI_REQUIRE_NETWORK=1`
- **2 at runtime,** from CI guard tests whose flag is unset:
  - `test_redis_is_required_when_ci_says_so` (`CI_REQUIRE_REDIS`)
  - `test_network_is_required_when_ci_says_so` (`CI_REQUIRE_NETWORK`)

Any other runtime skip would have pushed the total above 20. Details are in `7-skip-accounting.txt.gz`.

## Gate history in this window

1. **First run on `02e964a`: ABORTED.**
   - Section 9's mutation run, with a real Celery worker on shared Redis, overlapped it from 20:47:48Z to about 20:51Z, during the cache suites.
   - The H-1 session identified the mechanism. 11 test modules override `CACHES` to a fixed Redis DB and `cache.clear()` it (FLUSHDB), bypassing the per-process prefix. Overlapping suites therefore flush each other's keys, and a cache test can fail or pass for the wrong reason.
   - The run was discarded, and its summary is kept as `aborted-02e964a-summary.txt.gz`. H-1 is preparing a fix. Until it lands, gates must run strictly serially.
2. **`beta` moved during the window:** Section 5 took it `30b7b95` → `1d00b9f`, docs only. The commits were rebased onto `1d00b9f` and the new exact tip was gated.
3. **One launch was refused by the gate's own idle guard,** which matched its own command text. The guard was fixed to match executable names and relaunched. No test ran in that attempt.

## Logs

- **Permanent location:** `/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-gate-logs/fb9b29c-20260914T2115Z/`.
- **Compressed copies** are in `docs/evidence/s8_stripe_integration_gate/`, with sha256 sums of the originals and the copies in `SHA256SUMS.txt`.
- **Full unfiltered suite log:** 54,426 lines, sha256 `451fa7c55e8477e5e21e3267cba5e2421fa2cff15bd5dabc89bf320c476ecd0e`. It is `5-full-suite.log.gz` in this directory.

## Main checkout sync after the move

- **Updated:** 5 of the 6 changed paths.
- **Left untouched:** `.secrets.baseline`, because it holds another session's staged change.
- **Everything else:** `git status` outside the moved paths is identical before and after.
