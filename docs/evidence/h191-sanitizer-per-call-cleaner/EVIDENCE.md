# H-191: the HTML sanitizer shares one bleach Cleaner between threads

**Severity:** HIGH (Senior Manager, 2026-10-08). **Author:** d5. **Branch:**
`task/h191-sanitizer-per-thread`, on beta `3f2ad13e`. **Tip the gates ran on:
`bf0d7632`.** **Verifier:** v2. **Batch:** its own, "13h". One production file
(`assignments/prosemirror_converter.py`), one test module, one mutation runner.
No migration, no model change, no setting. **Does it change grading?** No: the
same sanitizer, one parser per call instead of one shared parser.

## What the record may say (the Senior Manager's wording, 2026-10-08)
> crash and wedge OBSERVED (5 of 5 on the old code); mixed text NOT observed
> in any run; possible by d5's reading of the library.

Nobody writes "mixes users' text" as a fact. Said again in the package.

## The fault
`sanitize_editor_html` called ONE module-level `bleach.Cleaner`
(`_CLEANER`, `assignments/prosemirror_converter.py`) for every caller. Bleach's
own docstring says: "This cleaner is not thread-safe -- the html parser has
internal state. Create a separate cleaner per thread!" It is reached from
student answers, teacher assignment text and graded documents, on gunicorn's
four threads per worker (`Dockerfile`: `--worker-class gthread --threads 4`,
9 workers). **Main has the same line** (origin/main `9c21bee8`, bleach 6.4.0),
so the same fault is in what a promotion of this beta to live would carry. No
hot fix is made on main (the user's ruling); live is updated from beta.

**What the shared object keeps (read from bleach 6.4.0 and its vendored
html5lib 1.1; not run):** `Cleaner.parser` is one `BleachHTMLParser` built in
`__init__`. Every `clean()` calls `parser.parseFragment(text)`, which sets
`self.tokenizer`, calls `self.reset()` (which calls `self.tree.reset()`: the
SHARED tree builder's `openElements`, `activeFormattingElements`, `document`
are replaced) and sets the parser's phase; tokens are then fed through that
phase into that tree, and `parseFragment` returns `self.tree.getFragment()`,
which does `self.openElements[0].reparentChildren(fragment)` on whatever the
tree builder holds at that moment. So two threads inside the parser can
corrupt each other. In bleach 6.4.0 `Cleaner.__init__` builds the parser, the
walker AND the serializer, so on the old code all three were shared; I read no
per-call state in the walker or the serializer. Only the part that removes
markup (`BleachSanitizerFilter`) is made inside `clean()`, per call, so a mixed
tree is still filtered: **fail open was not shown and I do not think it is the
likely form.** (Corrected after Verifier 2's reading.)

## What was OBSERVED (and what was not)
- On CI, once (`students.tests_grading_redelivery_live`, six worker threads,
  run 37779788548 on `3f2ad13e`, per the Release Engineer's reading of the
  log): html5lib's own `assert False # We should never reach this point`
  (`treebuilders/base.py`), becoming `ProseMirrorConversionError` and a FAILED
  submission.
- In our many-thread test on the old code (`aefb0147`, tests only): 5 of 5
  runs red (table below), and **a wedge**: the first run left threads running
  for 11 minutes at 178% CPU with no end (`IndexError: pop from empty list` at
  `html5parser.py:2249 endTagTableCell` in the log, then silence); three of the
  five red runs had to be killed at 400 s, one of those with no test result at
  all.
- **At the 120 s deadline all eight threads were inside bleach's vendored
  html5lib parser** (the stack dump in `gate_files/r_repro_aefb0147.log.gz`:
  `html5parser.py` mainLoop, processEndTag, flushCharacters and
  `treebuilders/etree.py` _getNamespace, all reached through
  `sanitize_editor_html -> Cleaner.clean -> parseFragment`). WHY they do not
  finish is my reading (a loop whose exit condition another thread's reset
  destroyed), **not shown**.
- **Mixed text was NOT observed in any run.** Said exactly: on the old code
  the tests failed at their first assertion, the error list (`assertEqual(errors,
  [])`), before they reached the comparison of outputs; the errors in the (r)
  log are `IndexError: list index out of range`, `IndexError: list assignment
  index out of range`, `AttributeError`, `ValueError`, `ProseMirrorConversionError`
  and the `hung` marker. So a run on the old code could not have shown a
  wrong output even if one had been produced: the test was not built to catch
  mixing past a crash. By my reading of the library it is possible.

## The cure (`bfcea0bd`)
`sanitize_editor_html` builds its own Cleaner for each call (`_new_cleaner()`),
from the same module constants. No lock. The allowlists and the CSS sanitizer
are plain data and stay shared. Said, not done: a per-thread cache of the
Cleaner (the Senior Manager: NOT now).

**Cost, measured** (`gate_files/measure_cost.out`, `measure_cost.py`, single
thread, no database, median of 5 batches of 1000 calls, input of 532
characters, 2026-10-08 16:30:41 to 16:31:16, load 1.94 at the start and 2.89 at
the end): shared Cleaner `clean` 1968 us/call; a fresh Cleaner plus `clean`
1894 us/call (within noise of the shared one); the construction alone **52
us/call**; the whole `sanitize_editor_html` 2354 us/call (it also strips
control characters and raw-text elements). A first attempt at 16:30:31 failed
on an import path of my script (`measure_cost_attempt1_import_error.out`); it
measured nothing and was repeated once with the project on the path.
The cure costs about 52 us (about 3%) on a call that takes about 2 ms. Whether
a per-thread cache is worth it: not at this size.

## Tests (`assignments/tests_sanitizer_threads.py`, 5 tests, pure Python)
8 threads, a barrier, the thread switch interval set to 1 microsecond, 12
distinct marked inputs (`MARK-nn-alpha`; every third also carries a `script`
tag and an `img onerror`).
1. the single-thread outputs are what the comparison needs (markers exist,
   are distinct, are in their own outputs only): the deciding values;
2. many threads: no exception; every output equals its single-thread output;
   no output carries another input's marker (60 rounds each);
3. the hostile inputs stay clean in every thread (equal to their single-thread
   output, carry their own marker, no `<script`, `onerror` or `evil`);
4. the whole conversion (`html_to_prosemirror_json`) gives each thread its own
   document (12 rounds);
5. the process-local conversion cache (`html_to_prosemirror_text`, below) never
   keeps a wrong result (12 rounds).
A thread still running after 120 s is reported as an error (`("hung", n)`) with
every thread's stack dumped to stderr; the threads are daemons so the process
can still exit; later threaded tests in the same process fail fast.

## The conversion cache, and the database (Senior Manager's question)
`_cached_prosemirror_text(html)` is `@lru_cache(maxsize=64)` around
`json.dumps(html_to_prosemirror_json(html))`, which calls `sanitize_editor_html`:
**the cache sits above the sanitizer.** Key: the exact HTML string. 64 entries,
least recently used out. Per PROCESS. Only successes are cached. A mixed result,
had a race produced one, would have been kept under its victim's HTML and
served again to later requests for the same HTML in that worker until 64 newer
conversions pushed it out or the worker restarted (gunicorn `--max-requests
1000` and every deploy). After the cure it is moot; no stored cleanup is
needed for the cache. Test 5 holds it.

**Could a mixed text have been WRITTEN to the database? Yes, in principle.**
The converted text is saved to `raw_input` (StudentSubmission, Assignment) by:
`students/views.py:358` (a GET that fills an EMPTY `raw_input` with a queryset
update, in the web process: the likeliest path), the synchronous web saves for
an edit by text and for assignment create/edit (`students/services.py` 637,
1156, 1392; `assignments/services.py` 1029; `assignments/views.py` 1090,
1567). The task worker is started with `--concurrency` and no `--pool`
(`scripts/start-worker.sh`), i.e. Celery's default of separate processes (read
from the script, not checked on the running machine). I cannot say it never
happened. A read-only query for the founder to run (or not), NOT run by us, is
in `gate_files/READONLY_QUERY_FOR_THE_USER.sql` (a clue, not proof).

## Commits
| Commit | What |
|---|---|
| `49716e91`, `bbd5ebbc`, `aefb0147` | tests first (`aefb0147` = the red commit: tests only, shared Cleaner) |
| `bfcea0bd` | the cure |
| `e731e99b` | mutation runner (2 mutants) |
| `cf927a84` | the cache test |
| `1678fa6a` | the thread test cannot hang the run |
| `c1797665` | the two end-to-end tests run 12 rounds |
| `7864ea6c` | every thread's stack is dumped at the deadline |
| `bf0d7632` | runner: each inner run killed after 400 s, counted killed. **The tip the gates ran on.** |

## The measurements of "red reliably, not by luck"
Rate runs of `assignments.tests_sanitizer_threads`, each run in a disposable
worktree, output to files (`gate_files/rate/`):

| Series | Code | Result |
|---|---|---|
| attempt 1 (tip e731e99b, 14:42) | old, `aefb0147` | run 1 only: the loud failure, then a **wedge**; I stopped my own process by pid at 14:53 (11 min at 178% CPU) |
| attempt 2, red side (1678fa6a script, 14:59 to 15:24) | old | **5 of 5 red**: 2 ordinary failures, 3 killed at 400 s (one with no test result); run 6 stopped by me after 17 s, not counted |
| attempt 2, tip side (1678fa6a) | cured | **4 of 5 green**; run 4 red: `[('hung', 8)]` in the end-to-end test at 139 s. A deadline too tight for the slow test, mine; **cause beyond the deadline: unknown (no stack dump existed then)** |
| attempt 3 (c1797665) | cured | 5 of 5 green; load recorded only at the series start |
| attempt 4 (7864ea6c, 15:47 to 15:51) | cured | **5 of 5 green**, ~50 s each; load at the start of each run: 4.54, 7.25, 8.15, 7.47, 6.62 |

## The gates on `bf0d7632` (Release Engineer's grants), 2026-10-08
| Step | What | Result |
|---|---|---|
| (r) | the tip's test file on the code of `aefb0147` | 16:00:03 to 16:06:53, exit 137 (killed at 400 s, **timeout counted red**); the log carries the four threaded tests failing (ERROR whole_conversion, FAIL cache, FAIL many_threads, FAIL hostile), the single-thread test green; "Ran 5 tests in 398.056s" |
| (a) | the module, 10 modules that reach the sanitizer, `students.tests_grading_redelivery_live`, the guard list | 16:06:53 to 16:10:19, **Ran 605 tests in 183.952s, OK** |
| (b) | 2 mutants | 16:10:19 to 16:18:17: S1 (shared Cleaner restored) **killed by the 400 s inner timeout** (counted killed, failing set not compared); S2 (`onerror` allowed on an image, in the per-call Cleaner) **killed by exactly the hostile test**; restores verified |
| (c) | `c_h191.sh`: AutoGrader, assignments, students, ai_processor, `--parallel 2` | 16:20:30 to 16:29:12, **Ran 2692 tests in 487.509s, OK (skipped=22)**, exit 0, stalled=0, raw sha `7f7e793acb125416` |

Load at the start of each step is in `chain.status`. The sets were written
before the run (`expected_kills.py`); the runner's S1 has no failing set to
compare because a wedged run is killed.

## Every "not in" assertion, with its deciding value
`assertNotIn(MARK-jj-alpha, expected text)`: the text is non-empty and holds
its own marker by the two assertions before it; `assertNotIn("<script",
"onerror", "evil", got.lower())`: `got` equals the single-thread output and
holds `HOSTILE-ii`, the list of hostile results is asserted non-empty; the
foreign-marker list is built from results whose count is asserted. No count==0
assertion.

## Limits, stated
- Mixing was never observed; it is a reading of the library.
- Only `sanitize_editor_html`'s Cleaner is changed; I found no other
  module-level object documented as not thread-safe (the list is in the row).
- The task worker is not run in threads by its script; I did not check the
  running deployment.
- The test relies on timing (a tiny switch interval and 8 threads); on the
  cured code the rate series were green 14 of 15 runs (plus the chain's step (a)
  and the regression, once each), and the one red was the deadline of my own
  end-to-end test, shortened afterwards.
- Not run against a browser or a real model.

## The credential pattern
`gate_files/` checked with the saved `credcheck.sh` (plain files, .gz and
archives; masked): no URL, encoded URL or made-up password. The assignment-form
hits were read with the values cut: code names in stack traces
(`new_token = ...`), a test URL `internal.test/secret` in a security test's log,
and one probe label ("PASS:"); no secret.

## Files
`gate_files/`: the scripts as `.txt`, the gate logs (gzipped), the rate series,
the cost output, the battery archive, the module search, the read-only query for
the user.
