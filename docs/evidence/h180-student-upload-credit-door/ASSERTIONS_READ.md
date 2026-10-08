# H-180: every assertion read against the real value of what it inspects

Written 2026-10-08 BEFORE any run of the row. Nothing here was seen by a run
(the route tests need a database; I did not call the tests by hand). The
"real value" column says what the assertion looks at and in what form, from
the code, and which mutant (runner `run_mutants.py`) makes it red.

| Test | What it inspects (form) | Negative checks and what decides them | Red under |
|---|---|---|---|
| S1 poor student refused | `response.status_code` (int 402), `response.data["code"]` and `["error"]` (str, the literal sentence typed in the test, not the code's constant), the launch mock, the count of tracked tasks | none | M1, M7, M16 (and on the base) |
| S2 sentence names no money | `response.data["error"]` read as a str; `assertTrue(message)` FIRST, then `assertNotIn` of six words in its lower-case form; the keys are exactly code and error | each `assertNotIn` is preceded by the non-empty check; the words are decided by M7 (the generic message contains "credit") | M1, M7, M16 |
| S3 rich student queued | status 200, `task_id` "task-1", launch called once, the payload's `content_b64` decoded equals the PNG bytes | none | M9 (the file is not rewound: payload empty) |
| S4 the line is the estimate | the estimate the REAL method returned for this file (list of one int, `assertGreater` 20,000 first), then 402 one below and 200 at exactly the estimate | none | M1, M4, M5, M16 |
| S5 the door asks the shared method | 402 when the method is patched to 10**9 on a rich wallet, 200 when patched to 0 on a one-credit wallet | none | M1, M5, M16 |
| S6 unreadable file left to the task | 200 and launch called, with a 5,000 wallet and `b"not an image"` | the test client re-raises a server exception, so a door that raises is an ERROR | M10 |
| S7 teacher without a wallet | 200 and launch called, the permission patched True | none | M11 |
| B1, A1 poor teacher refused | 402, code, `["error"]` equals the generic message constant (a different string from the student sentence, so a swap is seen), no launch, no session, no tracked task | none | M2/M3 (the route), M8 |
| B2, A2 funded teacher queued | 202, task count, each payload decoded equals the PNG | none | M9 |
| D1 super admin | a poor TEACHER is refused first (control), then the poor SUPER ADMIN gets None | the None is a decision because the control refuses | M12 |
| G1, G2 the gate asks the shared method | `InsufficientCreditsError` raised / not raised, `__ai_model` called or not | none | M6 |
| G3 the method counts | `estimate_total_token` called once with the text of the three places joined, `[b"img"]`, `[b"pdf"]`; value 777 | none | M17 (pdf), M18 (system prompt) |
| X1 student reads the fixed sentence | the task row's status (FAILURE), `response.data["meta"]` as str (non-empty first), the literal sentence in it, the row's `.error` equals it | `assertNotIn` of "5000", "25000", "wallet", "refill" after the non-empty check; "25000" is in the gate text the engine is made to raise, so it is decided | M13, M15 |
| X2 teacher keeps the generic text | same, the generic constant in `meta`, `.error` equals it, the student sentence NOT in it | non-empty first; the student sentence is a different string | M14 |

**Which defence each test proves:** the door (S1, S2, S4, S5, B1, A1, D1 and
the payload checks) and the gate (G1, G2) are separate defences; the
student's text on the polled status (X1, X2) is a third. G1/G2 do not pass
through the door and S/B/A tests do not reach the gate (the task is patched
or the launch is mocked), so each defence is shown on its own.

**Checks no mutant decides (not evidence):** `assertEqual(sorted(response.data),
["code", "error"])` in S2 (no mutant adds a key); the `assertGreater(estimate,
20_000)` in S4 (a constant); S3's task id "task-1" (the mock's own value);
the PDF branch of the door (no test uses a real PDF: the existing route
modules of the chain's step (a) upload PDFs through the door).

## The fixture wallets of the existing route modules (read BEFORE the gate)
A wallet that passed "balance > 0" must now also pass "balance >= the file's
estimate" (about 20,300 for a small image, more for a PDF's pages).
- `students/tests_async_edit_path.py` 100,000 (`_fund`); `students/tests_post_grading_submission_lock.py` 100,000; `students/tests_submission_tenancy.py` 100,000; `users/tests_credit_balance_permission.py` 100,000 (default), no upload route; `assignments/tests_upload_batch_billing.py` 500,000 (and a 20,000-credit PLAN, not a wallet); `billing/tests/test_h38_part2_removed_teacher_routes.py` 500,000 wallet (plan 20,000); `classrooms/tests_student_summary_tracking.py` 100,000; `students/tests_grading_idempotency.py` 100,000; `ai_processor/tests_grading_benchmark.py` 5,000,000.
- `assignments/tests_security.py` patches `HasCreditBalance.has_permission` True: the teacher has NO wallet, so the door leaves it to the permission (test S7 holds that).
- `billing/tests/test_refusal_handling.py`: `underfunded_teacher` has a 1,000-credit plan, but the three route entries that use an upload route post `{}` as an UNSUBSCRIBED teacher and are refused by the permission (402) before the door; the `underfunded_teacher` cases run through the synchronous routes and the gate, which this row does not change in outcome.
- `students/tests_no_grade_tell_before_release.py`, `AutoGrader/tests_submission_audience_guard.py`, `students/tests_student_upload_answer.py`: funded through `students.tests_post_grading_submission_lock` (100,000) or never reach the door (closed-paper refusals come BEFORE the door).
- Tests that patch `AssignmentProcessingService.prepare_ai_content` (a class attribute: the door calls the patch too): the async-route ones patch only `launch_processing_task`; the ones that assert `prepare_ai_content` NOT called are on the synchronous route, which has no door. If a patched prepare returns a non-list the door cannot read it and leaves the upload to the task.
