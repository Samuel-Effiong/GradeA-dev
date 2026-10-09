# Verification: H-180, the student upload credit door @ 4216575f (Verifier 1)

Verifier 1a. Author: d5. Tip 4216575f24db419acf7645996553ecdd77ed765a, branch task/h180-student-upload-credit-door, over beta 8567a7a0 (a base update merge; docs-only commit dba82a7c over the gated 90b5c763).
Written for the Senior Manager and d5 (to commit byte-identical). Clock: reads 12:37 to 12:48, chain run 12:52:29 to 12:54:22 on 2026-10-09, all from `date`.

## Verdict: VERIFIED-WITH-NOTES

By reading: the Senior Manager's four points. By running: two tests of my own (p1, p2), each seen red by a mutant of mine for the reason I wrote down. I did not repeat d5's gate, battery or regression (rule 15).

## Read (no run)
1. M16's corrected set {S3, S4, S7} is right. Every user, a student too, gets an empty wallet from users/signals.py; the mutant bills the student's own balance 0 and refuses every upload, so S1, S2, S5 (which expect a refusal or an estimate of 0) stay green and S3, S4, S7 fail ("402 != 200" three times in the raw output). Only S7 depended on "no wallet", and it now deletes the wallet and asserts it existed and is gone before the upload. No other written set depends on it. The correction is dated after the regression and the raw results are unchanged.
2. The no-wallet test patches HasCreditBalance to True but still reaches the door's own branch: the course's teacher is loaded fresh in the view; a reverse one-to-one miss raises an error that subclasses AttributeError (Django related_descriptors), so getattr(.., None) gives None and the door returns None. Mutant M11 (the branch removed) gives "500 != 200" in S7 alone.
3. The student sentence is on the student upload route (IsStudent) and in the task's stored and polled text. The other two routes are IsTeacher; they answer the generic credit message (tests B1, A1). The sentence holds none of credit, wallet, balance, refill, top up or a number.
4. Both first-stop logs are kept (first_stop_a_657e2d72, second_stop_cb2eb573: Ran 930, failures=2 each, the tests GATES_AND_STOPS names). Final (a) Ran 930 OK; (r) 11 red (failures=4, errors=7) with the written reasons; (c) the joined log sha256 matches, Ran 5880 OK (skipped=26).
Also read: the door compares `balance >= estimate` where the gate refuses `balance < estimate`, with the same shared method and the same wallet (the course's teacher for a student). No drift.

## Run: my two tests, students/tests_vf1a_h180.py (sha256 4f344c4990f4a4b0)
- p1: a real student upload through the real route; the teacher's balance is exactly the door's number (20806); the task and the real gate run with only the provider replaced (it raises if reached). The gate asked 27229 and refused.
- p2: a teacher batch of three files, each affordable alone, the wallet below two files' worth (door number + 1000); the door queued all three (202); other work then spent the balance down to 1000 (a direct charge); each task met the real gate.

| Run | Result (log's own lines) | Written set |
|---|---|---|
| base (tip) | Ran 2, OK | both green |
| Q1 task never shows a student the sentence | Ran 2, failures=1: p1; `self.assertIn(STUDENT_SENTENCE, text)` | p1; that fragment |
| Q2 task shows the sentence to every requester | Ran 2, failures=1: p2; `assertEqual(entry["error"], INSUFFICIENT_CREDITS_MESSAGE)` | p2; that fragment |
| Q3 the error helper does not pass the sentence | Ran 2, failures=1: p1; `assertIn(STUDENT_SENTENCE, text)` | p1; that fragment |
| Q4 door refuses a balance equal to the estimate | Ran 2, failures=1: p1; `402 != 200 : the door refused at its own number` | p1; 402 != 200 |
| Q5 door counts a running total | Ran 2, failures=1: p2; `402 != 202 : the door refused the batch` | p2; 402 != 202 |
Every failing set and every fragment is as written in h180_expected_kills.txt (written 12:42:15 before any run); each fragment is inside the failing test's own block (rule 22). Each mutant was applied before its run (abort 8 otherwise) and restored from the commit blob (True for all five, 0 tracked changes after). Mutants only on the tip; no old-code arm (the old code lacks the shared method my spy wraps).
The first chain (12:46:06 to 12:46:23) was red through a set-up fault of mine: I built the teacher's plan twice, a unique key refused it, both tests died in set-up before any product code. Log kept (h180_4216575f_run1_my_fixture_fault.log). Re-run once on the Senior Manager's word.

## What the student and the teacher read (printed by the run, unmutated tip)
- p1, polled status of the student's refused upload: meta = `{'step': 'Submission refused', 'assignment_id': '<id>', 'error': "Your answers were not submitted. Your teacher's account can't process uploads right now. Please keep your file and try again later, or let your teacher know."}`; the stored error is exactly that sentence; polled keys action, additional_ids, meta, resource_id, resource_type, status, task_id. No money word, no number (door 20806, gate 27229 absent).
- p2, each of the three later files, teacher: session result status FAILED with error "There aren't enough AI credits available for this. The credit wallet needs to be topped up before it can run." (all three), the polled meta the same, the stored error the same; never the student sentence, no number. No file in p2 reached the provider.

## Notes (not blockers)
- N1. Known limit, now pinned by p2 and Q5: the door compares each file with the whole balance, not a running total. A batch whose files are each affordable but not together is queued whole and the later files fail in the task with the generic message. Epic B's run-level hold cures it. The door's docstring does not list it; a note for d5 (the Senior Manager asked that the docstring or the package say so).
- N2. The window between the door's number and the gate's is real: p1 is queued at 20806 and refused at 27229 (the task adds the system prompt, questions and roster). The student is told to keep the file; the upload is not kept by the system. The author names this limit.
- N3. Not shown: the student site's handling of the new 402 body (code insufficient_credits plus the sentence) is read by nobody. For the package under "not shown".
- N4. p2 does not run any file to success: the balance is spent by a direct charge before the tasks, so no task passes the gate. What a first file that succeeds and a second that is refused read is not shown (the extraction response is not built).
- N5. c_h180 ran seven apps and not dashboard; dashboard does not reach the changed code.
- N6. Not done: no run of d5's module, battery or regression (rule 15); no pre-commit hooks run; no whole-tree credential scan (my files hold no value of that kind; the sentence and test fixtures only).

## Files (sha256 prefixes; GAP-1a-records)
h180_probe_tests_vf1a_h180.py (4f344c4990f4a4b0), h180_mutants_Q.py (94c90df93e6ea343), h180_expected_kills.txt (8d82f5b173dfdf3f), h180_run.sh.txt (a2945a7c9991ebea), runs/h180_4216575f.log (1db8e2f8fdd31bfe), runs/h180_mutant_Q1..Q5_4216575f.log (7398ecce61119e30, 9da480567a5864de, 0fe7dc5d3ba8f8a6, 4448cd4f9cae64cf, 73414a38d6cdebca), runs/h180_4216575f_run1_my_fixture_fault.log (1e84729957aa95d1), runs/h180_all_console.txt and h180_all_console_run1.txt.
The first probe version (a different sha, 4b954cb8886a5392) is the one the failed run used; it is not kept.
Worktree: vf_h180 (my scratch checkout at 4216575f): my probe copy is deleted after a cmp; released to 0b for removal. Test databases to drop: test_vf_h180, test_vf_h180_mut.
