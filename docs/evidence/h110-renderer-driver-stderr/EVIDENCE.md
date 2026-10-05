# H-110: the PDF renderer's Playwright driver shared the service's stderr

Author: ed (Security), 2026-10-05. Branch `task/h110-renderer-driver-stderr`, off
`task/beta-batch-7` 27b0d2e0 (first written on c823cdca; base update 80599da2). For the bundle after bundle 7 (SM), and before the renderer is
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

Frozen tip b8e9200e (base `task/beta-batch-7` 27b0d2e0), one grant from 0b, 2026-10-05 15:35:51 to
15:46:44, one process at a time, 6G scope, rules 12, 13, 16, 17 and 18 (every run wrote straight
to a file, stdin from the null device, nothing piped). No run was stopped, repeated or failed
outside what step 0 expects. Nothing ran before this grant except commit hooks and `mutate.py
--check`.

| Step | What | Result | Log |
|---|---|---|---|
| 0 | Reproduce-first: `tests_pdf_renderer_driver_stderr` on the renderer as at 27b0d2e0, own DB | exit 1 as expected: Ran 5, FAILED (failures=3), the three fresh-interpreter tests; the two pin tests pass there | `prefix_unchanged_renderer_failing.txt` |
| 1a | `makemigrations --check --dry-run` | exit 0, No changes detected | `makemigrations_check.txt` |
| 1 | The two new modules, the seven caller modules, 21 guard modules | exit 0: Ran 426 in 296 s, OK (skipped=8) | `modules_and_guards.txt` |
| 2 | 19 mutants on `assignments/pdf_renderer.py`, own DB (dropped afterwards) | 19 KILLED, 0 SURVIVED, 0 BROKEN | `mutation_log.txt`, `mutation_results.json`, `mutant_logs/` |

Step 1's eight skips are all `tests_load` ("load tests are opt-in: set RUN_LOAD_TESTS=1"). None
is a Chromium skip: the fresh-interpreter tests ran.

### The owning-app regression (`assignments`)

Frozen tip 59b844c4 (the gated tip b8e9200e plus evidence only), same base 27b0d2e0, its own
grant from 0b, 2026-10-05 23:56:29 to 2026-10-06 00:01:31. One run, serial, 12G scope, the
full-suite lock, timeout 3600, rules 12, 13, 16 and 18. Not stopped, not repeated.

| What | Result | Log |
|---|---|---|
| `manage.py test assignments` | exit 0: Ran 655 tests in 263.867s, OK (skipped=13) | `regression_59b844c4.log.gz` |

- The log is committed whole, gzipped and byte-exact: 1,053,058 bytes and 12,466 lines unpacked,
  sha256 `0ae51a79f7c66f3b84042e270c3b62f8f4b5348d33ebad8337aaa89954bc6c18`. "Ran" is line 12438
  and "OK" line 12440; the 26 lines after them are the load tests' buffered standard output, so
  the file does not end with "OK".
- The 13 skips are all opt-in: 8 "load tests are opt-in: set RUN_LOAD_TESTS=1", 1 "set
  RUN_LOAD_TESTS=1 to build the 6,000-student school", 4 "Real AI call is opt-in and billed: set
  RUN_REAL_AI=1". None is a Chromium skip. No FAIL or ERROR line.
- Driver lines: 26 `[PDF]` lines, 0 of `[PDF] Playwright driver stderr:`, as in step 1.
- Load average: 3.06 8.58 11.43 at the start, 3.50 5.92 9.51 at the end (both printed by the
  gate script). 0b read a 1-minute figure of 5.77 at 23:59:57, during the run.

**The machine was not fully quiet.** The SM's ruling is that a run holding the renderer's
wall-clock test (`ConcurrentRenderingTest.test_one_slow_render_does_not_stall_the_others`,
H-123) goes only on a quiet machine. The grant was given on that footing, and the check before
the start found no other test run. After the run, the process list showed another project's
browser test on the same laptop: a Playwright run started 23:53:44, whose headless browser
started 23:59:00 and was still alive at 00:01:52. So it overlapped at least the last two and a
half minutes of this run. The wall-clock test passed all the same. A green under extra load says
no less about H-110 than a green on a quiet machine would; what it cannot be is a timing
reference. Whether the run stands is the SM's decision. Nothing of that other project was
touched; the process list was only read.

**What this run does not cover:** `task/beta-batch-8` has since taken H-118, which changes
`assignments/tests_pdf_renderer.py`. H-110 does not change that file, but its test module
imports `_CHROMIUM_AVAILABLE` from it. This run was on the base before H-118.

### How the mutants were counted

By the SM's rule of 2026-10-05 (rule 18): a mutant is KILLED only if its inner run shows its own
"Ran" line and named failing tests; a non-zero exit alone is not enough, because a runner that
exits silently is non-zero too. The battery itself judged by exit status alone (`mutate.py` as
at b8e9200e). The rule was applied afterwards to the fields the battery recorded, with no re-run:
all 19 have `ran` = 27 and at least one named failing test (1 to 8 each), and each mutant's own
log in `mutant_logs/` carries its "Ran 27 tests" line.

`mutate.py` was changed AFTER the battery to judge the three ways itself (SURVIVED, KILLED,
BROKEN) and to record the exit status. That is a tooling change; the results file here is the
battery's own output and was not regenerated.

The failing tests expected for each mutant were NOT written down before the run. The names in
`mutation_results.json` are what the run found.

### One log is not verbatim

`mutant_logs/M16_a_line_goes_to_the_log_past_the_record_factory.txt`: M16's failure message
quotes the test's made-up database URL, which has a password part (a stand-in word the test
builds from pieces, so no such URL is in the source). No URL with a password is committed, a
stand-in or a masked label included, so the whole URL reads `[the test's made-up database URL,
removed]` in the committed copy, in one line. Nothing else in the file differs. The untouched copy is outside the repository, mode 600:
`~/Documents/Projects/GAP-evidence-logs/h110_M16_full_b8e9200e.txt`. Every other log here is
as the run wrote it.

### Driver lines per render (the SM's question on a rate limit)

Step 1's log holds 26 `[PDF]` renderer log lines (the crash tests kill Chromium six times), so
the renderer's WARNING lines do reach that log. It holds 0 lines of `[PDF] Playwright driver
stderr:` and 0 of the pipe-not-taken ERROR. Across every render in the seven caller modules,
including the killed-browser cases, the driver wrote nothing to its stderr. The reader is
therefore left as built, with no rate limit: the per-line cut and the bound on an unfinished
line stay as the only limits. What this does not show: a driver that fails in a way these tests
do not cause (for example a Node crash with a stack trace) could still write many lines.
