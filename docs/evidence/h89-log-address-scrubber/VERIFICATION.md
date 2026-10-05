# Verification: H-89: no email address and no URL password in what is logged or sent to Sentry @ 26774ba8

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-05.
**Branch:** `task/h89-log-address-scrubber` @ **26774ba8** (production code tip `24dfcda6`), on `task/beta-batch-7` `9a98714f`. Bundle 7. No migration, no model change, no new requirement.
- The scrubber: `64efdcff` (tests), `bb882332` (the log record factory and its switch), `6c7d7cac` (tests), `ed23c926` (the Sentry hooks), `3da28258` (test)
- The rework (installed from the package, not from `settings.py`): `c93d594e` (tests), `a04aaa3d`
- From my pre-review: `8f4e9ef8` (tests), `24dfcda6` (the fold)
- Test only: `8fd5dc9d` (the DSN tests build their URL from parts)
- `9a37e092` (mutation runner only), `5bd912ad`, `3f667b09`, `26774ba8`: evidence (docs only); `7d05b173`: 0b's base update

The evidence is in `docs/evidence/h89-log-address-scrubber/`.

I ran in 0b's slot from my detached scratch checkout at 26774ba8, with its own test DBs (`test_vf_h89`, the mutant on `test_vf_h89_mut`). The wrapper was rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`; serial; the output went straight to a file. Under rule 15 I cite d5's regression and don't repeat it.

**Verdict: VERIFIED-WITH-NOTES.**
- The scrubber does what the evidence says, on the paths I drove for real: a handler's output, a real database constraint error with its traceback, and a real Sentry client's event and transaction.
- The three gaps of my pre-review are folded and hold. My mutant is killed.
- **Nothing is required before the merge from my side.** N1 states what is still printed, by the SM's choice. N2 is for 0b and the founder: an earlier commit on this branch holds made-up credentials in its logs.

## What I checked by running
| Probe | Result |
|---|---|
| **U1:** the switch through three nested `override_settings` | **Holds.** Off, on, off, on, off, on, off: each level restores the one outside it, and it ends off, as the test runner starts. An override of another setting leaves it alone. |
| **U2:** a record still pickles | **Holds.** A record with a traceback, prepared as `QueueHandler` prepares it, pickles; the copy has the same class, prints `Refused [email] (user 4821)`, and its exception text has no address. |
| **U3:** a real constraint error from the database | **Holds.** A second account with the same address raises a real `IntegrityError` whose own text names the address (asserted as the control). Logged with `logger.exception` through a real handler, the line and the whole traceback print with `Key (email)=([email])` and no "@" anywhere. |
| **U4:** a real Sentry client | **Holds for the event and the transaction.** A real `sentry_sdk` client in a subprocess, with the logging integration as the settings configure it and the four hooks, and a transport that keeps what it is given and sends nothing. One ERROR with a traceback (the address in the message argument, in the exception's text and in two frame variables) and one sampled transaction with the address in a span's description. The event and the transaction each reached the transport with **0** occurrences of the address and with the `[email]` marker. See the limit below for the log stream. |
| **U5:** the shapes of my P1 and P2, through a real logger and handler | **Hold.** An apostrophe in the local part, a non-ASCII domain, its punycode form and a non-ASCII local part all print as `[email]` with nothing of the address left. A URL password that holds an "@", on a dotless host, prints as `[credentials]` with the host and path kept. Text that only looks like an address (a decorator, a version pin, `a @ b`, `x@y`) is unchanged. |

**A limit of my own probe.** U4 produced no item of Sentry's log stream at the transport, so it did not exercise `before_send_log` for real. That hook is covered by d5's test on a hand-built item (`test_a_log_items_body_and_attributes`) and by mutants Y6 and Y8 only. The breadcrumb hook was exercised only as part of the event.

## My pre-review findings
- **P1 (address shapes our validation accepts): folded, as the SM's choice "A".** At `5bd912ad` an apostrophe or other special character in the local part left the front of the address in print, and an address on a non-ASCII domain was printed whole. The pattern now takes letters and digits of any script and the special characters Django's validator accepts, except four (N1). U5 above, and the rows d5 added to the shapes table.
- **P2 (an "@" inside a URL password): folded.** The userinfo now runs to the last "@" before the next "/" or space. U5 above; d5's `test_a_password_with_an_at_sign_in_it`. A password with an unencoded "/" is only recorded: no parser reads such a URL.
- **P3 (sampled transactions passed no hook): folded.** `scrub_event` is also passed as `before_send_transaction`, and "spans" is among the scrubbed parts. U4 above shows it on a real client; d5's wiring test holds the setting.
- **The untested "threads" part:** d5 added `test_the_threads_of_an_event`; my mutant below.

## Evidence
| Check | Result |
|---|---|
| **Run** @ 26774ba8: my probes U1–U5 + `AutoGrader.tests_log_scrubbing` + `AutoGrader.tests_sentry_scrubbing` + `billing.tests.test_log_scrubbing_end_to_end` | **59 tests OK** (40 s): my 5, d5's 51 and the 3 end-to-end. |
| **My mutant Y9** ("threads" dropped from the scrubbed parts of a Sentry event) | **KILLED** by exactly d5's `test_the_threads_of_an_event` (17 tests, failures=1). Same as d5's Y10. At `5bd912ad` no test had a threads event. |
| d5's gates (cited) | On the fold `24dfcda6`: repro at `8f4e9ef8` 51 tests / 15 failures; (a) 25 modules 273 OK; (b) 36/36 mutants killed. On the test-only commit `8fd5dc9d`: the two touched modules 51 OK and 36/36 again (rule 15.4). (c) is in pieces: see the next paragraph. |
| No production change after the fold | `git diff 24dfcda6 26774ba8` outside the evidence folder touches the two test modules only. |
| Hooks | `pre-commit run --from-ref 9a98714f --to-ref 26774ba8` passes (the range; I did not run each commit separately). |
| Merges | `git merge-tree --write-tree` is **clean** against `task/beta-batch-7` `085adecd` and against H-91's `b6fbdbea`. |
| H-91's guard on H-89's files | H-91's guard is not in this tree, so I ran its helpers (from `b6fbdbea`) over H-89's four production files at `5bd912ad`: 0 calls. The fold adds no log or print call. |
| The hooks exist in the pinned SDK | `sentry-sdk==2.68.0` takes `before_send`, `before_send_transaction`, `before_breadcrumb` and `before_send_log` as top-level options. |
| Rule 14 | No MagicMock in the three test modules. |

**d5's regression is in pieces, and that satisfies me for this tip** (0b's question).
- The pieces: billing + users at `--parallel 2`, 2751 OK, at `9a37e092` on 2026-10-02, before the fold; the AutoGrader app serially, 551 OK, at the fold `24dfcda6`; after the test-only commit, the two touched modules and the battery.
- Why it is enough: the scrubber is off under the test runner, so the fold's two patterns cannot change what any billing or users test reads, except in a test that switches it on. Only H-89's own three modules do, and my run covers all three at the tip. The fold's one settings line is inside the Sentry block, which no test executes; the wiring test reads it from the source.
- The form (no `assignments` in a `--parallel 2` list) is the SM's interim ruling for H-107, not a choice of H-89's. Bundle 7's strict full run is the wider check.

**Rule 17.** Both runs had `PYTHONDONTWRITEBYTECODE=1`, and the mutant was applied with `python -B`. `__pycache__` under `AutoGrader/` and `billing/` was deleted before the baseline, before the mutant and after the restore (the logs show 0 directories each time). The restored `AutoGrader/sentry_scrubbing.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

## Notes
**N1 (what is still printed; SM's choices, all in the evidence).**
- **An address whose local part holds "/", "=", "?" or "&"** keeps the piece before that character in print (`a/b@school.edu` prints as `a/[email]`). I asked for "/" in the pre-review. The SM chose to stop at these four so that `email=…` keys and a URL with an address in its query stay readable. d5 pins each with a test row.
- **Not recognised:** a percent-encoded address (`%40`), an address on a dotless domain, an address Sentry has cut short inside its domain, the password of a `key=value` connection string, and a URL password with an unencoded "/".
- **Not reached at all:** a `print()` and a library that writes to stderr itself. `assignments/tasks.py:164` prints a formatted traceback when an upload task fails; in a Celery worker stdout normally becomes log records, which I have not confirmed for our worker's start command. The SM has made this a LOW backlog row (the six `print()` calls in that file become logger calls; owner d5).
- **Left alone on purpose in a Sentry event:** the user context, the tags and the request.
- **Over-replaced, harmless:** a quote, brace or bar just before an address goes with it, and a token such as `file@v2.tar.gz` is replaced.

**N2 (history, for 0b and the founder).** The first evidence commit, `5bd912ad`, holds test logs with a made-up DSN carrying a made-up password; d5's first redaction missed URLs with no user name. `3f667b09` removed them from every copy, and the SM ruled to leave the history. The merge waits for the founder's answer on that; my verdict does not depend on it. At the tip I repeated the check the SM asked for:
- d5's pattern for a URL with a password gives **0 lines in H-89's own files and its evidence folder**, gzipped logs included (I decompressed them).
- The made-up password on its own: 0 lines in the evidence folder. It remains as a constant in the two test modules, never inside a URL.
- Whole tree: I count 16 lines in 7 files where the evidence says 13 in six. The other 3 are inside `docs/evidence/h1_stampede/harness.tar.gz`, which a plain search does not open. The SM classified those three on 2026-10-05: shell-variable references in two run scripts pointing at 127.0.0.1, with no literal password. None is H-89's, and the 8 lines the branch's range adds are all in `AutoGrader/tests_redis_hygiene_databases.py` (H-97), as the evidence says.
- My record, probe, mutant and logs hold no such URL (same pattern, 0 lines).

**N3 (the switch).** The scrubber is on in every environment and off only while tests run, decided by the word `test` in the process's arguments or `pytest` being loaded: the same test two older settings lines use. So a management command given `test` as an argument would run unscrubbed. No environment variable switches it off.

**N4 (merge-down, for 0b's checklist).** `phase2/epic-a` has its own `AutoGrader/sentry_scrubbing.py` and `tests_sentry_scrubbing.py` (BE-A-04, `d960176d`: `before_send` only, addresses only, top-level strings only, fails open). Merging H-89 conflicts there (add/add) and in the Sentry block of `settings.py`. By the SM's ruling H-89's wider scrubber replaces the epic's.

**N5 (deploy).** A process that cannot import `AutoGrader.log_scrubbing` or, with Sentry configured, `AutoGrader.sentry_scrubbing` does not start. That is intended (it never runs without the scrubber) and read from the code, not tested by a failed start. A code-only rollback to the batch base needs no step.

Logs: `runs/h89_26774ba8.log`, `runs/h89_mutant_Y9_26774ba8.log`. Probe: `h89_probe_test_vf1a_h89_probe.py`. Mutant: `h89_mutant_Y9.py`.
