# The audit events of a licence Stripe-change intent (resolve-intent emit): evidence

Author: ed (Security). Branch `task/epic-a-resolve-intent-audit`, on `phase2/epic-a` 6bbb4ace.
Depends on `command_actor` (merged at 8a206ba5). Sources: H-28 (the intent machinery), H-69's
survey (`resolve_licence_stripe_intent` writes with no audit record).

## The problem

A licence's Stripe-change intent (H-28) records a change made at Stripe that the application
then has to record. Two of its status changes are the ones a human acts on, and neither left an
audit event:

- a move to **ESCALATED**: Stripe and the application may disagree, and a super admin is
  emailed to reconcile them;
- a **manual close** by `resolve_licence_stripe_intent --apply --by <super admin>`, which
  frees the licence again.

## SM rulings (2026-10-02)

- **Option B**: one `SUBSCRIPTION_CHANGE` event for **every** transition to ESCALATED,
  wherever it happens, and one for every manual close. Not every transition: the ordinary
  PENDING → COMPLETE path stays with the licence's own history.
- The pin: "every transition to ESCALATED has exactly one event, and every manual close has
  exactly one". (An earlier wording, "only the Beat job writes ESCALATED as SYSTEM", was
  withdrawn: the flow itself also escalates.)
- Emit inside the same transaction as the status change. A failed emit must not block the
  escalation.
- before/after `intent_status`, metadata `license_id`, ids only, no new key. `source` is not
  used. The command's events are identified by `metadata["command"]` plus the operator
  (command_actor).
- The Beat escalation stays SYSTEM with no job key. A general `beat_task` key set by the
  emitter (as `command` is) would be new scope across every background event: **a possible
  later backlog row, not built here**.

## What was built

| Commit | What |
|---|---|
| bbb6a1cc | `billing/license_stripe_mutation.py`: `audit_intent_status(intent, before, after)`; `_set_status` calls it inside its own durable transaction when the new status is ESCALATED; `escalate_stale_intents` puts its conditional claim and the event in one transaction. `resolve_licence_stripe_intent`: the close and its event in one transaction, under `command_actor(resolver, command=<this command>)`. `audit/metadata.py`: `intent_status` in `ALLOWED_KEYS` and in `SUBSCRIPTION_CHANGE`'s before/after list; `license_id` in `SUBSCRIPTION_CHANGE`'s metadata list. |
| 21213223 | Tests: `billing/tests/test_h28_intent_audit.py` (17 tests) and six tests added to the four phase modules. The shared assertions are in `test_h28_cancel_phases.py`, beside `LicencePhaseTestCase` (the `name-tests-test` hook does not allow a helper file under `tests/`). |
| a602adc5 | `mutate.py` (I1–I16) and `grep_callers.txt`. |
| aa72fffb | 0b's base update onto 6bbb4ace (clean; no shared file). |

The event: action `SUBSCRIPTION_CHANGE`, target `LicenseStripeMutationIntent` (its id), the
licence's school, `before` / `after` = `{"intent_status": ...}`, metadata
`{"license_id": ...}` (plus `command` for the command). The request, when there is one, is
passed for the trace id and source address, as history events do.

### Who is the actor

| Path | Actor | Test |
|---|---|---|
| The Beat stale-intent check (`escalate_stale_intents`) | SYSTEM | `StaleIntentCheckAuditTests` (5) |
| `update_seats` from its view | the signed-in user | `test_h28_seat_phases`: the service under a request, **and through the real route** with `AuditMiddleware` (409, one event, the request's address) |
| `convert_license_to_offline` from its view | the signed-in user | `test_h28_convert_phases` |
| `cancel_license_subscription` from its view | the signed-in user | `test_h28_cancel_phases` (and: a COMPENSATED cancel writes no event) |
| `change_license_plan` from its view (through `select_plan`) | the signed-in user | `test_h28_plan_phases` |
| A call with no request | SYSTEM | `test_with_no_request_the_event_is_system` |
| `resolve_licence_stripe_intent --apply` | the `--by` super admin, `command` = `resolve_licence_stripe_intent` | `ResolveCommandAuditTests`: apply = 1 event (both outcomes), a lost race = 0, a dry run = 0, a refused apply = 0 |

Except for the real-route test, "from its view" means the service method called inside
`request_audit_state(<a request by that user>)`, which is what `AuditMiddleware` gives the
view. Each test drives that flow's own failure to reach ESCALATED.

**There is no webhook path today.** I first told the SM there was one; that was a misreading.
`billing/stripe_service.py:1575` is `StripeSubscriptionMutationService.change_license_price`,
a service method that nothing in production calls (backlog **H-75**). The production callers
of the flow are the four views and the live-QA scenarios; `grep_callers.txt` has the greps.

### What happens when the emit fails (SM's question)

- The audit emitter never raises (FR-A-11): a rejected or unstorable event becomes one error
  line and `emit` returns None. So a failed event leaves the status change committed.
  `test_a_failed_event_does_not_stop_the_escalation`: with the audit table refusing the row,
  the intent is still ESCALATED and the super admins are still emailed.
- `audit_intent_status` also works in a savepoint of its own and catches anything else, so an
  error on the way to the emitter cannot spoil the caller's transaction either
  (`test_a_helper_that_raises_does_not_stop_the_escalation`, mutant I11).
- The other direction: if the status change does not commit, there is no event
  (`test_a_status_that_is_not_saved_leaves_no_event`; for the command,
  `test_the_event_is_written_with_the_close_or_not_at_all`, mutant I8).

### Three limits to know

- **Work handed to a task or thread inside a `command_actor` block stays SYSTEM** (the context
  does not cross into a Celery task or a new thread). `resolve_licence_stripe_intent` does its
  one write in-process, so its event names the operator; a command that queued a task would
  not get that.
- **The free-text note of `resolve_licence_stripe_intent` is never stored in the audit
  trail.** It stays on the intent (`resolution_note`). The event carries the two statuses and
  ids; `assert_ids_only` checks that neither the note nor the failure reason appears, and
  mutant I14 (the reason put into the metadata) is killed.

- **A second event is possible in one case (backlog H-101, LOW; found by v2's probe R3; SM
  ruling: a documented limit, not fixed in this slice).** `_set_status` takes the `before`
  status from the intent object in memory and saves unconditionally. If the Beat check has
  already escalated an intent (one event), and a flow still holding the old in-memory copy
  then calls `escalate()`, the row is saved as ESCALATED again and a second
  PENDING/STRIPE_APPLIED → ESCALATED event is written. So the pin "exactly one event per
  transition to ESCALATED" holds for every caller that exists today except that one. It needs
  a caller that holds an intent for more than the 10-minute stale window. No request can (a
  75 s Stripe budget, gunicorn's 100 s timeout); live QA or a later caller with no request
  could. The trail then errs to one event too many, never one too few. The fix designed for
  H-101: read the stored status under a row lock in the same transaction and write the event
  only if the row was not already ESCALATED, with `before` taken from the stored row.

On the real route (v2's probe R1, not asserted by my test): the 409 also leaves the super
admin's `ADMIN_ACTION` event with outcome FAILURE on the same trace id, so the trail records
both that the request failed and that the intent was escalated.

## The SM's scope condition for the regression

Under `audit/` the branch changes `metadata.py` only, additively: two allow-list entries added,
nothing removed or renamed (the one deleted line is `SUBSCRIPTION_CHANGE`'s entry re-wrapped
with `license_id` added). The emitter, history and context modules are untouched. So the
regression is billing + audit (`git diff --stat` in `grep_callers.txt`; 0b checked it on the
branch too).

## Runs

All runs: rules 12, 13 and 16 (the `idle:sleep:handle-lid-switch` prefix),
`--settings=settings_worktree`, slots granted by 0b. Mutation run (rule 17):
`PYTHONDONTWRITEBYTECODE=1`, the `__pycache__` of each mutated module's directory deleted
before each mutant and after each restore, own database
(`test_epic_a_resolve_intent_audit_mut`, dropped afterwards), every anchor asserted unique and
every mutant parsed.

### Gate (a), 2026-10-02 16:06–16:14 WAT, at aa72fffb (6G): green on the first pass

| Step | Result | Log |
|---|---|---|
| 0. Reproduce-first: the new module on 6bbb4ace's three production files | 17 tests, FAILED (failures=1, errors=9), test by test. The 7 that pass are the "no event is written" tests, which hold trivially before the fix. | `prefix_6bbb4ace_failing.txt` |
| 1. The new module, every caller module from the grep (the ten H-28 modules, `test_beat_lock_catch_up`, `test_h57_licence_patch`, `test_h60_licence_stripe_text`, `test_license_cancellation`, `test_live_qa_scenario_registry`, `test_mailerlite_sync`), audit's `tests_command_actor`, `tests_metadata`, `tests_emitter`, `tests_history`, `tests_license_admin_attribution`, and every repo-wide guard | 657 tests OK | `modules_and_guards.txt` (last 200 lines; full log sha256 prefix `3f4ee62ac325fe77`) |
| 2. Mutants I1–I16, against the new module and the seats module | 16 of 16 killed, no survivors | `mutation_log.txt`, `mutation_results.json` |

| Mutant | Killed by |
|---|---|
| I1 an in flow escalation writes no event | `test_a_helper_that_raises_does_not_stop_the_escalation`; `test_an_escalated_seat_change_is_audited_as_the_signed_in_user` (+3 more) |
| I2 every status change writes an event | `test_an_escalated_seat_change_is_audited_as_the_signed_in_user`; `test_an_escalation_over_the_real_route_is_audited_with_the_request` (+1 more) |
| I3 the beat escalation writes no event | `test_a_stale_stripe_applied_intent_records_what_it_was`; `test_each_escalated_intent_gets_one_system_event` (+1 more) |
| I4 the beat check records an intent it did not claim | `test_an_intent_the_check_does_not_claim_gets_no_event` |
| I5 a manual close writes no event | `test_a_manual_close_writes_one_event_by_the_resolver`; `test_closing_a_pending_intent_records_what_it_was` (+1 more) |
| I6 a lost race still writes an event | `test_a_lost_race_writes_no_event` |
| I7 the close is not under command actor | `test_a_manual_close_writes_one_event_by_the_resolver` |
| I8 the close and its event are not one transaction | `test_the_event_is_written_with_the_close_or_not_at_all` |
| I9 the request user is not the actor | `test_an_escalated_seat_change_is_audited_as_the_signed_in_user`; `test_an_escalation_over_the_real_route_is_audited_with_the_request` (+1 more) |
| I10 the request is not passed | `test_an_escalation_over_the_real_route_is_audited_with_the_request` |
| I11 a raising emitter stops the escalation | `test_a_helper_that_raises_does_not_stop_the_escalation` |
| I12 before and after are swapped | `test_a_manual_close_writes_one_event_by_the_resolver`; `test_a_stale_stripe_applied_intent_records_what_it_was` (+7 more) |
| I13 the licence id is left out | `test_a_manual_close_writes_one_event_by_the_resolver`; `test_a_stale_stripe_applied_intent_records_what_it_was` (+6 more) |
| I14 the reason goes into the event | `test_a_manual_close_writes_one_event_by_the_resolver`; `test_a_stale_stripe_applied_intent_records_what_it_was` (+6 more) |
| I15 intent status is not allowed in before after | `test_a_manual_close_writes_one_event_by_the_resolver`; `test_a_stale_stripe_applied_intent_records_what_it_was` (+7 more) |
| I16 license id is not allowed in the metadata | `test_a_manual_close_writes_one_event_by_the_resolver`; `test_a_stale_stripe_applied_intent_records_what_it_was` (+6 more) |

Not mutated: the `transaction.atomic()` around the Beat check's claim and event. Removing it
changes nothing a test can see, because the helper cannot raise and the claim is one
statement; it is there so the two commit together.

### Regression (b)

2026-10-02 16:15–16:21 WAT, at 84aeb2f1 (= aa72fffb plus gate (a)'s logs; no code change),
12G, flock, timeout 3600, `--verbosity 2`, serial (no `--parallel`), `RACE_COST_*` /
`AUDIT_BENCH*` / `ENABLE_GRADING_BENCHMARK` unset:

| Run | Result | Log |
|---|---|---|
| `manage.py test billing audit` | **Ran 2438 tests in 329.5s: FAILED (failures=1, skipped=2).** Per app: billing 2071, audit 367. 2437 passed, among them every new test and every H-28 module. | `run1_failed_84aeb2f1_regression_billing_audit.txt` (the failing test's own output with the server-side trace, then the last 200 lines; full log sha256 prefix `aee2a7715633aca6`, kept in `~/Documents/Projects/GAP-evidence-logs/`) |

**So there is no green regression for this slice.** The one failure is
`billing.tests.test_h38_part2_removed_teacher_routes.RemovedTeacherRosterNameMatchTests`
`.test_import_into_an_individual_course_creates_a_new_student`. A teacher removed from School
A imports the no-email roster row "Ada,Lovelace" into a new private course and must get a new
student. In this run the name match (`_find_existing_student_by_name`, scoped by
`teacher_course_access_q`) returned School A's Ada, and `check_existing_account_may_join`
then refused her (ROW_FAILED). Nothing was attached; it failed closed.

This branch changes nothing on that path: outside docs/ it touches
`billing/license_stripe_mutation.py`, the resolve command, `audit/metadata.py` and H-28 test
modules. The same test passed today in command_actor's regression (15:26, base 3fff1382) and
in Gate 10 on 3fff1382, and no production file under `classrooms/`, `billing/` or `users/`
changed between 3fff1382 and this branch's base 6bbb4ace.

SM ruling: the slice goes to v2 as it stands, the test is not changed here, and the failure is
investigated separately as backlog **H-99** (HIGH until diagnosed, because it is the H-38
rule). Refresh 8's Gate 10 waits for that answer. The regression was not repeated.

`users`, `classrooms`, `assignments`, `students`, `ai_processor`, `dashboard` and `AutoGrader`
are not in this run (SM ruling); refresh 8's Gate 10 covers them.

## Not verified here

- No run against real Stripe; every Stripe response is the stateful fake's (rule 14).
- Three of the four view paths are tested at the service method inside a request state, not
  through HTTP; only the seats route is driven end to end.
- The live-QA scenarios call the flow with no request; an escalation there is recorded as
  SYSTEM, which is tested by the direct-call test, not by running live QA.
- The author does not verify their own work: a verifier checks this.
