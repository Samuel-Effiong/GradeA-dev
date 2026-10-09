# Verification: H-152, the old activation door is closed and the leftover accounts are converted (ed)

- **Branch:** task/h152-old-activation-door-closed at **8b23502b**, stacked on H-148 (19f5c872). Code last changed at 76c82c35; 9e104937 and 8b23502b add only docs/evidence/h152-old-activation-door-closed/ (v2's own two-tree diff of 76c82c35 against 8b23502b: 31 files, none outside that folder). ed's last gate ran on 76c82c35, ed's last regression on 9e104937, v2's run on 8b23502b.
- **The change:** the two routes of the old sign-up scheme (`POST /auth/register/student` and `/course/student/renew-student-token`) answer 410 with one fixed sentence. The one-off command `backfill_pending_student_invites` converts the accounts the old scheme left behind: each gets a password, an email that carries it, and its pending place becomes an enrolment. The sender `send_student_login_invitation_email` now says whether the email was handed to the queue. No migration, no model change.
- **Verifier:** v2 (independent), 2026-10-07. One slot from 0b, 16:48:53 to 16:49:17 WAT, 1-minute load 1.88 to 1.78, nothing else of the team's running; nothing timed.
- **Verdict:** **VERIFIED-WITH-NOTES.** Both routes are closed behind their rate limit; a converted student signs in with the password the email carries; an account whose email was not queued, or whose conversion raised, is left exactly as it was and the run goes on; a database error stops the run and says at which account. Note 1 is a fault v2 found by reading at the first hand-over, cured since. The other notes are limits.

## The fault found by reading, and its cure (note 1)
At the first hand-over (7b49fc67, then called final) the sender read `course.teacher.get_full_name()`. `Course.teacher` may be empty. One leftover student whose pending place is in a course with no teacher raised inside the sender, and the command had no catch but for the unqueued email: the run stopped at that account, every time it was run again, and the preview (`--dry-run`) did not show it, because the preview does not call the sender. v2 found this by reading, before any run; ed's tests and v2's first probe both had only courses with a teacher.

The Senior Manager ruled the cure in two steps, and ed made it at 76c82c35:
1. The sender no longer needs a teacher: with none, or with a teacher whose name is empty, the sentence is "You have been invited to join <course> on Grade A+."
2. Around the conversion of one account: an error that is not a database error leaves that account as it was (its transaction is undone), prints `NOT converted (<the error's type>): student <id>`, is counted in the summary, and the run goes on. A database error (`django.db.Error`) prints a STOPPED line naming the account and is raised again. A keyboard interrupt is not caught.

v2 read 76c82c35 line by line: no fault found by reading. v2's probe as written before the cure is kept, unrun, beside the one that ran (`tests_vf2_h152_probe_as_written_at_7b49fc67.py`): its X4 and X5 said the run STOPS, which was true then.

## v2's probe, each expectation written first
`tests_vf2_h152_probe.py`, copied to `classrooms/` of v2's scratch worktree, never committed to the branch. Seven tests. The email task is replaced; no email leaves.

| Probe | What it holds |
|---|---|
| X1 | both closed routes through their REAL rate limit: 410 with the sentence while the budget lasts, then 429; the two routes share one budget |
| X2 | the password in the queued email signs the converted student in at the login route; the place is ENROLLED; the old code is gone |
| X3 | the broker's own error (kombu `OperationalError`): the account is left exactly as it was, the command's output has no error text, one log record carries the broker's text |
| X4 | an error that is not an outage: `NOT converted (NotABrokerError): student <id>` and nothing of the error's text; the run goes on to the next account; a second run takes the first up |
| X5 | a pending place in a course with NO teacher, and one in a course whose teacher has no name: both converted, the email names nobody, the password signs in |
| X6 | a database error inside the conversion (`DataError`) stops the run; the account is left as it was |
| X7 | the case ed left to v2 on purpose: the email IS queued and then the COMMIT fails (a deferred foreign key, in a `TransactionTestCase`, so it is a real commit and not a savepoint): the run stops with the STOPPED line, the account is as it was, the next account is not touched |

Baseline (ed's four modules and the probe): **Ran 48 tests in 2.419s, OK.**

| Mutant (rule 19) | Written before the run, every line of X1 to X7 read against it | As run (probe only: Ran 7 each) |
|---|---|---|
| U1 the email carries another password than the one saved | X2, X5 | FAILED (failures=2): X2, X5, both `401 != 200` at the sign-in |
| U2 the conversion of one account is not one transaction | X3, X4, X6, X7 | FAILED (failures=3, errors=1): X3, X4, X7 (the account is no longer as it was), X6 (the error) |
| U3 the closed door has no rate limit | X1 | FAILED (failures=1): X1, `410 != 429` |
| U4 the failed account's line carries the error's text | X4 | FAILED (failures=1): X4 |
| U5 the sender reads the teacher whatever the course | X5 | FAILED (failures=1): X5, the account listed `NOT converted (AttributeError)` |
| U6 a database error is taken for that account's and the run goes on | X6, X7 | FAILED (failures=2): X6 (`DataError not raised`), X7 (`IntegrityError not raised`) |

Six of six killed, every failing set exactly the one written; each restore matched the commit's file, `__pycache__` cleared, the tree clean at 8b23502b afterwards. Each of X1 to X7 was seen red. ed's tests were not run under U1 to U6 (rule 15).

## ed's gates, read by v2 from the committed logs (not repeated, rule 15)
v2 unpacked every committed log and read its end. EVIDENCE.md states one log checksum (the regression at 6cae1ad4, unpacked, starts 40ad4a37975e1db6): it matches. The other logs are stated by their Ran lines, which match.

| Gate | The raw log |
|---|---|
| First gate at 8b396aa9: tests before the change | Ran 11, FAILED (failures=10) |
| First gate: modules and guards | Ran 506 tests in 141.666s, OK (skipped=3) |
| Delta gate at 51cd2773 (the email must be queued) | Ran 11, FAILED (failures=7); Ran 553 tests in 241.860s, OK (skipped=3) |
| Second delta gate at 76c82c35, tests before the cure | Ran 10, FAILED (errors=9): nine lines, eight distinct tests (one test once per kind of database error), the eight ed named beforehand |
| Second delta gate: modules and guards | Ran 563 tests in 155.806s, OK (skipped=3) |
| Second delta gate: 22 mutants (C1 to C3, Q01 to Q09, R01 to R10) | 22 of 22 KILLED, each inner run Ran 32, `expected-but-passed []` 22 times |
| Regression, users and classrooms, at 9e104937, 16:41:56 to 16:44:35 | Ran 1132 tests in 143.619s, OK (skipped=4); no `FAIL:` or `ERROR:` line |

The six mutants on the two views files (D1 to D6) and Q10, Q11 (the enrolment file) ran in the earlier gates only; those files are untouched since. ed's hand-over file said "Q01 to Q11" for the last gate; ed corrected it; nothing committed says so.

- **Tests that EXERCISE what this row changes.** One `git grep` on 8b23502b over every test file, on 0b's grant, for the two routes' names and paths, the command and the sender: 49 lines in seven files, all in users and classrooms, the two apps of ed's regression. Outside ed's four modules: `classrooms/tests.py` and `classrooms/tests_roster_ready_to_use.py` replace the sender with a stand-in (a teacher's add ignores its answer), and `users/tests_throttle_client_identity.py` names a route in a comment. No test in another app touches any of them.

## ed's "where I would attack", answered
| Point | Answer | By |
|---|---|---|
| X1 to X7, U1 to U6 | as above | a run |
| A course whose teacher's name is only spaces | `get_full_name` strips each part and joins what is left: the result is empty, so the nameless sentence. v2's X5 has a teacher with EMPTY names, not spaces | reading only |
| A `django.db.Error` raised by something that is not the database | it stops the run and says a password MAY have been sent: the cautious side. v2 found no such raise in the sender or the dispatch | reading only |
| Can either line carry an address or a name? | both print `type(exc).__name__` and `student.pk`, nothing else from the error or the account. A class whose name is built from data would be needed; v2 knows of none and did not search the tree for one | reading only |
| The counts when all three kinds happen before a stop | on a stop there is NO summary; what was done so far is only in the lines printed above the STOPPED line. The runbook says to report that line | reading only |

## Notes
1. **The fault above**, found by reading, cured at 76c82c35, held by ed's ten tests and by X4 to X7.
2. **The catch is around the conversion step only** (stated by ed, accepted by the Senior Manager). An error while reading the list or printing the summary stops the run as before.
3. **The email is queued before that account's transaction commits.** X7 shows the consequence as a fact: after a failed commit the email with a password that was not saved has been handed to the queue. The STOPPED line says so. Not cured; the run is one-off and by hand.
4. **Queued is not delivered.** An email queued and lost later leaves a converted account whose password nobody holds; the way out is the password reset. Not tested.
5. **The service's log line for an outage carries the broker's own text** (X3). It is not for pasting into a ticket or a chat; the Senior Manager opened a row for it. The command's own output carries none.
6. **In ed's tests and in X3 to X6 "undone" is a savepoint** (the test's own transaction); only X7 crosses a real commit.
7. **Not checked:** the frontend page that used the two routes; the runbook against a real service; whether any course on a service has no teacher. The conversion is never run by the team on live data.
8. **In v2's logs:** under U2 the failure text shows the fixture's old six-digit code (a literal of the test, of the closed scheme) and the first eight characters of a digest of a password hash. No password and no hash is printed. v2's pattern check of its run files: no line matches.

## Files
In `~/Documents/Projects/GAP-v2-handover/`: this record; `tests_vf2_h152_probe.py` (ec0801f1a114e550), `vf_h152_mutants.py` (c7f35ed29daba8a9), `vf_h152_run.sh` (3a2d3ed86dbeaa75); kept unrun: `tests_vf2_h152_probe_as_written_at_7b49fc67.py` (3a5210477876eb79), `vf_h152_mutants_as_written_at_7b49fc67.py` (106bab9329a553d8); `runs/h152_8b23502b.status` (3bf173a58166ef64), `runs/h152_8b23502b_script.out` (ccb50d93688b04ac), `runs/h152_8b23502b_baseline.log` (bd1fe3385e49e0f6), `runs/h152_8b23502b_mutants.log` (5c957703821a44de), `runs/h152_8b23502b_v2_mutant_logs.tar.gz` (fffe907472acc19c: the baseline and six judgement files), `runs/h152_8b23502b_route_tests_search.txt` (5e65eaf546951d53).
