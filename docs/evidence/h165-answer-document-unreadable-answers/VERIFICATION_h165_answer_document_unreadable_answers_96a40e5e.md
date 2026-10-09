# Verification: H-165, the answer document builder does not raise on stored answers it cannot print (d5)

- **Branch:** task/h165-answer-document-unreadable-answers at **96a40e5e**, on 220f9cd6 (batch 12's line with H-150). Code last changed at 8e43c2a1; 96a40e5e adds only docs/evidence/h165-answer-document-unreadable-answers/ (v2's own two-tree diff: 22 files, none outside that folder). Verified in two steps: the row as first handed over at bbd01981 (code as at e42a8d2e), then the delta that cures v2's finding.
- **Severity:** MEDIUM. First row of batch 13.
- **The change:** `StudentSubmission.answers` takes any JSON. The builder of the answer document walked it as a list of objects and raised on anything else. Now one helper decides what can be printed; the builder prints that and ends with one fixed line when something was left out. A paper with NOTHING printable is refused for grading before the claim and before any paid call, and put in the teacher's review queue. A paper with SOME left out is graded and flagged. Both writers refuse a value that is not a list of objects. The delta: one function, `is_readable_answer`, is asked by the builder's helper, both writers and both partition steps of grading, and the step that reuses saved evaluations no longer calls `.get` on an entry that is not an object. Five production files in all, no migration, no model change, no setting.
- **Verifier:** v2 (independent), 2026-10-07. Two slots from 0b: 17:26:53 to 17:27:30 WAT at bbd01981 (1-minute load 5.06 to 4.25) and 18:13:42 to 18:14:24 at 96a40e5e (3.71 to 4.62; the Security Engineer's gate ran beside the second). Nothing timed.
- **Verdict:** **VERIFIED-WITH-NOTES.** No fault found in the change as first handed over. One limit found by v2 (note 1) was ruled into the row by the Senior Manager and is cured; the cure is verified. The other notes are stated facts and limits.

## Read before any run
- At bbd01981: the four production files line by line; d5's 31 tests; `ASSERTIONS_READ.md`; the runner U1 to U19; `DESIGN_NOTE.md` with the rulings. In `ai_processor/services.py`, every step between `grade_engine` and the model call that walks the answers.
- The callers of `grade_engine`: the grade route (400 with the teacher's sentence, by d5's test), the per-paper task (the refusal falls to its general handler: the tracked task and the batch row are marked failed with the sentence, nothing is retried), and a legacy bulk task that its own docstring says nothing dispatches. Bulk and automatic grading send one task per paper, so a refused paper fails alone.
- **The gap in the tests that shaped v2's probe:** every grading test of d5's replaced the whole of `students.services.ai_processor` or saved a grade directly. The real grading code had never run on a paper whose answers hold an entry that is not an object. v2's probe runs the real pipeline with only the provider call (`execute_graded_task`) replaced; nothing reaches a model.
- At 8e43c2a1: the delta by two-tree diff, line by line (two things change in meaning: the new function, and the new condition in `_partition_cached`'s second comprehension; the other changed lines swap `isinstance(..., dict)` for the function), and d5's eleven new tests.

## The finding, and its cure (note 1)
`_partition_cached` built `remaining_answers` by calling `.get` on every entry. Normally the step before it (`_partition_deterministic`) has already dropped entries that are not objects; that step is skipped when `GRADING_DETERMINISTIC_OBJECTIVE` is off (an environment switch, on by default). Then grading of a partly unreadable paper failed on every attempt before any paid call: no charge, the claim released as FAILED, the general "couldn't grade" message, and the paper NOT in the review queue. The code is older than this row, but the row's sentence "a paper with some left out is graded and flagged" did not hold under that switch. v2 expected it from reading and wrote it into the first probe as Y2, a stated limit; the run at bbd01981 showed it as a fact (Y2 green; red under the one-condition cure, M4 of that run).

The Senior Manager ruled it cured in this row. d5 did so at 48b22236, tests first (21d78678: four red before the cure, by the method of v2's probe, d5's own tests), gates and a five-app regression again. v2 read the cure, rewrote Y2 to say the opposite, and ran again.

## v2's probe, each expectation written first
`tests_vf2_h165_probe.py`, copied to `students/` of v2's scratch worktree, never committed to the branch. One `TransactionTestCase`. Two forms: the first as run at bbd01981 (`tests_vf2_h165_probe_as_run_at_bbd01981.py`, four tests), the second for the delta (five tests).

| Probe | What it holds |
|---|---|
| Y1 | a paper of three questions whose second entry is a string, settings as shipped: graded and saved (16 of 30), flagged `answers_unreadable` (1) beside `answer_not_found` for that question, tier critical; the stored document has the fixed line and the first answer, and nothing of the entry. A fact beside it: on this short paper the entry that is not printed IS in the prompt sent to the model |
| Y2, first form (bbd01981) | the limit as a fact: with `GRADING_DETERMINISTIC_OBJECTIVE` off the same paper is not graded; the error is the entry's missing `.get`; no provider call; the claim released as FAILED; no grade, no flag |
| Y2, second form (96a40e5e) | with that switch off: the same outcome as Y1, line for line |
| Y5, second form only | with both partition steps off (that switch and `GRADING_ANSWER_CACHE_ENABLED`): the same outcome as Y1 |
| Y3 | d5's push point 2: a paper ALREADY GRADED (24 of 30, state DONE) whose answers are then damaged, with a review reason of another kind on it. Asked to grade again: refused, no provider call; score, maximum, grading time, feedback, stored document, grading state and claim time exactly as before; the earlier reason kept and the new one after it; tier critical |
| Y4 | a never-graded paper with nothing printable while a fresh grading claim is held: the refusal is raised (not "already being graded"); the claim's state and time are untouched; the paper is in the review queue |

**First run, bbd01981.** Baseline (d5's module and the probe): **Ran 35 tests in 6.709s, OK.** Probe only under each mutant, Ran 4 each:

| Mutant (rule 19) | Written before the run | As run |
|---|---|---|
| M1 the refusal drops the reasons already on the paper | Y3 | FAILED (failures=1): Y3 |
| M2 the refusal comes after the claim | Y3, Y4 | FAILED (failures=2): Y3 (`'RUNNING' != 'DONE'`), Y4 ("already being graded" raised instead) |
| M3 grading does not add the reason | Y1 | FAILED (failures=1): Y1 |
| M4 the reuse step skips entries that are not objects (not a fault put in: the cure, so that Y2 is seen red) | Y2 | FAILED (failures=1): Y2, the paper is graded |
| M5 the builder walks the stored value again | Y1 | FAILED (failures=1): Y1, `'str' object has no attribute 'get'` at the save |

**Second run, 96a40e5e.** Baseline (d5's module with the delta's tests, and the probe): **Ran 47 tests in 8.250s, OK.** Probe only under each mutant, Ran 5 each:

| Mutant (rule 19) | Written before the run | As run |
|---|---|---|
| M1 as above | Y3 | FAILED (failures=1): Y3 |
| M2 as above | Y3, Y4 | FAILED (failures=2): Y3, Y4 |
| M3 as above | Y1, Y2, Y5 | FAILED (failures=3): Y1, Y2, Y5 |
| M4 d5's new condition taken out (the fault as it was) | Y2 only: Y1 has the first step filtering, Y5 returns before that comprehension | FAILED (failures=1): Y2, "All 3 attempts failed. Last error: 'str' object has no attribute 'get'" |
| M5 as above | Y1, Y2, Y5 | FAILED (failures=3): Y1, Y2, Y5 |

In both runs: five of five, every failing set exactly the one written; each restore matched the commit's file, `__pycache__` cleared, the tree clean at the tip afterwards. Every probe of each form was seen red. d5's tests were not run under v2's mutants (rule 15).

## d5's gates, read by v2 from the committed logs (not repeated, rule 15)
v2 unpacked the committed logs of both rounds and checked each against the checksum EVIDENCE.md gives: all match.

| Gate | The raw log |
|---|---|
| e42a8d2e: (r) tests before the change | Ran 30, FAILED (failures=5, errors=30; subtests counted) |
| e42a8d2e: (a) 73 labels | Ran 1030 tests in 182.230s, OK (skipped=1) |
| e42a8d2e: (b) 19 mutants | 19 of 19 killed, every set the expected one |
| e42a8d2e: (c) four apps | Ran 3868 tests in 470.496s, OK (skipped=16) |
| 8e43c2a1: (r2) the delta's tests before the cure, at 21d78678 | Ran 38 tests in 5.001s, FAILED (errors=4): the four d5 named |
| 8e43c2a1: (a) 84 labels | Ran 1231 tests in 290.773s, OK (skipped=1); no `FAIL:` or `ERROR:` line |
| 8e43c2a1: (b) 21 mutants | 21 of 21 killed with verified restore; "every mutant's failing set is the expected one" |
| 8e43c2a1: (c) five apps (ai_processor added), `--parallel 2`, 17:55:26 to 18:08:50, in a quiet window | Ran 4731 tests in 761.989s, OK (skipped=22); no `FAIL:` or `ERROR:` line |

d5 tells, and keeps whole, an earlier chain on 2b7e812e that stopped on one test of d5's own; v2 read the correction and `ASSERTIONS_READ.md` with its two addenda. The checks d5 lists there as decided by no mutant are controls or H-127's ground; v2 found nothing important hidden among them.

- **Readers of the stored answers in production code.** One `git grep` on bbd01981 over every non-test Python file for `.answers`, `get_answer(`, the builder and the student's document function: 38 lines. Outside `students/services.py`, the serializers and view that call the two document functions, grading and the offline benchmark tools, nothing walks the stored answers; dashboard names them in one comment.
- **Every walk of the answers inside grading:** d5's table in EVIDENCE.md (eight places). v2 read the same eight independently and agrees: after the cure each skips an entry that is not an object.
- **The 23 dashboard fixture lines** d5 did not read line by line: v2 read the list. All are `answers={"q1": ...}` on rows the dashboard tests create; by the grep above no dashboard code reads the answers. The dashboard app was in neither regression; the batch's full run is the proof.

## d5's "where I would push", answered
| Point | Answer | By |
|---|---|---|
| 1. the checks listed as not evidence | controls, or the student's serializer having no review field (H-127) | reading |
| 2. the refusal: a held claim, a graded paper later damaged, bulk grading | Y4, Y3; bulk is one task per paper | a run; bulk by reading |
| 3. the two writers | before: not a list was refused, and a list with a non-object was kept out by the builder raising after it. Now the writer refuses both with its own error. Nothing accepted before is refused; an empty list and a list of objects of any content are accepted as before | reading; d5's X tests |
| 4. a student's reads before release | the document carries the fixed line only; no review field is sent | d5's R tests; reading |
| 5. the 23 dashboard lines | above | reading |

## Notes
1. **The finding above**, cured at 48b22236, held by d5's four tests and by v2's Y2.
2. **What is not printed is still sent to the model on a short paper** (Y1, Y2, Y5; d5's test holds that it is inside the wrapper for untrusted student text). On a paper of up to ten questions with nothing claimed beforehand, the prompt carries the stored list as it is, as before this row. On a longer paper it is not sent: the pairing leaves such an entry out (d5's direct calls; not tested by v2).
3. **A stored `0` or `false` is "nothing there"** like an empty list: no line, no refusal. It is graded with every question "answer not found" and flagged, at the cost of a paid call, on a short and a long paper alike (d5's four tests). **v2 first read this wrongly** (that the long paper's pairing would raise on it) and told the Senior Manager so; d5 showed the line that turns such a value into an empty list first; v2 sent the correction.
4. **The new function's docstring overstates.** It says the document, the writers and "the grading steps below" all ask it. Five places do; five others in the same file (the pairing, the evidence check, the second opinion's selection, the saved-answer store, the answer-status stamp) skip such an entry with their own check, unchanged. EVIDENCE.md says so plainly. v2 advised leaving the gated code as it is; a comment, not behaviour.
5. **Not tested by d5 or v2: the second opinion left ON**, as shipped. Both harnesses switch it off. By reading, its selection skips an entry that is not an object.
6. **A value inside an answer that is not text.** The cleaner that strips markup (`sanitize_ai_html`) returns a value that is not a string as it is, so a list or a number in `answer_html` or `question_text` is printed into the HTML unsanitised. Older than this row and untouched by it; the writers check that entries are objects, not what the objects hold. Every caller converts the HTML to the stored document form afterwards; v2 did not read the converter. By reading only; told to the Senior Manager, not judged here.
7. **The refusal reads the answers of the instance it is handed** and then saves the review columns on a freshly read row. A paper cured between the two would be flagged once more; the next grading clears it. By reading only.
8. **d5's stated limits stand:** a stored document made from unreadable answers stays as stored if the answers are repaired by hand; a paper refused up front cannot be graded by hand (ruling (d)); `founder_count_query.sql` has never been run on any database, and v2 did not run or check it; one place at upload (line 2098, named for H-159) still calls `.get` on every entry of a model's reply.
9. **Not run against a browser or the frontend.**

## Files
In `~/Documents/Projects/GAP-v2-handover/`: this record.
- Second form, run at 96a40e5e: `tests_vf2_h165_probe.py` (195edae06413394c), `vf_h165_mutants.py` (8e1198024a2dfd67), `vf_h165_run.sh` (e4150ff9c90f096a); `runs/h165_96a40e5e.status` (f1b5c3e5404fb561), `runs/h165_96a40e5e_script.out` (d39c0a2e3c949513), `runs/h165_96a40e5e_baseline.log` (97be01de1f946fc8), `runs/h165_96a40e5e_mutants.log` (412200fa9ab586ab), `runs/h165_96a40e5e_v2_mutant_logs.tar.gz` (957a0c5702993c3f).
- First form, run at bbd01981: `tests_vf2_h165_probe_as_run_at_bbd01981.py` (3e26b36b12b63a85), `vf_h165_mutants_as_run_at_bbd01981.py` (d91f2def901b2660); the script then differed from the present one in one comment line of its header (it was 334175c715152d2f; not kept); `runs/h165_bbd01981.status` (94d4840a7c738c4e), `runs/h165_bbd01981_script.out` (b946e2b8bb083caf), `runs/h165_bbd01981_baseline.log` (cfdd562623f5cd72), `runs/h165_bbd01981_mutants.log` (acb7775117e260d8), `runs/h165_bbd01981_v2_mutant_logs.tar.gz` (270267654b1d4473), `runs/h165_bbd01981_answers_readers_search.txt` (3be6b2478ee2faf5).
