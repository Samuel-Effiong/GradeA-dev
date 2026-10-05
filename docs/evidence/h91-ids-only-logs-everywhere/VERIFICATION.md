# Verification: H-91: no log or print call anywhere passes an address or a person's name @ 63765f80

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-05.
**Branch:** `task/h91-ids-only-logs-everywhere` @ **63765f80** (code tip `b6fbdbea`), on beta `141c8031`. Bundle 7. No migration, no model change, no settings change.
- `becbd499` (test), `0e123246` (billing's log calls), `f1ce00c6` (the rule over every production file), `f45dd0ef` (the remaining log and print calls)
- `6a82ef41`: the repository-wide guard moved under `AutoGrader/` (my pre-review's note, SM ruling)
- `b6fbdbea`: 0b's base update onto beta; `63765f80`: evidence (docs only)

The evidence is in `docs/evidence/h91-ids-only-logs-everywhere/`.

I ran in 0b's slot from my detached scratch checkout at 63765f80, with its own test DBs (`test_vf_h91`, the mutant on `test_vf_h91_mut`). The wrapper was rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`; serial; under rule 18 the output went straight to a file and stdin was `/dev/null`. Under rule 15 I cite d5's regression (billing + users + classrooms + assignments + the guard modules, 3878 OK, skipped=19, at `b6fbdbea`) and don't repeat it.

**Verdict: VERIFIED.** Nothing is required before the merge. The notes are for the package and the merge-down.

## What H-91 fixes, in one plain case
**On beta `141c8031` the worker log gets a pupil's full name on every grading; H-91 removes it.** `assignments/tasks.py:1115` printed the bound method `submission.student.get_full_name`, not a call. Its text holds the user object's text, and that is the full name. My probe V3 builds the same line with a made-up user: it reads `Starting grading of Submission <bound method CustomUser.get_full_name of <CustomUser: Vfsentinel Teacher>>`. At this tip the line prints the submission's id. The guard's own shapes test has that exact form (a print with the attribute not called), so it cannot come back unseen.

The same kind of change is made in 64 calls across 11 files: an id where an address or a name was passed.

## What I checked
| Point | Result |
|---|---|
| The rule is Epic A's hook's rule, exactly | **Holds** (pre-review, by reading and by running the epic's own hook code over the trees): a call to `print()` or to a logging method on any receiver, with `.email`, `.first_name`, `.last_name` or `.get_full_name` anywhere in an argument, in every `.py` file that is not a test module or a migration. |
| 64 calls in 11 files on beta, none here (the SM's check) | **Holds.** V2 runs the moved guard's own helpers, from `AutoGrader/tests_no_pii_in_logs.py`, over beta's tree read from git: **64 calls in 11 files** (billing/services 17, billing/stripe_service 16, billing/tasks 11, billing/access_control 7, the one-off script 4, and nine in six other files). Over this tree, through the guard's own file walk: **0**. |
| The changed lines still fire, and still let an operator find the row | **Holds.** V1 drives five of them for real: an AI-access error, the MailerLite skip and the MailerLite failure, a school-admin invitation resend with no school, and a failing roster row. Each line is logged, names its user by id (the roster line: the parsed row's number and the course's id), and has no address and no name in its message. |
| The guard moved under `AutoGrader/` runs from its new home | **Holds.** It ran in my baseline as `AutoGrader.tests_no_pii_in_logs`, and it is what kills my mutant, which sits in `users/`. |
| The battery was not repeated after the base update | **The reason holds** (0b's question). Between `2c336a50`, where the 14 mutants ran, and `b6fbdbea`, one of H-91's files changed: `classrooms/serializers.py`, in H-99's hunks, not H-91's. Every mutant is killed by the guard, and the guard ran at `b6fbdbea` (d5's 49 OK, and my run). V1 drives H-91's one line in that file for real at the tip. |

## Evidence
| Check | Result |
|---|---|
| **Run** @ 63765f80: my probes V1–V3 + `AutoGrader.tests_no_pii_in_logs` + `billing.tests.test_logs_carry_no_email` | **17 tests OK** (54 s). |
| **My mutant Y8** (the address put back in `users/mailerlite_service.py`'s failed-sync line, a file outside billing) | **KILLED** (17 tests, failures=3): by the repository-wide guard, by V1 (which drives that line) and by V2 (which finds one call on the tree). Same idea as d5's Q1. |
| d5's gates (cited) | At `2c336a50`: repro 16 tests / 3 failures; modules 146 OK; 14/14 mutants killed. At `b6fbdbea`: the guard, H-80's guard and the classrooms modules, 49 OK; the regression, 3878 OK (skipped=19). |
| No code change after the base update | `git diff b6fbdbea 63765f80` touches the evidence folder only. |
| Hooks | `pre-commit run --from-ref 141c8031 --to-ref 63765f80` passes (the range; I did not run each commit separately). |
| Merges | `git merge-tree --write-tree` is **clean** against `task/beta-batch-7` `085adecd` and against H-89's `830ea9d5`. |

**The regression stalled four times before it passed, and that is not H-91's.** Each stall had the run's output piped to a timestamper; with the same tip, labels and worker count and the output written to a file, it passed. The cause is in the test runner and in how the PDF renderer starts its browser (H-107, which comes to me next). On 2026-10-02 I read H-91's guard for 0b and found nothing in it that could hold a worker pool: it walks and parses files in-process, 297 of them in half a second. The pass is the result I cite.

**Rule 17.** Both runs had `PYTHONDONTWRITEBYTECODE=1`, and the mutant was applied with `python -B`. `__pycache__` under `AutoGrader/`, `billing/` and `users/` was deleted before the baseline, before the mutant and after the restore (the logs show 0 directories each time). The restored `users/mailerlite_service.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

**The credential check on my own files** (the SM's widened form, values not printed). The probe, the mutant and the two logs hold no URL with anything in the password position and no percent-encoded form. Four lines of the probe's source have the assignment form, all made-up test fixtures: the repository's standard fixture account password in two `create_user` calls (the same literal is in 129 files of this tree), and two overrides of the MailerLite key setting (an empty string and a one-letter stand-in).

## Notes
**N1 (what H-91 does not cover; by design, in the evidence).** The rule is about what the code passes to a log or print call. Exception text and tracebacks are outside it: of the five lines V1 drove, the two with a traceback carried an address or a name in the traceback. H-89's scrubber covers those for log records. It does not reach a `print()`; `assignments/tasks.py:164` prints a formatted traceback, which the SM has made a LOW backlog row.

**N2 (merge-down, for 0b's checklist).** The rule is the epic hook's own, so the files H-91 cleaned can leave the epic's `scripts/pii_log_baseline.txt` at the next merge-down (seven of them are listed there, by my pre-review).

**N3 (the two guards).** `billing/license_service.py` and `users/signals.py` stay under H-80's stricter rule as well, in `billing/tests/test_logs_carry_no_email.py`; the repository-wide rule lives in `AutoGrader/tests_no_pii_in_logs.py` and belongs on the brief's guard list (SM ruling).

Logs: `runs/h91_63765f80.log`, `runs/h91_mutant_Y8_63765f80.log`. Probe: `h91_probe_test_vf1a_h91_probe.py`. Mutant: `h91_mutant_Y8.py`.
