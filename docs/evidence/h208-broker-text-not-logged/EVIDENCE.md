# H-208: log lines about a failed broker call carry the class and no text

Author: Hardening Engineer (d5). Base: beta `8567a7a0`. Gated tip: `994febe1`. Written 2026-10-09 from the files in `gate_files/`; the clock times are read from the logs. Verifier 1 read the test diffs before each run after the first; Verifier 2's judgment of mutants M1, M2 and M12 in his own slot is still to come and is NOT in this note.

## What was wrong
Verifier 2's probe on the score-printing stack showed that when the queue refuses a task, the line `mark_processing_task_failure` logged carried the broker's own error text and a traceback (`exc_info`). A broker error's text can carry the broker's address, and an exception's text in general is whatever the code that raised it wrote. The same shape was found in `cancel_processing_task` (the revoke line), `normalize_processing_task_status` (the state-read line), `AutoGrader.dispatch.safe_delay`, and the grading path's `_dispatch_followups` (which logged the chained cause).

## What changed
- `AutoGrader/safe_logging.py` (new): `describe_error_for_log(error)`. A broker outage (`BROKER_UNAVAILABLE_ERRORS`, also when the error that reaches the line is a wrapper raised EXPLICITLY `from` one) is named by the class of the broker error alone: `error=ConnectionError`. Any other error is named by its class and its stack FRAMES only (`file:line:function`, from the traceback, no message, no local value): `error=RuntimeError frames=...`. The chain walk follows `__cause__` only (ruling of the Senior Manager, 17:32): an error raised WHILE a broker error was being handled is described as itself and is not hidden behind "broker unavailable".
- The five lines use it, with no `exc_info`: `students/task_tracking.py` (failure line, revoke line, state-read line), `AutoGrader/dispatch.py` (`safe_delay`), `students/services.py` (`_dispatch_followups`, which also now names the submission id).
- Tests: `students/tests_broker_text_not_logged.py` (new, 13 tests) and `students/tests_task_tracking.py`; two existing tests changed because they pinned the old traceback line (below).
- Another road to error reporting: Sentry's LoggingIntegration still turns these ERROR lines into events, but they are now MESSAGES (class, ids, frames) and no longer exception events with a stack object. For the lines that swallow (revoke, state read, safe_delay, the follow-up dispatch) the line is the only report, which is why a non-broker error keeps its frames. `mark_processing_task_failure`'s callers re-raise (read by Verifier 2), so a real fault still reaches Celery/Sentry as an exception.

## Design fact (named, by the Senior Manager's request)
`BROKER_UNAVAILABLE_ERRORS` contains the BUILTIN `TimeoutError` and `ConnectionError`, so at every use a non-broker builtin one is logged by class without frames. On Python 3.12 (the project's) `socket.timeout`, `asyncio.TimeoutError` and `concurrent.futures.TimeoutError` are that builtin. Read in the repo: `assignments/pdf_renderer.py` catches the future timeout and raises `PDFRenderError`; `billing/live_qa/concurrency.py` builds one in a QA tool; openai/httpx/requests timeouts are not the builtin and keep frames. Not checked: what third-party libraries raise internally. No new effect for the user (`AutoGrader/error_messages.py` already maps the builtin to the "timed out" text). Full note: `gate_files/design_fact_timeout.md`.

## Gates
Tip `994febe1`, runs of 2026-10-09. Scripts in `gate_files/scripts/` (`.txt`), logs gzipped.

| Step | Result |
|---|---|
| Small slot (`small.sh`: billing refusal handling, task tracking, the row's module), 17:37:16 | Ran 56, OK |
| (r) at the base, the two test modules from the tip, 17:37:44 | red set as written, each for its written reason (rule 22); control L2 green by design |
| (a) 629-test module list incl. the cache test (rule 20) and the guard list, to 17:41:21 | OK, exit 0 |
| (b) 12 mutants, to 17:42:37 | 12 of 12 KILLED, restores verified |
| (c) `c_h208.sh`, seven apps, 17:55:18 to 18:07:21 | Ran 6100 in 689.7 s, OK (skipped=26), stalled=0 |

## The five chain runs, plainly
This row was gated by five chain runs, none of them clean the first time. Every stop is told, with its cause and its logs (`gate_files/stops/`, `gate_files/run4_d067e6d6_gated.tar.gz`).
1. `439c24f0`, 13:23:26: stopped at (r). My log-capture helper sat on the root logger; the `students` logger has `propagate=False`, so nine tests saw no records. Test fault; fixed in `84db38df`.
2. `84db38df`, 13:26:23: (r) passed, stopped at (a). `billing.tests.test_refusal_handling` had an existing test pinning the OLD behaviour (a transient failure logged with its traceback). I had searched `students/` but not `billing/` for old expectations before the run.
3. `1281e193`, 17:03:50: (r) passed, stopped at (a). My amended billing test used the builtin `TimeoutError` to ask for frames; it is in the broker list, so the code correctly gives the class alone. The test was wrong, not the code (the Senior Manager noted his own 13:30 ruling asked for that named frame without tracing it).
4. `d067e6d6`, 17:23:15: (r), (a) and (b) green (all 11 mutants killed); the chain stopped because mutant M1's failing set differed from the one I wrote: test H1 passed under M1. H1 built the broker error without raising it, so it had no traceback and "no `.py` in the line" was satisfied by an empty frames part. The Senior Manager ruled: the chain stands, the difference is disclosed, H1 is strengthened (`9fb99a09`: the error is raised inside a named function, the test asserts first that it has that frame). M1 itself was killed by two other tests.
5. `994febe1`, 17:37:44 (after the Senior Manager's ruling that the helper follows `__cause__` only, and F1 asserting on the follow-up line's own text): (r), (a) and (b) green, 12 of 12 mutants killed; the chain stopped by its expected-set check on three differences, all in MY written expectations:
   - M1 and M2 were also killed by F1 (`TheGradingFollowUpLine`). The follow-up line logs the wrapper `ProcessingTemporarilyUnavailable` raised `from` the broker error; with the broker branch off (M1) or the cause not followed (M2) it reads `error=ProcessingTemporarilyUnavailable frames=services.py:713:_dispatch_followups <- task_tracking.py:79:launch_processing_task`, and F1's `self.assertIn("ConnectionError", services_text)` (line 355) fails. That is F1 doing its new job; I had not re-traced the mutants' sets against F1 after strengthening it.
   - M12's reason check printed WRONG REASON for L5 and H3. My checker read `logs/M12.log` (the runner's condensed file: names only) instead of `logs/raw/M12.out` (the failure bodies). The bodies carry the written reason exactly (`'error=RuntimeError' not found in '... error=ConnectionError'`, assertion `self.assertIn("error=RuntimeError", text)` at lines 262 and 408), and M12's failing set `{L5, H3}` matched.
   The Senior Manager accepted this account and allowed `c_h208` on the same tip. The gate file `expected_kills.py` (`eccb59f38e8c6afd`) and the logs are untouched; the post-hoc file `expected_kills_posthoc_994febe1.py` (written AFTER the run, `979ed877a0e9eda6`) has M1 `{L2,L4,H1,H4,F1}`, M2 `{L4,H4,F1}` and reads `raw/`; its output (`posthoc_check_994febe1.txt`) shows all twelve sets as expected.

Lesson, one sentence: every expected set and every reason is re-traced after ANY test is strengthened, before the slot; and a checker is run against a known log before it is used as a gate.

## Limits, said now
- Verifier 2 has not yet judged M1, M2 and M12 in his own slot; this note does not claim it. His reading is also to cover every assertion of the row for "satisfiable by an empty or missing value".
- The `Captured` helper adds its handler to the loggers that exist (root and every non-propagating one) when a test starts; a logger created in the middle of a test is not covered (Verifier 1's limit).
- Verifier 1 hand-traced the new tests and F1 against the 12 mutants, not the older tests again and not the runner's apply and restore.
- The five lines no longer carry the error's text; an operator who needs the text reads it where the error is raised or re-raised (the task result in the failure path of `mark_processing_task_failure` keeps the user-facing message). The grading follow-up dispatch swallows everything: a non-broker fault there is located by its frames only.
- Not read: production log handling and retention, external monitors, what Sentry does with these messages in the dashboard (grouping changes from exception events to messages).
- Worktree (rule 21): `Grade-Automator-Plus-h208-broker-text-not-logged`; H-209 is stacked on this branch and has its own worktree.
