# Epic A completion S2: no state-changing route escapes the audit

Branch `task/epic-a-s2`. Cut from phase2/epic-a `3bbafdd` (after S1), then merged with phase2/epic-a `d7f2737` (the batch-2a merge-down). It also carries v2's N1 on that merge: the reset spend-guess test now checks each request's own event. Phase 2 only. The verifier is v2. No migration: `AuditEvent.action` is a plain `CharField` with no choices, so ACCOUNT_REGISTER needs none.

Plan: `08_epic_a_completion_plan.md` §3, plus the SM's rulings of 2026-09-30.

## The guard (`audit/tests_route_coverage.py`)
- **Enumerate.** Walk `get_resolver()` recursively: DRF router routes by their `actions` map, class views by their handler methods, and function views, which are probed with POST. The Django admin is left out and has its own test (D7). This finds **196 write routes**.
- **Anonymous reachability, tested behaviourally.** Every write route is fired anonymously with an empty body. Anything not refused with 401/403, or sent to a login page, is reachable, and must be an anonymous door (`ANONYMOUS_AUDITED_ROUTES`) or excluded with a reason (`EXCLUDED_ROUTES`). Permission classes aren't read: `login`'s view, for example, doesn't declare `AllowAny`.
- **The sweep.** Every other write route is fired as a superadmin with an empty body and made-up ids (a UUID, else 1). Each must leave **exactly one event naming the requester**, plus any side-effect events naming others (SM wording). A 400, 404 or 405 is fine, since FAILURE is recorded too.
  - Each route runs in its own savepoint and is rolled back, so side effects never reach the next route.
  - Outbound calls: the H-39 network guard is installed by the test runner; Celery `apply_async` is stubbed with a real task id; mail uses the test backend. No bare MagicMock reaches a response (rule 14).
- **Anonymous doors.** Each door, fired with an empty body, leaves exactly one non-success event: ANONYMOUS, its own action and `auth_method`.
- **Stale entries.** Every `ANONYMOUS_AUDITED_ROUTES` and `EXCLUDED_ROUTES` key must name a real route, and every exclusion must give a reason.

## What the guard found, and the rulings
| Route | Finding | Ruling | Change |
|---|---|---|---|
| `auth-register` (teacher self-signup) | Anonymous; it creates an account and recorded **nothing** | A NEW action, ACCOUNT_REGISTER (GENERAL retention). Actor ANONYMOUS (nobody is signed in yet), target the new account, `auth_method: self_registration`. The later `/auth/verify` success is the AUTH_LOGIN. | `users/views.py` `register` emits it; allow-list `{auth_method, http_status}` |
| `course-renew-activation-token` | Anonymous; re-issues a student's invitation code by email | EXCLUDE | Reason given, plus "scheduled for removal by retire (B)". The stale-entry test fails when the route goes, so the entry goes with it. |
| `stripe-webhook`, `stripe-webhook-thin` | Anonymous function views | EXCLUDE | Server-to-server and signature-verified; the billing effects are recorded by named events; S3 gives them a system actor |
| qa-console (function views) | Superadmin, hand-rolled check (G1) | S1's generic event through `request.user` | None; the sweep proves it |
| Django admin | — | Out of the sweep (D7) | One targeted test |

## Malformed bodies on the anonymous doors (SM decision: RECORD)
A door request refused before the door's own event (an empty or malformed body, a missing field) left no event. Now `AuditMiddleware` writes exactly one in that case:
- the door's action (AUTH_LOGIN, or ACCOUNT_REGISTER for `auth-register`);
- outcome FAILURE, or DENIED for 401/403;
- actor ANONYMOUS, no target, reason `INVALID_REQUEST`;
- `metadata {auth_method, http_status}`, and **never the body**.

It fires only when no event stored during the request survives (`audit.context.a_stored_event_survives`, any actor), and only for:
- a 4xx other than **429**: a throttled request changed nothing, and recording it would let a throttled caller write unlimited rows;
- an anonymous requester;
- a registered door.

A 5xx is not "malformed" and is not recorded here.

The doors are `login` (`password`), `auth-verify`, `auth-reset-password`, `auth-register-school-admin`, `auth-register-student`, `auth-google-auth` and `auth-register`. `auth-otp` stays excluded (S1).

## Tests
- `audit/tests_route_coverage.py`: the guard above.
- `SelfRegistrationTests`:
  - a new account is one ACCOUNT_REGISTER with the account as target;
  - a malformed registration is one INVALID_REQUEST failure, and the bad email is not stored.
- `DjangoAdminTests`: an admin write leaves exactly one event naming the superadmin (route `admin:users_waitlist_add`).
- `AnonymousDoorRefusalUnitTests`: 400 → one INVALID_REQUEST; 429 → nothing; 500 → nothing; a non-door route → nothing; a signed-in requester → nothing (that's the generic event's job).

## Gates (rule 15: changed modules + mutation + ONE owning-app regression; logs committed)
Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, RACE_COST 600/200, `EXEMPT_EMAIL_DOMAINS=` and `--noinput`, one at a time, on `7b9151e`.

| Gate | Result |
|---|---|
| Reproduce-first | d7f2737's source for the 6 changed files (`prefix_d7f2737_failing.txt`): the guard **cannot import** (`INVALID_REQUEST` and the door registry don't exist there). The substantive reproduction is what the guard found on the real tree (the table above). The first fix-run also caught the QA console's deliberate 404, now in `CONCEALED_ROUTES`. |
| Changed modules | `audit.tests_route_coverage` + `users.tests_auth_audit_doors` + `audit.tests_state_change`: **71 OK** (`changed_modules.txt`). The sweep fires every write route (196 found). |
| 2 Mutation | `mutate.py`, **8 mutants, 8 killed** (`mutation_log.txt`): a door left out of the registry, the refusal never recorded, a throttled (429) request recorded, a signed-in requester recorded as a door refusal, the refusal written although the door recorded its own, the Stripe exclusion removed, ACCOUNT_REGISTER not emitted, and ACCOUNT_REGISTER naming the new account as actor. |
| 1 Regression (owning app) | `audit`: **221 OK** (`regression_audit.txt`) |
| mypy | whole-repo `pre-commit run mypy --all-files`: **Passed** |
| Migrations | none needed (`action` has no choices) |

**Found while running the gates:** the QA console views answer 404 to anyone but a signed-in superadmin ("no hint this exists"). The guard treated that as reachable; `CONCEALED_ROUTES` now lists them explicitly (stale-checked). A 404 in general stays "reachable", since an open route given a made-up id answers 404 too.
