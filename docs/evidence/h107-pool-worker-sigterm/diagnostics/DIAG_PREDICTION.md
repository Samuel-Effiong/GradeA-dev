# The second slow-pipe diagnostic: what I expect, written before either run

d5, 2026-10-05, about 14:15. Asked for by 0b: the arithmetic first, because
the first diagnostic (14:01, reader at one line per 2 ms) was approved by
both of us without it and did not fill the pipe.

## The form
Eight labels, `--parallel 2`, output through a pipe to a reader that takes
one line and then sleeps 100 ms. First on the UNFIXED runner (`085adecd`,
detached in the h78-repro worktree), then on the FIXED runner (`c3c10348`).
Both are stated exceptions to rule 18.

## What the run writes (measured in the 14:01 run, where nothing was lost)
- 2,582 lines, 205,405 bytes, about 80 bytes a line, over 131 seconds:
  about 20 lines a second on average.
- 186 of those lines are the parent's test result lines; the rest are
  worker log lines and tracebacks, and the database set-up lines.
- The busiest stretch is +80 to +90 s (the upload-batch tests), at 77 to 79
  lines a second.
- The first PDF renderer result is at +34 s. From then on Playwright's
  driver is alive in a worker, so the shared pipe is non-blocking (shown in
  H107_RECORD.md; observed in real services by ed).

## The reader
One line per 100 ms, plus the cost of a `date` per line: about 9.8 lines a
second, about half the run's average and an eighth of its burst.

## When the pipe fills
A Linux pipe holds 65,536 bytes, about 820 of these lines. Replaying the
14:01 run's own timestamps against a 9.8 lines/s reader: the backlog first
reaches the pipe's size at about **+80 s**, in the burst, 46 s after the
driver started. From then to the end of the tests (about +131 s) the pipe
is full or nearly full.

## What I expect of the unfixed control (085adecd)
- In that stretch the parent writes about **44** result lines into a pipe
  that is full and non-blocking. The first one that does not fit raises
  BlockingIOError out of Django's result loop.
- So: **no "Ran" line**, no summary. The parent starts to exit; its pool
  workers, mid-test, survive the terminate (finding A is unfixed there
  too); the reader drains what is in the pipe (about 800 lines, 80 s) and
  then the log is silent; the watchdog ends it 300 s later.
- Total: about 80 s to the fault, 80 s of drain, 300 s of silence: 7 to 8
  minutes. An expected stall, kept, not reportable (SM).
- If it prints "Ran 139 tests … OK" instead, the form still does not fill
  the pipe at a moment the parent writes, and the fixed run would prove
  nothing: I stop and RELEASE.

## What I expect of the fixed run (c3c10348)
- "Ran 139 tests in N s", OK, and exit 0.
- **N will NOT stretch to 4 or 5 minutes, and I should not have said it
  would.** That figure assumed every line waits its turn behind the reader.
  It does not: once the driver is alive, worker lines that do not fit are
  refused, not queued, and the parent only has to wait until the reader has
  freed room for its own short line, at most one reader tick (0.1 s) per
  line. About 44 such waits: N should be about 101 s plus a few seconds,
  **between 100 and 130 s**.
- So N is the wrong sign. The signs I propose instead, both readable from
  the logs without touching the runner:
  1. The control and the fixed run are the same form, and the control ends
     with no "Ran" line while the fixed run completes. That difference is
     the fix.
  2. In the fixed run the stamped log is well short of 2,582 lines: about
     **2,100** (131 s at 9.8 lines a second, plus the 820 that the pipe
     still holds when the tests end, read during the drain). A shortfall
     of some hundreds of lines means lines were refused, which only
     happens when the pipe is full and non-blocking, which is the
     condition under which the unfixed parent fails.
- What may also show, as limits and not failures of B (H-110's ground): a
  test that prints while the pipe is full would be that test's error; lost
  worker log lines are the shortfall itself.
- Total: about 131 s plus 80 s of drain: 3.5 to 4 minutes.

## If the SM wants a direct sign
The only direct observation that the parent waited would be a system-call
trace of its failed writes (EAGAIN on fd 1 or 2, then success). That needs
strace on the run, which changes its timing (it hid the stall on
2026-10-05 12:15). I do not propose it unless the two signs above are
judged not enough.
