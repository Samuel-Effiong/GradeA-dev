# Verification: bundle 7 merge-down into Epic A (ed)

- **Branch:** task/epic-a-merge-down-b7 at **318895d0**, from phase2/epic-a db6f5155. Merge 335e6c81 takes beta 63c3da22 (bundle 7 as pushed). After the merge: 77f4dc2b (one comment), 6029963f (the PII log baseline emptied), then evidence only.
- **Verifier:** v2 (independent), 2026-10-05. One slot from 0b, 16:58 to 17:01.
- **Verdict:** **VERIFIED-WITH-NOTES**

## The merge, as read (no test run)
| Check | Result |
|---|---|
| Parents of 335e6c81 | db6f5155 and 63c3da22 |
| Conflicts, from v2's own in-memory merge before ed's | Six files, the same six ed resolved |
| `AutoGrader/sentry_scrubbing.py`, `AutoGrader/tests_sentry_scrubbing.py` | Identical to beta's (SM ruling: H-89's scrubber replaces the epic's) |
| `AutoGrader/settings.py` | The Sentry block is beta's. Against beta the file differs only in the epic's own parts: the `client_request_id` log format, the audit app and middleware, two audit beat entries with their health rows, four `FAILED_AUTH` settings |
| `assignments/tasks.py`, `classrooms/serializers.py`, `classrooms/services/roster_import.py` | Identical to db6f5155 |
| The eight divergences recorded by earlier merge-downs | All identical to db6f5155 |
| v2's survival script on the merge | 112 lost lines, all in the six conflicted files: 102 epic lines (the old scrubber, its tests, the old Sentry block) and 10 beta lines in the three files where the epic's side was kept. None elsewhere |
| The old hook's users on the epic | Only settings.py and its own five tests; both replaced |
| Beta's scrubber against the epic's old one | It covers every part the old one covered (log entry message, formatted text and params; exception value; frame variables) and more |
| 77f4dc2b | Comment only, and true on the epic. v2's pre-read had missed this second copy; ed found it |
| 318895d0 against af350044 (the tip v2 matched earlier) | Only files under docs/evidence/epic-a-merge-down-b7/ |

## Beta's ids-only guard against the epic's code (v2's own scan)
v2 wrote a scan with the rule of `AutoGrader/tests_no_pii_in_logs.py` and ran it on trees, not on a checkout.

| Tree | Log or print calls passing an address or a name |
|---|---|
| db6f5155 (the epic alone) | 58, in exactly the seven baselined files |
| 318895d0 | 0 |

- `scripts/pii_log_baseline.txt` has no entry left at 318895d0. That is right: all seven files are clean.

## The scrubber under the epic's log format (v2's probe and mutants)
Neither side tests this pairing. The epic's two formatters print a `client_request_id` field and its console handler runs the epic's filter; H-89 was written against beta's format.

- **By reading:** the field is the only one in the format that comes from the client, and the epic keeps it only if it parses as a UUID.
- **Probe** (GAP-v2-handover/tests_vf2_md7_probe.py, 6 tests, built from the real `settings.LOGGING` formatters and the real filter): **Ran 6, OK.** With the scrubber on, a line holds neither an address nor a URL password passed as an argument, as exception text, or in a traceback, and the epic's two id fields are still printed. With it off (the test runner's default) the same line holds the address, so the first three tests are not vacuous.

| Mutant | Against the sides' own tests | Against v2's probe |
|---|---|---|
| Z1: the message is returned unscrubbed | KILLED. Ran 35, failures=17; the expected test among them | KILLED. Ran 6; b1 (expected) and b2 |
| Z2: the factory does not scrub exception text | KILLED. Ran 35, failures=6, errors=1; the expected test among them | KILLED. Ran 6; b3 (expected) |
| Z3: the client's id is kept as sent (epic side) | **Not killed as expected (KILLED_OTHER).** Ran 78, failures=4, without the test v2 had named | KILLED. Ran 6; b4 (expected) |

- **Z3's wrong expectation is v2's.** The test v2 named sets the id on the request object directly, a path the mutated function is not on. The four tests that did fail are the ones on the header path: `test_a_client_cannot_choose_the_trace_id_by_sending_one`, `test_a_non_uuid_inbound_id_is_not_kept_at_all`, `test_an_inbound_id_that_is_not_a_uuid_is_not_kept`, `test_an_inbound_uuid_is_kept_as_untrusted_context`. The mutant does not survive. v2's judgement: no gap in the epic's tests. The header path, which Z3 breaks, is held by those four; the path the named test covers is a different check and is not touched by Z3.
- **The expected names were written before the run** (16:53, in the runner; 0b read it before the grant).
- **Rules 17 and 18:** `PYTHONDONTWRITEBYTECODE=1`; `__pycache__` of the mutated module's directory deleted before each mutant and after each restore; every restore equal to the commit's blob by sha256. Each inner run wrote straight to its own file with stdin from the null device.
- **A run that tested nothing:** v2's first command (16:58:20, one second) used a Python without Django and stopped at the import. Log kept (`md7_318895d0_baseline_run0_wrong_python.log`). Re-run at once with the team's virtualenv, inside the same grant; 0b was told.
- The mutated code (`AutoGrader/log_scrubbing.py`, `AutoGrader/request_context.py`) is not changed by the merge. These mutants show the tests of the pairing have teeth; they do not judge ed's resolutions.

## ed's gates (read, not repeated: rule 15)
| Gate | Tip | v2 read in the committed logs |
|---|---|---|
| Reproduce-first: beta's guard on the seven files as at db6f5155 | af350044 | Ran 4, FAILED (failures=1), the guard's repository-wide test |
| `makemigrations --check` | af350044 | No changes detected |
| 14 changed modules and 18 guard modules | af350044 | Ran 426, OK |
| 22 mutants on the epic's files | af350044 | 22 KILLED in the results file; each of the 22 per-mutant logs has its own "Ran" line (16 tests, or 56 for the six on settings.py) and FAILED; the expected test is among the failing ones for all 22 |
| Regression, AutoGrader and billing | b4e8da84 | Ran 2752, OK. v2 unpacked the committed log and its sha256 matches the evidence (prefix c6772a76d7cf9829) |

## The credential pattern (SM rulings of 2026-10-05)
- ed's evidence folder at 318895d0, 30 files, the gzipped log opened: 0 URLs with anything in the password position; 0 literal assignments to a name containing PASS, PWD, SECRET, TOKEN or KEY. Counted by program; nothing printed.
- The branch's commit messages: 0.
- This record, the probe and the runner: 0.

## Notes (none blocks)
1. **Two of v2's own mutant logs print the probe's made-up URL** with its made-up password (a failing assertion prints the line it checked): the probe-side logs of Z1 and Z2. They stay in GAP-v2-handover/runs/ and are **not for commit**. If the logs are wanted in the repository, those two need the lines replaced first, and the record must say so.
2. **Stale names in two epic documents.** Plan 04 and the docstring of `scripts/check_no_pii_in_logs.py` still describe the old hook. v2 asked ed to leave them: plan 04 is a versioned architecture document and changes through a docs branch. `docs/evidence/epic-a-pii-cleanup/EVIDENCE.md` is a record of its time and stays as written.
3. **The scrubber is off under the test runner by design.** No run here, ed's or v2's, shows it scrubbing in a running service; v2's probe switches it on for its own tests, as H-89's tests do.
4. **Not covered here:** every app other than AutoGrader and billing (0b's Gate 10 full run); H-89's mutants on the files identical to beta's; H-97's, H-107's and H-109's on test-runner files the epic had not changed.
5. **One epic line logs exception text as an argument** (`roster_import.py`, the refused-row warning). It names the row and the course, not the student; any address inside the exception's own text is the scrubber's to remove, which the probe's b2 shows.
6. **For later merge-downs:** the recorded divergences are unchanged, and none is added by this merge. The three files where the epic's side was kept this time (`assignments/tasks.py` already listed; `classrooms/serializers.py` and `classrooms/services/roster_import.py`) will conflict again if beta touches the same lines.

## Files
- Probe: GAP-v2-handover/tests_vf2_md7_probe.py. Runner: GAP-v2-handover/vf_md7_mutants.py.
- Logs: GAP-v2-handover/runs/md7_318895d0_baseline.log, md7_318895d0_mutants.log, md7_318895d0_mutant_logs/ (see note 1).
