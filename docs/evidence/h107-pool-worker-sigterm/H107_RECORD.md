# H-107: why a piped parallel test run exits silently and then hangs

Author: d5, 2026-10-05. For the SM, 0b, ed and the verifier. Friday's
first record is `../h91/HANG_RECORD.md`; this one replaces its "lead".

Each statement below is marked **OBSERVED** (seen in a real test run),
**DEMONSTRATED** (shown by a small plain-Python script outside the suite)
or **INFERRED** (reasoned from the two, not seen directly).

## The runs

All on H-91's `b6fbdbea`, the same 16 labels, `--parallel 2`, unless said.
"Piped" means the run's stdout and stderr went through a pipe to my
timestamper (a bash loop).

| Run | Form | Result |
|---|---|---|
| 2026-10-02 17:41 and 17:45 | piped | stalled, twice |
| D1, 10-05 12:10 | `assignments` alone, piped | no stall, 628 OK |
| D2, 10-05 12:15 | piped, under strace (signals only) | no stall, 3878 OK |
| D2b, 10-05 12:32 | piped, nothing added | stalled at 12:43:33 |
| D4, 10-05 13:04 | piped, observe-only instrument on | stalled at 13:14:48 |
| H-91's (c), 10-05 13:26 | output straight to a FILE, nothing else changed | no stall, 3878 OK |

0b's Gate 10 and strict full runs write straight to a file and have never
stalled (0b, 2026-10-05).

## Finding B: why the parent leaves the result loop

- **OBSERVED (D4, `D4_full_p2_observe.observe`):** the test runner's
  PARENT process left Django's `ParallelTestSuite.run` with
  `BlockingIOError: [Errno 11] write could not complete without blocking`,
  raised in `unittest/runner.py` line 61, `startTest` ->
  `self.stream.flush()`, called from `django/test/runner.py` line 586
  (`handle_event`) and line 562 (`run`). The stream is the runner's result
  stream, which is the process's stderr. I saw the error in the parent
  only. I did not see an EAGAIN or BlockingIOError in a worker, and did
  not look for one.
- **OBSERVED:** nothing is printed when it happens: no traceback, no
  "Ran N tests" line. On 2026-10-02 the parent's exit code, once its
  workers were killed by hand, was 1.
- **OBSERVED (D2's strace):** no signal other than SIGCHLD reached the
  parent or a worker in a run that did not stall. Not a signal.
- **NOT OBSERVED:** the O_NONBLOCK flag on the pipe inside a real run. I
  never read it there. (0b suggests reading it with fcntl when the
  observer fires and around the PDF renderer's tests; not yet done.)
- **DEMONSTRATED (`node_child_leaves_shared_pipe_nonblocking.py`):** a
  pipe handed to a Node child as its stderr is in non-blocking mode, for
  every process that holds the pipe, while the child is alive. Shown for a
  plain Node child that writes to stderr and for Playwright's own driver
  started as Playwright starts it (`node cli.js run-driver`, stdin and
  stdout as its own pipes, stderr inherited). After the child is ended
  with SIGKILL the pipe is STILL non-blocking. After it is ended with
  SIGTERM the pipe is blocking again.
- **DEMONSTRATED (`node_child_clean_exit_restores_blocking.py`):** after a
  Node child that exits by itself, the pipe is blocking again.
- **Read in the installed Playwright (`playwright/_impl/_transport.py`):**
  it starts the driver with `stderr=_get_stderr_fileno()`, which is
  `sys.stderr.fileno()` of the calling process (falling back to
  `sys.__stderr__`).
- **INFERRED:** in the stalled runs the shared output pipe was left
  non-blocking by Playwright's driver, and a write by the parent to the
  full pipe raised. Supports: only `assignments` drives real Chromium; in
  both runs of 2026-10-05 the parent left the loop within 30 seconds of
  `assignments.tests_pdf_renderer` (13:13:56 to 13:14:17, exit at
  13:14:48); in D2's strace a worker sent SIGKILL to eight short-lived
  children during the same stretch of tests; strace, which slows the
  writers, hid the stall.
- **Which test starts the driver for real:** `assignments.tests_pdf_renderer`
  (`ChromiumRendererTest`, `ConcurrentRenderingTest` and its neighbours),
  the renderer's own tests, through the production class in
  `assignments/pdf_renderer.py`. I have not checked whether any other test
  reaches the real renderer without a stand-in.

- **OBSERVED (13:26 run):** the same tip, labels and worker count, on the
  same machine within the hour, with the test process's stdout and stderr
  a regular file and its stdin /dev/null (read from the running parent's
  file descriptors): 3878 tests, OK, no stall.
- **Corrected after ed's reading (2026-10-05):** Playwright's Python side
  stops its driver by closing the driver's stdin, and the driver then
  exits by itself, which restores the pipe. The hard kills in D2's strace
  are the driver's own, on Chromium processes. So in these runs the pipe
  was non-blocking because the worker's renderer keeps its driver alive
  for the rest of the run (still INFERRED), not because a driver was
  killed. "For good after SIGKILL" is true of a killed driver but is not
  what happened here.

- **OBSERVED BY ed, in real services (2026-10-05 13:39, not a test
  run; `~/Documents/Projects/GAP-ed-scripts/h110/out_20261005_133926/`,
  `REPORT.txt` read with `NOTES_ed.txt`):** a real gunicorn (gthread) and
  a real Celery prefork worker, each given a pipe the script owned as
  stderr. The pipe's flag, read from outside and inside: blocking before
  any render, non-blocking after one render and three seconds later,
  blocking again after the service stopped on SIGTERM. Playwright handed
  the driver fd 2 in both (in Celery through the `sys.__stderr__`
  fallback). It crosses processes: the write that failed was in a
  different worker from the one that rendered. With the pipe full and
  non-blocking, logging returns with no exception and the line is lost or
  cut; a print to stderr raises BlockingIOError in gunicorn and is
  swallowed in Celery; `os.write` to fd 2 raises in both. With the pipe
  full and still blocking (the control), the writer waits and loses
  nothing. So the flag, which I had only by demonstration, is observed in
  real services; it is still not read inside a test run. Not covered by
  ed's run: the SIGKILL case, and more than one driver alive at once.

## Finding A: why the exit becomes a hang

- **OBSERVED (three stalls):** at the stall the parent is in interpreter
  exit: multiprocessing's atexit hook -> `Pool._terminate_pool` -> `join`
  of a worker. Both workers are alive and idle in the pool's worker loop,
  waiting for the task queue's read lock.
- **Read in the standard library:** `_terminate_pool` takes that read lock
  and keeps it, then sends each worker SIGTERM.
- **Read in the repository:** `AutoGrader/redis_test_hygiene.py` installs
  a SIGTERM handler in the main process that raises SystemExit (from
  `d50aa744`, 2026-09-25; on beta `141c8031` and the epic). Forked workers
  inherit it.
- **DEMONSTRATED (`sigterm_mid_test_is_swallowed.py`):** a process with
  such a handler, signalled while a unittest test is running, stays alive;
  unittest records "SystemExit: 143" as that test's error.
- **INFERRED:** each worker was inside a test when the terminate arrived,
  swallowed it, finished the test, went back to the queue and blocked on
  the lock the parent holds. (In D2b one worker was in a long concurrency
  test and the other in the large-batch upload.)

## What is being done (SM rulings, 2026-10-05)

- A and B, test runner: `task/h107-pool-worker-sigterm`, frozen at
  `f3c7ee13`. A (`380b97db`, tests `e487b97a`): each pool worker starts
  with SIGTERM's default action. B (`f3c7ee13`, tests `de91a6a0`): the
  parent's result stream waits and retries when a write would block, and
  gives up with a clear error after 120 s of an unread pipe.
- Gates (rule 18): a gate's test output goes straight to a file; the
  timestamper and the watchdog read the file from the side.
- The cause at its root, H-110 (ed): the PDF renderer gives Playwright's
  driver a pipe of its own as stderr. `assignments/pdf_renderer.py` starts
  the same driver inside Celery workers in production; how stderr is wired
  there is ed's to read.

## Files

`D1_assignments_p2.log`, `D2_full_p2_strace.{log,strace,ps.txt}`,
`D4_full_p2_observe.{log,observe,ps.txt,tree.txt}`, `iso.status`,
`observe/sitecustomize.py` (the instrument), the three scripts named
above, and in `../h91/`: `c_apps_guards_b6fbdbea_1005.{log,tree.txt}`
(D2b), Friday's `hang2_*.txt`. The observe file's times are UTC, one hour
behind the logs.
