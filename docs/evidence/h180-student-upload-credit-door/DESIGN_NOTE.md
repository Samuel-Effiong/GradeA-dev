# H-180 (STU-LOSS): a student's upload is refused at the door when the teacher's wallet cannot pay for it

Draft, 2026-10-08, read from beta 035e0a07 (worktree
`Grade-Automator-Plus-h180-student-upload-credit-door`). Rulings: the Senior
Manager's of 2026-10-07 20:25 (summary at the end). Nothing built, nothing run.

## The fault, by reading
`students/views.py` `upload_answers_async` (a student), `batch_upload`
(a teacher) and the assignment upload-async are guarded by
`HasCreditBalance`, which refuses only a wallet at 0 or below. The queued
task `upload_answers_engine_async` (`assignments/tasks.py` 787) converts the
file (`prepare_ai_content`: an image is compressed; a PDF becomes one image per
page), then the extraction call goes through the gate in
`ai_processor/services.py` (~4735): `estimate_total_token(...)` against
`wallet.total_remaining_credits()`, and a balance below the estimate raises
`InsufficientCreditsError`, a final refusal (`UPLOAD_REFUSALS`). The estimate
is the text tokens + image tiles + pdf pages*1200 **+ 20000 flat**. So a
wallet of 5,000 passes the door, the student is told "Answer Extraction
Started", and the task then fails: the student's file is gone from their side
and the answers are not submitted.

## The cure
1. One function answers "what will this call be estimated at":
   the traversal of `user_prompt`, `system_prompt` and `messages` that the gate
   does inline today becomes a method (`estimate_messages_cost`), and the gate
   calls it. The door calls the **same** method. Not a copy.
2. The door (three routes) converts the file as the task will
   (`prepare_ai_content`), asks the method for the estimate of that content
   (the file's part only: the task adds the system prompt, the questions and
   the roster, so the door's number is a LOWER bound of the task's), and
   compares it with the wallet of the right user (the course's teacher, for a
   student; the caller, for a teacher) with the same balance call
   (`total_remaining_credits()`). Superadmin with both flags / unmetered
   accounts are passed as `HasCreditBalance` passes them.
   A lower bound means the door never refuses what the task would accept; it
   refuses only what the task would certainly refuse. The in-task gate stays
   the authority.
3. Refusal: 402, the same code the synchronous route gives, with the fixed
   student sentence: "Your answers were not submitted. Your teacher's account
   can't process uploads right now. Please keep your file and try again later,
   or let your teacher know." No balance, amount, "credit" or "wallet" in it.
   A teacher is refused with the existing generic credit message (the one
   `billing/errors.py` already holds).
4. The failed-task text a **student** reads in `users/views.py` `task_status`
   for a credit refusal gets the same sentence (a teacher's stays).

## Not done, said now
- The **window** between the door and the task: another job can spend the
  balance after the door and before the gate. The in-task gate still refuses
  then. Epic B's hold closes it. Stated in the package.
- **No teacher notification** (Epic B warnings: a follow-up line).
- The door is a lower bound; a wallet between the door's number and the
  task's number still loses the upload (the system prompt, the questions and
  the roster are not in the door's number). The size of that gap is measured
  and stated, not guessed.
- Request-time cost: the door converts the file in the request. One number,
  measured on a large PDF, goes in the evidence.

## Tests first (their own commit), each seen red
- the door refuses: student, wallet below the file's estimate (> 0): 402, the
  fixed sentence read from the response **key** (non-empty before any
  "not in"), no task created, no credit spent;
- the same for a teacher batch upload and the assignment upload-async;
- the door lets through: wallet above the estimate (task is queued);
- unmetered / superadmin both flags: passes as before;
- one function: the door's estimate and the gate's are equal for the same
  messages, and a test fails if the door stops calling the shared method;
- the status route: a student reads the fixed sentence for a credit refusal; a
  teacher reads the old text;
- the fixed-sentence test: no balance, amount, "credit" or "wallet" in it.
Rule 15/20: the existing modules of the three routes are in the gate list, and
**every fixture wallet in them is read and listed first** (e.g.
`students/tests_upload_pipeline.py`, `students/tests_async_edit_path.py`,
`assignments/tests_partial_update_credit_gate.py` with total_credits=100,
`users/tests_credit_balance_permission.py`, `students/tests.py`, the
superadmin/unmetered modules) because a wallet that was enough for "balance >
0" may not be enough for "balance >= estimate" (the flat +20000).

## Summary of the rulings
Door on three routes with the shared function; fixed student sentence, 402;
status text for students; no teacher notification; state the window; measure
the cost; tests first as their own commit; read fixture wallets before the gate.
