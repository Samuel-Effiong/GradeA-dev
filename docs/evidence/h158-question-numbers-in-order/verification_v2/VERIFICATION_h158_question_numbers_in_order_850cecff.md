# Verification: H-158, an AI reply's question numbers are kept when sound and made 1 to N when not (d5)

- **Branch:** task/h158-question-numbers-in-order, final tip **850cecff**, base 220f9cd6. The code the author's gates ran on is 38b4c6b6; v2's own two-tree diff of 38b4c6b6 against 850cecff: nothing outside docs/evidence/h158-question-numbers-in-order/. Second form of the row (the Senior Manager's ruling of 2026-10-07 19:32).
- **The change:** `assignments/services.py` `number_questions_in_order`: when a paper's question numbers are already distinct positive integers they are kept as they are (a paper that starts at 5 stays 5 to 10); otherwise the whole paper is numbered 1 to N in the order given. The model's own numbers are kept as bounded text (32 characters) in `ai_raw_payload["model_question_numbers"]` on the three extraction paths. Generated drafts and the draft save are numbered too (`views.py`); `AssignmentSerializer` refuses a repeated number as a last defence. A number sent as text counts only up to nine digits.
- **Verifier:** v2 (independent), 2026-10-08. Two slots from 0b, both at 850cecff: run 1 12:08:27 to 12:08:37, RED (v2's own probe defect, below); run 2 12:11:36 to 12:12:17, green. One-minute load 1.99 to 3.30 in run 2; nothing else of the team's running; nothing timed.
- **Verdict:** **VERIFIED-WITH-NOTES.** A three-essay paper the model numbered 1, 1, 2 is saved 1, 2, 3 and graded 16 of 30 through the real grading code with only the provider call replaced; a paper numbered 5, 6, 7 keeps 5, 6, 7 and is graded the same way; 5, 6, 6 is numbered 1, 2, 3 as a whole; a text number of 5000 digits does not raise. The notes are limits.

## What v2 found by reading, before any run
1. **Withdrawn premise.** v2's first question assumed a label such as "1(a)" could be stored as a question number. d5 showed it never can: the serializer's field is an integer, so such a paper is always numbered 1 to N and the label survives only as bounded text in the stored AI copy. v2 withdrew it.
2. **The 5-to-10 case** (v2's question, 2026-10-07) led the Senior Manager to rule: renumber only when the numbers are not already distinct positive integers.
3. **The fault in the second form, found by v2 by reading plus a plain Python call, cured by d5.** At 9f882c18 a question number sent as a string of more than 4300 ASCII digits made `int()` raise ValueError (Python 3.12), inside `_positive_integer`, after the paid call. d5 confirmed it, wrote the tests first (22e1559c, red), cured it at e59894de (a digit string counts only up to nine characters; longer is no number and the paper is numbered), mutant N24. Nine is d5's chosen bound.

## v2's probes, expectations written first
`tests_vf2_h158_probe.py` (b887e6bf436a05b5) and `tests_vf2_h158_edge_probe.py` (46b5cc89d555f273), copied to assignments/ of v2's scratch worktree, never committed to the branch. The real pipeline runs: the extraction (its AI call replaced), a saved Assignment, a submission with three answers, and `grade_engine`; only `AIProcessor.execute_graded_task` is replaced. Second opinion off, evidence enforcement log-only, saved-answer store on.

| Probe | What it holds |
|---|---|
| Z1 | the number field refuses a list, an object, "2a", null, true, 2.5 and turns "3" into 3 |
| Z2 | a paper numbered 1, 1, 2: saved questions 1, 2, 3; graded 16 of 30; state DONE; no `answer_not_found`; "Essay 3 answer." reaches the model |
| Z3 | control, numbered 1, 2, 3: the same outcome |
| Z6 | numbered 5, 6, 7: kept through extraction and the saved assignment; graded 16 of 30; `model_question_numbers == {"5":"5","6":"6","7":"7"}` |
| Z7 | numbered 5, 6, 6: numbered 1, 2, 3 as a whole; graded as Z2 |
| Z4 (edge, run apart) | a 5000-digit text number and a "2": no raise; the last number is 2; the kept text is at most 32 characters. PREDICTED red at 9f882c18, green on the fixed tip |

**Run 1 (RED, kept as evidence):** Ran 47 tests, FAILED (errors=4). Z2, Z3, Z6, Z7 raised `TypeError: 'NoneType' object is not iterable` at my line that iterates `saved.review_reasons`. **Cause: v2's probe.** A clean grade stores `review_reasons = None` (students/services.py, the else branch after the reasons list); v2 had read it as a list. The failure came after the numbering, graded_at, state, max_points and score assertions, which therefore passed. No mutant ran (the script stops on a red baseline). The Senior Manager approved one fresh slot on conditions: v2 read every assertion of the probes against what a clean and a flagged grade store, and re-read the five expected sets; both done before the second run. Fix: `saved.review_reasons or []`. The red probe form is kept (tests_vf2_h158_probe_as_run_at_850cecff_red.py, 8cc93f33fa620218), and its log (runs/h158_850cecff_run1_red/h158_850cecff_baseline.log, 6613a9e02daffe57).

**Run 2 baseline (d5's module assignments.tests_question_numbers_in_order and the probe): Ran 47 tests in 6.349s, OK.**

| Mutant (rule 19) | Written before the run, every line of Z1 to Z7 read against it | As run (probe only: Ran 5 each) |
|---|---|---|
| M1 extraction keeps the model's numbers | Z2, Z7 | FAILED (failures=2): Z2 `[1, 1, 2] != [1, 2, 3]`, Z7 `[5, 6, 6] != [1, 2, 3]` |
| M2 the numberer leaves each entry as it was | Z2, Z7 | FAILED (failures=2): the same two lines |
| M3 the number field takes text (CharField) | Z1 | FAILED (failures=1): Z1 `'3' != 3` |
| M4 a repeat is kept as the model gave it | Z2, Z7 | FAILED (failures=2): the same two lines |
| M5 distinct numbers are numbered all the same (the 72cd7ae2 behaviour) | Z6 | FAILED (failures=1): Z6 `[1, 2, 3] != [5, 6, 7]` |

Five of five killed, every failing set exactly the one written (the runner requires equality); each restore matched the commit's file, `__pycache__` cleared, the tree clean at 850cecff afterwards. The edge probe: **Ran 1 test, OK** (green as predicted; it would have been red at 9f882c18, by the plain call, not by a run). Each of Z1, Z2, Z6, Z7 was seen red (Z2 and Z7 under M1, M2, M4; Z1 under M3; Z6 under M5); Z3 is a control and is not claimed as killing anything. d5's tests were not run under M1 to M5 (rule 15).

## d5's gates, read by v2 from the committed logs (not repeated, rule 15)
Every log unpacked from the commit and its end read.

| Gate | The raw log |
|---|---|
| Reproductions, three, red as written | Ran 42, FAILED (failures=14, errors=27), 33 as written; Ran 40, FAILED (failures=9); Ran 42, FAILED (errors=1) |
| Modules (45 labels incl. guards, cache and migration-safety) | Ran 802 tests in 213.367s, OK |
| 24 mutants N1 to N24 | 24 of 24 KILLED, restores verified |
| Regression: AutoGrader, assignments, students, billing | Ran 3879 tests in 500.306s, OK (skipped=16); raw log sha d7481e8dce146bcf matches the one in EVIDENCE |

**The five mutants that failed more than d5 wrote** (N6, N8, N20, N21, N23): v2 read the unique failing tests of each in the committed battery logs and the code of the tests against the mutant. N6 (`if True:` counts all as changed) also fails the very-many-digits test (changed is 3, not 1); N8 (kept text unbounded) also fails it (5000 characters, not 32); N20 (the not-a-number check removed) also fails it and the long-label-kept-in-part test (the paper is kept as it is, and the kept map is keyed "None"); N21 (keep branch off) also fails the nine-digit test (the second number is 2, not "999999999"); N23 (digit branch off) also fails it (the paper is renumbered). Each reason holds; no failing set had fewer than written and none survived. d5's sets were short because they were written before the two digit tests existed. The Senior Manager ruled the chain stands and (b) is not re-run; 0b accepted v2's reading.

## Notes
1. **The fault in the second form** (above), found by reading, cured at e59894de, held by d5's two digit tests, mutant N24 and v2's Z4.
2. **A sectioned paper (Section A 1, Section B 1)** is stored 1 to N while a student's sheet may say "B 1". Whether that student's answer reaches the right question rests on the model; no test can show it without a paid call. H-171 (one paper, one run) is planned and was NOT run. Not checked by v2.
3. **Re-extraction of an assignment that has submissions:** the stored answers can stay under numbers no question has now; they are graded "answer not found" and sent to review. d5 states it; not tested by v2.
4. **A text number is kept as text** in the stored question ("5" stays "5"); the serializer turns it into 5. Tested by Z1 for the field, not through a whole save of a text-numbered paper.
5. **A question's text that refers to another by number** ("see question 3") is not rewritten when the paper is renumbered. d5 states it.
6. **H-172** (the "teacher overrode the AI" flag never sets on rows made by today's code) is its own row; one d5 test holds that the new key does not change that comparison.
7. **Not run:** the browser, the frontend, a real model. The provider call is replaced in every v2 probe; nothing reached a model.
8. **Probe defect of v2's** (run 1), told above; no code of d5 was in question.
9. **In v2's logs:** pattern check of the run files for password, secret and token: no line matches (zero in all five judgement files and the mutants log).

## Files
In `~/Documents/Projects/GAP-v2-handover/`: this record; `tests_vf2_h158_probe.py` (b887e6bf436a05b5), `tests_vf2_h158_edge_probe.py` (46b5cc89d555f273), `vf_h158_mutants.py` (2b2d96d8292ab205), `vf_h158_run.sh` (2ffa272b94a6cc09); kept: `tests_vf2_h158_probe_as_run_at_850cecff_red.py` (8cc93f33fa620218), `tests_vf2_h158_probe_for_72cd7ae2.py`, `vf_h158_mutants_for_72cd7ae2.py`, `vf_h158_run_for_72cd7ae2.sh` (first forms for the superseded 72cd7ae2, never run); `runs/h158_850cecff.status`, `runs/h158_850cecff_script.out`, `runs/h158_850cecff_baseline.log`, `runs/h158_850cecff_mutants.log`, `runs/h158_850cecff_v2_mutant_logs.tar.gz` (the baseline, five judgement files and the edge file), `runs/h158_850cecff_run1_red.tar.gz` (the red first run).
