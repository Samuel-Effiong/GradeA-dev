# Verification: H-211, a student's refused answer EDIT reads the fixed sentence @ c6a1856c

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-09.
**Branch:** `task/h211-student-edit-credit-sentence` @ **c6a1856c765bb1810008fae94fa9afdeda49cac8** (gated by d5 at 45e85882; 740f378a is docs only; c6a1856c is a clean base update onto H-180's 22090331). Stacked on H-180 (now in beta). For the beta line.

Change over H-180's tip, non-docs: `assignments/tasks.py` (`user = None` before the try of `extract_answer_background_task`; in the refusal handler a STUDENT who started the edit gets `StudentUploadNotProcessedError(STUDENT_UPLOAD_NOT_PROCESSED)` stored, returned and polled, the real refusal logged with `log_refusal`; a teacher keeps the generic text), `billing/tests/test_refusal_handling.py` (one expectation: the student-requested edit task expects the student sentence), and two new modules (`assignments/tests_edit_refusal_student_sentence.py`, `students/tests_upload_bills_the_teacher_only.py`).

**I ran at c6a1856c** on 0b's GRANT, from my own detached checkout, `h211_run.sh all`, START 17:43:24, END 17:45:00 (both read from `date`), one inhibit, 6G scope with MemorySwapMax=0, nice, timeout 1800, PYTHONDONTWRITEBYTECODE, `python -B`, own settings and test DBs, output to files, load 3.46 before. Under rule 15 I did not repeat d5's chain (972 OK, 6 of 6) or his seven-app regression (5884 OK).

**Verdict: VERIFIED-WITH-NOTES.** By reading and by my own tests through the REAL route, task, gate and status route (d5's tests replace the service by a fake refusal): a student's refused edit is stored, returned and polled as the fixed sentence with no money word and no number, the gate's own text stays in the server log, a teacher's edit keeps the generic text, a student with a funded wallet and a poor teacher is refused and neither balance changes. Every one of my tests is shown red by a mutant. One limit is told below (H-216).

## Read (not run)
- The handler: `isinstance(exc, InsufficientCreditsError)` and `getattr(user, "user_type", None) == STUDENT`; the swap happens after `log_refusal`; `_refusal_code(exc)` (the machine code `insufficient_credits`) stays on the row's meta. Other refusals (plan not available, closed submission) are not touched.
- `user = None` is pinned by d5's T3 (a refusal before the user exists: result not failed, generic text).
- Who is billed: the gate (`execute_graded_task`) bills `assignment.course.teacher` for a STUDENT caller; `HasCreditBalance` checks the teacher's wallet for a student. d5's R1 test (rich student, poor teacher) is asserted with both balances read first; it is green at base by design and killed by his mutant P1; my p3 repeats the property on the edit path and G1 (below) shows it red.
- d5's tests, "satisfiable by an empty or missing value": every negative assertion has a positive on the same value first (T1 `tracked.error` equality then the polled text non-empty; T2 equality; T3 not-failed then equality). One small weakness: T1's `"5000"/"25000" not in text` runs over the polled meta, which holds a random submission uuid: a false red about once in 2,000 runs, never a false green. Told to d5.
- The changed billing expectation uses `credits_message=STUDENT_UPLOAD_NOT_PROCESSED` for a student requester, correct (d5's note said GENERIC; a wording slip).

## Ran (c6a1856c). Expected sets and fragments were written before any run
| Run | Expected failing | Result (log's own Ran line and named failures) |
|---|---|---|
| base | p4 only | `Ran 4`, `FAILED (failures=1)`: p4. p1, p2, p3 green |
| Y1 student test inverted (`!=`) | p1 p2 p3 p4 | `Ran 4`, failures=4: p1 p2 p3 p4 |
| Y2 `log_refusal` removed | p1 p4 | `Ran 4`, failures=2: p1 p4 |
| Y3 student error carries the gate's text | p1 p3 p4 | `Ran 4`, failures=3: p1 p3 p4 |
| G1 gate bills the caller's wallet | p3 p4 | `Ran 4`, failures=2: p3 p4 |
The checker (`h211_expected.py`) read every log: "ALL AS WRITTEN", and each written fragment was in its test's own failure block (rule 22). Each mutant was applied before its run (exit 8 otherwise), restored from the commit blob (compare True for all four, 0 tracked changes after), pycache cleared. Under G1 the rich student passes the gate and the task fails with the fallback text ("We couldn't extract the answers ..."), the written reason of p3.
- **p1** student edit, real gate, poor teacher (5,000): row `tracked.error`, task result message and polled `meta.error` all equal the sentence; no money word (the word "credit" only checked in the error text, the machine code is "insufficient_credits"), no number of 3+ digits; the gate's text ("you only have") is in the server log of `assignments.tasks`; the provider not reached; teacher balance unchanged. Red by Y1, Y2, Y3.
- **p2** teacher edit, same: generic text stored, returned and polled; no student sentence. Red by Y1.
- **p3** funded student (200,000) + poor teacher (5,000): student sentence, both balances unchanged, provider not reached. Red by Y1, Y3, G1.
- **p4** (class `VF1aH211Finding`): FINDING, predicted red at the tip, see below.

## Notes (not blocking)
- **N1. Limit, row H-216 (SM ruling): the synchronous student edit (PATCH submissions/<id>, "kept until H-11 retires it") is unchanged.** A student editing that way gets 402 with the generic text that names the credit wallet. p4 shows it: it fails at the tip with `self.assertEqual(response.data["error"], STUDENT_SENTENCE)` against "There aren't enough AI credits available for this. The credit wallet needs to be topped up before it can run." p4 is the predicted red for H-216, not counted against H-211.
- **N2.** `user = None` before the try is pinned only by d5's T3; no refusal exists in practice before the user is loaded (the user lookup is the first refusal-capable step after the claim), so the guard is defensive. I did not mutate it (d5's E3 does).
- **N3.** The same gap as H-180's N1: the window between the route and the task (another job can spend the teacher's balance) stays Epic B's hold.
- **N4.** A plan refusal for a student (AIFeatureNotAvailableError) keeps its own student message and is not probed by me; a mutant that gave it the credit sentence would be killed by none of my tests. Unprobed, by reading it is untouched.
- **N5.** Not done: no re-run of d5's chain or regression; no whole-tree credential scan (my probe files hold no secrets; the hooks' check is d5's); the student site's handling of the failure text unread; d5 may still merge beta into the branch (tip moves): I would re-check that merge as a line-set comparison, not re-run.

## Files (sha256 prefixes; `~/Documents/Projects/GAP-1a-records/`)
- `h211_probe_tests_vf1a_h211.py` (d303fa71d2f92735), `h211_mutants_Y.py` (a8e92e4ca1d04962), `h211_expected.py` (0387872ed51d7e93), `h211_run.sh.txt` (2a3162a30832507b)
- `runs/h211_c6a1856c/h211_base.log` (0bd5aeabd4beac1e), `h211_mutant_Y1.log` (7078921085852487), `Y2` (b5c110c429e3531c), `Y3` (c37199efd8ab5d44), `G1` (179c6d6c0312f520); `runs/h211_all_console.txt`
