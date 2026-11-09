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
