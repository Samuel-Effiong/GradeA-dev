# Verification: Epic A S1 R1 @ f300c6b

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed).
**Branch:** task/epic-a-s1 @ f300c6b, on 419e9f6, base phase2/epic-a cc34081 (Phase 2 only). **Date:** 2026-09-30.
**Scratch checkout:** detached worktree at f300c6b with its own test DB. The probes were dropped in untracked and never committed.

**Verdict: REJECTED.** The R1 fix is correct, and the regression, migrations, mypy and mutants all hold. One new defect, V1, breaks the S1 standard ("every action any user can take is traceable") on the exact route plan 08 G1 names as S1's motivating gap. The fix is small.

## Independence
f300c6b's R1 fix has the same structure as 1a's tested patch (`vf_s1_rollback_fix.patch`), with renamed identifiers:
- the same list of stored ids;
- the same `pk__in … exists()` check;
- the same try/except → False.

ed's EVIDENCE says so. Per the SM's ruling, this verdict relies on black-box evidence (the probes, ed's tests, the mutants) and not on code review of an idea I would be checking against itself.

## V1 (defect): a named event that names someone else suppresses the request actor's only trace
`AuditMiddleware` writes STATE_CHANGE only when **no** stored event survives. Any surviving event counts, whoever it names. `CreditLedger.record(user=…)` emits CREDIT_TRANSACTION with the **wallet owner** as actor (`billing/models.py:1488`, gap G5). So a school admin's successful `POST license-subscriptions/<id>/add_teachers` leaves this and nothing else:

| Request (real JWT, real middleware) | Status | Events stored |
|---|---|---|
| School admin, add_teachers, 1 teacher | 200, successful=1 | `[CREDIT_TRANSACTION, actor_role=TEACHER, actor=the teacher]`. **No event names the admin** |
| School admin, remove_teachers (control) | 200 | `[STATE_CHANGE, SCHOOL_ADMIN, route=license-subscription-remove-teachers]` |
| School admin, POST sessions (control) | 201 | `[STATE_CHANGE, SCHOOL_ADMIN, route=session-list]` |

Probe: `audit/tests_vf2_s1_attribution.py` (attached as `tests_vf2_s1_attribution.py`); the output is in `runs/run1_probes.log`.

Consequences:
- G1 lists licence add_teachers/remove_teachers as unaudited. After S1, add_teachers is still unattributable to the school admin who did it: the audit trail says the teacher did something.
- The invariant is fragile. When S3 gives the remove_teachers clawback a ledger row (G7), remove_teachers loses its admin event the same way, unless S3 also fixes G5's actor. Any future named event that names someone other than the requester re-opens the hole silently.
- Zero-credit plans take the generic path. So whether the admin is recorded currently depends on the plan's credit allocation.

**Required (SM-endorsed invariant):**
1. For a state-changing request by an authenticated user, the generic STATE_CHANGE is written unless at least one surviving stored event names **that user** as its actor, e.g. `AuditEvent.objects.filter(pk__in=ids, actor_id=request.user.pk).exists()`. Keep the fail-safe (error → write the generic event).
2. A test pinning it: school admin add_teachers (with a credit grant) → at least one event whose actor is the school admin, and the teacher's CREDIT_TRANSACTION is still there.
3. A mutant dropping the actor condition, killed by (2).
4. Update the S1 "exactly one" wording, tests and EVIDENCE. The guarantee becomes "exactly one event that names the requester" (named or generic). Side-effect events naming other actors (a teacher's credit grant) are additional, not duplicates.

## Notes (not blocking alone; fold them into the fix round)
- **N1. Mutant coverage.** 1a's M4 (last id only), M5 (first id only) and M7 (all ids must survive) **survive ed's test labels**. They are killed only by 1a's two-event probe cases (`first_survives_second_rolled_back`, `first_rolled_back_second_survives`). Adopt those two cases into `audit/tests_state_change.py`.
- **N2.** ed's EVIDENCE route trace is correct about the add_teachers rollback exposure (CREDIT_TRANSACTION inside the view's `atomic()`), but it missed V1 on the success path of the same route.

## Evidence (all at f300c6b, each run wrapped: `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, `EXEMPT_EMAIL_DOMAINS=`)
| Check | Result |
|---|---|
| 1a's probe (4 rollback cases) | **OK**: control 1 event; rolled back then refused → exactly `[STATE_CHANGE FAILURE]`; first kept or second kept → exactly `[DATA_EXPORT "Kept"]` |
| v2 attribution probe (3) | add_teachers **FAIL** (V1); remove_teachers and plain admin-write controls OK |
| Regression `audit users` | 820 run, 1 failure = the V1 probe only; ed-owned 813 OK (skipped=4) |
| Regression `classrooms students assignments` | 1224 **OK** (skipped=14) |
| Regression total (ed-owned) | **2037 OK, skipped=18**, matching ed's EVIDENCE |
| `makemigrations --check --dry-run` | No changes detected (audit 0002 is choices only) |
| Whole-repo `pre-commit run mypy --all-files` | Passed |
| EXCLUDED_ROUTES resolve | `refresh` → /api/v1/auth/refresh; `auth-otp` → /api/v1/auth/otp; `auth-request-change-password` → /api/v1/auth/request-change-password |

**1a's mutants (`vf_s1_r1_mutants.py`)** (every restore was hash-checked against f300c6b):

| Mutant | ed's labels | 1a's probe |
|---|---|---|
| M1 no existence check | KILLED | KILLED |
| M2 emitter records no id | KILLED | KILLED |
| M3 check fails unsafe | KILLED (SurvivalCheckFailsSafeTests) | survived (not its target) |
| M4 last id only | **SURVIVED** | KILLED |
| M5 first id only | **SURVIVED** | KILLED |
| M6 generic event always written | KILLED | KILLED |
| M7 all ids must survive | **SURVIVED** | KILLED |

One process note: the first mutant run was unwrapped, so it was stopped during M2 for rule 13. The mutated `audit/emitter.py` was restored and hash-checked, then M2–M7 were re-run wrapped. M1's result is from the unwrapped run.

## Checked and sound
- **The R1 fix.** It is black-box proven by 1a's probe, with real commits and savepoints, and killed by M1 on both label sets.
- **Sign-in doors (code read).** Failures inside atomic blocks are recorded after the block exits, for the registrations and Google. The expired student-invitation branch emits inside the block but returns a 200, so the block commits and the event survives. The failure actor is ANONYMOUS or the signed-in user, never the targeted account; `school_id` is the target's.
- **Exclusions.** Keyed on `resolver_match.view_name`. The three names resolve to the intended routes. `name="refresh"` in `classrooms/views.py:1655` is an OpenAPI parameter, not a URL name, so there is no collision.
- **1a's 419e9f6 "Checked and sound" items** (429 exemption, middleware bypass, ContextVar, what is recorded, Celery events) still hold on f300c6b. The delta touches only context/emitter/middleware/tests.

## Re-verification owed after the fix
The V1 probe and the new pinned test, the actor-condition mutant, M1–M7 again, the regression `audit users classrooms students assignments` (plus `billing` for the add_teachers path), and mypy.
