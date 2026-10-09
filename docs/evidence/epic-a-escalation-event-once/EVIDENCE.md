# H-101: an intent's move to ESCALATED is audited once — evidence

Author: ed (Security). Branch `task/epic-a-escalation-event-once`, on `phase2/epic-a` ce0fd315
(the epic is 4e4326c3 = ce0fd315 plus the Gate 10 record, docs only). MEDIUM. Verifier: v2.
Follows the resolve-intent audit emit (merged at ce0fd315); found by v2's probe R3 on that
slice. Reaches staging with refresh 9.

## The defect

The resolve-intent slice writes one `SUBSCRIPTION_CHANGE` event when a licence Stripe-change
intent moves to ESCALATED. `_set_status` took the event's `before` from the intent object in
memory and saved unconditionally. So when the Beat stale-intent check had already escalated an
intent (one event) and a flow still holding the old in-memory copy then called `escalate()`,
the row was saved as ESCALATED again and a **second** event was written, with a `before` the
row no longer had. The SM's pin is "exactly one event per transition to ESCALATED".

Reach: a caller must hold an intent for longer than the 10-minute stale window. No request can
(a 75 s Stripe budget, gunicorn's 100 s timeout); live QA or a later caller with no request
could.

## The fix

| Commit | What |
|---|---|
| 5a23de21 | Two tests in `billing/tests/test_h28_intent_audit.py` (committed first; they fail on ce0fd315's code). |
| d88b399f | `billing/license_stripe_mutation.py`, `_set_status` only: when the new status is ESCALATED it reads the stored status under a row lock (`select_for_update`) inside its own transaction, and writes the event only if the row was not ESCALATED already, with `before` taken from the stored row. One extra query, on the escalation path only. |
| c3713333 | `mutate.py`: the slice's 16 mutants (two re-anchored on the changed line) plus I17 and I18. |

Nothing under `audit/` changes (the SM's condition for the billing + audit scope).

### What is deliberately unchanged

- **The save.** A second `escalate()` still writes the row (its `escalated_at` and
  `failure_reason` become the flow's). Only the event is conditional.
- **The alert (SM's question).** In the double-escalation case the alert IS sent again: the
  ERROR log line and the email to every active super admin. That was so before the
  resolve-intent slice and is not changed here; `test_a_stale_copy_does_not_record_the_escalation_twice`
  pins it (two emails). Is it intended? Nothing in H-28's design says either way. The
  stale-intent check is written to alert once per abandoned intent ("ESCALATED is not picked
  up again"); the flow's own `escalate()` has no such check. The second alert does carry news
  (the flow's specific reason, e.g. "the revert failed", where the first said only "stale"),
  so I would keep it; whether to suppress it is a product decision, not made in this slice.
- **The lock** is not mutated: removing `select_for_update` changes nothing a single-process
  test can see. It is there so the stale-intent check cannot claim the row between this read
  and the save.

## Runs

All runs: rules 12, 13 and 16 (the `idle:sleep:handle-lid-switch` prefix),
`--settings=settings_worktree`, slots granted by 0b. Mutation run (rule 17):
`PYTHONDONTWRITEBYTECODE=1`, the `__pycache__` of each mutated module's directory deleted before
each mutant and after each restore, own database (`test_epic_a_escalation_event_once_mut`,
dropped afterwards), every anchor asserted unique and every mutant parsed.

### Gate (a), 2026-10-02 16:50–16:59 WAT, at c3713333 (6G): green on the first pass

| Step | Result | Log |
|---|---|---|
| 0. Reproduce-first: the intent-audit module on ce0fd315's `license_stripe_mutation.py` | 19 tests, FAILED (failures=1, errors=1): exactly the two new tests (the strong form) | `prefix_ce0fd315_failing.txt` |
| 1. The intent-audit module + the caller modules of the resolve-intent gate (the H-28 modules, `test_beat_lock_catch_up`, the licence modules, audit's emitter, metadata, history and command-actor tests) + every repo-wide guard | 659 tests OK | `modules_and_guards.txt` (last 200 lines; full log sha256 prefix `9989841f8f6ba6eb`) |
| 2. Mutants I1–I18 | 18 of 18 killed. I17 (the second event comes back) by `test_a_stale_copy_does_not_record_the_escalation_twice`; I18 (`before` is not the stored status) by `test_before_is_the_stored_status_not_the_copys` | `mutation_log.txt`, `mutation_results.json` |

### Gate (b)

2026-10-02 17:00–17:05 WAT, at a2ef7a33 (= c3713333 plus gate (a)'s logs; no code change),
12G, flock, timeout 3600, serial, `--verbosity 2`, `RACE_COST_*` / `AUDIT_BENCH*` /
`ENABLE_GRADING_BENCHMARK` unset:

| Run | Result | Log |
|---|---|---|
| `manage.py test billing audit` (the SM's scope: the production change is one function in `billing/license_stripe_mutation.py`; nothing under `audit/` changes) | **Ran 2440 tests in 291.7s, OK (skipped=2)**. Per app: billing 2073, audit 367. | `regression_billing_audit.txt` (last 200 lines; full log sha256 prefix `5cdb0dd665023f79`, kept in `~/Documents/Projects/GAP-evidence-logs/`) |

This is also the first green billing + audit run on the intent-audit code: the resolve-intent
slice's own regression had one unrelated failure (H-99's 1-in-10,000 collision), which did not
occur here. That test can still fail on the epic at those odds until H-99 arrives by
merge-down.

## Not verified here

- No real concurrency: the Beat check and a flow escalating the same row at the same moment
  are not raced in a test; the row lock is the design's answer and is not exercised.
- v2's probe R3 on the previous slice is this slice's acceptance test; v2 runs it.
- The author does not verify their own work: a verifier checks this.
