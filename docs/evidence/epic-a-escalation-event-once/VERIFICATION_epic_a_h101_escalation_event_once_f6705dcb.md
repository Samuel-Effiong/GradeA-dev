# Verification: H-101, an intent's move to ESCALATED is audited once (ed)

- **Branch:** task/epic-a-escalation-event-once at **f6705dcb**, off phase2/epic-a ce0fd315. The code tip is c3713333; c3713333..f6705dcb is evidence only. The epic has since moved in docs only (4e4326c3), and the branch merges cleanly into it.
- **Commits:** 5a23de21 (two tests, red first), d88b399f (production: `_set_status` in billing/license_stripe_mutation.py).
- **Origin:** v2's probe R3 on the resolve-intent slice (verified at 7522707c): an intent the Beat check had escalated got a second event when a flow holding a stale copy escalated it too.
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static checks
| Check | Result |
|---|---|
| Production diff against ce0fd315 | One function, `_set_status` (+18/-4). Nothing under audit/ |
| The fix | On a move to ESCALATED, inside `_set_status`'s own `atomic(durable=True)`: the stored status is read with `select_for_update()`, the row is saved, and the event is written only if the stored status was not ESCALATED already. `before` is the stored status, not the in-memory copy's |
| The lock | It holds the row from the read to the commit, so the Beat check cannot claim it in between. The Beat check's own claim is a conditional UPDATE on the status it read, so the other order was already safe |
| Other statuses | Unchanged path: no extra query unless the new status is ESCALATED |
| The save and the alert | Unchanged: `escalate()` still saves its reason and still calls `_reconciliation_needed` |
| A row deleted meanwhile | `stored` is None, the save of the missing row raises, the transaction rolls back and no event is written |
| Rule 14 | The two new tests use no mock beyond the fixture's real stand-ins |

## ed's gates (read, not repeated: rule 15)
| Gate | Tip | Result |
|---|---|---|
| Reproduce-first on ce0fd315's production file | c3713333 | 19 tests, 1 failure + 1 error: exactly the two new tests (the strong form) |
| The intent-audit module, the caller modules, every repo-wide guard | c3713333 | **659 OK** |
| Mutants I1–I18 | c3713333 | **18 of 18 killed** (I17: the second event comes back; I18: `before` not from the stored row) |
| ONE regression: billing + audit | a2ef7a33 | **2440 OK** (skipped=2): a green regression for this slice |

- **Full logs, hashes recomputed by v2:** the gate `9989841f8f6ba6eb`; the regression `5cdb0dd665023f79`. No FAIL or ERROR header in either.
- **Rule 17:** mutate.py sets `PYTHONDONTWRITEBYTECODE=1` and deletes `__pycache__` before each mutant and after each restore; EVIDENCE.md states both.

## v2 run (0b's grant, rules 16, 13 and 12, scratch worktree at f6705dcb, DB test_vf2_s1)
One serial run: **25 tests OK** (ed's `test_h28_intent_audit` 19 + v2's probe 6). No mutants (ed's I17 and I18 are the two v2 would write), so rule 17 does not apply. Log: runs/h101_f6705dcb_probe.log.

**Probe (tests_vf2_resolve_intent_probe.py):**
- **R3, the acceptance case: ONE event.** The Beat check escalates a stale PENDING intent; a flow holding its stale copy then escalates it too. The intent has exactly one event: PENDING → ESCALATED, by SYSTEM (the Beat check's). At 7522707c this case wrote two.
- **The reverse order:** the flow escalates first (one event); the row is then made to look stale and the Beat check claims nothing. Still one event.
- **A first escalation by a flow** still writes its event, with `before` STRIPE_APPLIED from the row.
- **R1 and R2** (from the resolve-intent slice) still pass: a 409 leaves the failed ADMIN_ACTION and the intent event on one trace id; the command's event holds no note or reason; a second apply writes nothing.

## Note for the SM: what the second escalation still does
In the double-escalation case the audit trail now shows one move. Two other things still happen, both unchanged by this slice and both seen in v2's run:
1. **The reconciliation alert is sent again** (two alerts queued). ed's test pins it and EVIDENCE.md calls it a product decision.
2. **The intent's `failure_reason` is overwritten** by the flow's reason (the Beat check's "Stale for over …" text is replaced), and `escalated_at` is reset.

v2's view: keeping the second alert is reasonable, because the flow's reason is new information for the person reconciling. But the overwrite loses the first reason from the row, and the audit event deliberately carries no reason text. If the SM wants both kept, the row needs to append, not replace. Reach is as before: no request can hold an intent for the Beat check's 10 minutes; live QA or a later non-request caller could.

## Other notes (none blocks)
- **The Beat escalation stays SYSTEM with no job key** (earlier SM ruling).
- **The resolve-intent slice's EVIDENCE.md** still describes the second event as a limit "fixed by H-101"; after this merges that limit is closed.
- **Apps outside billing and audit** were not run (SM's scope: one function in billing); the next Gate 10 covers them.
