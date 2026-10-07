# Verification: H-154, a repeated or stray evaluation in the AI's reply was added into the score (d5)

- **Branch:** task/ai-reply-repeated-evaluation at **c1a62979**, on beta d7143538 (batch 11 as pushed). Code and tests last changed at 42eccc7b; 13a8f3b5 (the hand-over), 3a707780 and c1a62979 add and change only files in docs/evidence/h154-ai-reply-repeated-evaluation/ (v2's own name-only diffs of 42eccc7b against each: 29 files, none outside that folder). 3a707780 against 13a8f3b5: EVIDENCE.md and FRONTEND_NOTE.md, the two wording corrections of notes 3 and 4. c1a62979 against 3a707780: EVIDENCE.md and founder_count_query.sql, the correction of one sentence (note 4) and the count withdrawn (note 7). Every gate, d5's and v2's, ran on 42eccc7b.
- **Severity:** HIGH (Senior Manager). Found by the Next-stage Checker.
- **The change:** `AIProcessor._finalize_grading_result`, the one place a score is added up, keeps ONE evaluation per question: of a model's repeats the lowest, an evaluation the system already held stands, an evaluation for a question the assignment does not have is dropped. The saved arithmetic note then says `CORRECTED` with counts, the saved-answer store gets only what was kept, the paper goes to the teacher's review queue (`ai_reply_corrected`), the feedback formatter is sent the arithmetic and nothing of the correction, and a reply cannot mark its own evaluation as the system's. No migration, no model change, no setting. Fix-forward: saved grades are not recalculated.
- **Verifier:** v2 (independent), 2026-10-07. One slot from 0b, 13:53:39 to 13:55:13 WAT, 1-minute load 5.43 to 8.89, beside the Security Engineer's gate; nothing timed, no timeout.
- **Verdict:** **VERIFIED-WITH-NOTES.** Through the whole pipeline a corrected reply gives the cautious score, the review reason and the `CORRECTED` note, the formatter and the student are told nothing of it, and each of these was seen to fail under a mutant. The notes are two misses of v2's own, one sentence of the frontend note that says more than the code, and the stated limits.

## Found by reading, before any run
At a3c3a194 v2 read `_held_by_the_system` and found it trusted two marks, `graded_by == "deterministic"` and `from_cache`, that a model's reply can carry as easily as the system can set them. A reply that repeated a question and put either mark on the HIGHER copy would have had that copy kept as "the system's". d5 cured it, tests first (806f7027 red, a11b7109): `_stamp_as_a_models` assigns `graded_by` and removes `from_cache` on every evaluation of a reply, at both places a reply enters. v2's E3 and W3 below show it through the pipeline.

## v2's probe: the whole pipeline, each expectation written first
d5's tests hold the two halves apart (the grader's result for a reply; the save of a hand-made result). `tests_vf2_h154_probe.py` goes through `students.services.grade_engine` with only the provider call and the two follow-up tasks replaced: the real grader makes the result, the real save stores it, and the formatter's prompt is taken as the pipeline queued it. No model is reached.

| Probe | What it holds |
|---|---|
| E1 | a short paper whose reply repeats question 2 (8, then 10): saved 24 of 30, in the review queue with the one reason naming question 2, the saved note `CORRECTED`; the formatter's prompt has "8 + 8 + 8 = 24" and no word of the correction |
| E2 | a 50-point evaluation for a question that does not exist: 24 of 30, the reason counts one unmatched |
| E3 | the repeat carries one of the system's two marks and the higher score: still 24 of 30 (both marks) |
| E4 | E1's paper released: the student reads 24 and nothing of the correction anywhere in the answer |
| E5 | a long paper marked in parts, a part repeats a question: 96 of 120, the SAVED note is `CORRECTED`, the paper is in the queue, the prompt is clean |

Baseline (d5's three modules and the probe): **Ran 59 tests in 5.776s, OK.**

## v2's mutants (rule 19), each judgement in its own file
| Mutant | Judge | Written before the run | As run |
|---|---|---|---|
| W1 the note keeps `PASS` | probe | E1, E2, E5 | Ran 5, failures=4: E1, E2, **E4**, E5. Differs, see below |
| W2 the formatter is sent the whole note | probe | E1, E5 | Ran 5, failures=2: E1, E5 |
| W3 the short path does not stamp | probe | E3 | Ran 5, failures=2 (both marks): E3 |
| W4 the student reads the saved result whole (not this row's code; H-127's shield) | probe | E4 | Ran 5, failures=1: E4 |
| W5 the long path's note is not carried | probe | E5 | Ran 5, failures=1: E5 |
| W6 strays are dropped but not counted | d5's tests | `test_the_note_counts_it`, `test_evaluation_with_no_matching_question_is_left_out_of_the_sum` | Ran 40, failures=1 errors=1: those two, no other |
| W6 | probe | E2 | Ran 5, failures=1: E2 |
| W7 a stored answer is not the system's | d5's tests | `test_the_stored_one_stands_whichever_is_lower`, `test_a_returned_one_that_calls_itself_the_systems_does_not_lower_it` | Ran 29, failures=3 (subtests): those two, no other |

Every restore matched the commit's file by sha256, `__pycache__` cleared each time, the tree clean at 42eccc7b afterwards. Each of E1 to E5 was seen red. W6 and W7 are shapes d5's nineteen do not have: d5's tests catch both.

### W1's set differed: v2's expectation, not the code
E4 begins with a control (probe line 220): the saved note must say `CORRECTED`, so that the test cannot pass on a row with nothing to hide. W1 makes the note say `PASS`, so E4 fails there: `'PASS' != 'CORRECTED'`. v2 named E4 only under W4 and did not read its control against W1. Reported, not re-run. The Senior Manager's ruling: the run stands, no file that ran is changed, this record is the dated note. E4 was seen red for its real reason under W4 (the student's answer then held the saved note whole).

## d5's gates, read by v2 from the raw logs (not repeated, rule 15)
v2 unpacked the logs committed at 13a8f3b5 (unchanged at c1a62979) and checked each against the checksum EVIDENCE.md gives: the three red steps, step (a), the battery log, `results.tsv`, both expected files, both comparison outputs and the regression's raw and stamped logs all match.

| Gate | The raw log |
|---|---|
| (r1) the new tests at 96300987, before any fix | Ran 30, FAILED (failures=26); the written red set |
| (r2) at 27400a7a | Ran 34, FAILED (failures=3, errors=2); the written red set |
| (r3) at 806f7027 | Ran 39, FAILED (failures=8); the written red set |
| (a) the changed modules and the guard list, 42eccc7b | Ran 674 tests in 224.816s, OK; `AutoGrader.tests_cache_bespoke_1114` is in it (rule 20) |
| (b) 19 mutants, 42eccc7b | baseline green; 19 of 19 killed with verified restore; Ran 54 in every run |
| (c) ai_processor, AutoGrader, students, assignments, `--parallel 2`, 14:14:24 to 14:27:33, in a quiet window | Ran 2505 tests in 738.483s, OK (skipped=22); exit=0; stalled=0; no `FAIL:` or `ERROR:` line; load 3.21 and 3.77 |

- **d5's ask (1), the mutant table's counts:** v2 counted the distinct failing tests in each of the 19 logs: 11, 5, 5, 3, 9, 4, 1, 2, 1, 4, 1, 1, 4, 4, 5, 4, 1, 1, 1 for R1 to R19. They equal the table. (`results.tsv` counts subtests and is higher for R1, R4, R5, R8, R11, R16, R17.)
- **The two amendments of d5's expected file, both checked by v2 by diff.** After the first, stopped chain: one test added to the two later red commits' sets, nothing else. After the gate chain: one name line, the control added to R10, a dated note; nothing else (as run 56a57f140e92a02e, amended a3c2bfc6e7a6c4ac). By v2's own reading only R10 among the 19 can reach that control: it saves a hand-made note through `_populate_and_save_grade`, which never calls the grader, and reads the row and the TEACHER's answer; R18 and R19 only add to a student's answer. The 19 logs agree (its name is in R10.log alone).
- **The replaced pinning test** (`..._is_floored_but_uncapped`, dc0fa2e6) and d5's whole-tree search are in the evidence; v2's own text search of the test tree before the freeze found no other test pinning the old sum.

## Notes
1. **v2 read d5's `test_whatever_the_list_holds` and did not add up its shape** (26, not 30). d5's first chain stopped on it. A miss of the reader as well as of the author.
2. **W1's set**, above. Lesson kept: a control line inside a test is read against every mutant.
3. **One sentence of FRONTEND_NOTE.md says more than the code.** "The total is never above the maximum" holds when the assignment's questions are known. With no question known nothing is dropped as unmatched and there is no cap (a stated limit, accepted by the Senior Manager), so the sentence wanted "when the assignment's questions are known". Corrected at 3a707780 on the Senior Manager's order, with the limit stated beside it. No code change asked.
4. **d5's ask (2), the limits: one is worded too softly, v2's own N1.** A rubric that itself holds a question number twice can come only from an AI path or the admin, not from what a teacher sends. **Corrected 2026-10-07 14:52, before this record was committed:** v2's first wording here, taken from d5's reading without testing that part, said it was reachable because `AssignmentSerializer.validate` does not check uniqueness. v2 then read the routes: every POST, PUT and PATCH of the assignment routes uses `AssignmentTextSerializer` (`assignments/views.py`, `get_serializer_class`), which has no questions field; the save-draft route's serializer has none either. `AssignmentSerializer` is given a question list only by the server, from what the AI generated (the two generation routes), and it does not check uniqueness; the single-pass text extraction returns the model's numbers as they came, where the two chunked paths renumber; and the Django admin can edit the field. On generation, which d5's evidence says was not read: v2 read the save side only. The generated content passes a filter of top-level keys that keeps the question list whole (`ai_assignment_content_only`) and goes to `AssignmentSerializer`, which takes each `question_number` as a whole number and neither renumbers nor checks uniqueness. Whether the generator itself renumbers before that, v2 did not read. The evidence at c1a62979 has this corrected, in d5's words. v2's own N1 said "a client-sent rubric": that was wrong too. The arithmetic part v2 did test against the code at 42eccc7b: `points_by_question` holds one entry per NUMBER, so the maximum counts such a pair once, at the last one's points, and the answer lookup by number gives both questions the same answer; both older than this row. What this row changes for that shape: before, both evaluations were summed against the under-counted maximum; now a CORRECT reply with one evaluation for each of the two questions is folded as a repeat, the lower mark is kept and the paper is flagged `ai_reply_corrected`. So keep-lowest can drop a legitimately earned mark there. It is never silent: the teacher is sent the paper. The hand-over's two lines ("the two are one question to this code, and one evaluation is kept") were true and did not say this. Since 3a707780 the evidence says it, with an example v2 checked against the code (question 2 twice, worth 5 and 10, a student earns 5 and 0: before 5 of a counted 10, now 0 of a counted 10 and flagged, truly 5 of 15). It is its own row, H-158, MEDIUM, leading batch 13 (Senior Manager). No other limit is missing or softened.
5. **d5's ask (3):** "no other reader of the arithmetic note expects only `PASS` or `FAIL`" is v2's N3 and is true of THIS repository's Python, by reading. The frontend is not in this repository; what its screens do with a third status is the frontend note's first item, not something v2 read.
6. **What no test here shows:** a real model's reply. The provider call is replaced in d5's tests and in v2's probe. That production has the same fault is by reading only, as the evidence says.
7. **The founder's count query was not run by anyone on the team**, v2 included, and v2 did not judge its SQL beyond reading that it only counts. At c1a62979 it is withdrawn as a request: the user's word, passed on by the Senior Manager, is that no saved grade is above its maximum and no assignment has a repeated question number. That is the user's statement; nobody on the team checked it.

## Files
In `~/Documents/Projects/GAP-v2-handover/`: this record; `tests_vf2_h154_probe.py` (7e3fdd13f35e5208), `vf_h154_mutants.py` (1fd8090267d63826), `vf_h154_run.sh` (bb9a2a9b31997282); `runs/h154_42eccc7b.status` (2c16cdcdf7aec1c0), `runs/h154_42eccc7b_script.out` (53548d92aade2019), `runs/h154_42eccc7b_baseline.log` (d765b49ca786330e), `runs/h154_42eccc7b_mutants.log` (b1b4cb1d494b0fcf), `runs/h154_42eccc7b_v2_mutant_logs.tar.gz` (ac2c334b7d7ac7a8: the baseline and eight judgement files).
