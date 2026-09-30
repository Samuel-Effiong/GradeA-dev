# Epic A completion S8: volume and alerting (the parts that need no founder)

Branch `task/epic-a-s8`, cut from phase2/epic-a `fda47d7` (S4 in). Phase 2 only. The verifier is v2. There's no migration and no change to app behaviour: a read-only management command, a rates module, test tooling and a proposal doc.

Plan: `08_epic_a_completion_plan.md` §8, as scoped by the SM: the volume measurement, the alert-rule proposal, and the D5 note. Both founder parts are left for the founder: the counts-only query on main, and setting the rules up in Sentry.

## 1. Volume: `python manage.py audit_volume_report`
**Measured:** from whatever database it runs against, it reports:
- events per day per action over `--days` (default 30);
- events per retention class;
- bytes per row;
- the table's heap, index and total size.

**Projected** (with `--teachers N --students M`):
- rows per day per action and per retention class;
- the steady-state table size at each class's age limit (12 months GENERAL, 3 years STUDENT_RECORD, as in `audit.tasks.sweep_audit_retention`);
- rows and MB at 12 months and at 3 years;
- the sweeps' daily delete volume at steady state (equal to rows added per day, per class).

**Safe to run against production (SM conditions):**
- **Counts and sizes only:** it never selects an actor, a target, an address, a user agent or any metadata. A test asserts that no email, no person id, no target id, nothing email-shaped and nothing UUID-shaped reaches the output.
- **Read-only:** everything runs inside one `SET TRANSACTION READ ONLY` transaction. A test in a real transaction (TransactionTestCase) shows a write inside the report is refused by Postgres.
- **Cheap:**
  - bytes per row come from a bounded sample (`TABLESAMPLE SYSTEM (1)`, at most 5,000 rows, falling back to the first 5,000 rows on a small table);
  - the row count is the planner's estimate (`pg_class.reltuples`);
  - the per-day counts read only the window.

  A test captures every statement touching the table and checks that each one is windowed or bounded.

**Where the rates come from:** `audit/volume.py` holds the per-person daily rates the projection uses. The S8 harness measured them.

## 2. The harness: `audit/bench_volume.py` (test tooling, run by label on the test DB)
One **busy school day**, deliberately an upper bound, driven through the real routes and tasks, with Celery dispatch and the AI provider patched as the suite's own tests do. It makes no real outbound calls (the H-39 guard stays active) and renders no mock (rule 14).
- **A teacher:** a roster import of 30, a batch upload of 30, grade-all of 30, the 30 grading runs (the real Celery task with a fake provider), publish-all, and 3 grade edits.
- **A student:** one sign-in (viewing writes nothing).
- **The system:** the two audit sweeps.

Results: _pending_ (§5).

## 3. Alerting: `docs/ops/epic_a_alert_rules.md` (a proposal for the founder)
Nine Sentry rules, each with its signal, condition, priority, environment (staging/beta/main) and owner (the founder, for now), plus a step-by-step UI walkthrough and the FR-A-10 staging acceptance step:
- the five thresholds in `audit/metrics.py`: grading failure rate, model fallback rate, credit-ledger anomalies (EXPIRE failures included), reason-code spikes, audit write failures;
- a SERVER_ERROR burst on open routes;
- a sign-in attack (`audit_failed_auth_suppressed_total`, the FAILED_AUTH_CAPPED counter);
- H-28's stale-intent escalation (an ERROR log; P0 once H-28 is on main);
- dropped audit metadata.

**Nothing is written into config.**

## 4. Founder actions (not done here)
- **D5, the counts-only query on main:** plan 08's D5 is to run **this command against main**, via Railway, as a founder action on the founder's return:
  `python manage.py audit_volume_report --days 30 --teachers <N> --students <M>`
  Its output is counts and sizes only. If the founder prefers estimates instead, the same command projects from `--teachers`/`--students` alone.
- **Set up the alert rules** in Sentry from `docs/ops/epic_a_alert_rules.md`. Then run the staging acceptance step, the founder confirms the notification arrived, and 1a records it.

## 5. Other S8 items
- **v2's N1 on S4 / the SM:** `audit/bench_history.py`'s entry in `SUPPRESSION_ALLOWED` is now labelled "test tooling (Gate 6 benchmark, run by explicit label only)". v2 confirmed nothing imports it. **The production `history.suppressed()` sites are exactly two:**
  1. `audit/history.py` `record_bulk`;
  2. `students/services.py` `_populate_and_save_grade`.

  The other entry is the test tooling above.
- **The harness is also scanned by S4's guards:** its one setup write to a tracked field goes through `record_bulk`, as production code must.

## Harness results
_pending_

## Gates
_pending_
