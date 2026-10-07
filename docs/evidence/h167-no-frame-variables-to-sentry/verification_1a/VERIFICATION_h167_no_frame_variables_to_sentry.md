# Verification: H-167: an error report carries no frame variables and no request body @ a1397c5b

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-07. **Severity:** HIGH (Senior Manager).
**Branch:** `task/h167-no-frame-variables-to-sentry` @ **a1397c5b**, the final tip, from the pushed beta tip `d7143538`; stacked on nothing of batch 12 or 12b. For batch 13. No model, no migration.

Commits: `42f97130` tests first; `27ee0005` the change; `5f30f780` tests only, AFTER the change and before any run (the tests look for every piece of a made value, on the Senior Manager's word); `fd5245dd`, `682f192e`, `a1397c5b` docs only. The three production files (`AutoGrader/settings.py`, `AutoGrader/log_scrubbing.py`, `AutoGrader/sentry_scrubbing.py`) are unchanged from `27ee0005` to the tip. The evidence is in `docs/evidence/h167-no-frame-variables-to-sentry/`.

**I ran at a1397c5b** (2026-10-07) in 0b's slots, from my own detached scratch checkout, serial, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, `--settings=settings_worktree` (mutants: `settings_worktree_mut`), every run's output straight to its own file with stdin from `/dev/null`. Two attempts: the first (17:49:39) stopped at a red baseline through a fault of my probe and ran no mutant; the second (17:52:16 to 17:52:40; hooks ended 17:53:13) ran as written. Both are told below. The one-minute load at each start is in each log (2.81 to 3.66). No timeout, no kill. The modules are `SimpleTestCase`; no database was made.

Under rule 15 I cite ed's gate (at `fd5245dd`) and ed's regression (the AutoGrader app, serial, at `682f192e`) and repeat neither. Under rule 20 I cite ed's gate.

**Nobody opened a log, a Sentry project, an event that was sent, or a settings value: not the author, not I.** The fault was found by reading and is closed by reading and by tests in this process. Whether Sentry is switched on anywhere, and what it has already been sent, this record cannot say.

**Verdict: VERIFIED-WITH-NOTES.** What the evidence lists under "What changes" is true on everything I read and drove, and the author's first worry (the settings are tested by an overlay of options, not by a client) is answered by a real client below. I found no defect. The notes say what still reaches a report without passing a scrub, what the pattern does not know, and what main still does; none asks for a change before the merge.

## What changes (checked by reading the code at the tip)
- **The one `sentry_sdk.init(...)` call** (`AutoGrader/settings.py`; the only one in production code, and nothing later in the file touches Sentry) passes `include_local_variables=False` and `max_request_body_size="never"`, beside `send_default_pii=False` which was there.
- **The H-89 scrub** (`AutoGrader/log_scrubbing.py`, used by every log line and by the Sentry hooks) gains a third pattern: after one of the four names `password`, `passwd`, `secret` or `token` followed by an equals sign (any case, also at the end of a longer name) the rest of that line becomes `[secret]`.
- **The Sentry hooks** (`AutoGrader/sentry_scrubbing.py`) scrub four more parts of an event: `request`, `tags`, `user`, `contexts`.

## What I checked by reading
- **The installed SDK** is sentry-sdk 2.68.0, the version `requirements.txt` pins. Both keywords exist in it. A frame's variables are attached where the SDK reads `client_options["include_local_variables"]`; a request body is read only where `request_body_within_bounds` allows it, which reads `max_request_body_size`. This SDK also has a newer `data_collection` option under `_experiments`; our call does not set it, so the older path above is the one in force. If someone sets it later, the two keywords are read differently: see N5.
- **A queued task's arguments** (the "temporary password in a queued email's arguments" of the row): the SDK's Celery integration puts a withheld marker in their place while `send_default_pii` is False. The author's settings test holds `send_default_pii` False.
- **Would the settings test fail if a keyword were dropped or overridden later in the file** (the Senior Manager's question). The test reads the init call's keywords from the source with `ast`. A keyword taken out fails it; a value of True fails it; a value that is not a literal (a name, a call) errors it; a second `sentry_sdk.init(...)` call anywhere in `settings.py` errors it, because it demands exactly one. The author's mutants S1 to S4 show the first two red. What it would NOT see: an init made under another name (`from sentry_sdk import init`, or the module under an alias), an init in another file, or a later change of the running client's options. None exists today (I searched production code for every use of `sentry_sdk`: the settings, and two places that set a `request_id` tag).
- **Our own log lines.** I searched one commit's production code for strings that put a value after one of the four names: two, both the activation and verification links built for an email (`classrooms/serializers.py`, `users/services.py`). No log format string of ours uses the names. So "the rest of the line is lost" costs our own logs nothing; it can shorten a third party's log line.
- **ed's raw logs, read by me from the commit.** `prefix_base_production_failing_fd5245dd.txt.gz`: one Ran line, `Ran 39 tests`, `FAILED (failures=34)`. `modules_and_guards_fd5245dd.txt.gz`: one Ran line, `Ran 393 tests`, `OK`. `regression_682f192e.txt.gz`: one Ran line, `Ran 632 tests`, `OK`. `mutation_results_fd5245dd.json`: 19 entries, 19 KILLED; 19 mutant logs.
- **The two committed logs that are not raw bytes** (the author says so): I compared them by program with the raw files outside the repository (`~/Documents/Projects/GAP-ed-scripts/logs/h167_raw_fd5245dd/`, sha256 starts e00b70532b05a722 and 7bece1cde8357bb4, as the author gives them). The same number of lines; exactly 2 lines differ in the reproduce-first log and 4 in mutant H4's log; in each, one 12-character span in the password position of a made queue address is replaced by the author's mask label and nothing else. No value was printed by my comparison. See N7.

## What I checked by running (at a1397c5b)

The author's 23 tests and 19 mutants cover the keywords, the pattern name by name and the four parts one by one. The author names as the first thing to attack that the settings are tested by laying our call's literal values over the SDK's default options and calling the SDK's functions, since Sentry is never initialised under tests. My probes build a REAL `sentry_sdk` client from the init call's three privacy values and collect what it hands to its transport. The transport is a class of my own that keeps envelopes in memory; the made address ends in `.invalid`; default and auto-enabling integrations are off; each client is closed at the end of its test. Nothing can leave the process.

**First attempt, 17:49:39 to 17:49:43, stopped at a red baseline:** `Ran 44 tests in 0.125s`, `FAILED (errors=3)`: my own sa, sb and se, each `TypeError: 'Client' object is not callable`. I had named a helper method `client`, and Django's test case sets `self.client` to its own test client on every instance, which hid it. It was my probe's fault and said nothing about the row; none of the three reached the SDK. The author's 39 tests and my sc and sd were "ok". I released the slot at once and ran no mutant. The probe as it ran, its log and the note written before it are kept (files below).

**Second attempt.** The only change to the probe: the helper is called `real_client` (one definition, five uses) and a paragraph in its docstring. The mutants and the expected sets were unchanged (`h167_expected_kills.txt`, written 17:50:11, before the runs). Rule 19: each probe was seen green on the tip and red under its own mutant, and each mutant failed exactly the set written.

| Run | What it shows | Result |
|---|---|---|
| Baseline: my probe module (5), `AutoGrader.tests_sentry_sends_no_variables` (23), `AutoGrader.tests_sentry_scrubbing` (16) | green at the tip | `Ran 44 tests in 0.171s`, `OK` |
| Z1: the init call does not pass `include_local_variables` | **sa**: a real client made from our call's values resolves the option to False and hands its transport an event whose frames carry no variables and nowhere the made value; a client with the SDK's defaults, the control, sends the variable | `Ran 5`, `FAILED (failures=1)`: sa |
| Z2: the init call lets medium request bodies through | **sb**: the same real client resolves `"never"` and refuses a body of every length tried; the default client accepts a small one (the control) | `Ran 5`, `FAILED (failures=1)`: sb |
| Z3: the hooks leave an event's contexts alone | **sc**: the four new parts in shapes the author's tests do not use (tags as a list of pairs, nested contexts with a list, a query string, a header): every made value gone, numbers and plain text kept, the same event object returned; an absent or None part stays so | `Ran 5`, `FAILED (failures=1)`: sc |
| Z4: the pattern does not know the fourth name | **sd**: a line with an address before and after and a second name after the first is cut at the first name; a value that is an object, not text yet, is scrubbed through its text. sc holds the fourth name twice | `Ran 5`, `FAILED (failures=2)`: sc and sd |
| Z5: the hooks leave an event's exception alone | **se**: an event that passes through a real client WITH our `before_send` hook still arrives at the transport (the hook does not drop it and the client accepts what the hook returns), with the address and the value after the name replaced | `Ran 5`, `FAILED (failures=1)`: se |

Every mutant log has "applied", "mutated sha differs: True", "restored_sha256_matches_commit_blob: True" and 0 tracked changes after the restore. Each log holds exactly one Ran line.

**What I did outside a test run, said plainly.** To predict, before asking for the slot, I called the functions under test directly in plain Python (no test loader, no Django settings, no database): `scrub` and `scrub_event` from copies of the tip's two modules on sc's, sd's and se's inputs, and the SDK's `Client` with an in-memory transport and typed-in values. That showed the SDK calls work; it did not cover the test class, which is where the first attempt fell.

**What the real client does and does not show.** It shows that the SDK's own client resolves our three values as the author's overlay assumes and that an exception event built with the client's options and passed through the client carries no variables. It does not start the Django, Celery or logging integrations (they patch the process), so the roads by which those integrations build an event are covered by reading (above) and by the author's tests, not by a client of mine.

**Commit hooks** over `d7143538..a1397c5b`: exit 0, 18 passed, 0 failed, 7 skipped (no files to check).

## Notes
- **N1. What still reaches a report without passing our scrub.** (a) Parts of an event the hooks do not list: `transaction` (the route's name), `fingerprint`, `modules`, `server_name`, `release`. I know of no personal data in them. (b) Dictionary KEYS are never scrubbed, only values. (c) Profiling is on in the same init call (`profile_session_sample_rate=1.0`): profiles carry frames (function and file names) and no variables, and pass no hook of ours. (d) The SDK's own filtering (headers, cookies, a task's arguments) depends on `send_default_pii=False`, which the test holds.
- **N2. What the pattern does not know,** as the module's docstring says: other names (`pwd`, `api_key`, `key`, `authorization`), a space before the equals sign, the name followed by a colon, and the dictionary form (the name in quotes, a colon, the value in quotes). With frame variables and bodies off, the text that can still carry such a form is an exception's own message and a log line.
- **N3. To the end of the line.** Everything after the name on that line is replaced, also a traceback's source line that holds one of the names and the sign (the author names it). Accepted by the Senior Manager.
- **N4. The settings test's blind spots** are in "What I checked by reading": an init under another name or in another file, and a later change of the client's options. A one-line guard ("`sentry_sdk` is named in production code only where this list says") would close the first two; I suggest it for H-169 or a row of its own.
- **N5. The SDK's `data_collection` option** (under `_experiments` in 2.68.0) replaces `send_default_pii` when set, and then decides frame variables and bodies by its own keys, falling back to the two keywords only where a key is absent. Our call does not set it. An SDK upgrade that makes it the main road, or someone adding it, needs this row read again.
- **N6. main:** the same init call without either keyword and without any hook (H-89 is not on main), as the author says. Production is the service that would be sending; the two keywords alone would close the two leaks there. That is a decision for the Senior Manager and the user, with H-110, H-121 and H-127 under "before promotion to main".
- **N7. Six committed log lines hold a made queue address with a mask label in its password position** (two logs; the author's masking, on the Senior Manager's word). The grant's rule counts a label in that position as something the address check lists, so the credential tool will show six address rows for these files at the batch's Gate 1. They are made addresses on an `.invalid` host with the value gone. Row H-169 (tests should not print such a value) is the cure.
- **N8. The mutants' logs of the author hold many lines with a name, the sign and a random made value** (failure messages); the author says so. Not credentials.
- **N9. Three of the author's 23 tests are controls on the libraries** and cannot be made to fail by our files (the author says so). My sa and sb each carry such a control too (the default client), for the same reason: so that the test cannot pass on an SDK that no longer behaves as read.
- **N10. Two of my baselines today fell to my own fixture** (H-148 and this row). Each cost a second grant and nothing else; each first attempt is kept as it ran.

## Credential check
My two probe versions, mutant file, two expected notes and seven logs, searched for a URL with anything in the password position and for assignment forms whose name contains PASS, PWD, SECRET, TOKEN or KEY, masked output only: 0 lines in each but three logs. Mutant logs Z3 (line 20), Z4 (lines 20, 28, 29, 30) and Z5 (line 20) hold failure messages that quote a name, the sign and a value. By program, without printing a value: every such value is either the replacement marker or one of the six made constants written in my probe file (five or six characters, beginning "mv"). None is a credential. My probe file joins a name and a value only at run time, so no line of it matches; this record names the forms in words for the same reason. No line over 4000 characters; no trailing whitespace. No archive among them.

## Files (in `~/Documents/Projects/GAP-1a-records/`, sha256 prefixes)
Second attempt:
- `h167_probe_tests_vf1a_h167_probe.py` 44509419351fb2c4
- `h167_mutants_Z.py` ae3a5e67322b458a
- `h167_expected_kills.txt` ffd666da6ce7a54e
- `runs/h167_a1397c5b.log` 80e7d4850b444751
- `runs/h167_mutant_Z1_a1397c5b.log` 5242b0002402a1dd
- `runs/h167_mutant_Z2_a1397c5b.log` 64f3db1e8637bcb3
- `runs/h167_mutant_Z3_a1397c5b.log` 5ec238e0249a2a29
- `runs/h167_mutant_Z4_a1397c5b.log` 258ed432bb3a336b
- `runs/h167_mutant_Z5_a1397c5b.log` 9e2ffc58399dd336

First attempt, kept as it ran:
- `h167_probe_v1_as_run_17h49.py` 8b408df335bbd27c
- `h167_expected_kills_first_attempt.txt` a858539643f07694
- `runs/h167_a1397c5b_first_attempt_17h49.log` d4ec9f694f7fec68

The runner (`~/Documents/Projects/GAP-1a-scratch/h167_run.sh`, 6beffe291406c3f6) and the comparison helper (`h167_expect.sh`, 3ea769217e845cdc) were read by 0b before each grant.
