# H-167: an error report carries no frame variables and no request body

Author: Security Engineer (ed). Branch `task/h167-no-frame-variables-to-sentry`, from the pushed beta
tip d7143538 (the Release Engineer's choice: no row of batch 12 or 12b touches the three production
files). Beta line. HIGH (Senior Manager, 2026-10-07). First of my batch 13 rows.

**Everything down to the heading "Results" was written on 2026-10-07 before any run.** Nothing in it
was observed in a run, in a log, in a Sentry project or on a service. **[R]** = read by me in code:
ours, or the installed libraries' source at the pinned versions (sentry-sdk 2.68.0, redis 7.1.0,
kombu 5.5.4, celery 5.5.3). No log was opened, no settings value printed, no connection looked for.

## How it was found

The row was opened for one line: `AutoGrader/dispatch.py`, `safe_delay`, logs "Could not dispatch
task %s - broker unavailable" with the exception when the queue cannot be reached. The question was
whether that line can hold the queue's URL with its password.

- **The printed line cannot. [R]** Its message is the task's name; the client's own error text is
  "host:port" and the error (`redis/connection.py`, `_host_error`); a printed traceback shows lines
  of source, never a variable's value; and H-89's record factory replaces the user-and-password part
  of any URL in what is printed.
- **The copy sent to Sentry can. [R]** `settings.py` passes `LoggingIntegration(event_level="ERROR")`,
  so that ERROR line becomes an event. The call does not pass `include_local_variables`; the SDK's
  default is True (`sentry_sdk/consts.py`), and then every frame of the event carries its local
  variables as text (`sentry_sdk/utils.py`, `serialize_frame`). A frame of a failed publish is
  `redis/client.py` `_execute_command`, whose locals are the client and its pool; and
  `ConnectionPool.__repr__` (`redis/connection.py`) prints every connection keyword as `key=value`,
  the password among them (kombu's Redis transport passes it under `password`).
- **Neither scrubber catches that form. [R]** Ours knows an email address and a URL's
  user-and-password part; its docstring named `key=value` as a limit. The SDK's own filter goes by
  variable NAMES (`self`, `pool` are not on its list) and does not look inside a text.

## Why it is HIGH, and wider than the queue's password (Senior Manager)

With frame variables on, EVERY error event sends whatever the failing code held: a student's name,
an address, an answer's text, a token, and the temporary password that a queued email task carries
in its arguments (the Celery integration itself withholds a task's arguments when
`send_default_pii` is off **[R]**, but the same arguments are local variables of the task's frames
and of `safe_delay`'s). The queue's password is one instance.

A second road, found while reading the rest of the call **[R]**: `max_request_body_size` is not
passed; its default "medium" puts the failing request's parsed body, up to 10,000 bytes, in the
event (`sentry_sdk/integrations/_wsgi_common.py`). That does not depend on `send_default_pii`. The
SDK's filter blanks only keys on its list; and H-89's hooks left the event's `request` alone on
purpose. A failing add-by-email or registration sends the address and the names; a failing answer
upload sends what the student typed.

## What else the call sends, read item by item [R]

| What | Now | After this row |
|---|---|---|
| Frame variables | default True | **False** |
| Request body | default "medium" | **"never"** |
| Request URL, query string, headers | sent; hooks did not touch them | sent; now pass the scrub |
| Cookies, Authorization, the user's id and address, IP | only with `send_default_pii`; ours is False | unchanged |
| A task's arguments (`celery-job`) | withheld while `send_default_pii` is False | unchanged |
| SQL in breadcrumbs and spans | the text with placeholders; parameters only with a switch we do not set | unchanged |
| The log stream (`enable_logs=True`) | each record's message and each argument, through `scrub_log` | kept on (Senior Manager); the new pattern applies |
| Source lines around a frame | code, no values | unchanged |

## What changes

1. `AutoGrader/settings.py`, the one `sentry_sdk.init(...)`: `include_local_variables=False` and
   `max_request_body_size="never"`. Cost, accepted by the Senior Manager: a report no longer shows a
   frame's variables nor what was posted.
2. `AutoGrader/log_scrubbing.py`, `scrub` (used by the record factory for printed logs and by all
   three Sentry hooks): what follows `password=`, `passwd=`, `secret=` or `token=` becomes
   `[secret]`, in any case, also at the end of a longer name (`new_password=`), **to the end of that
   line**. The second defence, for text that reaches a log line or an event by another road. The
   old shortcut "a text with no @ is returned as it is" no longer skips this pattern (a pool's
   printed form has no @).
3. `AutoGrader/sentry_scrubbing.py`: the event's `request`, `tags`, `user` and `contexts` join the
   parts `scrub_event` scrubs (one line each). This reverses H-89's ruling that they be left alone;
   its test is changed in the tests-first commit and says so.

No model, no migration, no route, no change to what any request answers.

## Limits, stated

- **To the end of the line.** A value may hold a comma, a bracket or a space, so nothing shorter is
  safe; whatever follows the value on its line is lost with it (in a printed traceback, the rest of
  a source line such as `authenticate(email=email, password=password)`).
- **Other forms are not recognised:** `password: x`, a dict's `'password': 'x'`, a bare value, a
  secret under another name (`key=`, `auth=`, `dsn=`).
- **A value logged in another form passes the log stream** (`enable_logs` stays on).
- **A name or free text in a query string** is recognised by no pattern.
- **Past events.** If Sentry is on and an error has ever happened, events with variables and bodies
  are already there. That, and whether to change the queue's password, the Senior Manager puts to
  the user; nobody on the team looks into Sentry.
- **A server's installed SDK** may differ from the pinned one I read; I cannot see a server.
- Nothing here observes an event that was sent.

## For the live line (origin/main 9c21bee8) [R]

- main's `settings.py` (lines 153 to 178) has the same init call with `send_default_pii=False`, the
  same integrations and `enable_logs=True`, **without** `include_local_variables`,
  `max_request_body_size`, or any `before_send` hook. `AutoGrader/log_scrubbing.py` and
  `AutoGrader/sentry_scrubbing.py` are not on main: H-89 is not there.
- So, when main's DSN setting is set and its environment is prod or dev (not looked at), the live
  service sends frame variables and request bodies today, with no scrubbing of our own at all.
- **The smallest thing that could be approved for live:** the two keywords of item 1 alone, two lines
  in main's init call, no new module. By reading, that closes both roads there: the SDK reads a
  frame's variables in one place only (`serialize_frame`, behind that option; the profiler reads a
  frame only for a class's name), and a body is attached only when `request_body_within_bounds`
  says so, which "never" makes false for every length. This row's first two test classes
  (`TheInitCallTests`, `WhatTheSdkDoesWithOurCallTests`) need no module of ours and would apply to
  main as they are.
- **What the two lines do NOT give main:** items 2 and 3 and all of H-89 (an address or a URL's
  password in a message, an exception's text, a breadcrumb, the request's URL). For those main needs
  H-89's two modules and their wiring first, then this row's commits.
- This row's settings hunk sits beside `send_default_pii=False`, which main has in the same form, so
  the two lines can be taken by hand; the hunk as committed here also carries H-89's comment below
  it as context and would not apply to main by `git cherry-pick` without that.

## Tests

`AutoGrader/tests_sentry_sends_no_variables.py`, 23 tests, no database, no network; and one changed
test in `AutoGrader/tests_sentry_scrubbing.py`. Committed first, tests only.

- **The init call (3):** the two keywords read from the settings' source; `send_default_pii` still False.
- **What the SDK does with our call (4):** the SDK's default options overlaid with our call's literal
  keywords, given to the SDK's own `event_from_exception` and `request_body_within_bounds`. Two are
  CONTROLS on the bare defaults (a frame's variable IS in the event; a small body IS within bounds).
- **A pool's printed form (4):** a real `redis.ConnectionPool` with a made password (building one
  opens no connection) as a frame's variable text, in an exception's text, a message, an extra, a
  breadcrumb, a log item. One is a GUARD: the library still prints `password=<value>`.
- **The pattern (7):** each name; a text with no @; case and longer names; to the end of the line and
  no further; two lines; beside an address and a URL's password; text that only looks alike.
- **What a log line prints (2):** through the record factory, a message and an exception's text.
- **The rest of the event (3):** `request`, `tags`, `user`, `contexts`, together and one at a time
  with the deciding value asserted present first.

Made values are built at run time (three pieces, cut by a comma, a bracket and a space), so that no line of
the file and no failing test's source context holds one. The queue URL in the tests is built from
parts: no line holds a URL with a password part.

**Three of the 23 are controls on the libraries** and no change to our files can make them fail. They
are not evidence of the change; they keep the others from passing on a library that no longer
behaves as read. **Rule 19 does not hold for them and I do not claim it.**

**A weakness I named, closed before any run (Senior Manager, 2026-10-07 17:10):** as first committed
(42f97130) the tests of the pool, the log line and the rest of the event asserted only that the WHOLE
made value was absent, so a pattern that stopped early and left a tail would have passed them; only
the pattern tests compared whole texts. A made value now has three pieces, cut by a comma, a bracket
and a space, each at least ten characters; those tests look for each PIECE, and two of them compare
the exact text that remains (computed from the pool's own printed form, not written by hand). Tests
only, committed after the change and before any grant. Mutants G3 and G4 named six tests before;
they now name all thirteen.

## Written before the runs

Gate script `~/Documents/Projects/GAP-ed-scripts/run_h167_gate.sh`; its base argument is d7143538.

**0. Reproduce-first** (the new module and `tests_sentry_scrubbing` on the three production files as
at the base): **Ran 39, FAILED, 19 distinct tests red**, by name in the script and here:
- `TheInitCallTests`: frame variables off; no request body.
- `WhatTheSdkDoesWithOurCallTests`: no frame has variables; no body within bounds.
- `APoolsPrintedFormTests`: as a frame's variable text; in an exception's text and a message; in a
  breadcrumb and a log item.
- `TheNamedValuePatternTests`: all but "text that only looks alike".
- `WhatALogLinePrintsTests`: both.
- `TheRestOfTheEventTests`: all three.
- `tests_sentry_scrubbing.BeforeSendTests.test_user_context_tags_and_request_are_scrubbed_too`.

Green there, as they must be: the three controls, `send_default_pii` still False, "text that only
looks alike", and the other 15 tests of `tests_sentry_scrubbing`. The script halts if the Ran count
or the set differs.

**1a.** `makemigrations --check`: no changes.

**1. Modules and guards at the tip:** OK. No count written. Rule 20: no serializer's or cached
route's answer changes; `AutoGrader.tests_cache_bespoke_1114` is in the list all the same.

**2. Mutants: 19**, each KILLED with exactly the tests `mutate.py` names (`--check` passes). Inner
runs are the new module, `tests_sentry_scrubbing` and `tests_log_scrubbing`.

| Mutant | Must fail |
|---|---|
| S1 the variables keyword removed; S2 set True | frame variables off; no frame has variables |
| S3 the body keyword removed; S4 set "small" | no request body; no body within bounds |
| S5 `send_default_pii=True` | still no default pii; H-89's wiring test |
| G1 the pattern is not run | the 13 tests in which a made value follows a name |
| G2 run only on a text with an @ | those 13 less "beside an address" (its text has one) |
| G3 the value ends at a comma; G4 at a space | the same 13 (re-derived: each looks for every piece of the value) |
| G5 lower case only | case and longer names |
| G6 without `passwd` | each of the four names |
| G7 without `secret` | four names; case; two lines; none left; each part |
| G8 without `token` | four names; case; end of line; none left; each part |
| G9 the name is not kept | the 13 less "none left" and "each part" (those two assert absence only) |
| G10 a name with nothing after it is replaced | text that only looks alike |
| H1 `request` left alone; H4 `contexts` | none left; each part; what holds none is kept |
| H2 `tags` left alone; H3 `user` | none left; each part; the reversed H-89 test |

Rule 19 as it should stand after these runs: 19 tests red in step 0; "still no default pii" under
S5 and "text that only looks alike" under G10; the three controls never, as said.

**3. Regression** (the AutoGrader app, serial; own grant): OK.

## A search of the whole tree for users (0b's GRANT of 17:18:21; run 17:19, before any test run)

`git grep` over every tracked file outside docs/, at 5f30f780, for `log_scrubbing`,
`sentry_scrubbing`, `sentry_sdk`, `include_local_variables`, `max_request_body_size`. Output:
`users_search_5f30f780.txt` (98 lines of matches and headings). What it shows:

- `scrub` and the hooks are used by `AutoGrader/__init__.py` (installs the record factory),
  `AutoGrader/settings.py` (passes the hooks), and by tests in three modules:
  `AutoGrader/tests_log_scrubbing.py`, `AutoGrader/tests_sentry_scrubbing.py`,
  `billing/tests/test_log_scrubbing_end_to_end.py`. All three are in the gate's part 1; the first two
  are also the mutants' inner runs. No other application imports either module.
- `sentry_sdk` is used outside the settings in two places only, `AutoGrader/middleware.py` and
  `AutoGrader/celery_signals.py`, each to set one tag, `request_id`. Their tests
  (`tests_middleware`, `tests_celery_signals`) are in part 1. A request id passes the scrub
  unchanged unless it happens to hold one of the patterns. Nothing in the tree sets a user, a
  context or an extra on an event by hand, and nothing calls `capture_exception` or
  `capture_message` (no match for `sentry_sdk` elsewhere).
- The two new settings are named nowhere but in the init call, the hooks' docstring and this row's tests.

## Not done

Nothing run when this was written (17:19 WAT) but that search and `mutate.py --check`. No frontend
involved. `origin/main` read through git for the one file only.

## Results

### The gate at fd5245dd (0b's GRANT, 2026-10-07 17:18:21 WAT)

fd5245dd is 42f97130 (tests only), 27ee0005 (the change), 5f30f780 (tests only: every piece of a
made value) and the docs commit. One run of `run_h167_gate.sh fd5245dd 1 d7143538` (script sha256
starts c54a1afc33efccfd; `mutate.py` starts 9c00fabbbe68175f), 17:19:58 to 17:24:49, script exit 0.
The Release Engineer's full run had ended a minute and a half before: one-minute load 4.55 at the
start, 4.35 at the start of part 1, 5.16 at its end, 5.12 at the end. Not stopped, not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0. Reproduce-first, on the base's three production files | Ran 39, FAILED, 19 distinct tests red by name | **Ran 39 tests in 0.106s, FAILED (failures=34)**: 34 lines (sub-tests counted singly), 19 distinct tests; the script's own comparison with the 19 written names: "step 0 is as written" | `prefix_base_production_failing_fd5245dd.txt.gz` |
| 1a. makemigrations --check | no changes | "No changes detected" | `makemigrations_check_fd5245dd.txt` |
| 1. Modules and guards at the tip | OK | **Ran 393 tests in 129.210s, OK** (no skip) | `modules_and_guards_fd5245dd.txt.gz` |
| 2. Mutants | 19 KILLED with exactly their named tests | **19 of 19 KILLED**: each exit 1, its own "Ran 74", `expected-but-passed []` nineteen times; SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN empty. Source clean after. | `mutation_log_fd5245dd.txt`, `mutation_results_fd5245dd.json`, `mutant_logs_fd5245dd/` |

Nothing differed from what was written before the run. The console is `gate_console_fd5245dd.txt.gz`.

**Each failing set is exactly the written one**, compared by program from the results file: for all
19 mutants the sorted expected list equals the list of failing tests (sizes 2, 2, 2, 2, 2, 13, 12,
13, 13, 1, 1, 5, 5, 11, 1, 3, 3, 3, 3). G3 and G4, the patterns that stop early, fail all thirteen
tests, as re-derived before the run.

**Rule 19, counted from these records:** 19 tests red in part 0. Under the mutants 22 distinct tests
failed: the 21 that must be named and H-89's wiring test (under S5). So "still no default pii" was
seen red under S5 and "text that only looks alike" under G10. **Never seen red, as said from the
start: the three controls on the libraries.** They are not evidence of the change.

**What these runs do not show:** an event that was sent; a server; the SDK at another version; that
a road I did not read is closed.

**Two committed logs are NOT the raw bytes, and I say so.** In part 0 and under H4 the failure texts
print the tests' made queue address, a URL of the host `queue.invalid` with a 12-character random
value in the password position (2 lines and 4 lines). It opens nothing, but the team's rule is that
no committed file holds a URL with a password part, even a made one. In those six lines the value
is replaced by "[made value masked by ed]" (one substitution, nothing else touched: 2 and 4 lines
differ from the raw files). The raw files are kept outside the repository, in
`~/Documents/Projects/GAP-ed-scripts/logs/h167_raw_fd5245dd/` (sha256 start e00b70532b05a722 for part
0, 7bece1cde8357bb4 for H4), for the verifier to compare. Row H-169 (tests should not print such
values) is the cure; this module should be changed with it.

Credential-pattern check before this commit (every new file, before gzip, values masked): after
that substitution no URL with a password part (the pattern used allows an empty user name; the one I
used earlier today did not, so I re-checked the committed evidence of H-147, H-141, H-148, H-152 and
H-153 with it: 0 lines in each). Name-and-value lines: 30 in part 0's log and 79 in the mutants'
logs, every one a failure text of this row's own tests showing a made random value after one of the
four names (that is what those tests are about); none in the other files.

Written 17:26 WAT. Still owed as this is committed: the regression (the AutoGrader app), on its own
grant.
