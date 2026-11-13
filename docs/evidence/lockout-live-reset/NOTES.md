# Lockout live-server ConnectionResetError (diagnosis, beta)

Task from the SM (grade-automator-plus-08), 2026-11-09, owner-approved as a
DIAGNOSIS: no fix applied, nothing pushed, until the SM rules and the owner
gives a beta go. Worktree branch task/lockout-live-reset off origin/beta
e4a893197.

Target: `users.tests_login_lockout.LoginLockoutLiveServerTests.test_concurrent_brute_force_against_live_http_server_is_held`.

## The CI failure

PR #123 (task/epic-b1a), run 42505252513, 2026-11-08,
`coverage run manage.py test --noinput --parallel 4`: **Ran 9483, FAILED
(errors=1, skipped=81)**. The one error is this test (log:
excerpt `pr123_ci_excerpt.txt`; full log line 73481):

    tests_login_lockout.py:519  results = [f.result() for f in as_completed(futures)]
    tests_login_lockout.py:490  with urllib.request.urlopen(
    http/client.py:304          line = str(self.fp.readline(_MAXLINE + 1), ...)   # _read_status
    socket.py:720               return self._sock.recv_into(b)
    ConnectionResetError: [Errno 104] Connection reset by peer

The client had connected and sent its POST, and was reading the status
line when the server side reset it.

Ruled out from that log:
- The DB connection limit. Postgres max_connections is 100, and the
  container log has no "too many clients". A DB error would also come back
  as a 500 response, not a reset.
- Unread request body on close. Django's test `ServerHandler.close()`
  drains the body (LimitedStream), and `handle()` ends with
  `shutdown(SHUT_WR)`.

## H-27 history

H-27 already touched this test: fba129402 (2026-09-18) replaced the
1,000,000-iteration hasher with MD5 for the concurrency classes and gave
`_post` a 120 s hang detector. That fixed the 2026-09-17 *timeout* (CPU
starvation). It did not touch the listen queue, which is what this note is
about. c0e05e670 (H-31) and 90c3229bf (H-274) changed nearby assertions and
wording only.

## Hypothesis

Django's test server listens with `WSGIServer.request_queue_size = 10`
(django/core/servers/basehttp.py:71, Django 5.2.17). This test opens
`WORKERS = 30` connections at once. Every connection waits in the kernel's
listen queues until the server thread's single accept loop takes it. The
clients, the accept loop and the 30 request threads all share one process
and one GIL, and under `coverage run --parallel 4` on a 4-vCPU runner
that loop can fall seconds behind. When it does, the SYN queue (capped at
the backlog) answers with SYN cookies, the accept queue overflows, and the
kernel drops the client's ACK and request data. When the loop resumes, it
resets those connections. Production does not use this server.

## Probe 00 (standalone, no DB, no test runner; exploratory, not declared)

`backlog_probe.py.txt`: Django's own ThreadedWSGIServer and a trivial 401 app,
accept loop stalled S seconds, 30 concurrent urllib POSTs, kernel counters
from `nstat`. Raw output: `00_probe.txt`.

| stall | backlog | trials | trials with resets | resets | where |
|---|---|---|---|---|---|
| 0 s | 10 | 4 | 0 | 0 | (max wait 1.0 s: one SYN retransmit, so it overflowed even unstalled) |
| 3 s | 10 | 1 | 0 | 0 | |
| 6 s | 10 | 2 | 2 | 2, 4 | `ConnectionResetError@readinto` |
| 8 s | 10 | 2 | 2 | 6, 2 | `readinto`; ListenOverflows 146, SyncookiesSent 8/7 |
| 10 s | 10 | 3 | 2 | 5, 3, 0 | `readinto` |
| 20 s | 10 | 1 | 1 | 4 | `readinto` |
| 3, 8, 10, 20 s | 64 | 4 | 0 | 0 | ListenOverflows 0, SyncookiesSent 0 |

The reset matches CI exactly: raised from `readinto` while reading the
status line. In every trial, the number of requests the app saw equaled
the number answered (8 s / 10: app_saw 24 with 6 resets, 28 with 2). A
reset request never reached Django.

## Runs (declared before running; all under flock ~/.machine-fullsuite.lock,
venv h284_deps_env, --settings=settings_worktree --keepdb)

Counts from build_suite().countTestCases():
LoginLockoutLiveServerTests 1; users.tests_login_lockout 13;
users.tests_activity_middleware_load 11; students.tests_async_edit_path 17;
students.tests_post_grading_submission_lock 29.

- **01** reproduction, plain: LoginLockoutLiveServerTests, 20 serial runs.
  Predicted **20/20 OK, 0 resets** (8 cores, no coverage, nothing else
  running). Log `01_loop_plain.log`.
- **02** reproduction under coverage: the same class, `coverage run`, 10
  runs. Predicted **10/10 OK**. Log `02_loop_coverage.log`.
- **03** with the other live-server classes, the CI shape:
  users.tests_login_lockout, users.tests_activity_middleware_load,
  students.tests_async_edit_path, students.tests_post_grading_submission_lock
  (70 tests) under `coverage run ... --parallel 4`, 5 runs. Predicted **5/5
  OK** (8 cores is twice the CI runner's 4). Log `03_loop_parallel.log`.
- **03b** the same as 03, with 8 busy-loop CPU hog processes running so the
  box looks like a saturated runner, 5 runs. Predicted: **at least 1 of
  5 runs has a ConnectionResetError in a live-server class**. This is a
  low-confidence prediction. Log `03b_loop_parallel_loaded.log`.
- **04** forced interleaving (`tests_zz_forced_backlog.py`, untracked,
  copied here as `.txt`): the real test body, accept stalled 8 s before the
  burst. ForcedStallBacklog10 (Django's 10) against ForcedStallBacklog64,
  5 runs. Predicted: **Backlog10 errors with ConnectionResetError at
  readinto in at least 4 of 5 runs; Backlog64 passes 5 of 5**. Log
  `04_forced.log`.
- **05** overflow without any stall (declared after 01 showed one request
  burst taking 3.6-7.9 s with the fast hasher, a sign of SYN retransmits):
  Unstalled10 and Unstalled64 (same module, stall 0), each alone, 5 runs
  each, `nstat` TcpExtListenOverflows delta per run. That counter is
  machine-wide, so other sessions add noise; run under flock. Predicted:
  **Unstalled10 overflows above 0 in every run, and its test takes 1 s or
  more; Unstalled64 overflows 0 and its test takes under 1 s; all 10 runs
  OK**. Log `05_unstalled.log`.

## Results (under flock; logs gzipped)

| Run | What | Predicted | Result | Verdict |
|---|---|---|---|---|
| 01 | class alone, plain, 20 runs | 20/20 OK | **20/20 OK**, 0 resets (test 3.6-7.9 s) | As predicted |
| 02 | class alone, coverage, 10 runs | 10/10 OK | **10/10 OK**, 0 resets | As predicted |
| 03 | 4 live-server modules (70), coverage --parallel 4, 5 runs | 5/5 OK | **5/5 Ran 70 OK (skipped=2)**, 0 resets | As predicted |
| 03b | as 03 + 8 CPU hogs, 5 runs | at least 1 of 5 with a reset | **5/5 Ran 70 OK (skipped=2)**, 0 resets | **Prediction wrong**: 8 cores plus 8 hogs did not stall the accept loop long enough |
| 04 | forced 8 s accept stall, real test body | Backlog10 errors in at least 4 of 5; Backlog64 5/5 OK | **Backlog10 ERROR ConnectionResetError in 2/5** (runs 1, 2), same frame as CI; **Backlog64 5/5 OK** | **Prediction wrong** for Backlog10's rate (2 of 5, not at least 4); Backlog64 as predicted |
| 05 | no stall, nstat ListenOverflows | Unstalled10 > 0 every run and at least 1 s; Unstalled64 0 and under 1 s | **both 5/5 OK, ListenOverflows +0 for all 10, both about 2.7 s** | **Prediction wrong**: the real burst does not overflow without a stall here; 01's 3.6-7.9 s was DB work, not SYN retransmits |

Natural reproduction: **0 resets in 45 natural runs** (01, 02, 03, 03b). Forced:
**2 of 5** with Django's backlog, **0 of 5** with 64, using the same test body
and assertions. Together with the probe (resets at stalls of 6 s or more with backlog 10, never with
64, `app_saw` equal to the number of successes), this pins the CI signature to listen-queue
overflow during an accept-loop stall of roughly 5 s or more.

## Cause (step 3)

The test harness's server, not the lockout, caused this. Django's live server listens with a
backlog of 10 (`basehttp.py:71`), and the test sends 30 connections at once. When the
server thread's accept loop stalls for several seconds, the kernel overflows the queue
(SYN cookies, ACKs dropped). When the loop resumes, it RSTs the dropped connections, which surface as
`ConnectionResetError` in `recv` while reading the status line. That is the CI frame.

The CI runner can stall that long: the 2026-09-17 incident (fba129402) had this same
server not answer for 15 s on a 4-vCPU runner under coverage. On 2026-11-08 the runner was 4 vCPU,
`coverage run --parallel 4`, a 9483-test suite, with postgres on the same VM. I could not
reproduce a natural stall on this 8-core box, which matches CI's "local re-run OK".

Ruled out: DB connection limit (no "too many clients"; a DB error is a 500, not a reset),
unread body on close (Django drains it), keep-alive (urllib sends `Connection: close`;
one request per connection), teardown order (the error is mid-burst, inside `_post`).

Production is not affected: gunicorn (Dockerfile:93, no `--backlog`) listens with
gunicorn's default 2048.

## Is a reset a lockout bypass?

No. A reset connection was never accepted, so the request never reached Django: in every probe
trial the app saw exactly the answered requests. Nothing was counted, nothing was checked,
and no token was issued. The test's own assertions (all answered requests 401, account locked,
failures between MAX and WORKERS, correct password then refused) stay true for the requests that ran.
Even so, retry-on-reset is **not** proposed: it would also hide a real server crash.

## Proposed fix (step 4; not applied)

Test-only. Give the live server a listen backlog that holds the whole burst:

- In users/tests_login_lockout.py: a `LiveServerThread` subclass whose `server_class` is
  `ThreadedWSGIServer` with `request_queue_size` large enough for every in-flight
  connection (proposal: 128; anything at or above WORKERS + 1 holds this test). Set it as
  `server_thread_class` on `LoginLockoutLiveServerTests`. `WORKERS = 30`, every assertion,
  the fast hasher and the 120 s hang detector stay as they are. The test still sends 30
  concurrent real-HTTP wrong passwords, and must still see the lockout hold.
- Red first, deterministic: a guard test asserting the class's live server listens with a
  backlog of at least WORKERS. Today it is 10 < 30, so it fails on every run. The forced-stall test cannot be the red: it fails
  only 2 of 5. Proposed as a kept regression test with the stall shortened to keep it
  cheap, green 5/5 with the fix. SM to rule whether it earns its runtime (about 10 s).
- Plant: revert `server_thread_class` and declare the kill by the guard test.
- Scope question for the SM: the same latent flaw is in
  `users.tests_activity_middleware_load` (WORKERS = 40) and
  `students.tests_async_edit_path.UpdateAsyncConcurrencyLiveHTTPTest` (WORKERS = 12),
  both above 10. Option (a) fix only the lockout class (the one that failed). Option (b) a shared
  helper (e.g. `AutoGrader/test_live_server.py`) used by all three, plus one
  guard that scans every LiveServerTestCase for a backlog of at least WORKERS. I recommend (b).
- Near: users + students + the 66-label guard list, serial. Beta first (owner go), then
  merge beta into staging (standing rule).

## SM ruling (2026-11-09) and the fix

The SM accepted the checkpoint and ruled:
- Scope (b): one shared helper with backlog 128, used by every live-server class.
- Red: one deterministic guard, `AutoGrader.tests_live_server_backlog_guard`. It **joins the standing guard list as label 67**.
- No forced-stall test in the suite; the probe and its logs stay here as the proof.
- Plants PL1 (one class reverted) and PL2 (helper backlog back to 10).
- No retry-on-reset.

**Correction to my checkpoint:** there are **four** live-server classes, not three.
- `users.tests_activity_middleware_load.HeartbeatThrottleLiveServerTests` has WORKERS = **30**. The 40 I quoted belongs to `HeartbeatThrottleConcurrencyTests`, a TransactionTestCase with no server.
- I missed `students.tests_post_grading_submission_lock.PostGradingLockLiveHTTPTest` (WORKERS = 12).

The ruled guard scans every class, so the fix covers all four:
- LoginLockoutLiveServerTests (30)
- HeartbeatThrottleLiveServerTests (30)
- UpdateAsyncConcurrencyLiveHTTPTest (12)
- PostGradingLockLiveHTTPTest (12)

Guard rule: backlog >= max(128, WORKERS).
- Why not WORKERS alone: a class need not declare its fan-out, and ThreadPoolExecutor's default is up to 32.
- Why the max: a class declaring more than 128 still needs more.
- A second test checks that the scan finds at least the four known classes, so the scan cannot go blind without failing.
- The scan is an AST walk to a fixed point over base-class names, so a subclass in another module is found too. It takes about 4.3 s and is cached, so it runs once.

Helper: `AutoGrader/testing/live_server.py`. `BurstWSGIServer(ThreadedWSGIServer)` sets `request_queue_size = LIVE_SERVER_BACKLOG = 128`, and `BurstLiveServerThread` uses it. Each class sets `server_thread_class = BurstLiveServerThread`. Nothing else in the four classes changes.

Counts from build_suite: guard 2; users + students 1935; sweep 49.

### Declared before the runs

- **R1 red** (guard added, helper not yet written): `AutoGrader.tests_live_server_backlog_guard`, 2 tests. Predicted **Ran 2, FAILED (failures=1)**.
  - test_every_live_server_class_holds_its_burst lists all four classes as "listen backlog 10 < required 128".
  - test_the_scan_finds_the_known_live_server_classes passes.
  - Log `06_red.log`.
- **G1 green** (helper written and set on all four): the guard plus the four modules (2 + 13 + 11 + 17 + 29 = 72). Predicted **Ran 72, OK (skipped=2)**, the same two skips as run 03. Log `07_green.log`.
- **PL1**: remove `server_thread_class = BurstLiveServerThread` from LoginLockoutLiveServerTests only; label: the guard. Predicted **Ran 2, FAILED (failures=1)**, naming only `users.tests_login_lockout.LoginLockoutLiveServerTests: listen backlog 10 < required 128`. Killed by test_every_live_server_class_holds_its_burst. Log `08_plant_pl1.log`.
- **PL2**: `LIVE_SERVER_BACKLOG = 10` in the helper; label: the guard. Predicted **Ran 2, FAILED (failures=1)**, naming all four classes with "listen backlog 10 < required 128". Log `09_plant_pl2.log`.
  - Each plant is restored from a saved copy and its sha256 checked.
- **N1 near**: `users students`, --parallel 2, count **1935**. Predicted **OK (skipped=2)**: the two RedisAvailability/CI_REQUIRE skips. Any other skip will be recorded as prediction incomplete. Log `10_near_apps.log`.
- **N2 guard sweep, SERIAL**: the 59 of the 66 standing guards that exist on beta (the same 7 staging-only labels are absent as in H-300), minus the 11 inside users/students (run in N1), plus the new guard. That is 49 labels, count **830**. Predicted **OK (skipped=1)**, the ssrf peer-introspection skip. Log `11_guard_sweep.log`. Labels:

    AutoGrader.tests_credscan
    AutoGrader.tests_h271_static_files
    AutoGrader.tests_network_guard
    AutoGrader.tests_student_classmates_guard
    AutoGrader.tests_student_feedback_guard
    AutoGrader.tests_submission_audience_guard
    ai_processor.tests_call_record_static
    ai_processor.tests_h2_grading_benchmark_guard
    ai_processor.tests_ssrf_guard
    audit.tests_history_guard
    audit.tests_route_coverage
    audit.tests_h273_query_filters
    billing.tests.test_license_admin_user_guard
    billing.tests.test_qa_console_guardrail
    billing.tests.test_qa_time_travel_guardrail
    billing.tests.test_renewal_guards
    AutoGrader.tests_cache_invalidation_coverage
    AutoGrader.tests_codederror_serialization
    AutoGrader.tests_management_commands_are_commands
    AutoGrader.tests_no_pii_in_logs
    AutoGrader.tests_no_playwright_at_import
    AutoGrader.tests_no_wildcard_invalidation
    AutoGrader.tests_reason_codes
    AutoGrader.tests_redis_test_isolation
    ai_processor.tests_grading_run_pipeline
    ai_processor.tests_h205_image_fallback
    ai_processor.tests_provider_block
    ai_processor.tests_reading_config
    assignments.tests_schema_extension
    classrooms.tests_course_roster_scope_sweep
    classrooms.tests_teacher_access_sweep
    AutoGrader.tests_h246_sentry_environment
    AutoGrader.tests_log_scrubbing
    AutoGrader.tests_sentry_scrubbing
    AutoGrader.tests_sentry_sends_no_variables
    AutoGrader.tests_h285_djoser_removed
    AutoGrader.tests_pip_audit_gate
    ai_processor.tests_h262_prod_image_dev_tools
    ai_processor.tests_ai_call_trace
    ai_processor.tests_grading_cache_key_v2
    ai_processor.tests_grading_config
    ai_processor.tests_grading_run_checker
    assignments.tests_task_results_carry_no_text
    billing.tests.test_allocation_anchor
    billing.tests.test_h28_stripe_budget
    billing.tests.test_logs_carry_no_email
    billing.tests.test_live_qa_clock
    billing.tests.test_price_drift_reconciliation
    AutoGrader.tests_live_server_backlog_guard

### Results (under flock)

- **R1 red** (`06_red.log.gz`): **Ran 2, FAILED (failures=1)**. test_every_live_server_class_holds_its_burst named all four classes as "listen backlog 10 < required 128"; the scan test passed. **As predicted.**
- **G1 green** (`07_green.log.gz`): **Ran 72, OK (skipped=2)** (real-AI opt-in, CI_REQUIRE_REDIS). **As predicted.**
- **PL1** (`08_plant_pl1.log.gz`): **Ran 2, FAILED (failures=1)**, 1 element: `users.tests_login_lockout.LoginLockoutLiveServerTests: listen backlog 10 < required 128`. Killed, **as predicted**. Restored, sha256 MATCHED 2ee9f106b7c22e2c.
- **PL2** (`09_plant_pl2.log.gz`): **Ran 2, FAILED (failures=1)**, 4 elements, all four classes. Killed, **as predicted**. Restored, sha256 MATCHED 1341a2eb045afb7e.
- **N1 near** (`10_near_apps.log.gz`, --parallel 2, 124 s): **Ran 1935, OK (skipped=5)**. **Prediction incomplete**: I declared 2 skips. The other 3 are opt-in live-network tests (2 "live Google contract tests are opt-in", 1 "Live network check is opt-in"), all CI_REQUIRE_NETWORK. No failures.
- **N2 guard sweep** (`11_guard_sweep.log.gz`, serial, 101 s): **Ran 830, OK (skipped=1)** (ssrf, CI_REQUIRE_NETWORK). **As predicted.**

**Standing guard list: `AutoGrader.tests_live_server_backlog_guard` becomes label 67** (SM ruling). It is on beta only until beta is merged into staging, where guards66.txt gains it.

Ready for the SM. The SM asks the owner for the beta go. No push from Lane 2.

## Beta → staging merge (2026-11-09)

The SM pushed beta as a fast-forward: origin/beta = 50183be49 (was e4a893197). Branch `task/staging-merge-lockout` was made off origin/staging bc7dd7a89 and `git merge --no-ff origin/beta` produced merge **b9c594bff** with no conflicts: 23 files, +672, all test, testing-helper or evidence files. Staging has the same four LiveServerTestCase classes as beta (grep), so the helper covers them all.

### Declared before running (counts from build_suite().countTestCases())

- **S1 near apps**: `users students`, --parallel 2. 2048 tests. Predict **Ran 2048, OK (skipped=5)**: the same 5 as N1 (2 opt-in real-AI/Redis plus 3 CI_REQUIRE_NETWORK opt-ins).
- **S2 guard sweep**, serial: all 66 labels of guards66.txt (task/epic-b1b) exist on staging. The 12 inside users/students are covered by S1, so the sweep runs the other 54 plus label 67 `AutoGrader.tests_live_server_backlog_guard`, 55 labels. 873 tests. Predict **Ran 873, OK (skipped=1)** (ssrf, CI_REQUIRE_NETWORK).
- No plant: a merge with no conflicts and no staging-side edits to the merged files.

S2 labels:

    AutoGrader.tests_credscan
    AutoGrader.tests_h271_static_files
    AutoGrader.tests_network_guard
    AutoGrader.tests_student_classmates_guard
    AutoGrader.tests_student_feedback_guard
    AutoGrader.tests_submission_audience_guard
    ai_processor.tests_call_record_static
    ai_processor.tests_h2_grading_benchmark_guard
    ai_processor.tests_ssrf_guard
    audit.tests_history_guard
    audit.tests_route_coverage
    audit.tests_h273_query_filters
    billing.tests.test_license_admin_user_guard
    billing.tests.test_qa_console_guardrail
    billing.tests.test_qa_time_travel_guardrail
    billing.tests.test_renewal_guards
    classrooms.tests_e1_grade_guard
    AutoGrader.tests_cache_invalidation_coverage
    AutoGrader.tests_codederror_serialization
    AutoGrader.tests_management_commands_are_commands
    AutoGrader.tests_no_pii_in_logs
    AutoGrader.tests_no_playwright_at_import
    AutoGrader.tests_no_wildcard_invalidation
    AutoGrader.tests_reason_codes
    AutoGrader.tests_redis_test_isolation
    ai_processor.tests_grading_run_pipeline
    ai_processor.tests_h205_image_fallback
    ai_processor.tests_metering_chokepoint
    ai_processor.tests_provider_block
    ai_processor.tests_reading_config
    assignments.tests_schema_extension
    billing.tests.test_wallet_save_guard
    billing.tests.test_zero_credit_registry
    classrooms.tests_course_roster_scope_sweep
    classrooms.tests_teacher_access_sweep
    AutoGrader.tests_h246_sentry_environment
    AutoGrader.tests_log_scrubbing
    AutoGrader.tests_sentry_scrubbing
    AutoGrader.tests_sentry_sends_no_variables
    AutoGrader.tests_h285_djoser_removed
    AutoGrader.tests_pip_audit_gate
    ai_processor.tests_h262_prod_image_dev_tools
    ai_processor.tests_ai_call_trace
    ai_processor.tests_grading_cache_key_v2
    ai_processor.tests_grading_config
    ai_processor.tests_grading_run_checker
    assignments.tests_task_results_carry_no_text
    billing.tests.test_allocation_anchor
    billing.tests.test_h28_stripe_budget
    billing.tests.test_holds_entry
    billing.tests.test_holds_single_call
    billing.tests.test_logs_carry_no_email
    billing.tests.test_live_qa_clock
    billing.tests.test_price_drift_reconciliation
    AutoGrader.tests_live_server_backlog_guard

### Results (under flock)

- **S1 near apps** (`12_staging_near_apps_aborted.log.gz`): **no result; the run aborted**. **Prediction wrong.** The cause is the harness, not the code. `--keepdb` reused the parallel clone DBs test_lockout_live_reset_1..4, which still had beta's schema; the log reads "Using existing clone". Django migrates the main test DB under --keepdb but never rebuilds an existing clone, and staging has 15 migration files beta lacks. The result was 490 × `column billing_creditwallet.reserved_credits does not exist`, then a MaybeEncodingError (an unpicklable MagicMock in a failure result) that killed the runner.
- **S2 guard sweep** (`13_staging_guard_sweep.log.gz`, serial, 142 s): **Ran 873, OK (skipped=1)**. **As predicted.** It ran serially on the main test DB, which --keepdb did migrate.

### S1b, declared before rerunning

The four stale clones were dropped (0 connections; own test DBs). The run is the same as S1: `users students`, --parallel 2, with Django recloning from the migrated main DB. Predict **Ran 2048, OK (skipped=5)**.

- **S1b near apps** (`12b_staging_near_apps.log.gz`, --parallel 2, 126 s): **Ran 2048, OK (skipped=5)**. **As predicted.** (Django prints "Using existing clone" under --keepdb even when it has just created the clone; all four had been dropped.)

**Merge b9c594bff is ready for the SM's staging push.** Label 67 `AutoGrader.tests_live_server_backlog_guard` now exists on staging as well as beta. No push from Lane 2.
