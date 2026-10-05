# H-89: email addresses and URL passwords are scrubbed from what is logged and what is sent to Sentry

What it recognises, and what it does not, is listed under "The patterns"
and "What it does not cover".

**Author:** d5 (moved from ed by the SM). **Branch:**
`task/h89-log-address-scrubber`, off beta `74bfc8d3`, base-updated by 0b onto `task/beta-batch-7`
`9a98714f` (`7d05b173`). Bundle 7.

## The change (4 points)
1. **Before:** H-80 and H-91 made the log calls pass ids. Text the code does
   not write was out of their reach: an exception's own message in a
   traceback (an IntegrityError's "Key (email)=(…)", a ValueError built with
   an address: the 13 traceback sites H-80 recorded as a known limit, and
   1a saw one live), a third-party library's line, a DSN with its password
   in a connection error. The same text went to Sentry with every ERROR
   record.
2. **After, logs:** `AutoGrader/log_scrubbing.py` is a log record factory.
   The `AutoGrader` package installs it when it is imported (see below). Every record, from any logger and through any
   handler:
   - `getMessage()` returns the formatted message with each email address
     replaced by `[email]` and each URL's userinfo by `[credentials]`;
   - a record with `exc_info` carries its traceback text already rendered
     and scrubbed in `exc_text`, which formatters reuse.
   `record.msg`, `record.args` and `record.exc_info` are left as they were.
3. **After, Sentry:** `AutoGrader/sentry_scrubbing.py`, three hooks passed
   to `sentry_sdk.init`: `before_send` and `before_send_transaction`
   (events; a sampled transaction is an event that does not pass
   `before_send`), `before_breadcrumb`, and
   `before_send_log` (settings have `enable_logs=True`, so there is a log
   stream too). They scrub the log entry (template, formatted text and raw
   arguments), the plain message, the exception values and the threads,
   **the frames' local variables**, a transaction's spans, the breadcrumbs
   and the extras. The user context, the tags
   and the request are left alone (SM ruling). `send_default_pii` was
   `False` and stays `False`; a test holds it.
4. **Tests:** `AutoGrader/tests_log_scrubbing.py`,
   `AutoGrader/tests_sentry_scrubbing.py`, and
   `billing/tests/test_log_scrubbing_end_to_end.py`.

## Where it is installed, and which processes get it
`AutoGrader/__init__.py` installs the factory as its first statement, before
the package's Celery import. `settings.py` only defines the switch and
imports nothing from the project.

- **Every process that uses `AutoGrader.settings` gets it**, because Python
  imports the package before the module inside it: gunicorn (through
  `AutoGrader.wsgi`), the Celery worker and beat (`AutoGrader.celery`),
  every management command and the shell, and the per-worktree test
  settings, which import `AutoGrader.settings`. It is in place before
  settings load, so a record made while they load is scrubbed too.
- **The case that does not get it:** a process that never imports the
  `AutoGrader` package. That is code that runs `settings.py` by its path
  (the two frontend-domain setting tests do, with the project not
  importable) or a stand-alone script that logs without setting Django up.
  Neither exists in production.
- **If `log_scrubbing` cannot be imported** (0b's question), importing the
  `AutoGrader` package raises and the process does not start: web, worker,
  beat or command. It fails loudly at start; there is no path on which the
  process runs without the scrubber. Once installed, a failure inside the
  scrubber never raises into the caller (the table below).
- **The Sentry hooks** are imported and passed in the single
  `sentry_sdk.init` call, after and outside the `try/except ImportError`
  that exists for a missing `sentry-sdk` package (SM ruling). A broken
  hooks module fails the process at start instead of leaving Sentry
  silently off, and Sentry is never initialised without the hooks.

**The first design was different, and its regression was red.** At
`3da28258` `settings.py` imported `log_scrubbing` and installed the factory
itself. Step (c) of that gate failed 4 tests in 3216:
`AutoGrader.tests_school_admin_frontend_domain` and
`AutoGrader.tests_student_frontend_domain` (two each) execute `settings.py`
by path, where the project cannot be imported. Step (a) had not included
those two modules; it does now, with the other modules that load settings
(`settings_loading_tests.txt`). The SM approved the rework. Logs of that
gate: `first_gate_3da28258/`.

## Why a record factory and not a handler filter
The row said "a filter on every handler". `settings.LOGGING` has ONE handler
(`console`) and no root logger; only `django`, `ERROR_REPORT`,
`ai_processor` and `students` are routed to it. Every other logger
(`billing.*`, `users.*`, `classrooms.*`, …) is printed by whatever the
process provides: Python's last-resort stderr handler, Celery's handlers in
a worker, gunicorn's in a web process. A filter on the configured handler
would have covered a small part of the output. The SM approved the factory
(D1) with these requirements, each tested:
- it wraps the factory already installed (another factory's record class is
  kept and scrubbed);
- it is idempotent on re-import;
- only `getMessage()` and the rendered exception text change;
- the console handler, the last-resort handler, a Celery task logger with
  Celery's own formatter, and a record made in a real process before
  Django's logging configuration is applied all print scrubbed text.

## The patterns and their false positives
Widened after 1a's pre-review (P1, P2; SM rulings of 2026-10-05).

- An address, `local-part@domain`, becomes `[email]`.
  - Local part: letters and digits of any script, and the special
    characters Django's validator accepts (``. ! # $ % ' * + ^ _ ` { | } ~ -``),
    **except `/`, `=`, `?` and `&`** (below).
  - Domain: dotted labels of letters and digits of any script and "-", so
    an international domain matches as it is read (`münchen.de`) and in
    punycode (`xn--mnchen-3ya.de`, a `.xn--p1ai` last label). The last
    label starts with a letter and has two or more characters.
- A URL's userinfo (`scheme://…@`) becomes `[credentials]`, whatever the
  host looks like, so a DSN never prints its password: the tests cover a
  dotted host and the dotless `redis`, `localhost` and `rabbit`. It runs to
  the **last** "@" before the next "/" or whitespace, so a password with an
  "@" in it is removed whole.
- Left alone: a decorator in a traceback's source line
  (`@transaction.atomic`), a version pin (`pkg@1.2.3`, `pkg@1.2.30`),
  `a @ b`, `x@y`, a URL without credentials. An address in a query string
  is replaced and the rest of the URL kept.
- A message with no "@" is returned without running either pattern.

**Too much is replaced in two cases, by decision (SM), each pinned by a
test:**
- A quote, brace or bar directly before an address is a character an
  address may start with, so it goes with the address: `email='x@y.z'`
  prints as `email=[email]'`
  (`test_a_quote_brace_or_bar_just_before_an_address_goes_with_it`).
- A URL with no path and an address in its query
  (`https://host?to=x@y.z`) is read as a URL with userinfo and prints as
  `https://[credentials]@y.z`. Harmless: nothing is revealed.

**Too little is replaced in one case, by decision (SM ruling "A"), pinned
by one test row per character:** the scrubber does not follow an address
through `/`, `=`, `?` or `&`. They are legal in a local part, and they are
also what separates a key from its value and the parts of a URL. Following
them would print `email=x@y.z` as `[email]` and
`https://app.example.com/register?email=x@y.z&t=1` as `https:[email]&t=1`.
So an address whose local part itself contains one of the four keeps the
piece before that character in print: `left/right@school.edu` prints as
`left/[email]`. What is left is a fragment, never a usable address
(`test_the_four_characters_an_address_is_not_followed_through`).

**One change that replaces the same text.** The address match now starts
only at the start of a run of local-part characters. A long unbroken token
before an "@" is scanned once; the old pattern rescanned it from every
character (26 ms for a 3,000-character token, measured; now 0.14 ms). The
output is identical: every start inside a token meets the same "@" and the
same domain as the token's own start, so a match from inside succeeds only
where the match from the start does. No mutant can show a speed-only
change, and it has no test of its own. A change in WHAT is replaced would
fail `test_addresses_of_many_shapes`,
`test_the_four_characters_an_address_is_not_followed_through`,
`test_a_quote_brace_or_bar_just_before_an_address_goes_with_it`,
`test_a_key_before_an_address_stays_readable` or
`test_text_that_only_looks_like_an_address_is_left_alone`.

## It never raises, and it fails closed (D4)
| Case | What is emitted | Test, mutant |
|---|---|---|
| The message cannot be formatted or scrubbed | the unformatted template plus a marker, never the arguments | `test_a_message_that_cannot_be_formatted_fails_closed`, S4 |
| The exception text cannot be rendered | the exception's class plus a marker | `test_exception_text_that_cannot_be_rendered_fails_closed`, S9 |
| A record whose class cannot be replaced | the template plus the marker; arguments dropped | `test_a_record_whose_class_cannot_be_replaced_fails_closed`, S8 |
| A Sentry event part cannot be scrubbed | that part is replaced by a marker; the event is still sent | `test_it_fails_closed_and_never_raises`, Y2 |

## Off under the test runner (D3)
`settings.LOG_SCRUB_ADDRESSES = not _TESTS_ARE_RUNNING`, read by the
scrubber once Django's settings are configured; until then it is on
(`BeforeSettingsAreConfiguredTests`). On in every
environment, with no environment variable and no `ENVIRONMENT` branch (a
test reads `settings.py`'s AST to hold that). It is off while tests run so
that H-80's and H-91's tests read what the code really logged; a scrubbed
capture would make them pass vacuously. Tests of the scrubber switch it on
with `override_settings(LOG_SCRUB_ADDRESSES=True)`.

Because the suite therefore never runs with it on,
`billing/tests/test_log_scrubbing_end_to_end.py` does: with a real handler
attached it drives the licence add-teachers enrolment (nothing raises, the
ids survive, no address) and an unexpected failure with a traceback (the
class and the frames survive, the address does not), plus a control with
the scrubber off that shows the address is there.

## One real Sentry frame, before and after
Built with `sentry_sdk.utils.event_from_exception` from a real caught
exception (no client, nothing sent; the address is made up), then passed
through `scrub_event` (`frame_before_after.txt`):

    BEFORE
      exception: ValueError: "Teacher someone.private@school-example.edu does not belong to this school"
      frame renew, vars:
        teacher:       "<CustomUser: someone.private@school-example.edu>"
        license_id:    "'3f2b8c1e-9d4a-4c7b-8e21-5a6f0d9b7c13'"
        allocation_id: "77"
        attempts:      "2"
    AFTER
      exception: ValueError: "Teacher [email] does not belong to this school"
      frame renew, vars:
        teacher:       "<CustomUser: [email]>"
        license_id:    "'3f2b8c1e-9d4a-4c7b-8e21-5a6f0d9b7c13'"
        allocation_id: "77"
        attempts:      "2"

Frame locals are scrubbed by the same rule (SM ruling: they are the
likeliest place for an address). A local with a user id, a UUID, a number,
a repr or a nested structure is unchanged
(`test_frame_variables_that_hold_no_address_are_unchanged`).

## Cost per record
`getMessage()` on one record, 200,000 calls, plain Python, `bench.py`, best
of 7 repeats. The first column was measured at a load of about 8 and the
second at about 2, hours apart, so read each as an upper bound and do not
subtract one column from the other:

| | per call, first pattern (`9a37e092`) | per call, after the fold (`24dfcda6`) |
|---|---|---|
| scrubber off | 1.25 µs | 1.07 µs |
| on, a message with no "@" | 1.26 µs | 1.09 µs |
| on, a message with an address | 6.49 µs | 8.21 µs |

A record without "@" pays one substring check. Traceback text is rendered
when the record is made only for records that carry `exc_info`.

## What it does not cover
- Text that never becomes a log record or a Sentry event: a `print()`, or
  a library that writes to stderr directly (SM: accepted and recorded).
  H-91's guard covers this code's own `print()` calls.
- An address whose local part contains `/`, `=`, `?` or `&`: the piece
  before that character stays (above).
- An address written percent-encoded (`%40` for "@"), as in an encoded
  query string: not recognised.
- An address Sentry has already cut short (it truncates long strings before
  the hooks see them): a cut address with no complete domain is not
  recognised.
- A password in a `key=value` connection string (`password=… host=…`, no
  "://"): not recognised. Only URL userinfo is.
- A URL password with an unencoded "/" in it: no parser reads that as a
  URL, and nothing is replaced.
- In a Sentry event, the user context, the tags and the request (its URL
  and query string) are left alone (SM ruling); `send_default_pii=False`
  keeps Sentry from adding the user's address there.
- The switch is `"test" in sys.argv or "pytest" in sys.modules`. A
  management command given the bare argument `test` (none exists) would
  run with the scrubber off.

| Commit | What |
|---|---|
| `64efdcff` | tests for the factory and the switch (red) |
| `bb882332` | `log_scrubbing.py`, the settings switch, the end-to-end test, the runner |
| `6c7d7cac` | tests for the Sentry hooks (red) |
| `ed23c926` | `sentry_scrubbing.py`, wired into `sentry_sdk.init` |
| `3da28258` | the frame-locals-unchanged test (SM) |
| `c93d594e` | tests for the rework (red): settings load on their own; the package installs; a record before settings are configured; the hooks' import is not swallowed |
| `a04aaa3d` | the rework: installed from `AutoGrader/__init__.py`; the Sentry import outside the `try` |
| `7d05b173` | base update (0b) |
| `9a37e092` | the mutation runner only: two mutants it could not judge |
| `5bd912ad` | evidence for the gates at `9a37e092` |
| `8f4e9ef8` | tests for 1a's pre-review points (red) |
| `24dfcda6` | the fold: wider address shapes, "@" in a password, transactions and spans; nine more mutants |

## Gates after the fold (the current tip)
On the frozen code tip `24dfcda6`, 2026-10-05, under 0b's grants. Status:
`chain.status` and `iso.status`.

| Gate | Result | Log |
|---|---|---|
| Repro: the fold's test commit `8f4e9ef8` (the two scrubbing modules on the code before the fold) | RED as expected: 51 tests, failures=15 | `repro_8f4e9ef8.log` |
| (a) the same 25 modules | GREEN: 273 tests, OK | `a_modules_24dfcda6.log` |
| (b) battery (`test_h89_mut`), 36 mutants | 36/36 killed, restore verified | `battery_24dfcda6/` |
| (c) part 2 only: AutoGrader + the two classrooms sweeps + the two schema-extension guards, serial, 11:58 to 12:09 | GREEN: 551 tests, OK | `c2_autograder_serial_24dfcda6.log.gz` |

**Three of the fold's tests pass on the old code, by design**, so they are
not among the 15: the "threads" test (threads were already scrubbed, only
untested; its mutant Y10 is killed), the four-character limit test (the old
pattern stopped at those characters too) and the punycode-domain row (plain
ASCII). They pin behaviour; they are not repros.

**Why (c) is part 2 only (SM, 2026-10-05).** The fold changes only the two
scrubbing modules and the Sentry init call in `settings.py`, and the
scrubber is off under the test runner
(`AutoGrader.tests_log_scrubbing.TheSwitchTests.test_it_is_off_under_the_test_runner`
asserts the setting is False there and that a logged address is printed
unscrubbed), so billing's and users' tests cannot see the change. No
production file outside `AutoGrader/` changed.

**Run 2 ran once.** 0b granted it at about 09:20; that message did not
reach d5, and the machine rebooted at 10:47. Fold run 2 had not started
(no log, no status line, no test database in use). It ran under a new grant
after the reboot. Vezi's full suite ran beside run 1 (repro, (a), (b)), so
its wall times are not a baseline.

**Logs, and a miss of mine in the first evidence commit.** A failing
assertion prints the test's made-up DSN, so the repro and mutant logs carry
URLs with a made-up password. The rule (SM, 2026-10-02) is that no such URL
is committed, even a fake one. In the committed copies each is replaced by
"[credentials removed from this log]", and the made-up password where a
line prints it on its own by "[made-up password removed from this log]";
nothing else in any log is changed. The originals are outside the repo.
The first evidence commit (`5bd912ad`) did this with a pattern that
required a user name, and so left 13 lines with an empty-user URL
(a scheme, "://", a colon, the password, "@", the host) in nine files, and the lines that print the made-up
password alone in the two copies of `repro_64efdcff.log`. d5 found it on
2026-10-05 while adding the fold's logs and reported it to the SM. This
commit removes them from every copy, checked with a wider pattern (any or
no user name, plain and gzipped logs): none is left in the tree. They
remain in the history at `5bd912ad`; what happens to that is the SM's
ruling. The password is the tests' invented one, not a credential.
The battery at `9a37e092` is kept in `battery_9a37e092/`.

## Gates before the fold
On the then-frozen tip `9a37e092`, under 0b's grants. (a) ran at `7d05b173`;
`9a37e092` on top of it changes only the mutation runner. Status:
`chain.status` (repro, (a), (b)) and `iso.status` ((c)).

| Gate | Result | Log |
|---|---|---|
| Repro: the test commit `64efdcff` (`AutoGrader.tests_log_scrubbing` on beta's code) | RED as expected: 20 tests, failures=20, errors=2 | `repro_64efdcff.log` |
| Repro: the test commit `6c7d7cac` (`AutoGrader.tests_sentry_scrubbing` before the hooks exist) | RED as expected: 12 tests, failures=1, errors=11 | `repro_6c7d7cac.log` |
| Repro: the rework's test commit `c93d594e` (both modules on the first design) | RED as expected: 45 tests, failures=4 | `repro_c93d594e.log` |
| (a) 25 modules, at `7d05b173` | GREEN: 267 tests, OK | `a_modules_7d05b173.log` |
| (b) battery (`test_h89_mut`), 26 mutants, at `9a37e092` | 26/26 killed, restore verified | `battery_9a37e092/` |
| (c) part 1: billing + users + `users.tests_schema_extension`, `--parallel 2`, 2026-10-02 18:16 to 18:28 | GREEN: 2751 tests, OK (skipped=4) | `c1_billing_users_p2_9a37e092.log.gz` |
| (c) part 2: AutoGrader (which holds the repo-wide guards) + the two classrooms sweeps + the two schema-extension guards, serial, 2026-10-05 07:59 to 08:11 | GREEN: 545 tests, OK | `c2_autograder_serial_9a37e092_rerun.log.gz` |

(a)'s modules: the two scrubbing modules and the end-to-end one; H-80's log
guard and the renewal partial-failure module; AutoGrader's middleware,
handlers, celery-signals and network-guard tests; the modules that load
settings in their own way (the two frontend-domain ones, the two Redis
hygiene ones, `ai_processor`'s benchmark-archive and tiktoken-cache,
`assignments.tests_pdf_renderer_gevent`, `assignments.tests_schema_extension`,
`students.tests_app_boundaries`); and the repo-wide and
settings-reading guards (`tests_beat_locks`,
`tests_management_commands_are_commands`,
`tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`,
`tests_beat_health`, `tests_redis_test_isolation`,
`tests_no_wildcard_invalidation`).

(c)'s scope is the SM's: settings change and every app logs, so the
bundle's strict full run is the real regression; this gate runs the whole
AutoGrader app with billing and users.

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep:handle-lid-switch … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16). (c) ran with `--verbosity 2` through a
timestamper, under `flock ~/.machine-fullsuite.lock`, with a watchdog
(process tree and the parent's traceback after five minutes of silence;
it did not fire). Rule 17: the battery
and its baseline ran with `PYTHONDONTWRITEBYTECODE=1`, and the runner
deleted `__pycache__` in each mutated module's package before the baseline,
before each mutant and after each restore.

**(c) is in the interim form, in two parts on two days.** H-91's (c) hung
at interpreter exit on a label list that put `assignments` into a
`--parallel 2` run (open, to be H-107). Until that is understood the SM
ruled (2026-10-02 18:10) that no gate does that. So H-89's (c) is billing +
users at `--parallel 2`, then the AutoGrader app serially. Part 1 finished
green on 2026-10-02 at 18:28. Part 2 started straight after and was cut by
the machine's shutdown at about 18:31, mid-test and with no failure
(`c2_autograder_serial_9a37e092_CUT_1833.log.gz`). It was run again in
full on 2026-10-05 under a new grant; that run found the test database the
cut run had left and replaced it (the log's first lines). Three short Vezi
test passes ran alongside part 2 (load 7 to 10), so its 693 s is not a
timing baseline. `iso.status` shows `ok=352` for part 2: that is the
script's count of lines ending in "ok", which undercounts a serial run
where application log lines share those lines; Django's summary (545, OK)
is the result.

**Earlier runs, kept.** The first gate at `3da28258` (before the rework)
had a red (c): `first_gate_3da28258/`. The first battery on the reworked
code, at `7d05b173`, judged 24 of 26: S7 and Y9 were BROKEN, both faults of
the runner (S7's mutant was malformed after the rework; Y9's load-failure
check matched "ImportError" anywhere in the output). `9a37e092` fixes the
runner only; the battery above is the re-run. Logs:
`battery_7d05b173_two_broken/`.

**Logs edited in one way.** Nine of the committed logs (the first repro
log and the S2, S10 and S12 mutant logs, in each copy) printed a test's
made-up URL with a made-up password in an assertion message. Each such URL
has its user and password replaced by "[credentials removed from this
log]" (SM, 2026-10-02: no URL with a password in a record, even a fake
one). Nothing else in any log is changed.

## Mutants
The battery at `24dfcda6`. S18 to S24 and Y10 to Y12 are the fold's; S15
follows the new pattern.

| Id | Guards | Result |
|---|---|---|
| S1 | an address is replaced | KILLED, `FAILED (failures=43)` |
| S2 | a URL's credentials are replaced, dotless hosts too | KILLED, `FAILED (failures=9)` |
| S3 | the exception text is rendered and scrubbed when the record is made | KILLED, `FAILED (failures=7, errors=1)` |
| S4 | a message that cannot be scrubbed fails closed (SM's D4) | KILLED, `FAILED (failures=1)` |
| S5 | the wrapped factory makes the record | KILLED, `FAILED (failures=2)` |
| S6 | a second install does not wrap again | KILLED, `FAILED (failures=1)` |
| S7 | a second install still sets the switch | KILLED, `FAILED (failures=4)` |
| S8 | a record whose class cannot be replaced fails closed | KILLED, `FAILED (failures=1)` |
| S9 | exception text that cannot be rendered fails closed | KILLED, `FAILED (failures=1)` |
| S10 | override_settings switches the scrubber | KILLED, `FAILED (failures=4)` |
| S11 | the scrubber is off while tests run | KILLED, `FAILED (failures=4)` |
| S12 | the AutoGrader package installs the factory | KILLED, `FAILED (failures=26, errors=1)` |
| S13 | a record made before settings are configured is scrubbed | KILLED, `FAILED (failures=2)` |
| S14 | a switched-off scrubber leaves the text alone | KILLED, `FAILED (failures=2)` |
| S16 | settings.py imports nothing from the project | KILLED, `FAILED (failures=3)` |
| S17 | the switch is read from settings once they are configured | KILLED, `FAILED (failures=1)` |
| S15 | text that only looks like an address is left alone | KILLED, `FAILED (failures=3)` |
| S24 | a version number after an @ is not an address (the last label starts with a letter) | KILLED, `FAILED (failures=1)` |
| Y1 | Sentry: the exception values are scrubbed | KILLED, `FAILED (failures=4)` |
| Y2 | Sentry: a part that cannot be scrubbed fails closed | KILLED, `FAILED (failures=2)` |
| Y3 | Sentry: the user context is left alone | KILLED, `FAILED (failures=1)` |
| Y4 | Sentry: an argument that is not text yet is scrubbed | KILLED, `FAILED (failures=3)` |
| Y5 | Sentry: a breadcrumb's data is scrubbed | KILLED, `FAILED (failures=1)` |
| Y6 | Sentry: a log item's attributes are scrubbed | KILLED, `FAILED (failures=1)` |
| Y7 | Sentry: settings pass before_send | KILLED, `FAILED (failures=1)` |
| Y8 | Sentry: settings pass before_send_log | KILLED, `FAILED (failures=1)` |
| Y9 | Sentry: a failure to import the hooks is not swallowed | KILLED, `FAILED (failures=1)` |
| S18 | P1: an apostrophe and the other special characters of a local part | KILLED, `FAILED (failures=6)` |
| S19 | P1: a local part in letters that are not ASCII | KILLED, `FAILED (failures=1)` |
| S20 | P1: the scrubber stops at / = ? & (key=value and URLs stay readable) | KILLED, `FAILED (failures=8)` |
| S21 | P1: a domain in letters that are not ASCII | KILLED, `FAILED (failures=2)` |
| S22 | P1: a last label that is punycode or not ASCII | KILLED, `FAILED (failures=2)` |
| S23 | P2: the userinfo runs to the last "@" before the host | KILLED, `FAILED (failures=3)` |
| Y10 | Sentry: an event's threads are scrubbed | KILLED, `FAILED (failures=1)` |
| Y11 | P3: a transaction's spans are scrubbed | KILLED, `FAILED (failures=1)` |
| Y12 | P3: settings pass before_send_transaction | KILLED, `FAILED (failures=1)` |

## For the verifier
- The switch is module state (`log_scrubbing._enabled`), read lazily from
  settings, that then follows the
  setting through Django's `setting_changed` signal; worth a probe that a
  nested `override_settings` restores it.
- A record still pickles (the scrubbed class is a module-level class).
- **The merge-down onto the epic conflicts in three files** (1a's find):
  the epic already has its own `AutoGrader/sentry_scrubbing.py` and
  `AutoGrader/tests_sentry_scrubbing.py` (BE-A-04, `d960176d`), so both are
  add/add conflicts, and `settings.py` conflicts in the Sentry block. The
  two scrubbers have to be made one there; neither side's file can simply
  win.
- The P1 choice: SM ruling "A" (stop at `/ = ? &`). 1a named "/" in the
  pre-review; it is a recorded limit, not covered.
- H-89 edits `AutoGrader/settings.py` in two places (the switch after
  `LOGGING`, and the Sentry block, whose `try` now ends before the init
  call) and `AutoGrader/__init__.py`; on the epic `settings.py` differs
  from beta, so these need a read at the merge-down.
- Before settings are configured the switch is `None` and the scrubber is
  on; `set_enabled(None)` forgets it again when an override ends.
- The evidence and the tests use made-up addresses and a made-up password
  only.
