# H-211: a student's refused answer-edit reads the fixed sentence (gates on `45e85882`)

Written 2026-10-09 from the files in `gate_files/`. Stacked on H-180's tip `90b5c763` (it reuses `StudentUploadNotProcessedError` and `STUDENT_UPLOAD_NOT_PROCESSED`); it can only merge with or after H-180. Rule: a credit refusal of an answer EDIT stores and returns the fixed student sentence when the one who started the edit is a student; a teacher who started it keeps the generic text. Who can start it: both (`update_async` serves the student's own submission and a teacher's course; `requested_by=request.user`).

| Step | Result |
|---|---|
| (r) the two new modules on base `90b5c763` (rule 22) | 12:36:31 to 12:36:49 Oct 9: 1 failing as expected for its written reason (the student test: assertion line and the generic text); 3 controls green by design |
| (a) 62 labels | 12:36:49 to 12:41:22: Ran 972 tests in 249.187s, OK, 0 FAIL/ERROR |
| (b) 6 mutants E1 to E5, P1 | 12:41:22 to 12:42:22: 6/6 killed, restore verified, every failing set as written (E1 1, E2 2, E3 1, E4 1, E5 1, P1 1) |
| (c) `c_h211.sh`, seven apps, parallel 2 | 12:56:34 to 13:10:16: Ran 5884 tests in 778.340s, OK (skipped=26), exit 0, stalled 0 |

## The stop (first chain run on `c65dbe41`, `gate_files/first_stop_c65dbe41/`)
One sentence: an EXISTING test (`billing.tests.test_refusal_handling...test_extract_answer_background_task_does_not_retry_a_refusal`, requester a student) still expected the generic credit text, which is exactly the behaviour this row changes; I had not grepped the old expectation before the run. Fix (tests only, `45e85882`): that test passes `credits_message=STUDENT_UPLOAD_NOT_PROCESSED`, the parameter H-180 added. One re-run, approved by the Release Engineer under the delegation, green.

## The test the Senior Manager asked for after H-180's M16
`students/tests_upload_bills_the_teacher_only.py`: a student whose OWN wallet is funded (100,000) while the teacher's is below the estimate (5,000) is refused with the student sentence, nothing is queued, and neither wallet changes; both balances are asserted before the upload. It is green at base by design (the door already bills the teacher only) and is killed only by mutant P1 (`max()` of the two wallets), so it is shown able to fail.

## Not shown / not cured
- Whether the teacher's own edit of a student's submission, refused for the teacher's wallet, reads sensibly: it keeps the generic text (test T2), as before.
- The window between the door/queue and the task (another job can spend the balance in between): Epic B's hold.
- The student site's handling of the 402 body and of this failure text was not read (as in H-180).
- Worktrees (rule 21): Grade-Automator-Plus-h211-student-edit-credit-sentence (stacked on H-180's).

## Verifier 1's record and the limits he named (added 2026-10-09)
Verifier 1 verified the row at `c6a1856c`: VERIFIED-WITH-NOTES, all his sets as written. His record and files are in `verification_1a/`, committed byte-identical to the originals in `~/Documents/Projects/GAP-1a-records/` (python files as `.py.txt`, logs gzipped; checked with `cmp`). Limits he named:
- N1: the SYNCHRONOUS student edit route (`StudentSubmissionViewSet.partial_update`, kept until H-11 retires it) answers a credit refusal through `_failure_response` with the generic wallet text, 402. H-211 changes only `extract_answer_background_task`. This is its own row, H-216 (author: Hardening Engineer); his probe p4 is its predicted red.
- N3: the window between the door/queue and the task (another job can spend the balance in between): Epic B's hold.
- N4: a plan refusal (no active subscription) read by a STUDENT on this path was not probed.
- A note on `assignments/tests_edit_refusal_student_sentence.py`: its check that "5000"/"25000" are not in the polled text runs over meta that holds a random submission uuid; a uuid can contain those digits (about 1 in 2,000 runs): a false red, never a false green. Not changed in this commit; to be asserted with the ids removed in a later tests-only commit if the Senior Manager asks.
- Worktrees (rule 21): Grade-Automator-Plus-h211-student-edit-credit-sentence (stacked on H-180's, now in beta); the H-180 worktree stays until H-211 is merged.
