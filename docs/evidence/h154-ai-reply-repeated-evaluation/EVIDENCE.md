# H-154: a repeated or stray evaluation in the AI's reply was added into the score

**Severity:** HIGH (Senior Manager, 2026-10-07). **Author:** d5.
**Branch:** `task/ai-reply-repeated-evaluation`, on beta `d7143538`
(batch 11 as pushed). **Verifier:** v2. **Batch:** 12, first row.
**Found by:** the Next-stage Checker, while reading for slice C
(`GAP-0c-records/finding_repeated_answer_for_d5.md`).
No migration, no model change, no setting. A note for the frontend
(`FRONTEND_NOTE.md`). Fix-forward only: see "Saved grades".

## The fault
The grading reply is a list of evaluations, one per question. Nothing
held it to one per question. `_finalize_grading_result`
(`ai_processor/services.py`), the one place the score is added up, summed
whatever the list held:

- a question evaluated twice was counted twice (the Checker's shape: one
  answer three times);
- an evaluation for a question the assignment does not have was added in
  as well (an older test pinned exactly this: 1000 points on a paper of
  10, "a separate, still-open issue").

The saved score could pass the maximum, and the arithmetic note beside it
said `PASS`.

## The fix
1. **One evaluation per question** in `_finalize_grading_result`. Of a
   model's repeats the LOWEST corrected score is kept, the first of
   equals. An evaluation the system already held (the answer key's,
   `graded_by == "deterministic"`, or one reused from the saved-answer
   store, `from_cache`) stands against a model's, whichever is lower.
2. **An evaluation that matches no question is dropped**, when the
   assignment's questions are known.
3. **The note says so:** `verification_status` is `CORRECTED`, with
   `repeated_evaluations_dropped`, `unmatched_evaluations_dropped` and a
   sentence in `correction_note`. One warning line is logged,
   `[Grading] reply_corrected ...`, with question numbers and counts only.
4. **The saved-answer store gets only the kept evaluations**, on the
   short and the long path.
5. **The paper goes to the teacher's review queue**
   (`students/services.py`): a third review reason,
   `ai_reply_corrected`, tier at least moderate, beside the others and
   not instead of them. No model change.
6. **The feedback formatter is sent the arithmetic only**
   (`students/feedback_projection.py`): it words the result for the
   student and can restate what it is sent.
7. **A reply cannot mark its own evaluation as the system's**
   (`_stamp_as_a_models`, at both entry points of a reply): `graded_by`
   is assigned and `from_cache` removed. Found by v2 reading early, before
   any run; tests first.

## Rulings (Senior Manager, 2026-10-07)
- All four of my departures from the first sketch accepted: the
  system-held evaluation stands; the stamping; the formatter gets
  arithmetic only; nothing is dropped as unmatched when no question is
  known (a stated limit, below).
- The frontend note and the fix-forward line go into batch 12's package.
- The founder's read-only count goes to the user through the Senior
  Manager; the team does not run it.

## Commits
| Commit | What |
|---|---|
| `96300987` | tests for a repeated or stray evaluation in the AI's reply (red) |
| `de555697` | one evaluation per question goes into the score |
| `27400a7a` | tests, the formatter is not told of a correction (red) |
| `01a93d65` | the formatter is sent the arithmetic, not the correction |
| `5bce01f6` | base update: beta `d7143538` merged in (the Release Engineer) |
| `dc0fa2e6` | the test that pinned the uncapped stray replaced; a store test |
| `806f7027` | tests, a reply cannot mark its own evaluation as the system's (red) |
| `a11b7109` | a reply's evaluations are stamped as a model's |
| `a3c3a194` | mutation runner |
| `ba684640` | tests the Senior Manager asked for; two mutants for them |
| `42eccc7b` | one assertion of my own test corrected (tests only); the tip the gates ran on |

## A test that pinned the old behaviour, replaced
`ai_processor/tests_grading_arithmetic.py`, `FinalizeGradingResultTest.
test_evaluation_with_no_matching_question_is_floored_but_uncapped`
locked what this row removes. Replaced in `dc0fa2e6` by
`..._is_left_out_of_the_sum` (the stray is left out and counted in the
note). It is in R3's expected set.

## The whole-tree search for other pinning tests
One `git grep` on `a3c3a194` over every test file (`'*test*.py'`), on
the Release Engineer's grant (`tree_search_a3c3a194.txt`, 106 lines).
Terms: `floored_but_uncapped`, `no known cap`, `verification_status`,
`score_calculation_verification`, `"graded_by"`, `from_cache`,
`review_reasons`, `grading_result_for_formatter`.
Hits by file: this row's two modules (34 lines);
`students/tests_missing_answer_review.py` 12;
`AutoGrader/tests_student_feedback_guard.py` 10;
`students/tests_second_opinion_queue.py` 7;
`ai_processor/tests_objective_pipeline.py` 7;
`students/tests_student_feedback_routes.py` 6;
`students/tests_formatter_input.py` 4;
`ai_processor/tests_grading_cache.py` 4;
`students/tests_student_feedback_scoping.py` 3; and one or two lines in
twelve more files. Each read: none asserts a repeated or stray
evaluation in the sum, a `PASS` on a corrected reply, or a reply's own
`graded_by`/`from_cache` surviving. Nothing pinned beyond the replaced
test. The two later commits (`ba684640`, `42eccc7b`) touch this row's
own test modules and runner only.

## Gates
All on the Release Engineer's grants, capped at 6G, sleep inhibited,
timeout 1800, output to files, own databases, rules 17 and 18.

### A first chain that stopped (kept, not counted)
On `ba684640`, 13:23:10 to 13:24:18. (r1) red as written. (r2) stopped:
the three written tests failed and one more,
`TheTotalIsNeverAboveTheMaximum.test_whatever_the_list_holds`, subtest
"one question five times": `26 != 30`. The assertion was **my test's
mistake** (that shape's right total is 10 + 8 + 8 = 26, and the code gave
26). Not re-run. Ruling: correct the test with a below-maximum shape
(`42eccc7b`, tests only); add that test to the two later red commits'
sets, where the file as it stands still holds the mistake, with a dated
note; keep the stopped run. Its files are in
`stopped_chain_ba684640.tar.gz`.
v2 compared the as-run and amended files: that one test added to two
sets, nothing else.

### The chain that is the gate: `42eccc7b`, 13:40:10 to 13:50:14
| Step | What | Result |
|---|---|---|
| (r1) | the two new modules at `96300987`, tests before any fix | Ran 30, FAILED (failures=26): the written red set (log `5f45f2bcf0c669de`) |
| (r2) | at `27400a7a`, the formatter tests before their fix | Ran 34, FAILED (failures=3, errors=2): the written red set (`155809a664f28862`) |
| (r3) | at `806f7027`, the marking tests before their fix | Ran 39, FAILED (failures=8): the written red set (`41b3605316b4b4e0`) |
| (a) | the changed modules and the guard list | Ran 674 in 224.8 s, OK |
| (b) | 19 mutants, each restored and verified | baseline green; 19/19 killed; 144 s |

Load: 9 at the start, 19 during (r3) (another granted run beside it),
5 from the end of (a). No kill looks like a timeout: each mutant's run
took 4.8 to 8.4 s and ended with a FAILED line.

**The comparison after (b) stopped the chain: R10's failing set had four
tests where three were written.** The three written ones failed. The
fourth is `NoStudentFacingAnswerHoldsTheCorrection.
test_the_saved_row_really_holds_it`, the control added at `ba684640`. It
asserts that the saved row's review reasons are `["ai_reply_corrected"]`;
R10 removes that reason from the save; so the control fails under R10,
and rightly. When I added the control I put it in no mutant's set: an
omission in my expected file, not a fault in code or test. The other 18
sets were as written.

**Ruling (Senior Manager, between 13:51 and 13:52): the run stands as the gate; no second
chain.** Every mutant was killed, the tests' results do not depend on the
expected file, and the comparison is a text check of kept logs.
**The expected set for R10 was amended after the run; the run was not
repeated.**

| File | sha256 (first 16) |
|---|---|
| `expected_kills.as_run_56a57f14.py.txt`, the file as run | `56a57f140e92a02e` |
| `expected_kills.py.txt`, amended 13:52 (one name line, the control added to R10, a dated note) | `a3c2bfc6e7a6c4ac` |
| `expected_kills_42eccc7b.txt`, the stopped comparison's output | `4b86467c488dbf02` |
| `expected_kills_42eccc7b.amended.txt`, the same logs against the amended file, 13:52:25 | `b88dac34c97cc277` |
| `a_modules_42eccc7b.log` | `93d2174b84cf92c9` |
| `b_mutation_battery_42eccc7b.log` | `8e97e0cc58433553` |
| `battery_42eccc7b/results.tsv` | `154a8a4d929ffb46` |

v2 checked the amendment: it adds that one test to R10 and nothing else;
by its own reading and the 19 logs only R10 reaches the control (Ran 54
in every mutant's run; the control's name is in `R10.log` alone); the
kept checksums are these.

State looked at after the chain (13:50:29): worktree at `42eccc7b`,
clean; no H-154 test process; the database `test_h154_mut` exists (kept
by `--keepdb`).

### Mutants
| | What it guards | Failing tests (as run) |
|---|---|---|
| R1 | of a question's repeats the last does not simply win | 11 |
| R2 | nor the first | 5 |
| R3 | an evaluation that matches no question is dropped | 5 |
| R4 | an evaluation the system held stands against a model's | 3 |
| R5 | among a model's repeats the lowest is kept | 9 |
| R6 | the note does not say PASS when something was dropped | 4 |
| R7 | the long path's note carries what its first sum dropped | 1 |
| R8 | short path: only kept evaluations reach the store | 2 |
| R9 | long path: the same | 1 |
| R10 | a corrected reply puts the paper in the review queue | 4 (3 written; see above) |
| R11 | the formatter is sent a corrected note as arithmetic only | 1 |
| R12 | a warning line is logged | 1 |
| R13 | the count of dropped repeats is the count | 4 |
| R14 | a reply cannot say its evaluation came from the store | 4 |
| R15 | a reply cannot name its own grader | 5 |
| R16 | short path: the reply's evaluations are stamped | 4 |
| R17 | long path: the same | 1 |
| R18 | a student is not sent the review reasons (older shield, H-127) | 1 |
| R19 | a student is not sent the saved arithmetic note (H-127) | 1 |

The counts are tests; the runner's `failures=` figures are higher where
a test has subtests.

### The regression
`c_h154.sh 42eccc7b`, in a quiet window, 14:14:24 to 14:27:33: the apps
ai_processor, AutoGrader, students and assignments (AutoGrader for the
cache tests, rule 20), `--parallel 2`.
**Ran 2505 tests in 738.483s, OK (skipped=22)**; exit=0; stalled=0; no
`FAIL:` or `ERROR:` line in the raw log; none skipped for want of
Chromium. Load 3.21 at the start, 3.77 at the end. Raw log
`c_four_apps_p2_42eccc7b.raw.log`, sha256 begins `50077beaee237747`
(stamped copy `0bf635b58a9ca6ec`); here gzipped.

## Limits, stated
- **When no question of the assignment is known, nothing is dropped as
  unmatched.** Repeats are still reduced to one. Accepted by the Senior
  Manager.
- **A rubric that itself holds a question number twice** (v2, N1; read
  further on 2026-10-07 after the gates, by reading only). Grading keys
  everything by question number, so for such an assignment, before this
  row as after it: the **maximum is under-counted** (the pair is counted
  once, at the last one's points) and both questions are graded against
  one answer. What this row changes there: the two evaluations used to
  be added together; now they are taken for a repeat, so **keep-lowest
  can drop a legitimate mark**, and **every such paper is flagged** for
  the teacher (`ai_reply_corrected`). Example: question 2 twice, worth 5
  and 10, a student earns 5 and 0. Before: 5 of a counted 10. Now: 0 of
  a counted 10, flagged. True: 5 of 15. Wrong either way; no longer
  silent. **Who can cause it** (corrected 2026-10-07 14:52; the
  sentence here at `3a707780`, "It can be saved:
  `AssignmentSerializer.validate` does not check ...", read as if a
  teacher's request could save it, and I had said so to the Senior
  Manager; that was wrong, and the Senior Manager found it by reading
  the routes): NOT a teacher's request. Every POST, PUT and PATCH on
  the assignment routes uses `AssignmentTextSerializer`
  (`assignments/views.py`, `get_serializer_class`), which has no
  questions field: a teacher sends text and an AI makes the question
  list. A repeated number can come from an AI path (the single-pass
  text extraction returns the model's numbers untouched; the two
  chunked paths renumber; generation was not read for this row) or from
  the Django admin, a staff tool. Nothing checks uniqueness on any
  path. The user's word of 7 October 2026 is that no saved assignment
  has a repeated number; the team has run no query. **Not this row:** its own row, H-158, MEDIUM, leading batch 13
  (Senior Manager, 2026-10-07): a save with a repeated number refused,
  the single-pass extraction renumbered, a read-only count for the
  founder.
- **Which repeat is right is not known.** The lowest is the cautious
  choice, and the paper goes to the teacher for that reason.
- **Grades already saved are not recalculated** (below).
- **Production has the same fault** by reading (`origin/main` 9c21bee8);
  not shown there by a run. Under "before promotion to main" in the
  package.

## Downstream, read
No serializer caps a score. `classrooms/final_grade.py` clamps the
course figure only. No other reader of the arithmetic note expects only
`PASS` or `FAIL` (v2, N3). A student is sent neither the note nor the
review reasons (H-127's shields; R18 and R19 show the tests of that can
fail).

## Saved grades
Fix-forward. A saved score above its maximum stays until the paper is
graded again. `founder_count_query.sql` is a read-only count (score above
maximum, percentage above 100, by month, counts only) for the founder to
run if they choose. **Nobody on the team has run it.** It is a lower
bound: a repeat that left the total at or under the maximum cannot be
found afterwards.

**Withdrawn as a request, 2026-10-07:** the user's word, passed on by
the Senior Manager, is that no existing grade is above the maximum. That
is the user's statement; the team has run no query. The file is kept and
its first lines say so.

## Two wrong predictions of mine, and what changes
Both stops of this row's chains were my own expectations, not the code:
a total I did not add up, and a control I did not read against every
mutant. Rule 19 now says: expected numbers in a new test are added up by
hand or by calling the function before the first gate; and a test added
after the expected sets are written is read against every mutant before
the freeze, the expected file re-derived, not patched from memory.

## The credential pattern
`credcheck.sh -v` on this folder alone, 2026-10-07 14:38, archives opened,
masked output: no URL with a password, no encoded URL, no bare made-up
password. Six files carry assignment-form names, each judged by reading
the line with its value masked: `passed:` in the two expected files
(the comparison's own wording); `secret:` and `PASS:` in the two
regression logs (the path of a test's refused address,
`http://internal.test/secret`, logged by the fetch guard, and an older
probe's "PASS:" line); `KeyError:` in a red step's log. None is a
credential.

## Files
Logs are gzipped where they are large or carry trailing spaces; the
checksums above are of the files before gzip.
- `run_mutants.py`: the runner (R1 to R19). `chain.sh.txt`,
  `run_chain.sh.txt`, `c_h154.sh.txt`, `iso_file.sh.txt`: the scripts as run.
- `chain.status`, `chain_wait.log`: the gate chain. `r1_`, `r2_`,
  `r3_repro_*.log.gz` and `expected_r*.txt`: the three red steps.
  `a_modules_42eccc7b.log.gz`: step (a).
- `b_mutation_battery_42eccc7b.log`, `battery_42eccc7b.tar.gz` (results
  and one log per mutant): step (b).
- `expected_kills.py.txt` (amended after the run),
  `expected_kills.as_run_56a57f14.py.txt` (as run),
  `expected_kills_42eccc7b.txt` (the stopped comparison),
  `expected_kills_42eccc7b.amended.txt` (the comparison after).
- `stopped_chain_ba684640.tar.gz`: the first, stopped chain.
- `c_four_apps_p2_42eccc7b.raw.log.gz`, `.log.gz`, `.load.txt`,
  `iso.status`, `c_h154.out.txt`: the regression.
- `tree_search_a3c3a194.txt`: the search's hits.
- `FRONTEND_NOTE.md`: for the frontend. `founder_count_query.sql`: the
  founder's read-only count, **not run by the team**.
