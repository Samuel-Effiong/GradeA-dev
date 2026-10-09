# Verification: Epic A S2 @ 883ee93

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-s2 @ 883ee93, base phase2/epic-a d7f2737 merged in.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at 883ee93. Rule 15: v2's probes, mutants and ed's changed modules. The audit regression is ed's committed log (221 OK), not repeated.

**Verdict: REJECTED** on one defect in the guard itself (G1). The fix is a few lines. Everything else holds.

## G1 (defect): the route guard cannot see an unnamed route
`write_routes()` skips every URL pattern with no name (`if not name …: continue`). S2's purpose (plan 08 §3, gap G4) is that "a new route must not quietly escape the audit". An unnamed route escapes both:
- **the guard:** it is never classified (a) nor swept (b);
- **the runtime:** for an anonymous requester, S1's generic event is not written (anonymous) and the door fallback does not apply (not a registered door).

The probe (`audit/tests_vf2_s2_probe.py`, URLconf = the real one plus two `AllowAny` POST routes):

| Route | In the guard? | Anonymous POST | Events |
|---|---|---|---|
| `vf2-probe/named-write/` (name given) | yes, and (a) would demand a decision | 201 | — |
| `vf2-probe/unnamed-write/` (no name) | **no** | 201 | **[]** |

Today all 29 unnamed patterns are Django admin (legacy `<path:object_id>/` redirects and `catch_all_view`), so no current route is affected. The guard exists to catch the next one.

**Required:**
1. The guard fails on any unnamed write route outside `admin/`, naming its path.
2. A test proving it, e.g. a URLconf override like the probe's.
3. A mutant restoring the silent skip, killed by (2).

## Notes
- **N1 (SM decision).** An anonymous door that fails with a **5xx** leaves no event: `POST /auth/register` with the serializer raising gives 500 and `events=[]`. That is by design (`test_a_server_error_is_not_a_malformed_request`), since a crash is not a malformed request, but it is still a failed sign-in or registration attempt with no trace. The option is a FAILURE with error_class SYSTEM and reason e.g. `SERVER_ERROR` for a 5xx on a registered door, under S1b's cap.
- **N2 (evidence).** EVIDENCE says the sweep finds **196** write routes. `write_routes()` at 883ee93 returns **153** (name, method) pairs, and walking the resolver without the dict gives the same 153 path-level pairs, so no two paths collapse under one name. Please correct the figure.
- **N3.** The sweep proves "exactly one event naming the requester" on the superadmin refusal/validation paths (empty body, made-up ids), as plan §3.3 intends. Success paths are covered by S1's middleware invariant and its tests, not by the sweep.

## Evidence
| Check | Result |
|---|---|
| v2 probes + ed's changed modules (`audit.tests_route_coverage`, `users.tests_auth_audit_doors`, `audit.tests_state_change`) + the S1 probes on this tree (81) | **OK**. The V1 (S1) probe still names the school admin on add_teachers |
| v2 mutants (`vf_s2_mutants.py`, 3) | **3/3 KILLED**: the fallback on 5xx; the fallback on 429; every anonymous route treated as a door |
| Unnamed patterns | 29, all `admin/` (read-only resolver walk) |
| ed's gates | 8/8 mutants; audit 221 OK; mypy passed (committed) |

**Checked and sound:**
- The door fallback is written only when no stored event survives, for a 4xx other than 429, for an anonymous requester, and only on a registered door. So a door that records its own event cannot also get the fallback, and ed's D5 mutant kills that case.
- `ACCOUNT_REGISTER`: actor ANONYMOUS, the new account as target, no body stored.
- The three new exclusions carry reasons.
- `UserActivityMiddleware` cannot short-circuit a write before `AuditMiddleware`.
- The sweep isolates each route in a rolled-back savepoint and stubs Celery with a real id. No bare MagicMock reaches a response (rule 14).

Logs: `runs/s2_run1_probes_changed_modules.log`, `runs/s2_run2_mutants.log`.
