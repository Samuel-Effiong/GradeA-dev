# H-216: a student's synchronous credit refusal reads the student sentence

Redo on the coded road (beta 913f248f replaced refusal_response() with coded_response(); the old attempt task/h216-sync-student-credit-sentence f5d5ddf3 could not be rebased). SM rulings 2026-10-14: approved as proposed; Q5 the student's `remediation` is null (the sentence already says what to do); grade (teacher-only) unchanged; a control for a student's non-credit refusal.

## The fault (read on 913f248f)
`StudentSubmissionViewSet.partial_update` (PATCH, the synchronous edit) and `upload_answers` (the synchronous answer upload) answer a failure through `_failure_response`, now `coded_response`. A credit refusal (the teacher's wallet above 0 but below the call's estimate) gave a STUDENT the generic sentence "There aren't enough AI credits available for this. The credit wallet needs to be topped up before it can run." in `error` AND "Top up credits, or ask your school administrator, then try again." in `remediation`.

## Change
064e7bb2 tests only, then daac5cf5: `_failure_response(exc, fallback_message, requester=None)`; for a STUDENT and an `InsufficientCreditsError` the coded body keeps reason_code INSUFFICIENT_CREDITS, the 402 and the legacy `code`, reads STUDENT_UPLOAD_NOT_PROCESSED and carries remediation null. The two student arms pass `request.user`. No migration.
Existing tests changed to expect the student sentence for a STUDENT caller (each pinned the generic text): billing.tests.test_refusal_handling D9 (`credits_message` parameter on assertCleanRefusalResponse), billing.tests.test_refusal_handling_gates RefusalIsolationTest.test_a_refused_student_learns_nothing_about_the_teachers_wallet, students.tests_async_edit_path UpdateAsyncRouteTest.test_sync_patch_uses_the_same_service_and_answers_4xx_for_user_errors.

## Written reasons and expected sets
In `chain_daac5cf5/config.py.txt` (sha256 bbf7a70dd4a342c1a8ca7ae3b061c68c182d4bfe9750b50559dd6b6243696c42), written before the run and pre-read by the Verifier; its K5 set was one test short at first (Verifier's F1: the gates module's student plan test) and was fixed before the grant.

## The chain (a6's GRANT 12:07 WAT; logs gzipped in chain_daac5cf5/)
Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e (with the subTest block fix, re-read by the Verifier and a6). One systemd-inhibit around the whole chain. Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) 4 modules from the tip, on 913f248f | "Ran 57 tests in 17.518s", FAILED (failures=6, skipped=1); "6 failing, as expected"; "the red set is the expected one, each for its written reason" (D9's [credits] subTest block holds both fragments, r log lines 964-973) |
| (a) 20 labels | "Ran 303 tests in 79.760s", OK (skipped=1) |
| (b) baseline | OK (skipped=1) |
| K1 switch removed | 6 failing, as expected |
| K2 the edit drops the requester | 5 failing, as expected |
| K3 the sync upload drops the requester | 1 failing, as expected |
| K4 remediation left as the spec's | 5 failing, as expected |
| K5 the credit check dropped | 3 failing, as expected |
| K6 the student check always true | 1 failing, as expected |

"(b) every mutant killed, each failing set and reason as written"; "h216 CHAIN END: green as written" at 12:14:15. Rule 24: the row's regression is its batch's full run.

## Not covered here
At a wallet of exactly 0 the request never reaches the view (HasCreditBalance refuses first): row H-225, stacked on this branch.
