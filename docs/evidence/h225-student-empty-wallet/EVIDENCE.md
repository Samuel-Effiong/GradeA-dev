# H-225: a student at an EMPTY teacher wallet reads the student sentence

Found by the Verifier by reading (2026-10-14); the SM made it its own row, shipping with H-216 on a branch stacked on H-216's tip daac5cf5. Approved design: a subclass raised in the permission, which knows the requester.

## The fault (read on daac5cf5)
At a teacher wallet of exactly 0, `HasCreditBalance` (users/permissions.py) refuses before the view with `EmptyWalletError`; `users.exceptions.custom_exception_handler` answers it through `coded_response`: 402, INSUFFICIENT_CREDITS, `error` = the generic wallet sentence and `remediation` = "Top up credits, ...". HasCreditBalance reaches a STUDENT only on StudentSubmissionViewSet (students/views.py:477 create/upload_answers/upload_answers_async/update, :484 partial_update/update_async, and the two @action declarations at :563/:665); every other use is an IsTeacher route, and assignments/views.py:602 is the teacher's assignment edit.

## Change
- aca7da9a tests only: students.tests_h225_student_empty_wallet (s1 sync upload, s2 async upload, s3 PATCH, s4 async edit, s5 the student's own funded wallet changes nothing; controls c1 a teacher at 0 keeps the generic body, c2 a student's non-credit refusal is unchanged).
- c5e8ef1a fix: `billing.errors.StudentEmptyWalletError(EmptyWalletError)`; HasCreditBalance raises it when it refuses a student; the handler's coded branch sets `error` = STUDENT_UPLOAD_NOT_PROCESSED and `remediation` = None for it. The permission unit test's comment no longer calls a student's message the generic one. No migration.
- 2fd72948 tests: D11 (billing.tests.test_refusal_handling) hits all 18 credit-guarded endpoints at an empty wallet, six as the STUDENT, and pinned the generic text for all; its student rows now expect the student sentence. **This belonged in the tests-only commit and was found only while writing the expected sets, after the fix**; it is in the (r) stage below, so it is still seen red on the base.

## Repo grep before the fix (SM's check; a6's GRANT 12:15; output `repo_grep_aca7da9a.txt.gz`)
One `git grep` over aca7da9a (tests included) for: every mention of EmptyWalletError; `type(...) is/==/in`, `__class__`, `__name__`, `__qualname__` near credit/refusal names; class-keyed mappings naming the credit errors; the refusal registries; pickling/serialization of the credit errors. Findings:
- Every type decision on the credit errors is `isinstance`: `billing.refusals.is_permanent_refusal` (PERMANENT_AI_REFUSALS tuple), `AutoGrader.reason_codes._refusal_reason`, the handler. A subclass is seen wherever the parent is.
- users/tests_credit_balance_permission.py uses `assertRaises(EmptyWalletError)` and `assertIsInstance(..., InsufficientCreditsError)`: satisfied by the subclass.
- No class-keyed dict, no `type(exc) is EmptyWalletError`, no pickling or Celery transport of EmptyWalletError (it is raised in the request only).
- Names in LOGS change: `log_refusal` and the safe-logging helpers print `type(error).__name__`, so a student's refusal is logged as `StudentEmptyWalletError` instead of `EmptyWalletError`. No test asserts that log text (grep: the only "refused: EmptyWalletError" lines are in committed evidence logs under docs/evidence/).

## Written reasons and expected sets
GAP-58-runs/h225/config.py (copied here with the chain's logs after the run): (r) on daac5cf5 = {s1-s5, D11 (its six student subTests)}, each printing the generic and the student sentence; mutants P1 (the SM's: plain EmptyWalletError for a student), P2 handler branch removed, P3 remediation left, P4 subclass raised for everyone, P5 handler widened to the parent class.

## The chain on 4d3780cb (a6's GRANT 12:21 WAT; logs gzipped in chain_4d3780cb/)

Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e; config `config.py.txt` sha256 e891417d638f8b5f519cb9d6c77d688bc5fbddcb1d18ba77993ddbd885d47cf7 (Verifier pre-read: clear). One systemd-inhibit around the whole chain. Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the h225 module and test_refusal_handling from the tip, on daac5cf5 | "Ran 46 tests", FAILED (failures=11: s1-s5 and D11's six student subTests); "6 failing, as expected"; "the red set is the expected one, each for its written reason" |
| (a) 21 labels | "Ran 331 tests in 71.551s", OK (skipped=1) |
| (b) baseline | OK |
| P1 plain EmptyWalletError for a student (SM's mutant) | 6 failing, as expected (failures=11) |
| P2 handler branch removed | 6 failing, as expected (failures=11) |
| P3 remediation left as the spec's | 5 failing, as expected |
| P4 the subclass raised for everyone | 2 failing, as expected (failures=13: c1 and D11's 12 teacher subTests) |
| P5 the handler widened to the parent class | 2 failing, as expected (failures=13) |

"(b) every mutant killed, each failing set and reason as written"; "h225 CHAIN END: green as written" at 12:25:17. Rule 24: the row's regression is its batch's full run.

The Verifier's note N1 (assert the student subclass in users/tests_credit_balance_permission's student case, so P1 also dies at the unit level) was not taken for this chain: it would have moved the cleared tip; P1 is killed by the five routes and D11.
