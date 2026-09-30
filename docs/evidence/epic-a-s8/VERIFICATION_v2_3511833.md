# Verification: Epic A S8 @ 3511833

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-s8 @ **3511833** (code 80eac88, on the staging base b2890d9). No app behaviour change: `audit_volume_report` (command), `audit/volume.py` (rates), `audit/bench_volume.py` (test tooling), `docs/ops/epic_a_alert_rules.md` (a proposal only). It also relabels S4's `bench_history` guard entry as test tooling (v2's S4 N1).

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot, from a scratch worktree detached at 3511833. Rule 15: v2's probes + `audit.tests_volume_report` + the guard; ed's audit run (330 OK) and 7/7 mutants are cited.

**Verdict: VERIFIED-WITH-NOTES.** N1 should be fixed before the founder runs the report on production (D5).

## Evidence
| Check | Result |
|---|---|
| v2 probes (`tests_vf2_s8_probe.py`) + `audit.tests_volume_report` + `audit.tests_history_guard` | **25 OK** |
| P1 PII: events with a sentinel actor email, IP `203.0.113.77`, a sentinel user agent, random target ids | the report's 27 output lines contain **none** of them, nor the actor id |
| P2 projection arithmetic, recomputed independently from `audit.volume` (10 teachers, 100 students) | steady rows **1,515,480 = 1,515,480**; rows at 12 months **602,980 = 602,980** |
| P5 read-only (the production shape: top level, a write injected inside the command) | **refused**: `InternalError: cannot execute INSERT in a read-only transaction`; nothing written |
| P3 the command inside an OUTER transaction (tests/shell) | `transaction_read_only` is `off` before and after, so READ ONLY does not outlive the command (no leak into the caller) |
| Alert-rule signal names vs the code | all match: `audit_emit_failures_total`, `audit_failed_auth_suppressed_total`, `credit_ledger_anomaly`, `reason_code_rate` (counts); `grading_failure_rate`, `model_fallback_rate` (distributions, `audit/emitter.py:407/419`); `SERVER_ERROR`, `FAILED_AUTH_CAPPED` (codes); `audit_kind=metadata_dropped` (log extra, `emitter.py:181`) |
| The alert doc writes no config | docs only; no settings, Sentry API or file writes in the code delta |
| Sampled sizes | `SELECT * FROM <t> TABLESAMPLE SYSTEM (1) LIMIT 5000`, falling back to `LIMIT 5000`; both are bounded. Only `pg_column_size` values leave the database |

## Notes
- **N1 (fix before the production run).** The "class … (all time)" line comes from `AuditEvent.objects.values("retention_class").annotate(Count("pk"))` with **no bound**. P4's EXPLAIN: `HashAggregate … -> Seq Scan on audit_auditevent`. That is a **full-table scan** on every run, contradicting the command's docstring ("no statement scans the whole table") and the SM's cheapness condition. Everything else is bounded. The fix: drop it, restrict it to the `--days` window, or estimate per class (the sampled class distribution × `reltuples`).
- **N2 (for the record).** The command reads personal columns inside the database (`SELECT *` in the size sample) but emits only sizes; the output is PII-free (P1). If the founder's Railway role is column-restricted, `SELECT *` would need `pg_column_size` on named columns instead.
- **N3.** `CREDIT_TRANSACTION`'s rate is derived, not measured (the harness patches `execute_graded_task`). This is stated in `audit/volume.py`; treat that one projection as an estimate.

Logs: `runs/s8_run1.log`, `runs/s8_run1b.log`.

---

## Re-check (N1) @ **5f7ab88**, 2026-09-30: **VERIFIED**
The code is 08195fb. Per-action counts are `action = A AND occurred_at >= X` (audit_action_time_ix); per-class counts are `retention_class = C AND occurred_at >= X` (audit_retention_ix, window only); the all-time figure is the `reltuples` estimate; `--exact-all-time` (whose help says FULL SCAN) is opt-in. The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot, beside bundle 4's full run.

v2's independent check (not ed's test): capture **every** statement the default run issues against `audit_auditevent`, and `EXPLAIN` each with `enable_seqscan = off`.

| Check | Result |
|---|---|
| v2 probes + `audit.tests_volume_report` | **15 OK** |
| P4: every default statement | **36/36** with an `Index Cond` on the leading column (`action` / `retention_class`) and no Seq Scan; 0 unbounded (bounded sample and catalogue statements exempt) |
| P4b: opt-in | default: 0 "(all time, exact)" lines, **0 unbounded statements**. `--exact-all-time`: 1 line, exactly 1 unbounded statement (`SELECT retention_class, COUNT(id) FROM audit_auditevent GROUP BY 1`) |
| P1 / P2 / P5 (still) | no leaks; the projection is exact (1,515,480 / 602,980); a write inside the command is refused (read-only) |

N1 is closed. The note that the production planner choice is the founder's EXPLAIN on main (per EVIDENCE) stands as stated. Logs: `runs/s8_n1.log`, `s8_n1b.log`, `s8_n1c.log`. (The earlier P4b/P5 failures in `s8_n1.log`/`n1b` were v2's probe code: an outdated `measured` signature and a SQL match that missed Django's `GROUP BY 1`. Both are fixed in the probe.)
