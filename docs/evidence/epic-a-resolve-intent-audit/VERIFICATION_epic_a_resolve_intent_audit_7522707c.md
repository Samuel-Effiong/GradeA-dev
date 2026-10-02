# Verification: the audit events of a licence Stripe-change intent (resolve-intent emit, ed)

- **Branch:** task/epic-a-resolve-intent-audit at **7522707c**, on phase2/epic-a 6bbb4ace. The code tip is aa72fffb (0b's base update); aa72fffb..7522707c is evidence only.
- **Commits:** bbb6a1cc (production), 21213223 (tests), a602adc5 (harness and the caller grep).
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static checks
| Check | Result |
|---|---|
| Base update aa72fffb: `git show --remerge-diff` | Empty |
| Added-line survival (`vf_merge_survival.py`) | base 8a206ba, sides a602adc / 6bbb4ac, **0 lines lost** |
| Production diff against the epic | billing/license_stripe_mutation.py, the resolve command, audit/metadata.py (two allow-list entries added, nothing removed) |
| Every write of ESCALATED | Two places only: `_set_status` (which `escalate` calls) and the stale-intent check. Both now write the event |
| Same transaction (SM ruling) | `_set_status`: the event is inside its own `atomic(durable=True)` block. The Beat check: the conditional claim and the event share one `atomic()`. The command: the conditional close and the event share one `atomic()` |
| The command emits only after the close matched | `if closed: audit_intent_status(...)`, inside the transaction; a lost race raises CommandError and writes nothing |
| The operator | `--by` resolves an active SUPER_ADMIN with `is_superuser`, the same rule `command_actor` enforces, so the block can't refuse a user the command accepted |
| A failed emit can't stop the change | The helper works in its own savepoint and catches everything; the emitter never raises |
| Ids only; the note is never stored (SM point) | `before` / `after` hold `intent_status` only; metadata holds `license_id` (plus `command`). The note and the failure reason stay on the intent |
| A task or thread inside the block (SM point) | The command does its one write in-process. EVIDENCE.md states the limit |
| Rule 14 | Every Stripe response is the stateful fake's. Patches use `side_effect` functions that return real values or raise |

## ed's gates (read, not repeated: rule 15)
| Gate | Tip | Result |
|---|---|---|
| Reproduce-first on 6bbb4ace's production files | aa72fffb | 17 tests: 1 failure, 9 errors, test by test. The 7 that pass are "no event is written" tests |
| The new module, 16 caller modules, five audit modules, every repo-wide guard | aa72fffb | **657 OK** |
| Mutants I1–I16 | aa72fffb | **16 of 16 killed** |
| Regression: billing + audit, serial | 84aeb2f1 | **2438 run, 1 failure** (H-99, below) |

- **Full logs, hashes recomputed by v2:** the gate `3f4ee62ac325fe77` (no FAIL or ERROR header); the regression `aee2a7715633aca6` (one FAIL header).
- **Rule 17:** mutate.py sets `PYTHONDONTWRITEBYTECODE=1` and deletes `__pycache__` before each mutant and after each restore; EVIDENCE.md states both.

## There is no green regression for this slice
- **The one failure:** `billing.tests.test_h38_part2_removed_teacher_routes.RemovedTeacherRosterNameMatchTests.test_import_into_an_individual_course_creates_a_new_student`. 2437 passed, among them every new test and every H-28 module.
- **Its cause (H-99, diagnosed by ed, confirmed by 0b):** a no-email student gets a placeholder address `first.last<N>@student.local` with `N = secrets.randbelow(10000)` (classrooms/serializers.py). The test's second "Ada Lovelace" drew the first one's suffix. The serializer then found the existing account by that address and tried to attach it, and the cross-school gate refused the row. It failed closed. The H-38 name match had returned nothing.
- **v2 agrees it is unrelated to this branch,** on three facts:
  1. **Order.** In the red log the failing test ran at 16:16:37, line 13,677. Every test module this branch adds or changes first appears from line 33,946 on. They are TransactionTestCases, which Django runs after every TestCase. None of the branch's tests ran before the failing one.
  2. **Production.** The branch's three production files are not on the roster-import path.
  3. **No leftover state.** The branch's tests use context-managed patches only.
- **SM ruling:** not repeated; the test is not changed in this slice; refresh 8's Gate 10 may run with H-99 open. H-99 is a product defect on beta and the epic (owner ed, bundle 6).
- **Apps not in the regression** (SM ruling): users, classrooms, assignments, students, ai_processor, dashboard, AutoGrader. Refresh 8's Gate 10 covers them.

## v2 run (0b's grant, rules 16, 13 and 12, scratch worktree at 7522707c, DB test_vf2_s1)
One serial run: **21 tests OK** (ed's `test_h28_intent_audit` 17 + v2's probe 4). No mutants, so rule 17 does not apply. Log: runs/resolve_intent_7522707c_probe.log.

**Probe (tests_vf2_resolve_intent_probe.py):**
- **R1, the trail still says the request failed.** Through the real seats route, an escalated change answers 409 and leaves two events naming the super admin: `ADMIN_ACTION` with outcome FAILURE, and the intent's `SUBSCRIPTION_CHANGE`. They share one trace id. No generic STATE_CHANGE is written.
- **R2, the command's event.** No column of the stored event holds the note, the failure reason or the outcome word. The actor is the `--by` super admin, `metadata["command"]` is the command's name, the school is the licence's, and there is no source address. A second `--apply` on the closed intent is refused and writes no second event.
- **R3, an intent escalated twice: TWO events.** The Beat check escalates a stale PENDING intent (one event). A flow still holding its own in-memory copy then calls `escalate`. `_set_status` reads `was` from that stale copy and writes a second event, also PENDING → ESCALATED. See note 1.

## Notes
1. **"Exactly one event per transition to ESCALATED" does not hold if a flow escalates an intent the Beat check already escalated (R3).**
   - **Reach:** the Beat check claims an intent only after `STALE_AFTER` (10 minutes). A request's flow is bounded by its 75 s Stripe budget and gunicorn's 100 s timeout, so a live request cannot still be running then. It needs a caller with no such limit: live QA, or a later non-request caller.
   - **It is older than this slice in part:** `_set_status` saves unconditionally, so the second escalation already re-sent the reconciliation alert. This slice adds the second audit event.
   - **For the SM:** accept it as a documented limit, or make the audited move conditional on the row not being ESCALATED already (a production change with its test and mutant). I recommend a documented limit plus a LOW backlog row: the trail errs towards one event too many, never one too few.
2. **The escalation event's outcome is SUCCESS** (the emitter's default): it records a status change that happened. The request's failure is carried by the super admin's `ADMIN_ACTION` (outcome FAILURE, same trace id), as R1 shows. Anyone filtering the trail by outcome alone will not find escalations; filter on `after.intent_status`.
3. **All four flows are super-admin routes,** so the `ADMIN_ACTION` is always there beside the intent event. Three of the four are tested at the service inside a request state; only the seats route is driven end to end (ed discloses this).
4. **The Beat escalation is SYSTEM with no job key** (SM ruling; a general `beat_task` key is a possible later row).
5. **No green regression** for this slice, and the apps listed above were not run: refresh 8's Gate 10 is the gate for both.
