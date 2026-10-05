# H-110: the PDF renderer's Playwright driver shared the service's stderr

Author: ed (Security), 2026-10-05. Branch `task/h110-renderer-driver-stderr`, off
`task/beta-batch-7` c823cdca. For the bundle after bundle 7 (SM), and before the renderer is
promoted to main. MEDIUM. Verifier: 1a.

## The defect

`assignments/pdf_renderer.py` starts Playwright (1.58.0), and Playwright starts its Node driver
with the calling process's stderr as the driver's stderr (`playwright/_impl/_transport.py`,
`_get_stderr_fileno`: `sys.stderr.fileno()`, falling back to `sys.__stderr__`). Node sets a pipe
it is given non-blocking. That setting is the pipe's, not Node's, so it holds for every process
that shares the pipe: all of gunicorn's workers, or a Celery worker's parent and children. The
renderer keeps the driver alive for the life of the process.

Found by d5 as the trigger of the parallel test-run hang (H-107, `GAP-d5-runs/hang/H107_RECORD.md`).
Read by ed for production, then measured once in a real gunicorn (gthread) and a real Celery
prefork worker, each with a pipe the script owned as stderr (a script, not a test run; SM-approved;
output in `GAP-ed-scripts/h110/out_20261005_133926/`, summarised here):

| | Before any render | After ONE render |
|---|---|---|
| The pipe's mode, read from outside and inside | blocking | non-blocking, also 3 s later |
| A log line of 8000 bytes while the pipe is full | the writer waits (20 s in the run) until the pipe is read; nothing lost | `logger.error` returns at once, no exception. Celery: the line never arrived. gunicorn: it arrived cut, late, at worker exit |
| A bare `print` to stderr, pipe full | waits, arrives | gunicorn: raises BlockingIOError. Celery: swallowed (stdout/stderr are redirected into logging) |
| `os.write(2, ...)`, pipe full | waits, arrives | raises BlockingIOError in both |
| Which process was affected | | a different worker from the one that rendered, in both services |
| After SIGTERM of the service | | blocking again (the driver exits by itself and Node restores it) |

Not measured: how Railway wires stdout and stderr and whether its collector ever lets the pipe
fill (the SM has put that to the founder; nobody probes); more than one driver alive at once.
d5 demonstrated that a SIGKILLed driver leaves the pipe non-blocking for good.

main (9c21bee8) does not have the renderer. beta and staging do.

## The fix

The driver gets a stderr pipe the renderer owns; the process's fd 2 is never handed to Node.

- `_DriverStderr`: `os.pipe()` plus a daemon thread, `pdf-driver-stderr`, that reads the pipe into
  this module's logger: one WARNING per line, `[PDF] Playwright driver stderr: <line>`. Because it
  goes through the logger, a line is scrubbed by H-89's record factory like any other. A line is
  cut at 2000 characters, control characters are replaced, an unfinished line is held up to one
  pipe's worth (64 KiB) and then logged cut with the rest dropped. Nothing a line contains ends
  the reader; only the end of the stream does.
- The start (`_startup`): Playwright has no public option for the driver's stderr. For the length
  of one `async_playwright().start()`, under a module lock, the private
  `playwright._impl._transport._get_stderr_fileno` is replaced by a function that returns our write
  end; the saved original is put back in a `finally`. After the start the renderer closes its own
  copy of the write end, so the reader sees the end of the stream when the driver is gone.
- If Playwright did not ask for the pipe (the hook renamed or inlined), the renderer logs an ERROR,
  once per process, and goes on rendering with the old behaviour; the pin test fails first, at the
  upgrade. (SM ruling: downloads matter more than a MEDIUM log risk.)
- Shutdown closes the pipe after Playwright has stopped.

Agreed with d5 (author of H-107): the tests below, and that the fresh-interpreter test exists once,
here. d5's two alternatives for the hand-over were weighed: a `sys.stderr` stand-in for the length
of the start would redirect every other thread's stderr writes for that time; replacing the
Playwright module attribute touches Playwright only. The second is used.

### The trade (SM's wording)

The fix returns us to back-pressure: a blocked writer waits for the collector instead of losing
lines. With a full log pipe a request, or a whole Celery worker, waits until the collector reads.
That is how main behaves today. No bounded alternative is built.

### Limits

- A private Playwright function. Pinned by `PlaywrightStderrHookPinTest`; re-check on every upgrade.
- **H-107's fix and rule 18 are still needed after this change** (agreed with d5). Two places
  still start a driver on the calling process's own stderr, and any other child process that sets
  a shared pipe non-blocking would do the same:
  - `assignments/tests_pdf_renderer.py` runs `_chromium_available()` at import: a `sync_playwright`
    start and a real Chromium inside the importing process, which in a parallel run is the
    runner's parent at discovery. The shared pipe is non-blocking for that moment in every run
    that imports the module. It does not go through the renderer, so this change does not remove
    it. d5 has proposed moving the probe into a subprocess as a row of its own.
  - `ai_processor/benchmark/render.py`, an offline tool: not a service and not run by the suite.
    If the benchmark is ever run with its output piped, it has the same exposure.
- The driver's output is now IN the log (it used to go to stderr raw). It can carry URLs and page
  text; the scrubber removes addresses and URL credentials, not everything.
- Linux `/proc` is used by two tests (parent pid, open descriptors).

## Tests

`assignments/tests_pdf_renderer_driver_stderr.py`
- `TheProcessStderrStaysBlockingTest` (needs Chromium): the real renderer in a fresh interpreter
  whose stderr is a pipe the test owns. While the driver is alive the pipe is blocking; Playwright
  took our pipe and its hook is the original again; after SIGKILL of the driver (the test's own
  grandchild, checked by parent pid) the pipe is still blocking and the reader has ended.
- `PlaywrightStderrHookPinTest`: the hook exists and is what the driver's stderr comes from.

`assignments/tests_pdf_renderer_driver_stderr_reader.py` (no browser): the hand-over (inside only;
put back on failure; two starts cannot overlap; no hook, no crash), the pipe not taken (an ERROR once
per process and the renderer still starts; nothing said when it is taken), the reader (lines, split writes,
last line with no newline, blank lines, invalid UTF-8, control characters, cut, a line longer than
the pipe, a line that never ends, scrubbing, a 180 KiB burst with a slow logger, a logger that
raises), and descriptors (both ends closed, close twice, 25 starts leak none, the reader outlives
our write end while the driver has one).

Commits: 655bdf38 test first (fresh interpreter + pin), c03fd2b6 production, 2d9463d6 reader tests,
6d5ed3ff two cases the mutants needed, then the SM's condition that the not-taken ERROR is logged
once per process (all written before any run).

Callers: every test module that reaches the renderer is in the gate's first step (`grep -rl
pdf_renderer --include='test*.py'`: tests_download_pdf, tests_load, tests_pdf_cache,
tests_pdf_renderer, tests_pdf_renderer_gevent, tests_prerender, tests_renderer_crash, and the two
new ones).

## Mutants

`mutate.py`, 19, on `assignments/pdf_renderer.py`, rule 17 and rule 18. M16 is the scrubbing
mutant: the scrub itself is H-89's; what this change owns is that a driver line reaches the log
through the record factory, so the mutant builds the record past it.

## Runs

@@RUNS@@
