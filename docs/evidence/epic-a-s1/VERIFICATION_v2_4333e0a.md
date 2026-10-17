# Verification: Epic A S1 R2 @ 4333e0a

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed).
**Branch:** task/epic-a-s1 @ 4333e0a (R2 on f300c6b), base phase2/epic-a cc34081 (Phase 2 only). **Date:** 2026-09-30.
**Scratch checkout:** detached worktree at 4333e0a with its own test DB. The probes were dropped in untracked and never committed. Every run was wrapped:
- `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`;
- `nice -n 10`;
- `timeout -k 60 1800`;
- `RACE_COST_ENROLLMENTS=600 RACE_COST_ROWS=200`;
- `EXEMPT_EMAIL_DOMAINS=`.

The slot was granted by 0b.

**Verdict: VERIFIED-WITH-NOTES.** V1 (REJECTED at f300c6b, `VERIFICATION_v2_f300c6b.md`) is fixed. The R1 fix still holds. Every required item from the f300c6b record is done.

## V1 fix: required items
| Required at f300c6b | At 4333e0a |
|---|---|
| Write the generic event unless a surviving stored event names the requester | `a_surviving_event_names(state, user)`: `pk__in=ids, actor_id=user.pk`. It still fails safe (error → False → generic written). An unauthenticated requester → False with no query |
| A pinned add_teachers test | `audit/tests_license_admin_attribution.py`: real JWT, real middleware, a credit grant. The teacher's CREDIT_TRANSACTION stays, and exactly one event names the admin |
| An actor-condition mutant | ed's `V1_any_actor_counts` in `mutate.py`; v2's M8, M9 and M11 below |
| Wording: "exactly one event naming the requester" | middleware, request_audit and EVIDENCE updated |
| N1: adopt 1a's two-event cases | in `audit/tests_state_change.py`. M4, M5 and M7 are now killed by ed's labels |
| Record committed verbatim | `docs/evidence/epic-a-s1/VERIFICATION_v2_f300c6b.md` is byte-identical to v2's file (`cmp`) |

## Evidence
**Probes + ed's S1 labels** (147 tests: 1a's probe, v2's V1 probe, `audit.tests_state_change`, `tests_emitter`, `tests_admin_action`, `tests_license_admin_attribution`, `users.tests_auth_audit_doors`, `tests_auth_audit_events`): **OK**.

| Probe | Events |
|---|---|
| School admin add_teachers (was the V1 FAIL) | `[CREDIT_TRANSACTION TEACHER, STATE_CHANGE SCHOOL_ADMIN route=license-subscription-add-teachers]`: the admin is named, and the teacher's grant is kept |
| remove_teachers / plain admin write (controls) | one STATE_CHANGE naming the admin |
| 1a's rollback cases (4) | control 1; rolled back then refused → `[STATE_CHANGE FAILURE]`; first or second kept → exactly `[DATA_EXPORT "Kept"]` |

**Mutants** (`vf_s1_r2_mutants.py`, 1a's M1–M7 re-anchored for R2 plus M8–M11; every anchor asserted unique; every restore hash-checked against 4333e0a):

| Mutant | ed's labels | Probes |
|---|---|---|
| M1 no existence/actor check (`return True`) | KILLED | KILLED |
| M2 emitter records no id | KILLED | KILLED |
| M3 check fails unsafe | KILLED | survived (not its target) |
| M4 last id only | KILLED (was SURVIVED at f300c6b) | KILLED |
| M5 first id only | KILLED (was SURVIVED) | KILLED |
| M6 generic event always written | KILLED | KILLED |
| M7 all ids must survive | KILLED (was SURVIVED) | KILLED |
| M8 V1: actor condition dropped | KILLED (pinned add_teachers + "someone else" tests) | KILLED |
| M9 V1: actor condition inverted | KILLED | KILLED |
| M10 anonymous guard dropped | KILLED (`test_an_anonymous_requester_needs_no_query`) | survived (outcome-equivalent by design) |
| M11 middleware passes no requester | KILLED | KILLED |

**11/11 killed by ed's own labels.**

**Regression:**

| Labels | Result |
|---|---|
| `audit users` | 829 OK (skipped=4), including v2's 7 probe tests |
| `classrooms students assignments` | 1224 OK (skipped=14). `test_pdf_renderer.ConcurrentRenderingTest`, which failed for ed under load, passed here |
| `billing` | **1652 OK** |
| ed-owned total | 2046 + 1652, all OK |

**Static:**
- Whole-repo `pre-commit run mypy --all-files` at 4333e0a: Passed.
- `makemigrations --check --dry-run`: no changes.
- 767f848 → 4333e0a touches evidence files only.

**Rule 14 (mocks):** the only new mock is `patch.object(AuditEvent.objects, "filter")`, in unit tests that assert it is never called. No mock value can reach a Response, serializer, log line or JSON.

## Notes
- **N1. Independence.** Both fixes were shaped by verifiers: R1 is 1a's patch, and V1's invariant is v2's, endorsed by the SM. This verdict rests on black-box evidence: the probes, ed's reproduce-first `prefix_f300c6b_r2_failing.txt`, and mutants with anchors v2 re-derived.
- **N2. Gates not run in this slice**, as plan 08 §9 lays out:
  - Gate 6 (p95 write overhead, now up to one indexed `EXISTS` per write that stored events) is deferred to S8.
  - Gate 8 (the staging end-to-end trace) comes after 0b merges into phase2/epic-a.
  - Gate 10 (the strict full suite on the batch tip) is 0b's.
- **N3. Carried to later slices (SM decisions of 2026-09-30):**
  - S2's sweep asserts "exactly one event naming the requester, plus side-effect events naming others", under the h39 guard with Stripe, email and Celery mocked.
  - A malformed body on anonymous auth/registration doors records one FAILURE (ANONYMOUS, target null, e.g. INVALID_REQUEST, no body), in S2 or S1b.
  - S1b's caps are keyed by account id.

Logs: `~/Documents/Projects/GAP-v2-handover/runs/r2_*.log`.
