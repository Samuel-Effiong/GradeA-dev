# H-246: Sentry's environment named apart from ENVIRONMENT

The owner asked for a Sentry dashboard for the Epic A metrics. This was the one open Epic A item, after the S8 alert-rules proposal (`docs/ops/epic_a_alert_rules.md`). Branch `task/h246-sentry-environment`, commit `55e38501`.

## What was found in Sentry (read through the API, 15 October 2026)

- **The live Sentry is "Enhanced Electronics"** (`enhanced-electronics`, US region), project `python-django`.
  - In the last 30 days it accepted 910 errors and 4,161 transactions.
  - Since 12 October it has 48 trace-metric points, all `reason_code_rate`: `INVALID_CREDENTIALS` 29, `WRONG_PASSWORD` 13, `ACCOUNT_LOCKED` 6.
- **Its only environment is `prod`, and no event has a release.** `reason_code_rate` is sent only by Epic A code, which is not on main, so **beta reports as `prod`**. Beta runs with `ENVIRONMENT=prod`, and `settings.py` passed `ENVIRONMENT` to `sentry_sdk.init`.
- **The `.env` DSN on this machine is a different organisation** (`bondservant`, EU region). It has received nothing since 24 August 2026.
- **The organisation uses Sentry's newer "Monitors" alert system**: the `detectors/` and `workflows/` endpoints. The older alert-rule endpoints return 404.

## What changed

- **`AutoGrader/sentry_environment.py`**: `sentry_environment(app_environment, override)`. It returns the override trimmed, or `ENVIRONMENT` when the override is empty, blank or not text.
- **`AutoGrader/settings.py`**:
  - `environment=sentry_environment(ENVIRONMENT, env.str("SENTRY_ENVIRONMENT", default=""))`.
  - The helper is imported beside the scrubbing hooks, outside the missing-package `try`.
  - `release` is not passed: sentry-sdk 2.68.0 reads `SENTRY_RELEASE` itself (`utils.get_default_release`).
  - With `SENTRY_ENVIRONMENT` unset, nothing changes.
- **Docs**:
  - `docs/ops/sentry/README.md` records the dashboard, the per-server variables and the owner's choice of alert recipients (all organisation members).
  - `docs/ops/sentry/grade_a_health_dashboard.json` is the definition sent.
  - `docs/ops/epic_a_alert_rules.md` gains an update note.

## The dashboard (created through the API, not by this commit)

**"Grade A+ health (Epic A)"**, id `10448614`, has 15 widgets: 13 trace-metric widgets, one error-events widget and one issues widget.
- Every widget query was first run read-only against the events and events-stats endpoints, and returned 200.
- The first create was refused: a trace-metric widget cannot be a table. "Top error codes" became a categorical bar.
- It was read back after creating: 15 widgets, as sent.

## Gates

- `AutoGrader/tests_h246_sentry_environment.py`: 8 tests.
  - The function.
  - The settings wiring (AST): the `environment=` expression, no `release=`, and the import outside the `try`.
  - A real `sentry_sdk.Client` with a capturing transport: the event carries `environment: beta` and the `SENTRY_RELEASE` value.
- Together with `AutoGrader.tests_sentry_scrubbing`: 24 tests OK.
- **Mutation: 6/6 killed** (`mutation_55e38501.json`). Every restore was taken from `git show`, and its sha256 matched.
- **Final gate** on `55e38501`: a detached worktree, a fresh database `test_h246_gate`, run under `systemd-inhibit`.
  - Result: **8022 tests, OK (skipped=30)**.
  - Tree fingerprint `ec69f18bc2d2c6b7`.
  - Excerpt in `final_gate_55e38501_excerpt.txt`.

## Owner actions (not code)

1. Set `SENTRY_ENVIRONMENT=beta` on the beta server, and `staging` on staging if it reports. Leave production unset. Optionally set `SENTRY_RELEASE`.
2. Redeploy.
3. The alerts (the 9 rules, sent to all organisation members) are created after beta reports as `beta`.
