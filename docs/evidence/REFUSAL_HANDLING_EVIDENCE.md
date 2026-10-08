# Refusal handling (H-24): evidence

Branch `task/refusal-handling`. Base beta `b744c9f`. Owner: fix-refusal-handling
(session grade-automator-plus-04 `[32039c]`, not the Red Team Lead `[73b69e]`).
Assigned by the Senior Manager 2026-09-17.

**Commits under this evidence** (`git log b744c9f..HEAD`):

| SHA | What |
|---|---|
| `f7cd15e` | The fix for D1-D11 plus its tests |
| `5ba3887` | Pin why `EmptyWalletError` is an `APIException` (killed mutation survivor M19) |
| `ce449f1` | D12: stop telling a student their teacher's billing state; Gate 3/5/9 coverage |
| `1848a93` | Assert the withheld D12 reason is logged at WARNING (killed survivor M25) |

Every figure below is copied from a log in `docs/evidence/refusal_handling/`,
checksummed in `docs/evidence/refusal_handling/SHA256SUMS` (36 files).

## 10-gate table

| Gate | Status | Evidence |
|---|---|---|
| 1. Baseline / Regression | PASS | All of D1-D11 reproduced on `b744c9f` in a disposable worktree detached at that commit, fresh DB, no `--keepdb`: `Ran 18 tests`, `FAILED (failures=53)`, 0 errors, each failure with its intended cause (`g1_repro_b744c9f.log`). Affected-module regression on the fix: `Ran 558 tests`; 24 non-passing, every one an approved behaviour change, each carrying the 4-point record; after updating those assertions, `Ran 169 tests`, `OK (skipped=1)`. No new skips, xfails or deleted assertions. |
| 2. Mutation | PASS | 25 mutants, at least one per guard added or changed by the diff, **25/25 KILLED, 0 survivors** (`mutation/SUMMARY.md`, `mutation_m19/`, `mutation_d12/`). Disposable worktrees detached at the commit; every restore verified by sha256 against `git show <commit>:<path>`; all worker trees confirmed clean before removal. Two mutants survived their first run and were killed by NEW tests, not excused - see "Mutation survivors" below. |
| 3. Concurrency | PASS | 20 simultaneous refusals x 10 rounds, real threads against real PostgreSQL: every caller 402 + `insufficient_credits`, 0 `CreditUsageLog`, no new `CreditLedger`, 0 `ChatMessage`. Plus a credit top-up landing mid-flight: every caller either refused with no charge or served with exactly one charge, and chat turns equal 2 per served caller. Each thread closes its own connection in `finally`; every `join` is followed by an `is_alive` assertion (`ConcurrentRefusalTest`). |
| 4. Adversarial | PASS (LOCAL-REAL) | Independent HTTP replays by the Red Team Lead (it did not write the fix), each proven to SUCCEED on `b744c9f` before counting against the fix: **D11** at `f7cd15e` - 6/6 credit-gated endpoints leaked `<b>` HTML pre-fix, clean after (`docs/evidence/security_replay/logs/d11_fix_f7cd15e.log`); **D10** at `f0001aa` - pre-fix `500` carrying "Task requires ~22174 credits, but you only have 1000", fix `402` + `insufficient_credits` with no detail strings (commit `9a6b1b6`, `d10_phaseA_b744c9f.log` / `d10_phaseB_f0001aa.log`); **D12** at `f0001aa` over the async polled-task surface (student -> `POST submissions/{id}/update-async` -> `GET tasks/status/{id}`) - pre-fix the student was told "...Trial period has expired...", fix returns only the generic student message (commit `25d3c28`, `d12_phaseA_b744c9f.log` / `d12_phaseB_f0001aa.log`). All on `task/security-exploit-replay`. **Declared gap:** the dispute-deficit wording ("Credit consumption is blocked ... chargebacks ... refunds") is covered by unit-level proof only (`D10CreditDetailNeverSurfacesTest`) - not replayed, because staging a lost Stripe dispute against partly-spent credits was out of reach today. The H-19 x D11 composition is replayed on the integration commit (see Gate 10). |
| 5. Failure / Recovery | PASS | A refusal never charges and never persists (asserted inside the real `billing_refund_scope`); the DB write that records the refusal failing does not turn it into a success (task ends FAILURE, row stays non-terminal, submission unchanged); redelivery of a refused task recharges nothing; a transient failure still retries 4x and still logs ERROR with a stack (`RefusalFailureRecoveryTest`). |
| 6. Stress / Scale | PASS | Measured at the doctrine's sizes, 600 vs 6,000 students (10x), with the identical module run on BOTH commits so the before/after is a real diff, not two different tests (`refusal_handling/gate6/`). Queries counted with `connection.execute_wrapper`; the Postgres connection count recorded beside every figure (19/100 throughout, not starved); a 5xx or 2xx is rejected as "not a refusal" rather than measured. Per-request refusal cost is flat in school size on both commits (permission layer 1-3 queries, service gate 14-16, at 600 AND 6,000). The change is subtractive, as claimed: a refused task makes **4 -> 1** credit-gate calls, **89 -> 26** queries, **309 -> 73 ms** per item; the weekly summary writes **220 -> 0** stack traces for 220 refused courses at a flat 9.0 queries/course. See the Gate 6 section. |
| 7. Real Infrastructure | PASS (LOCAL-REAL) | Every test above runs against real PostgreSQL and real Redis. The model provider is stubbed deliberately and asserted never called - a refusal that reached the provider would be the defect. No result here is presented as DEPLOYED-REAL. |
| 8. Live / E2E | PARTIAL (LOCAL-REAL) | The Red Team Lead's D11 replay ran HTTP + JWT against a running local app (LOCAL-REAL), which per H8.1 is at most PARTIAL for this gate. This task is tiered LOGIC-ONLY by the Fixes Coordinator: Gate 8 = LOCAL-REAL plus the post-landing QA-beta smoke. **No DEPLOYED-REAL evidence exists yet.** |
| 9. Security / Isolation | PASS | Both directions probed - the actor's own data and every foreign boundary (teacher/teacher, student/teacher, school/school, role). Whole-payload assertions: no foreign id, email, name, course or school appears anywhere in the body, and none of the 7 internal credit fragments does either. D12 (a student being told their teacher's billing state) found by the Red Team Lead, fixed, and pinned by three tests plus mutants M24/M25 (`RefusalIsolationTest`). |
| 10. Final Production Gate | NOT RUN | This branch lands in the integration batch (93, 57, 7e, then this branch last, each its own merge commit); the integration commit gets two consecutive strict runs, which certify the landing. The integrator also commits `refusal_handling/composition/tests_credit_balance_composition.py` there and proves it against two mutants (H-19 reverted, D11 reverted) - it can only pass on the merged tree. **Nothing here is landed until that gate passes on the landed SHA.** |

## The 8 completion answers

1. **What changed.** One classification of AI refusals (`billing/refusals.py`): `AIFeatureNotAvailableError` -> 403 `ai_feature_not_available`, `InsufficientCreditsError` -> 402 `insufficient_credits`, never retried, logged at WARNING. 12 defects fixed across HTTP and Celery (D1-D12, table in section 2). Every `InsufficientCreditsError` shows one generic, role-neutral message; its real text (balance, estimate, chargeback/refund deficit) stays in server logs. A student is no longer told their teacher's billing state. Transient failures keep exactly the retry behaviour they had.
2. **Why it was necessary.** Users who were simply out of credits or not entitled were shown 500s (server fault), 400s (malformed request) or HTML markup in an API error body; permanent refusals were retried 3-4 times, re-running the entitlement and balance checks for nothing; background tasks recorded blank errors or logged expected refusals as crashes; and internal billing state - including another tenant's - reached clients.
3. **What was tested.** 32 new tests (18 defect tests + 14 gate tests), plus 24 updated existing assertions. Reproduction on the pre-fix commit, a 25-mutant battery, 20-way concurrency over 10 rounds on real PostgreSQL, failure injection at each step of the changed paths, isolation probes in both directions, and an independent Red Team replay of D11.
4. **Which gates passed.** 1, 2, 3, 4 (LOCAL-REAL), 5, 6 (measured, both commits), 7 (LOCAL-REAL), 9.
5. **Which gates remain incomplete.** **Gate 8 PARTIAL** (LOCAL-REAL only; no DEPLOYED-REAL evidence; this task is tiered LOGIC-ONLY, with the post-landing QA-beta smoke as its Gate 8). **Gate 10 NOT RUN** (the integration batch's two strict runs will certify it). Gate 4 has one declared gap: the dispute-deficit message variant has unit-level proof only.
6. **What risks remain.** (a) Two frontend-visible changes: student submission endpoints answer 402/403 instead of 400, and 18 endpoints answer 402 + plain text instead of 400 + HTML. **Accepted by the user on 2026-09-18** ("land it on QA now"), accepting that the QA frontend may look off until it is updated; the frontend lives in a separate repository. Handoff for its developer: `refusal_handling/FRONTEND_CONTRACT_HANDOFF.md`. Known gap noted there: `POST assignments/generate/{course_id}` answers 402/403 without a `code`. (b) The generic credit message is a deliberate loss of detail for clients; operators keep it in logs. (c) `AIFeatureNotAvailableError` text still passes through verbatim on self-facing surfaces, which is intended, but only the student-facing variant has been audited for disclosure. (d) Gate 10 has not run, so nothing here is proven against the full suite.
7. **What exact commit contains the verified implementation.** `1848a935379e9c5f0757aee29d8dbc0048c9c5d6`.
8. **Is the verified commit the same commit intended for release.** The code is `1848a93` and has not changed since; the branch head carries only docs commits on top (verified `git diff --quiet 1848a93 HEAD -- . ':!docs'`). What gets released is the **integration merge commit**, which is a new commit and is gated itself (H10.3). **Not release-approved until that gate passes.**

## Gate 6: measured, at doctrine scale, on both commits

Module: `refusal_handling/gate6/test_refusal_handling_scale.py`, kept under
`docs/` on purpose so every future full gate doesn't build a 6,000-student school.
To rerun it, copy it into `billing/tests/`. It imports nothing this branch added and
asserts counts and timings, never messages, which is why the identical file runs on
the pre-fix commit. Logs: `gate6/scale_f0001aa.log`, `gate6/scale_b744c9f.log`
(the latter from a disposable worktree detached at `b744c9f`, fresh DB, no `--keepdb`).

| Measurement | `b744c9f` (pre-fix) | `f0001aa` (fix) |
|---|---|---|
| Permission-layer refusal, queries at 600 / 6,000 students | 1-3 / 1-3 | 1-3 / 1-3 |
| Service-gate refusal, queries at 600 / 6,000 students | 14-16 / 14-16 | 14-16 / 14-16 |
| Service-gate refusal, p95 at 600 / 6,000 students | 41.6 / 55.0 ms | 40.4 / 38.4 ms |
| Refused task: credit-gate calls per item | **4** | **1** |
| Refused task: queries per item | 89 | 26 |
| Refused task: ms per item | 308.9 | 73.1 |
| Weekly summary: queries per course, 20 / 220 courses | 9.05 / 9.0 | 9.05 / 9.0 |
| Weekly summary: stack-trace log records, 20 / 220 courses | **20 / 220** | **0 / 0** |
| Postgres connections during the run | 19 / 100 | 19 / 100 |

Reading it: per-request cost was already flat in school size on the old code, and
it stays flat. The fix neither adds nor removes per-row work there. What the fix
changes is that it stops repeating a refusal (4 -> 1 gate calls per task) and stops
logging an expected refusal as a crash (220 -> 0 stack traces). **The measurement
could have falsified both claims, and on `b744c9f` it produces exactly the
failures the claims predict** (`AssertionError: 4 != 1` and `220 != 0` in
`gate6/scale_b744c9f.log`).

Provenance: the logs were produced by the module at sha256 prefix `baf395651fcd6eb4`, the same bytes copied into both worktrees. The committed copy differs from that ONLY by the repository's pre-commit fixes: black formatting, loop variables bound into two lambdas (flake8 B023), a single return value instead of a 1-tuple, and an `assert` on two regex matches (mypy). None of these changes what is measured. The committed file's own sha256 is in `SHA256SUMS`.

Declared deviations: (1) `PASSWORD_HASHERS` is set to MD5 for the fixture build;
rows still go through `CustomUser.objects.create_user`. (2) While fixtures are
**built** (never while they are measured), Redis wildcard `delete_pattern` scans
are skipped. Profiling a 20-student build found 80 of its 84.8 s in those SCANs,
fired by cache-invalidation signals on every user and enrollment save, at roughly
4 s per student against the shared local Redis. That cost is the H-1 wildcard
invalidation debt, owned by H-1 Stage 3, not a refusal cost, and it was reported to
that owner. Every measured request ran with invalidation live.

Two bugs in the measurement itself were caught by its own guards before any
figure was recorded: the permission measurement at first used a teacher holding
1,000 credits, which `HasCreditBalance` lets through, so it was timing a grade-all
dispatch rather than a refusal. The "a 2xx is never a refusal" guard failed on it,
and the wallet is now spent to zero through the production consume path first. The
weekly measurement at first divided by the courses it built rather than the courses
the task processed.

## Mutation survivors (H3.2: killed, never excused)

| Mutant | Why it survived | How it was killed |
|---|---|---|
| M19 (`EmptyWalletError` without its `APIException` base) | My browsable-API test called the submissions LIST endpoint, whose action is not credit guarded, so the permission never ran and the test proved nothing. | Replaced with a test that takes the real path: an HTML request to a guarded endpoint, where DRF's `show_form_for_method` re-runs the SAME action's permission check while rendering the 402 page and catches only `APIException`. Commit `5ba3887`. |
| M25 (D12 server-side log downgraded to DEBUG) | The test captured at `level=DEBUG`, so a log record nobody would see in production still satisfied it. | The test now asserts the record's level is at least WARNING. Commit `1848a93`. |

## 1. Classification (written before any code)

Every exception that can leave an AI call path falls into exactly one class.

### PERMANENT: a refusal of the request

The same request by the same user fails the same way until the user (or an
admin) changes something: plan, wallet, entitlement or feature flag. Retrying
can't help, and each retry re-runs the entitlement and balance checks.

| Exception | Raised by | HTTP | `code` |
|---|---|---|---|
| `billing.access_control.AIFeatureNotAvailableError` | `execute_graded_task` tier/plan gate, assignment-teacher gate, `DASHBOARD_CUSTOM_AI_PROMPT_ENABLED` kill switch, H-19 single-flag superadmin (fix-idor) | 403 | `ai_feature_not_available` |
| `billing.errors.InsufficientCreditsError` | `execute_graded_task` balance and estimate checks, `CreditWallet.consume` (balance, consumption blocked by dispute or refund deficit) | 402 | `insufficient_credits` |

The 402 and 403 statuses follow the existing precedent at
`assignments/views.py:1371-1400` (generate-assignment), so every AI
endpoint answers the same way.

Rules for PERMANENT:
1. Never retried, neither by an in-process `*_with_retry` loop nor by a Celery `self.retry`.
2. The exception TYPE is preserved end to end. No layer rewraps it in a bare `Exception`.
3. HTTP: 402 or 403 with `{"error": <user-facing message>, "code": <code>}`. Never a 500.
4. Background: recorded as a terminal FAILURE with the refusal's own message
   (never `error=None`, never swallowed silently). Logged at WARNING, not as
   an ERROR with a stack trace, because it's an expected business outcome.
5. Best-effort side features (weekly narrative emails): a refusal skips the AI
   part, the email still goes, and the refusal is counted and logged at WARNING.

Out of scope, unchanged: other refusal types already handled (`UPLOAD_REFUSALS`
members, `SUBMISSION_CLOSED_ERRORS`, DRF `PermissionDenied`), and
`TaskCancelledError`, which has its own CANCELLED path.

### TRANSIENT: everything else

Provider timeout, connection error, rate limit, provider 5xx, malformed or
empty model output (JSON decode), and any other unexpected exception.
**Retry behaviour stays exactly as it is today.** This change doesn't narrow or
widen what gets retried, apart from pulling PERMANENT out. Users still see the
existing classified or fallback messages (`describe_user_error` /
`describe_background_task_error`).

Mechanism: one tuple, `PERMANENT_AI_REFUSALS`, in a new module
(`billing/refusals.py`), plus a helper that maps a refusal to (status, code,
message). Checks are explicit `isinstance` on the type. No cause-chain walking,
because the fix is to stop destroying the type, not to dig it back out.

## 2. Inventory of defects (b744c9f)

| # | Site | Today | Fix |
|---|---|---|---|
| D1 | `ai_processor/services.py:767-768` `extract_assignment`, `:820-821` `extract_assignment_image` | `except Exception` rewraps a refusal as `Exception("Error during AI model: ...")`. The refusal passthrough in `extract_assignment_with_retry:1286` then never matches, so a refusal is retried 3x (3 entitlement checks) and surfaces as generic text | Re-raise PERMANENT before the rewrap |
| D2 | `dashboard/views.py:197-214` `run_dashboard_ai_chat` (3 endpoints: :1183, :2751, :3547) | Any exception, refusals included, returns 500 | PERMANENT returns 402/403 + code |
| D3 | `billing/views.py:2475-2499` superadmin custom AI prompt | Same, returns 500 | Same |
| D4 | `users/exceptions.py` DRF `custom_exception_handler` | An uncaught refusal becomes a bare 500 (hits sync `AssignmentViewSet.create` :401 and `partial_update` :547, and any future view) | Map PERMANENT to 402/403 + code centrally |
| D5 | `assignments/tasks.py:58` `UPLOAD_REFUSALS` lacks both types. Affects `extract_answer_background_task` (max_retries=3) and `upload_answers_engine_async` (max_retries=3) | A refusal goes through `self.retry` 3x (4 executions, each re-checking entitlement) before being recorded | Treat PERMANENT as terminal: recorded, not retried |
| D6 | `assignments/tasks.py:~585` `grade_engine_async` batch result | No processing task (auto_grade_due_assignment / grade_batch_async path) records `error=None` in the batch session | Record the described message (taken over from fix-idor, agreed) |
| D7 | `dashboard/tasks.py` `send_weekly_course_summaries` :74-87, `send_weekly_school_admin_summaries` :~224-240 | A refusal gets `logger.exception` (ERROR + stack), not counted | WARNING + `ai_refused` counter; email still sent (course summaries taken over from fix-idor, agreed) |
| D8 | `students/task_tracking.py` `mark_processing_task_failure` | Logs every failure, refusals included, at ERROR with a stack | WARNING for PERMANENT |

Already correct, pinned by tests only (no code change): the in-process retry wrappers
`extract_assignment_with_retry` (chunked), `extract_answer_with_retry`,
`extract_grade_with_retry`, `generate_assignment_from_prompt_with_retry`,
`custom_ai_prompt_retry`; the non-retrying tasks (`extract_assignment_background_task`,
`update_assignment_background_task`, `grade_engine_async`, `format_grade`,
`formatted_grade_async`, `upload_assignment_async`, `classrooms.student_summary_async`)
record the refusal message and re-raise, so they end as Celery FAILURE with no retry.
The re-raise stays, because fix-idor's `CeleryPathBothFlagsTest` asserts
`result.failed()` with the refusal type.

## 3. Senior Manager decisions (2026-09-17)

Design approved as written in section 1.

- **Q1: ALIGN (D9).** `students/views.py:121` `_failure_response` answers a
  refusal with 400. It moves to 402/403 + `code`. **FRONTEND-VISIBLE BEHAVIOUR
  CHANGE, flagged to the Senior Manager, who tells the user so the student
  frontend can be checked for anything keyed on 400.** Four-point record:
  1. Previous: `upload`, `upload async` and `update` submission endpoints return
     HTTP 400 `{"error": <refusal text>}` for AIFeatureNotAvailableError /
     InsufficientCreditsError.
  2. New: 403 `ai_feature_not_available` / 402 `insufficient_credits`, with `{"error", "code"}`.
  3. Why it's correct: 400 means "your request is malformed", and a
     well-formed request refused for plan or balance reasons isn't malformed.
     One convention across every AI endpoint (precedent: `assignments/views.py:1371-1400`).
  4. Proving tests: listed in section 5 once written.
- **Q2: SANITISE (D10).** InsufficientCreditsError text is never shown to the
  client. Every user-facing surface (HTTP body, recorded background-task
  error, batch result) shows one fixed generic message + `insufficient_credits`.
  The original text, including the chargeback/refund deficit breakdown
  from `CreditWallet.consume`, is kept in server logs only. The exact original
  strings went to the Red Team Lead (grade-automator-plus-04 [73b69e]) for a G4
  check that the sanitised output leaks none of it. Four-point record:
  1. Previous: `describe_user_error` / `describe_background_task_error` passed
     InsufficientCreditsError text through verbatim (e.g. "Credit consumption is
     blocked on this account: a reversed payment left an unsettled deficit of N
     credits (A from chargebacks, B from refunds).", "Task requires ~N credits,
     but you only have M credits...").
  2. New: a single fixed message for every InsufficientCreditsError.
  3. Why it's correct: internal billing and fraud state doesn't belong in an API error body.
  4. Proving tests: section 5.
- Weekly-summary swallow (D7), batch `error=None` (D6), retry-3x upload paths
  (D5) and the Gate-5 "worsened paths" obligation formerly on fix-idor's branch
  are confirmed as owned by this branch.

- **D11 (Senior Manager ruling, option a): FRONTEND-VISIBLE BEHAVIOUR CHANGE on
  18 endpoints, flagged to the Senior Manager, who tells the user.**
  `users/permissions.py` `HasCreditBalance` (lines 36-50 only; fix-idor owns
  17-19) answered an empty wallet with `ParseError`, i.e. HTTP 400 with HTML
  markup in the message. Four-point record:
  1. Previous: 400 `{"detail": "<b>Insufficient Credits:</b> Your Credit Wallet is
     currently empty. Please contact your teacher..."}` (student) or
     "...Please top up your credits..." (teacher).
  2. New: 402 `{"error": <generic message>, "code": "insufficient_credits"}`. The
     rendered top-level `message` is the plain generic text with no markup.
     Raised as `billing.errors.EmptyWalletError` (an `InsufficientCreditsError`
     and a DRF `APIException`, so the browsable API's form-permission checks
     keep working).
  3. Why it's correct: the same refusal gets the same convention everywhere.
     HTML doesn't belong in an API error body, and a credit refusal isn't a
     malformed request.
  4. Proving tests: `D11EmptyWalletPermissionTest` (all 18 endpoints, plus the
     browsable renderer).
  Endpoints: PATCH `assignments/{pk}` (inline check, raw_input edit),
  PATCH `assignments/{pk}/update-async`, POST `assignments/upload-async`,
  POST `assignments/{pk}/grade-all`, POST `assignments/{pk}/schedule_grade_all_submission`,
  GET `course/{pk}/student-summary`, POST `submissions`, POST
  `submissions/{assignment_id}/upload`, POST `submissions/{assignment_id}/upload-async`,
  POST `submissions/{assignment_id}/batch-upload`, PUT and PATCH `submissions/{pk}`,
  POST `submissions/{pk}/update-async`, POST `submissions/{pk}/grade`,
  POST `submissions/{pk}/grade-async`, POST `submissions/{pk}/schedule-grade-async`,
  GET `submissions/{pk}/teacher_feedback`, PATCH `submissions/{pk}/update-grade`.
- **Generic credit message (D10, D11):** "There aren't enough AI credits
  available for this. The credit wallet needs to be topped up before it can
  run." It's worded for every role: a student can't refill a wallet, so
  "Refill your wallet" would be wrong advice.
- **Renderer (needed for the contract):** `users/renderers.py` `flatten_errors`
  ignores a string `code` sitting next to a message key. Without this, the
  rendered `message` would read "1. Error: ... 2. Code: ...".

## 5. Gate evidence (in progress)

### G1: reproduction on b744c9f (PASS for reproduction; regression run pending)

Method: the final `billing/tests/test_refusal_handling.py` (sha256
`2c38cf54be57964b88b8d52b7c35d1a1d39fdd6b36966ab990bb4aa884038213`) copied
into a disposable worktree detached at `b744c9f` (no fix code) with its own
test DB, then run with no `--keepdb`. Worktree removed; DB confirmed dropped.
Log: `docs/evidence/refusal_handling/g1_repro_b744c9f.log`.

Result on b744c9f: `Ran 18 tests`, `FAILED (failures=53)`, 0 errors. 15 of the
18 tests fail. Failure causes, tallied from the log:

| Defect | Evidence in log |
|---|---|
| D1 retried refusal | `3 != 1 : a permanent refusal was retried` x2; wrong exception type on the text path x2 |
| D5 Celery self.retry | `4 != 1 : a permanent refusal was retried` x4 (1 run + 3 retries) |
| D2/D3/D4 500s | `500 != 403` x5, `500 != 402` x2 |
| D9 400 | `400 != 403` x1, `400 != 402` x1 |
| D11 400 + HTML | `400 != 402` x18 (all 18 endpoints). `<b>Insufficient Credits:</b>` appears 36 times in the log |
| D6 error=None | `None is not true : batch failure recorded error=None` x2 |
| D7 no counter | "AI narration refused for 1" not found x2 |
| D8 ERROR logging | `40 != 30` x2 |
| D10 raw credit text | 4 original texts x 3 describers = 12 |

The 3 tests that pass on both commits are deliberate guards against
over-widening: feature-refusal text still passes through verbatim,
transient failures still log at ERROR with a stack, and the browsable API
still renders for a refused user.

Fixtures are real refusals created through production write paths:
`SubscriptionService.activate_free_trial` ->
`finalize_trial_to_paid_conversion` -> `StripeWebhookHandler.handle_subscription_deleted`
(cancelled; credits left, so the gate refuses "No active subscription");
`SubscriptionService.activate_subscription` on a 1000-credit plan (balance
below the estimate); a never-subscribed teacher (empty wallet); the
`DASHBOARD_CUSTOM_AI_PROMPT_ENABLED` kill switch. Only the model provider is
stubbed, and it's asserted never called.

Fix run (interim, `--keepdb`): `Ran 18 tests`, `OK`.

## 6. Found out of scope (report-only, not fixed here)

- **Follow-up item (mild information exposure):**
  `assignments/tasks.py:1072-1075` `auto_grade_due_assignment` returns
  `str(e) + traceback.format_exc()` as the Celery result, so a stack trace is
  stored in the result backend.
- `assignments/tasks.py:~1006` `upload_assignment_async` writes
  `error=str(e)` (raw exception text) to the batch session for non-refusal errors.
- `ai_processor/services.py:723` `extract_assignment` (the AI-processor method)
  has no non-test caller found. It may be dead code, but it's fixed anyway (D1).

> Note: the repo pre-commit hook stripped trailing whitespace from
> `mutation/M21.log` when the evidence was committed; SHA256SUMS was
> regenerated afterwards, so it matches the committed bytes.
