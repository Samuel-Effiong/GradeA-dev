# Epic A completion S1: every write leaves exactly one event; every sign-in door is audited

Branch `task/epic-a-s1` off `phase2/epic-a` `cc34081`. Plan: `docs/phase2/architecture/08_epic_a_completion_plan.md` §2 (approved; merged to phase2/foundation as `5d93da6`). Phase 2 only; this never merges to beta.

**MIGRATION: `audit/migrations/0002_alter_auditevent_actor_role.py`.** Choices-only `AlterField`, adding `ActorRole.ANONYMOUS`. No data change. 0b: phase2/epic-a gains audit 0002.

## 1. Generic STATE_CHANGE event (plan §2.1, gaps G1/G2)
- **`audit/context.py`**: a per-request `RequestAuditState` held in a ContextVar. `request_audit_state()` opens it and always restores the previous state, even when the block raises. `record_stored_event()` does nothing outside a request (see the R1 section).
- **`audit/emitter.py`**: `emit()` records the id of each **stored** event. A rejected or failed write records nothing, so the generic fallback still records the request.
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

## R1 fix: a rolled-back named event no longer suppresses the fallback (after the Verification Engineer's REJECTED at 419e9f6)
**Defect.** `emit()` stores its row in a savepoint inside the caller's transaction and marked the request at once. If that transaction then rolled back, the row was gone but the mark stayed, so `AuditMiddleware` skipped STATE_CHANGE and the request ended with **zero** events. `VERIFICATION.md` has 1a's analysis and probe.

**Fix (authored here; 1a's tested idea, re-implemented):**
- `RequestAuditState` keeps `stored_event_ids` instead of a yes/no flag.
- `emit()` records each stored row's id (`record_stored_event`).
- At the end of the request, the middleware writes the generic event unless `a_stored_event_survives(state)`: one `EXISTS` query on those ids, run only when something was stored.
- If that check itself errors it answers False, so the generic event is written (a second event is better than none).
- **`transaction.on_commit` was considered and rejected.**
  - Inside a test's transaction the callback never fires, so every exactly-one test would record two events.
  - It also still depends on commit timing, whereas the existence check reads what actually survived.
  - The ids approach is correct under both savepoints and real commits.

**Reproduce-first** on 419e9f6's code (`prefix_419e9f6_r1_failing.txt`): the rolled-back case ends with `[]` (zero events) in both the savepoint variant and the real-commit (TransactionTestCase) variant. The surviving-event controls pass.

**Tests added** (`audit/tests_state_change.py`):
- `RolledBackNamedEventSavepointTests` and `RolledBackNamedEventCommitTests`:
  - a rolled-back named event leaves exactly one STATE_CHANGE FAILURE;
  - a surviving named event is the only event.
- `SurvivalCheckFailsSafeTests`:
  - an erroring existence check means "none survived";
  - no stored id means no query.
- The isolation tests now use the ids.

**Mutants:**
- **R1** (a stored id counts without the existence check) must be killed.
- G1–G3 re-anchored.
- G4 replaced with "survival check fails unsafe". The old G4 (record on attempt) is now equivalent: a never-stored id fails the existence check anyway.

**Does a real route hit it today?** Traced:
- `CreditLedger.record()` / `after_bulk_create()` emit CREDIT_TRANSACTION inside whatever transaction the caller holds.
- **A school admin's `POST license-subscriptions/<id>/add_teachers`:** the view wraps `add_teachers_batch` in `transaction.atomic()`, and each enrolled teacher's credit grant records ledger rows, and so emits, inside it. An unexpected error mid-batch (e.g. a DB error on a later teacher) rolls all of it back. Before the fix, that request left zero events.
  - The superadmin variant is not affected: its ADMIN_ACTION is written by the middleware after the view's transaction and survives.
- **Checked safe:**
  - `generate_assignment_from_prompt` and the dashboard `custom_ai_prompt` helper make their charged AI calls **outside** their `atomic()` blocks.
  - `billing` `custom_ai_prompt` charges inside one, but it is superadmin-only (ADMIN_ACTION survives).
  - Grading and extraction charges run in Celery, outside any request.

**R1 gates** (on the R1 code, each run alone):

| Gate | Result |
|---|---|
| 1 Regression | `audit users classrooms students assignments` with `EXEMPT_EMAIL_DOMAINS=`: **2037 OK** (skipped=18). That is 2031 plus the 6 R1 tests |
| 2 Mutation | `mutate.py`: **26 mutants, 26 killed**, survivors `[]` (`mutation_log.txt`, `mutation_results.json`). R1 is killed by the rolled-back test, and G4 by the fail-safe test |
| mypy | whole-repo `pre-commit run mypy --all-files` → Passed |
| Reproduce-first | `prefix_419e9f6_r1_failing.txt`: zero events on 419e9f6, in both variants |
| 5 Failure | an erroring existence check writes the generic event (test + G4) |

**Note on mypy.** The first mypy run flagged the new test mixin, whose attributes are untyped. It now uses the repo's `_MixinBase` idiom, which is `object` at runtime. `audit.tests_state_change` was re-run after that change: 30 OK.

## R2 fix: only an event naming the requester stands in for the generic one (after v2's REJECTED at f300c6b)
v2's record is committed verbatim as `VERIFICATION_v2_f300c6b.md`.

**Defect (V1).** A school admin's successful `POST license-subscriptions/<id>/add_teachers` left exactly one event: the teacher's CREDIT_TRANSACTION, whose actor is the wallet owner. Any surviving event suppressed the generic STATE_CHANGE, so nothing named the admin who acted. That is the route plan 08 G1 names as S1's motivating gap.

**N2 (v2's note).** My R1 route trace covered this route's rollback exposure but missed V1 on its success path.

**Fix (the SM-endorsed invariant).**
- `audit.context.a_surviving_event_names(state, user)`: the generic event is written unless a stored event **still exists and has `actor_id` = the requester** (`pk__in=stored_ids, actor_id=user.pk`).
- Still fail-safe: an erroring check answers False, so the generic event is written.
- The middleware passes `request.user`, read after the view, as before.

**Wording.** The S1 guarantee is now **"exactly one event naming the requester"**: their surviving named event, or the generic STATE_CHANGE. Events naming other actors are side effects recorded in addition, not duplicates.

**Tests**
- `audit/tests_license_admin_attribution.py` (adapted from v2's probe; real JWT and middleware; a plan with a credit grant):
  - add_teachers: the teacher's CREDIT_TRANSACTION is still there, and exactly one event names the admin (STATE_CHANGE SUCCESS, route `license-subscription-add-teachers`);
  - remove_teachers: the control.
- `audit/tests_state_change.py`, in both the savepoint and the real-commit variants:
  - "an event naming someone else does not stand in": the generic event is written for the requester;
  - 1a's two-event cases, adopted per v2's N1: first kept/second rolled back, and first rolled back/second kept.

**Mutants.**
- V1: drop the actor condition.
- 1a's M4 (last id only), M5 (first id only) and M7 (all ids must survive).
- G1, G2 and R1 re-anchored onto the new check.

**Also (v2's pre-read):** an anonymous requester is answered False without a query. `AnonymousUser.pk` is None and must never match NULL-actor events. The outcome is unchanged, because `should_record` never writes the generic event for an anonymous request. `test_an_anonymous_requester_needs_no_query` pins it. The fail-safe test now uses an authenticated stand-in, so it still reaches the erroring query and still kills G4.

**R2 gates.** Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, `EXEMPT_EMAIL_DOMAINS=` and RACE_COST 600/200, one at a time, on WIP 767f848.

| Gate | Result |
|---|---|
| Reproduce-first | f300c6b's `context.py` and `middleware.py` against the new tests (`prefix_f300c6b_r2_failing.txt`): **the pinned add_teachers test FAILS** (no event names the admin). "An event naming someone else" errors in both variants with `DoesNotExist`, because no STATE_CHANGE was written; that is V1 surfacing through `.get()`. Three unit tests error with `AttributeError` because the renamed check does not exist there. |
| On the fix | `audit.tests_license_admin_attribution` + `audit.tests_state_change`: **39 OK** |
| 2 Mutation | `mutate.py`: **30 mutants, 30 killed**, survivors `[]`. V1 (the actor condition dropped) is killed by the pinned test and the someone-else test. 1a's M4, M5 and M7 are killed by the adopted two-event cases. G4 is killed by the fail-safe test. |
| 1 Regression | `audit users classrooms students assignments`: 2046 run, **1 failure**, `assignments.tests_pdf_renderer.ConcurrentRenderingTest.test_one_slow_render_does_not_stall_the_others`. It is a timing test, and it failed while 0b's full-suite run loaded the machine (load average above 6). Re-run alone twice on the same tree: **8/8 OK both times**. It does not touch audit code. So: 2045 OK + 1 load flake (skipped=18). |
| 1 Regression (billing, for add_teachers) | `billing`: **1652 OK** |
| mypy | whole-repo `pre-commit run mypy --all-files`: **Passed** on 767f848 |
