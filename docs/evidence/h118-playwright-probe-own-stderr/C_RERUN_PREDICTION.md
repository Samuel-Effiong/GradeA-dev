# H-118 (c): what I expect of a second run, written before asking for it

d5, 2026-10-05, about 17:15.

The first (c) on 7c2a55f3 (17:01:50 to 17:10:00) had one failure, a
wall-clock assertion in ConcurrentRenderingTest: the slowest healthy
render took 7.2 s against a limit of 4.0 s.

What I observed
- The same 628 tests took 128.8 s on 2026-10-05 12:10 (D1, H-91's tip,
  same form, same worker count) and 372.9 s in this run: 2.9 times as
  long.
- One minute after the run ended the load average was 19.2 / 21.6 / 16.0
  on 8 cores. The 15-minute figure covers the whole run. At that moment
  the load was a Vezi pytest with its own headless Chromium, a Vezi
  development server and a Vezi front-end server (LOAD_AT_RED.txt).
- The test passed in (a) on this tip at about 16:08 (serial) and in D1.

What I infer, and cannot show from this run alone
- The machine was heavily loaded for the whole run, and the failure is
  the 4-second threshold missed under that load. The six healthy
  renders took 5.6 to 7.2 s; that is also what the old defect the test
  guards (renders pinned to the hung one's 5 s) would look like, so the
  numbers alone do not tell the two apart.
- H-118 changes where the import-time probe runs. It touches no
  renderer code. I see no path from it to this test, but "I see no
  path" is not evidence.

The check that tells them apart: the same (c), same tip, same form, at
a time when no Vezi test is running, with the load average recorded at
the start and the end by the script.
- If load was the cause: about 130 to 170 s of tests, 628 OK, load
  average at the start well under 8.
- If it fails again on a quiet machine: it is not load. Then I stop and
  compare with the same test class on beta 63c3da22 before touching
  anything.
- If the machine is not quiet at the start (load average over 8), the
  script does not start the run at all. (0b asked for a lower bar before the
  run: the script now refuses over 4, as Gate 10 started under 4.)
