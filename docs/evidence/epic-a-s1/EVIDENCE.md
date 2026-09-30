# Epic A completion S1: every write leaves exactly one event; every sign-in door is audited

Branch `task/epic-a-s1` off `phase2/epic-a` `cc34081`. Plan: `docs/phase2/architecture/08_epic_a_completion_plan.md` §2 (approved; merged to phase2/foundation as `5d93da6`). Phase 2 only; this never merges to beta.

**MIGRATION: `audit/migrations/0002_alter_auditevent_actor_role.py`.** Choices-only `AlterField`, adding `ActorRole.ANONYMOUS`. No data change. 0b: phase2/epic-a gains audit 0002.

## 1. Generic STATE_CHANGE event (plan §2.1, gaps G1/G2)
- **`audit/context.py`**: a per-request `RequestAuditState` held in a ContextVar. `request_audit_state()` opens it and always restores the previous state, even when the block raises. `mark_named_emitted()` does nothing outside a request.
- **`audit/emitter.py`**: `emit()` marks the request after a **stored** event. A rejected or failed write does not mark it, so the generic fallback still records the request.
- **`audit/middleware.py`**: `AuditMiddleware` generalises `AdminActionAuditMiddleware`, which stays as an alias. It opens the state, finishes `ADMIN_ACTION` as before, then writes the generic event if nothing was stored. It is registered last in `MIDDLEWARE`, because it reads `request.user` after the view: DRF writes the authenticated JWT user back onto the Django request.
- **`audit/request_audit.py`**: `emit_generic_state_change` writes one event:
  - `action`: `STATE_CHANGE`, a new enum value (D1).
  - `metadata`: `{route: <view_name>, method, http_status}`. The allow-list gained `route` and `method`.
  - Target: the view class, plus the URL `pk`/`id`/`object_id` only if it is a UUID.
  - Outcome: from the status (2xx/3xx SUCCESS, 401/403 DENIED, other FAILURE).
  - A student actor's event goes to STUDENT_RECORD retention.
  - **The request body, query string and path are never recorded.**
- **Not recorded:** anonymous requests; GET/HEAD/OPTIONS; a **429**, because the throttle refused it before the view ran and nothing changed, and counting it would let a throttled client write unlimited rows; and `EXCLUDED_ROUTES`.
- **`EXCLUDED_ROUTES`**, each entry with a reason:
  - `refresh` (token rotation);
  - `auth-otp` (anonymous code request);
  - `auth-request-change-password` (a code by email).
  No AI suggestion POST route exists on this tree (grep of url_path and view names), so none is listed. S2's guard will force a decision when one appears.
- **Query API (D1):** a `route` filter on `metadata__route`, on both audit endpoints.

## 2. Sign-in doors (plan §2.2, gap G3)
`users/auth_audit.py` provides `sign_in_succeeded`, `sign_in_failed`, `failure_actor` and `account_for_email`. Every door records exactly one `AUTH_LOGIN` per success and per failed attempt, with `metadata.auth_method`:

| Door | Success | Failure reason codes |
|---|---|---|
| `/auth/verify` | `email_verification` | `CODE_MISSING`, `INVALID_CODE`, `CODE_EXPIRED` |
| `/auth/reset-password` | `password_reset` | `INVALID_CODE`, `RESET_LOCKED` (DENIED), `CODE_EXPIRED` |
| `/auth/change-password` | `password_change` | `WRONG_PASSWORD`, `CODE_NOT_REQUESTED`, `CODE_EXPIRED`, `INVALID_CODE` |
| `/auth/register/school-admin` | `school_admin_invitation` | `INVALID_CODE`, `CODE_EXPIRED` |
| `/auth/register/student` | `student_invitation` (sets a password; issues no token) | `INVALID_CODE`, `CODE_EXPIRED` |
| `/auth/google-auth` | `google` | `GOOGLE_CODE_MISSING`, `GOOGLE_EXCHANGE_FAILED` (PROVIDER), `GOOGLE_TOKEN_INVALID`, `GOOGLE_EMAIL_UNVERIFIED`, `ACCOUNT_DEACTIVATED` (DENIED), `GOOGLE_SIGN_IN_REFUSED` |

`/auth/login` already had its events.

**Rollback-safe:** the two registration doors and Google refuse inside `transaction.atomic()`. An event written there would roll back with it. Their failures are therefore recorded **after** the block has exited:
- the registrations through a local `audit_failure` recorded in their `except` branch;
- Google through a wrapper (`google_auth` → `_google_auth`), with each raise site tagging its reason.
Tests prove these events survive the rollback.

**Attribution (SM ruling, strict), including the existing `/auth/login`:**
- A success names the user as actor.
- A failure **never** names the account holder as its actor. The actor is the request's signed-in user if there is one (only change-password), else the new **`ActorRole.ANONYMOUS`**. The targeted account is the **target**; it is null for an unknown email, so an unknown address is never stored.
- Emitter rule: `actor=None` → SYSTEM (background work); an unauthenticated user → **ANONYMOUS** (it used to become SYSTEM). The SM approved this, including the migration.
- Three tests that pinned "anonymous → SYSTEM" were updated on purpose: `audit/tests_emitter.py`, `audit/tests_admin_action.py` (an unauthenticated superadmin-route denial) and `audit/tests_schema.py` (the closed role list).

**School scoping for failures (SM ruling, Gate 9):** a failure carries the **targeted account's** `school_id`. That school's admin sees attempts on its own accounts through the audit query API, and another school's admin never does. Both directions are tested.

**Consumers of `actor_role` checked for the new value:**
- the query API `actor_role` filter (built from `ActorRole.choices`; ANONYMOUS filtering is tested);
- the serializer (passes the value through);
- the PII check constraint (targets STUDENT only);
- retention (keyed on action);
- metrics (not tagged by role).

## 3. Failed-auth volume (SM note 3; plan §2.3)
- Per-IP throttles bound one IP to about 650 failed-auth rows an hour: login 10/min, google 20/h, register 10/h shared, reset 10/h, verify 5/h. A 429 records nothing.
- There is no bound across IPs.
- **Aggregation is approved as S1b, after S1 verifies:** per-target and global caps with summary events, and fail-open. Not built here.

## 4. Housekeeping
`users/tests_auth_audit_events.py`'s logout tests now mint `EpochRefreshToken` (1a's note).

## 5. Tests
- **`audit/tests_state_change.py`:**
  - The exactly-one matrix: plain write, failure, denied, 404 with the target id from the URL, named-event route (logout), superadmin write and denial (ADMIN_ACTION only), excluded route, read, anonymous.
  - Student retention with no email or IP.
  - The body is never recorded (sentinel).
  - The route filter.
  - FR-A-11: the store is down and the write still succeeds; a failed named event falls back to the generic one.
  - Isolation: state restored on exception; concurrent requests don't share state; marking outside a request is a no-op; 429 not recorded; every exclusion has a reason.
  - Gate 9 anonymous scoping, both directions, plus the ANONYMOUS filter.
- **`users/tests_auth_audit_doors.py`:** every door's success and failures, attribution, the unknown email storing nothing, rollback survival, provider class, the target school, and FR-A-11 on verify.
- **`users/tests_auth_audit_events.py`:** login failures are now ANONYMOUS with the account as target.

## 6. Gates
| Gate | Result |
|---|---|
| 1 Regression | `audit users classrooms students assignments` with `EXEMPT_EMAIL_DOMAINS=`: **2031 OK** (skipped=18) on the final S1 code, run alone. An earlier run had 2 migration-test errors, an artefact of creating audit 0002 mid-run; the fresh DB is clean |
| 2 Mutation | `mutate.py`: **25 mutants, 25 killed**, every anchor asserted unique. G8 (a throttled request recorded) survived the first run because its unit test's fake request had no user; the test now uses an authenticated request on a real route with a 201 control, and the re-run kills it (`mutation_log.txt`) |
| mypy | whole-repo `pre-commit run mypy --all-files` → Passed |
| 3 Concurrency | request-state isolation across threads (test) |
| 4 Adversarial | the body/sentinel is never stored; an unknown email is never stored; a throttled request is not recorded; the account holder is never named for an attack |
| 5 Failure | FR-A-11 on the generic and door paths |
| 9 Isolation | anonymous failures scoped to the target's school, both directions |
| 6 Stress | S1 overhead p95: deferred to S8, per plan Gate 6 |
