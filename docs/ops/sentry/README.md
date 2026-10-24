# Sentry: the Grade A+ health dashboard

**Where:** Sentry organisation **Enhanced Electronics** (`enhanced-electronics`, US region), project `python-django`, dashboard **"Grade A+ health (Epic A)"**, id `10448614`. Created through the Sentry API on 15 October 2026. The definition sent is [`grade_a_health_dashboard.json`](grade_a_health_dashboard.json).

**What it shows.** These are the Epic A metrics from `audit/metrics.py`, plus Sentry's own error counts:

| Row | Widgets |
|---|---|
| Headline (big numbers) | Grading failure rate · Credit ledger anomalies (should be 0) · Audit write failures (should be 0) |
| Grading | Failure rate over time · Gradings · Model fallback rate · Unknown model rate |
| Money and audit | Credit ledger anomalies by `kind` · Audit write failures by `action` |
| Errors users hit | Error codes over time by `code` · Top error codes · `SERVER_ERROR` count |
| Security | Sign-in attempts capped, by `cap` (target / global) |
| App errors | Error events · Top unresolved issues |

The metrics use Sentry's trace-metrics aggregates, of the form `fn(value,<metric>,<counter|distribution>,none)`. A trace-metric widget cannot be a table; "Top error codes" is a categorical bar for that reason.

**A widget is empty until its event happens** on a server running Epic A code. On 15 October 2026 only `reason_code_rate` had arrived, from beta.

## Beta and production (H-246)

Beta runs with `ENVIRONMENT=prod`, so until H-246 every beta event reached Sentry as `prod`. Set these on each server:

| Variable | Beta | Production |
|---|---|---|
| `SENTRY_ENVIRONMENT` | `beta` | leave unset (Sentry keeps `prod`) |
| `SENTRY_RELEASE` | optional: the deployed commit, e.g. `beta-<sha>` | optional: the deployed commit |

`SENTRY_ENVIRONMENT` changes only the name Sentry files events under; `ENVIRONMENT` still decides how the app behaves. `SENTRY_RELEASE` is read by sentry-sdk itself.

Once beta reports as `beta`, filter the dashboard by environment, `prod` for production.

## Alerts

The rules are listed in [`../epic_a_alert_rules.md`](../epic_a_alert_rules.md). This organisation uses Sentry's newer **Monitors** alert system, so the rules are created there.

**Recipients:** the owner decided on 15 October 2026 that alert emails go to **all organisation members**.

The alerts are created only after beta reports under its own environment. Until then a beta test spike would page as production.
