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

Results: see *Harness results* below.

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

## Harness results (`harness.txt`; measured 2026-09-30 on the S8 branch)
| Flow | Events written | Retention |
|---|---|---|
| roster import of 30 | 31 ROSTER_CHANGE (30 enrolments + 1 aggregate) | STUDENT_RECORD |
| batch upload of 30 | 1 SUBMISSION_UPLOAD (one per batch) | STUDENT_RECORD |
| grade-all of 30 (the request) | 30 GRADING_REQUESTED | STUDENT_RECORD |
| the 30 grading runs (the Celery task) | 30 GRADING_COMPLETED (with before/after, S4) | STUDENT_RECORD |
| publish-all of 30 | 30 GRADE_CHANGE | STUDENT_RECORD |
| 3 grade edits | 3 GRADE_CHANGE | STUDENT_RECORD |
| a student's sign-in | 1 AUTH_LOGIN | GENERAL |
| Beat: the two audit sweeps | 2 AUDIT_RETENTION_SWEEP | GENERAL |

Bytes per row: **485** (`pg_column_size`, over 129 rows).

**Derived, not measured: CREDIT_TRANSACTION, 30 per busy teacher day.** The harness patches the AI call (`execute_graded_task`), which is also where a grading consumes credits. In production each grading writes one CONSUME per bucket drawn, usually one. `audit/volume.py` marks this as derived.

**Example projection** (worked by hand from `audit/volume.py`; the command prints the same). A school of **20 active teachers and 600 students**, with every teacher having a busy day **every calendar day**, so a deliberate upper bound:
- **Rows per day:**
  - STUDENT_RECORD: 125 × 20 = **2,500**;
  - GENERAL: 30 × 20 (credits) + 600 (sign-ins) + 2 (sweeps) = **1,202**.
- **Steady state:** 2,500 × 1,095 + 1,202 × 365 ≈ **3.18 M rows**, ≈ **1.5 GB of heap** at 485 B/row. With indexes, ×(1 + 1.75) ≈ 4.2 GB, but that index ratio is from a tiny table and overstates it.
- **12 months:** ≈ 1.35 M rows. **3 years:** ≈ 3.18 M.
- **The sweeps' daily deletes at steady state:** ≈ 2,500 STUDENT_RECORD + 1,202 GENERAL.
- **Reading it:**
  - a school year has about 190 school days, not 365;
  - most teachers aren't grading 30 scripts every day.

  So the real volume should be several times lower. The founder's D5 run of the command on main replaces this estimate with measured usage.

## Gates (rule 15: changed modules + mutation + ONE owning-app regression; logs committed)
Run on **`80eac88`**: 0b's merge of phase2/epic-a `b2890d9` (S6c N1) into `c321e4a`, one step at a time at 6G. The harness pass ran first, on `32e57ad`.

| Gate | Result |
|---|---|
| Reproduce-first | `fda47d7`, with no command and no rates module, against `audit.tests_volume_report` (`prefix_fda47d7_failing.txt`): the module fails to import. The command is new. |
| Changed modules | `audit.tests_volume_report` + `audit.tests_history_guard`: **20 OK** (`changed_modules.txt`) |
| 2 Mutation | **7 mutants, 7 killed** (`mutation_log.txt`, `mutation_results.json`): V1 not read-only; V2 no TABLESAMPLE; V3 an unbounded sample; V4 an email in the output; V5 the window ignored; V6 the student rate dropped; V7 three years kept as one. |
| 1 Regression (owning app) | `audit`: **330 OK** (`regression_audit.txt`). S8 touches only the audit app and docs. |
| mypy | whole-repo: **Passed** |
| Migrations | **No changes detected** |
| Real infra | Postgres: the read-only transaction, TABLESAMPLE, `pg_column_size`, `reltuples` and the size functions all run against the real test database, not a mock. |

## N1 (v2's record `VERIFICATION_v2_3511833.md`, SM ruling): no full scan by default
v2's EXPLAIN showed that the "class … (all time)" line, an unwindowed per-class count, was a full sequential scan. My statement-capture test had let it through, because its allowed pattern included `retention_class`. When 0b asked for the EXPLAIN plan to be recorded, a second gap showed: **no audit index leads with `occurred_at`**. The indexes are (school, action, actor, department, reason_code, retention_class) + `occurred_at`, and the trace id. So a window alone (`occurred_at >= X`) would still scan a large table.

**Fix:**
- **Every count names an index's leading column with an equality and bounds `occurred_at`, its second column.** Per day and per action, the query is `action = A AND occurred_at >= X`, one query per action on `audit_action_time_ix (action, -occurred_at)`. Per class, it is `retention_class = C AND occurred_at >= X` on `audit_retention_ix (retention_class, occurred_at)`. Each is an index range scan over the window. There are about 30 small queries instead of one grouped scan.
- **All-time is the planner's estimate:** `rows_all_time N (planner estimate, not a count)`. A new **`--exact-all-time`** flag, off by default, adds exact all-time per-class counts. Its help text says it is a full scan.
- **Tests:**
  - `test_every_windowed_count_has_an_index_path` captures every windowed statement the report runs and EXPLAINs each with `enable_seqscan = off`. It asserts no `Seq Scan on audit_auditevent` and an Index node. **What this proves:** a usable index path exists for every count, so none *has* to scan the table. **What it does not prove:** what the planner picks at production size. On the test database's tiny table the planner would choose a seq scan anyway, which is why seq scans are disabled for the check.
  - The statement-capture test is tightened: in the default path, anything touching the table is windowed, sampled with a `LIMIT`, or a catalogue lookup.
  - `--exact-all-time` is shown to count rows outside the window, and the default is shown not to.
- **Mutants:** V5 and V8 are re-anchored to the per-action and per-class queries (V8 drops the class equality, so no index leads the count).

v2 also checked that `SET TRANSACTION READ ONLY` does not leak into a caller's transaction: `transaction_read_only` is off before and after.

### Before the first run on production (founder, read-only)
Run these on main via Railway's psql. They are plain `EXPLAIN`, not `ANALYZE`, so nothing is executed and no row is read:

```sql
EXPLAIN SELECT count(*) FROM audit_auditevent
 WHERE action = 'AUTH_LOGIN' AND occurred_at >= now() - interval '30 days';
EXPLAIN SELECT count(*) FROM audit_auditevent
 WHERE retention_class = 'GENERAL' AND occurred_at >= now() - interval '30 days';
EXPLAIN SELECT count(*), avg(pg_column_size(s.*))
  FROM (SELECT * FROM audit_auditevent TABLESAMPLE SYSTEM (1) LIMIT 5000) s;
```

Expected:
- the first two: an `Index Only Scan` or `Index Scan` (or a `Bitmap Index Scan`) on `audit_action_time_ix` / `audit_retention_ix`;
- the third: a `Sample Scan` under a `Limit`.

If either count shows `Seq Scan on audit_auditevent` on a large table, don't run the report; send the plan to the team.

**N1 gates** (only the touched module, per the SM; on **`08195fb`**, 6G):

| Gate | Result |
|---|---|
| Changed module | `audit.tests_volume_report`: **9 OK** (`n1_changed_module.txt`) |
| 2 Mutation | **8 mutants, 8 killed** (`n1_mutation_log.txt`, `mutation_results.json`). V8 (the class count without its leading-column equality) is killed by `test_every_windowed_count_has_an_index_path`, `test_exact_all_time_is_opt_in_and_counts_everything` and `test_it_counts_per_day_per_action_and_per_class`. |

**How the EXPLAIN test was tightened inside the slot:**
- At `690bcef`, V8 was killed only by the count tests, not by the EXPLAIN test. With seq scans off, a count lacking the leading-column equality still plans as an index scan over the whole index, which has an Index node and no Seq Scan.
- The test now requires the **index condition** to pin the leading column (`Index Cond: … (action)::text = '…'` or `… retention_class = '…'`). The recorded plans are:
  - `Index Scan using audit_action_time_ix … Index Cond: (((action)::text = 'AUTH_LOGIN'::text) AND (occurred_at >= …))`;
  - `Index Only Scan using audit_retention_ix … Index Cond: ((retention_class = 'GENERAL'::text) AND (occurred_at >= …))`.
- Two intermediate commits fixed the regex to Postgres's two spellings (`8d5d2f1`, `a074f68`).
- A first N1 run on `2f1d130` was void (the read-only test's patched stand-in lacked the new argument).
