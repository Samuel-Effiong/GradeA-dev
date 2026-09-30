# Epic A S1b: a bound on failed-sign-in audit volume

Branch `task/epic-a-s1b`, **stacked on S2** (`task/epic-a-s2`). S2's anonymous-door fallback and this cap interact: a failure held back by the cap must not trigger S2's INVALID_REQUEST fallback. v2 verifies the S2 → S1b delta: `git diff task/epic-a-s2 task/epic-a-s1b`. Phase 2 only. No migration.

Rulings: the SM's S1b approval, then the re-ruling that closes v2's H1 and H2 (2026-09-30). Plan 08 §2.3.

## Why
Per-IP throttles bound one address, not a spray from many. S1 made every failed sign-in an audit row, so a distributed attack could write rows without limit.

## What is capped (`audit.failed_auth_cap`, one gate in `audit.emitter.emit`)
A failed AUTH_LOGIN or ACCOUNT_REGISTER (outcome FAILURE) whose requester is ANONYMOUS. **Never capped:**
- successes;
- DENIED events (a locked or deactivated account: the signal an account is under attack);
- failures by a **signed-in** requester (v2's H2: change-password). These need a valid session and are already bounded. Suppressing one would make S1's middleware fall back to an uncapped STATE_CHANGE.

**Caps**, per fixed window of `FAILED_AUTH_WINDOW_SECONDS` (default 3600). All are env-tunable settings.

| Setting | Default | Rule |
|---|---|---|
| `FAILED_AUTH_TARGET_FLOOR` | 5 | A **known** account's first 5 failures per window are ALWAYS written, whatever the global count (v2's H1: a junk-email spray can't blind real accounts) |
| `FAILED_AUTH_TARGET_LIMIT` | 30 | Past the floor, at most 30 per account |
| `FAILED_AUTH_GLOBAL_LIMIT` | 300 | Past the floor, at most 300 across everything. A failure with **no target** (an unknown email, a malformed body) is under this from its first event |

So a spray across real accounts is bounded by (number of accounts × 5) + 300, and a junk spray by 300.

**Keys** hold the account id, never an email (`audit:failed_auth:<bucket>:target:<account id>`).

## Summaries
A suppressed event is not written. When a bucket's suppressed count reaches **1, 10, 100, 1000 …** in the window, one summary event is written:
- the same action, FAILURE, `reason_code = FAILED_AUTH_CAPPED`, actor ANONYMOUS;
- target = the capped account, or none for the global cap;
- scoped to that account's school, so its school admin sees attacks on its own accounts;
- `metadata {cap: "target"|"global", suppressed_so_far, limit, window_seconds}`.

- **Lower bound (N1):** the latest summary is a lower bound on what was suppressed, within ×10. The exact count is the `audit_failed_auth_suppressed_total` metric (tagged by cap).
- **Fixed-window edge (N2):** a fixed window lets up to twice a limit through across a window boundary.
- **Atomicity (N3):** the counters are cache `add` + `incr`, atomic on the shared Redis cache, so each threshold is reached (and its summary written) exactly once under concurrency.
- **Fail-open:** any cache error writes the event as if uncapped, and logs at ERROR.

## Interplay with S1 and S2
- A suppressed event marks the request (`RequestAuditState.suppressed`). S2's door fallback then does **not** write an INVALID_REQUEST in its place; the event is counted in its summary.
- A summary is written through `emit(..., _bypass_failed_auth_cap=True)`, so it is never itself capped.

## Tests (`audit/tests_failed_auth_cap.py`)
They use small caps: floor 2, target 4, global 6.
- Past the target limit a failure becomes a summary, with the full metadata.
- Summaries are written at 1 and 10 suppressed.
- **H1:** a known account's floor is written even when the global cap is spent; past the floor the global cap applies; an unknown target is under the global cap from the first failure.
- Successes and lock denials are never capped. **H2:** a signed-in requester's failures are never capped.
- ACCOUNT_REGISTER failures are capped too.
- The keys hold the account id and never an email.
- Fail-open.
- Through the middleware: a suppressed `/auth/verify` failure leaves only the summary, with no INVALID_REQUEST fallback.
- **N3, Gate 3:** on the project's shared Redis cache (not locmem), 20 threads released together past the limit write exactly two summaries (1 and 10).

## Gates (rule 15: changed modules + mutation + ONE owning-app regression; logs committed)
Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, `EXEMPT_EMAIL_DOMAINS=` and `--noinput`, one at a time.

| Gate | Result |
|---|---|
| Reproduce-first | S2 tip c3ed1a5's source for the 5 changed files, against the new tests (`prefix_c3ed1a5_failing.txt`): **8 of 12 fail**. Nothing is capped, no summary is written, and the concurrency test sees no summaries. The 4 that pass are the "never capped" and fail-open cases, true of an uncapped tree by definition. |
| Changed modules | `audit.tests_failed_auth_cap` + `audit.tests_route_coverage` + `users.tests_auth_audit_doors` + `audit.tests_state_change`: **83 OK** (`changed_modules.txt`, on e51fb41) |
| 2 Mutation | `mutate.py`, **12 mutants, 12 killed** (`mutation_log.txt`): the floor, the target cap and the global cap removed; fails closed; no summaries; a summary for every suppression; wrong thresholds; **a non-atomic counter (killed by the real-Redis concurrency test)**; successes and denials capped; signed-in requesters capped; the suppression not marked; the door fallback ignoring it. |
| 1 Regression (owning app) | `audit`: **233 OK** (`regression_audit.txt`, on b924903; the later change is test-only and ran in the changed modules) |
| 3 Concurrency | 20 threads released together on the shared Redis cache: summaries at exactly 1 and 10 (`test_each_threshold_summary_is_written_exactly_once`) |
| mypy | whole-repo `pre-commit run mypy --all-files`: **Passed** |
| Migrations | none |

**Found while running the gates:**
- My test helper sent a non-success event with no `error_class`, which the emitter rejects, so the first run recorded nothing. Fixed in the helper.
- Mutants E3 and M1 survived at first. The door test's second attempt writes a summary, and that surviving summary alone stops S2's fallback. The test now makes a third attempt, suppressed with no summary, where only the request's `suppressed` mark stops the fallback.

## Rework: on S2 R1 and S6a, and the SM's DENIED ruling
- **Rebased:** onto S2 R1 (the unnamed-route guard; anonymous crashes recorded as `SERVER_ERROR`) and phase2/epic-a `75bf91a` (S6a). The middleware conflict is resolved to S2's `emit_anonymous_refusal` plus this slice's `not state.suppressed` guard.
- **Catalogue:** `FAILED_AUTH_CAPPED` joins `audit.enums.ReasonCode` and `AUDIT_ONLY_CODES`; `failed_auth_cap` takes it from the enum. Pinned by `test_the_summary_code_is_in_the_catalogue`. Otherwise S6a's emitter would refuse every summary, and capped failures would leave zero events.
- **DENIED lock events (SM ruling on v2's early flag):** no longer unlimited. They get the same always-write floor per target (the first 5 per window), then the per-target cap with summaries. They are **not** under the global cap, so a spent global cap never hides a lock. Their summaries carry outcome DENIED. Successes stay uncapped.
- **Anonymous crashes (S2's `SERVER_ERROR`, SM ruling):** capped in the global, no-target bucket, on a door or any route. A crash's summary keeps its own action and `error_class` SYSTEM (STATE_CHANGE's allow-list gains the summary keys).
- The scope now lives in `emitter._failed_auth_cap_scope(fields)`, which returns (the target to count, whether the global cap applies) or None.

**Rework gates** (rule 15; on 7d1aaae + the S2 merge):

| Gate | Result |
|---|---|
| Reproduce-first | S2 tip 0737583's source for the 5 changed files (`prefix_0737583_failing.txt`): **10 of 16 fail**. Nothing is capped or summarised; lock denials and crashes are uncapped. |
| Changed modules | `audit.tests_failed_auth_cap` + `audit.tests_route_coverage` + `users.tests_auth_audit_doors` + `audit.tests_state_change` + `AutoGrader.tests_reason_codes`: **123 OK** (`changed_modules.txt`) |
| 2 Mutation | **15 mutants, 15 killed** (`mutation_log.txt`). New: successes capped; lock denials under the global cap; lock denials uncapped; crashes uncapped. |
| 1 Regression (owning app) | `audit`: **246 OK** (`regression_audit.txt`) |

## Follow-up: v2's notes on `65aab2e` (VERIFIED-WITH-NOTES, `VERIFICATION_v2_s2r1_s1b_s5r.md`)
Test and doc only; no behaviour changes.
- **N1 (school scoping pinned).** v2's mutant B1 (a summary's `school_id` set to None) survived my labels. v2's probe P1 is adopted as `LockThenSprayThroughTheLoginTests`: a locked account in a school, sprayed 25 times from 25 IPs through the real `/auth/login` with small caps (floor 2, target 4, global 6). It gives exactly 4 individual DENIED rows and DENIED summaries at `suppressed_so_far` 1 and 10, each naming the account **and its school**. B1 joins `mutate.py`, so the harness now has 16 mutants.
- **N2 (stale docstring).** `audit/failed_auth_cap.py` no longer says DENIED is never capped. It now describes DENIED sharing the per-target counter (floor, then target cap, no global cap) and anonymous crashes under the global cap. It also says a summary keeps the outcome, error class and school of what it stands for.
- **N3 (bounded lock-then-spray).** FAILURE and DENIED events for one account share one per-target counter. Once the floor is spent, a lock-then-spray (a locked account hammered from many addresses) writes at most the target limit of individual rows per window, plus summaries at 1, 10, 100 … suppressed. This is intended (SM ruling): the lock is always visible through its first FLOOR rows and the summaries, and its volume is bounded.

**Follow-up gates** (rule 15: only the touched module and the harness):

| Gate | Result |
|---|---|
| Reproduce-first | B1 applied: the new test fails on the school assertion (`mutation_log.txt`, B1). |
| Changed module | `audit.tests_failed_auth_cap`: _pending_ |
| 2 Mutation | 16 mutants: _pending_ |
