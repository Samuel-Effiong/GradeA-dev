# H-165: the answer document builder does not raise on stored answers it cannot print

**Severity:** MEDIUM. **Author:** d5. **Branch:**
`task/h165-answer-document-unreadable-answers`, on `220f9cd6` (batch 12
with H-150). **Verifier:** v2. **Batch:** 13, first row.
Four production files, one test module, a mutation runner. No migration,
no model change, no setting. **A payload changes for affected rows only**
(rule 20): a 500 becomes a 200 whose document carries one fixed line.

**Read this first:** the tip is now `8e43c2a1`. After Verifier 2's
finding at `bbd01981` one more cure went in; it, its tests and the gates
that ran on the final tip are in the LAST section, "The delta after
Verifier 2's finding". The sections before it describe the row at
`e42a8d2e` and are kept as history; where they differ, the last section
wins (it replaces one sentence about what is sent to the model, and it
lists five production files, not four: `ai_processor/services.py` too).

The design note (`DESIGN_NOTE.md`, beside this file) has the fault, the
design, the Senior Manager's rulings and ruling (d). This file has what
was built, searched, run and seen.

## The fault, short
`student_submission_to_html` walked `submission.answers` as a list of
objects and raised on any other non-empty JSON. For such a row: a
student's read of the unreleased paper answered 500 on every read; any
read answered 500 when the stored document was empty; grading failed at
its save, after the paid call (refunded). Beta's two writers could not
store such a value only because the builder raised inside them; the live
service's edit-by-text path saves first, so such rows can exist on
production. How many do is not known: see the count file.

## What was built
| Where | What |
|---|---|
| `students/services.py` | `printable_answers` decides in one place what can be printed and what was left out (0, a count, or `"all"`). The builder prints what it can and ends the section with one fixed line when something was left out. An ordinary row's document is byte for byte what it was. |
| `students/services.py` | `grade_engine` refuses a paper with NOTHING printable before the grading claim and before any paid call: the paper goes to the review queue with `answers_unreadable` (`"all"`, critical), one log line, and `SubmissionAnswersUnreadableError` with the teacher's sentence. |
| `students/services.py` | `_populate_and_save_grade`, Source 4: a paper with SOME left out is graded and flagged `answers_unreadable` (the count, critical), one log line. |
| `students/services.py` | both writers (`upload_answers_engine`, `update_submission_from_raw_text`) refuse a value that is not a list of objects, with the error they already gave for a value that is not a list, inside the same refund scope where there is one. |
| `students/views.py` | the read that stores a rebuilt document logs the one line for such a row. |
| `students/exceptions.py`, `AutoGrader/error_messages.py` | the new error, shown to the teacher as written (400). |

The two lines a reader may see, fixed text of ours:
"This submission's answers could not be displayed." and "Some of this
submission's answers could not be displayed." The teacher's sentence on
the refusal: "This submission's answers could not be read. Upload the
paper again or re-enter its answers, then grade it."

**The log line** (`Unreadable answers: submission=<id> answers=<kind>
left_out=<n>`): ids and the kind of value, never the value. In FOUR
places: the three the Senior Manager ruled (grading's save, the read
that rebuilds an empty stored document, and, when score printing lands,
the manual grade: not in this row) and the up-front refusal itself,
which the Senior Manager accepted on 2026-10-07.

## Commits
| Commit | What |
|---|---|
| `122b28de` | tests (red), 30 tests |
| `a395df8e` | the change |
| `51c78a72` | mutation runner, U1 to U18 |
| `2b7e812e` | one more test: the edit's refusal refunds the charge |
| `069eccf9` | R4 corrected after the stopped chain (below) |
| `e42a8d2e` | the runner gains U19; the tip the gates ran on |

## The whole-tree search (rules 15 and 20)
One `git grep` over every test file of `51c78a72`, on the Release
Engineer's grant (`tree_search.sh`, `tree_search_51c78a72.txt`, 504
lines). Nine sets of terms: fixtures whose answers are a non-empty
object, string or list of non-objects; the builder; grading's entry
point and its save; the two writers; the grade routes; the read routes
that carry the document; the errors shown to a user; review reasons
asserted on; the writers' old error text. The patterns are in the script.

- **No test pins the old raise**, and none would now meet the refusal.
- **Fixtures with odd-shaped answers: 33 lines.** 10 are in modules that
  reach the changed code; I read each of those line by line. The other
  23 are in dashboard test modules; **those I did not read line by
  line: I cross-checked them against the search's other sections**, and
  none of those modules calls the builder, grading, a writer or a route
  this row touches. (I first told the Release Engineer I had read all
  33; that was wrong and I withdrew it.)
- **The chain's modules step** takes the existing test modules of every
  route and function this row touches, from all nine sections: 73
  labels. (My first list was built from five of the nine sections and
  missed seven modules; corrected before any run.) The cache-matrix
  scale, load and measurement modules that name those routes are left to
  the app regression.

## Every assertion read against the real value
`ASSERTIONS_READ.md`, beside this file: per test, what it inspects and in
what form, what decides each negative check, and which mutants fail it;
and the checks no mutant decides, listed as not evidence.

## Gates
All on the Release Engineer's grants; 6G cap, sleep inhibited, timeout
1800, output to files, own databases, rules 17 and 18. Expected failing
sets written before any run (`expected_kills.py`).

### The stopped chain, 16:37:59 to 16:41:36, on `2b7e812e` (kept, told)
(r) the red set at `122b28de` was the written one (21 of 30). (a) Ran
1030 tests in 184.493s, FAILED (failures=1, skipped=1): my own
`test_a_student_is_not_told_more_than_the_line`. It looked for the fixed
line in `str(response.data)`; the printed form of a dictionary writes
the line's apostrophe escaped, so the check could not pass on correct
code. The failure message shows the line present in the student's
answer. A fault of the test, not of the product; found by reading the
raw log. (b) did not run. Load 2.97 to 3.51. Files in
`stopped_chain_2b7e812e/`. The Senior Manager approved one re-run on the
corrected tip on condition that every assertion of the module be read
against the real form of what it inspects (above).

### The chain on the corrected tip
`run_chain.sh e42a8d2e`, 16:49:58 to 16:56:28, on the Release Engineer's
grant of 16:49:30 (`chain.status`, `80600a5f6f3e0be3`).

| Step | What | Result |
|---|---|---|
| (r) | the module at `122b28de`, tests before the change | Ran 30 tests in 3.222s, FAILED (failures=5, errors=30; subtests are counted): the 21 written tests fail, the 9 written ones pass (`r_repro_122b28de.log`, `b1aaf5f435ec17dc`) |
| (a) | the module, the existing test modules of the routes and functions touched, and the guard list: 73 labels in all | Ran 1030 tests in 182.230s, OK (skipped=1); no `FAIL:` or `ERROR:` line (`a_modules_e42a8d2e.log`, `e616e7931d8f7bf7`) |
| (b) | 19 mutants, each restored and verified | baseline green; 19/19 killed; 172 s (`b_mutation_battery_e42a8d2e.log`, `0c262df825fd656f`) |

Load 2.23 at the start, 1.87 to 3.17 through (b). Verifier 1's short runs
went beside step (b) from 16:54:05 to 16:55:44 (the Release Engineer's
log). No kill looks like a timeout: each mutant's run took 6.4 to 9.2 s
and ended with a FAILED line. Every mutant's failing set was the written
one (`expected_kills_e42a8d2e.txt`, `fae52a3f490b3cbf`):

| | What the mutant does | Tests failing (written beforehand, and as run) |
|---|---|---|
| U1 | a value that is not a list is walked, not refused | 3 |
| U2 | the fixed line is not printed | 8 |
| U3 | the two lines are swapped | 8 |
| U4 | no refusal up front | 8 |
| U5 | every paper is refused | 4 |
| U6 | the refusal does not put the paper in the queue | 2 |
| U7 | a second refusal adds the reason again | 1 |
| U8 | the refusal's tier is moderate | 1 |
| U9 | grading does not add the reason | 2 |
| U10 | grading adds the reason and does not log | 1 |
| U11 | the read that stores a rebuilt document does not log | 1 |
| U12 | the log line carries the value, not its kind | 2 |
| U13 | the upload accepts any list again | 1 |
| U14 | the edit accepts any list again | 2 |
| U15 | the refusal is not an error shown to a user | 2 |
| U16 | grading's reason is moderate | 1 |
| U17 | an empty value is treated as unreadable | 2 |
| U18 | the line is printed for every paper | 3 |
| U19 | the line carries the stored value after the fixed text | 5 |

State looked at after the chain (16:56): worktree at `e42a8d2e`, clean;
no test or mutant process; `git worktree list` shows no repro or mutant
worktree left. The mutant database `test_h165_mut`: NOT looked for in
the database server; the runner keeps it (`--keepdb`).

### The regression
`c_h165.sh e42a8d2e`: AutoGrader, students, assignments, billing,
`--parallel 2`, 16:58:44 to 17:07:10, on the Release Engineer's grant of
16:57:33, in a quiet period. **Ran 3868 tests in 470.496s, OK
(skipped=16)**, exit=0, stalled=0; no `FAIL:` or `ERROR:` line in the
whole raw log (`c_four_apps_p2_e42a8d2e.raw.log`, `5435c13b9adfce6a`;
stamped copy `ca7807b71cba6c19`). Load 3.11 at the start, 4.76 at the
end; a run of the other project on this laptop (Vezi) was going at the
start. No test skipped for want of Chromium.

## Limits, stated
- A stored document made from unreadable answers stays as stored (for
  staff, and for the student after release) if the answers are later
  repaired by hand; only the unreleased student's view follows the row.
- The fix prints and flags; it does not repair the answers.
- A paper refused up front cannot be graded by hand: the manual-grade
  route refuses a never-graded paper (ruling (d)). The way round is
  readable answers: a new upload or the edit by text; tests hold that
  each cures such a row and that grading then goes through. Whether a
  teacher may hand-grade a paper the AI never graded is a product
  question with the user.
- A stored number or `true` is "nothing printable" and is refused before
  the grading pairing is reached; the pairing is not changed.
- `upload_answers_engine`'s callers: I found no refund scope around
  them. Not changed here; it belongs with H-159.
- Not run against a browser or the frontend.

## For the package: before promotion to main
H-165 changes nothing on production until it is promoted. Rows whose
answers are not a list of objects may exist there (the live edit-by-text
path saves before it builds). `founder_count_query.sql`, beside this
file, counts them by month: read only, two SELECTs, counts only. **It
has never been run and has not been tried against any database**; it is
for the user to run if they choose. Nobody on the team runs it.

## The credential pattern
`credcheck.sh -v` on this folder alone, 2026-10-07 17:18, archives opened,
masked output: no URL with a password, no encoded URL, no bare made-up
password. Assignment-form names in five files, each judged by what
stands BEFORE the name (what follows was not shown): `Sort Key:` (a
query plan printed by a test), `{key:` (a line of prose in a test's
output) and `PASS:` (an older probe's own line) in the two regression
logs; `InvalidToken:` in the (a) log and in the stopped chain's (a) log
(the name of an exception class, `rest_framework_simplejwt.exceptions.
InvalidToken`, in a test's expected error); `pass:` and `passed:` in the
expected file (a comment of mine and the comparison's wording). None is
a credential.

## Files
Logs are gzipped where they are large or carry trailing spaces; the
checksums above are of the files before gzip.
- `DESIGN_NOTE.md` (`e242cb47c7b477dd`, as committed: the commit hook removed one trailing blank line), `ASSERTIONS_READ.md`
  (`8e08855268b4cad1`), `founder_count_query.sql` (`c45a1c39ecc9646f`).
- `run_mutants.py`: the runner (U1 to U19). `chain.sh.txt`
  (`e519ca174e2e845e`), `run_chain.sh.txt`, `c_h165.sh.txt`
  (`beb55a94f688c964`), `iso_file.sh.txt`, `tree_search.sh.txt`: the
  scripts as run. `expected_kills.py.txt` (`fedfcf157e34d528`): the
  expected sets, with their dated notes.
- `chain.status`, `chain_wait.log`, `r_repro_122b28de.log.gz`,
  `expected_r_122b28de.txt`, `a_modules_e42a8d2e.log.gz`,
  `b_mutation_battery_e42a8d2e.log`, `battery_e42a8d2e.tar.gz`,
  `expected_kills_e42a8d2e.txt`: the chain on the corrected tip.
- `stopped_chain_2b7e812e.tar.gz`: the stopped chain, whole: its status,
  its two logs (the (a) log `9b440ee969b6e159`), and the script and
  expected file as they were then.
- `c_four_apps_p2_e42a8d2e.raw.log.gz`, `.log.gz`, `.load.txt`,
  `iso.status`, `c_h165.out.txt`: the regression.
- `tree_search_51c78a72.txt` (`a03395f4f667bc74`): the search's hits.

## The delta after Verifier 2's finding: tip `8e43c2a1`

Everything above this section describes the row as it stood at
`e42a8d2e` and its evidence commit `bbd01981`, and is kept as the
history it is. Verifier 2 then verified `bbd01981`, found no fault in
the change, and found one limit with a probe of its own. The Senior
Manager ruled the limit cured in this row. **The tip the gates below ran
on is `8e43c2a1`.**

### The finding (Verifier 2, its probe's Y2, green at `bbd01981` as a stated limit)
Every grading test of mine replaced the whole of
`students.services.ai_processor`, or saved a grade directly, so the real
grading code never met a stored list holding an entry that is not an
object. Verifier 2 ran the real pipeline with only the provider call
replaced. With the settings as shipped such a paper was graded and
flagged. With the environment switch `GRADING_DETERMINISTIC_OBJECTIVE`
off it was NOT graded: the step that reuses saved evaluations
(`_partition_cached`, `ai_processor/services.py`) called `.get` on the
entry in its second loop; the tier-0 step, which filters the list first,
is skipped when the switch is off. No paid call, the claim FAILED, no
flag, not in the review queue.

### The cure (`48b22236`)
One function, `ai_processor.services.is_readable_answer`, answers "can
the system read this entry of a stored answers list" (it is an object).
It is now what these five ask: `printable_answers` and
`is_a_list_of_objects` in `students/services.py` (the answer document
and both writers), the tier-0 step, and BOTH loops of `_partition_cached`,
whose second loop gains the condition. With the settings as shipped
nothing changes.

**Said plainly, on Verifier 2's remark:** the function's docstring says
the document, the writers and "the grading steps below" all ask it. That
is true of the five places named here. Five other places in the same
file skip such an entry with their own `isinstance(a, dict)`: the
pairing, the evidence check, the second opinion's selection, the
saved-answer store and the answer-status stamp. They were not changed.

### Every walk of the stored answers in `ai_processor/services.py`
The Senior Manager asked whether another switch skips around another
filter. **None**, by my reading of every loop over the answers at
`e42a8d2e`; Verifier 2 read the same and agrees.

| Where (lines at `e42a8d2e`) | When | An entry that is not an object |
|---|---|---|
| the pairing, 2671 | before the call (long papers; second opinion) | skipped |
| the tier-0 step, 3121 | before the call; not run with `GRADING_DETERMINISTIC_OBJECTIVE` off | filtered out of what it hands on |
| the reuse step, first loop, 3200 | before the call; returns at its first line with `GRADING_ANSWER_CACHE_ENABLED` off | skipped |
| the reuse step, second loop, 3229 | as above | **`.get` on it: the fault. Cured.** |
| the saved-answer store, 3265 | after the call | skipped |
| the second opinion's selection, 3588 | after the call | skipped |
| the answer-status stamp, 3784 | after the call | skipped |
| the evidence check, 4060 | after the call | skipped |

Switches read near them: `GRADING_DETERMINISTIC_OBJECTIVE` (off was the
fault's condition), `GRADING_ANSWER_CACHE_ENABLED` (off: no fault, the
step returns first), `GRADING_SECOND_OPINION_ENABLED`,
`GRADING_EVIDENCE_ENFORCEMENT`, `GRADING_RESPONSE_SCHEMA_ENABLED`: none
of the last three skips a filter that another step relies on.

One place outside this row, named and not touched: line 2098, the
blank-answer re-check at UPLOAD, calls `.get` on every entry of the
model's reply (reached only when the reply has blank entries). It is in
H-159's note.

### A stored 0 or false: an earlier reading corrected
Verifier 2 read, and I passed on to the Senior Manager, that on a paper
of more than ten questions the pairing would raise on a stored answers
value of 0 or false. **That reading was wrong, Verifier 2's and mine in
passing it on.** The pairing does not raise: the value becomes "no
answers" straight after parsing (`if not isinstance(answers, list):
answers = []`, the two lines before the tier-0 step), before anything
walks it. Seen by direct calls of the pipeline (below). **No production
code was changed for it.**

**What the teacher gets for such a paper:** it is not refused (0 and
false are "nothing there" to the refusal, as `[]` and `{}` are). It is
graded with every question marked "answer not found" and goes to the
review queue with that reason, the same on a short and a long paper.
A paid call is made for it (one on a short paper, three on a
twelve-question one in my calls). That is older than this row and
applies to `[]` and `{}` too; whether a paper with nothing to grade
should cost a call is a product question the Senior Manager puts to the
user, as a row of its own. Not built.

### What direct calls showed (no test loader, no database)
After the cure I called `AIProcessor.extract_grade_with_retry` directly,
the provider call replaced, the saved-answer store cleared before each
call, on a 3-question and a 12-question paper:

| Stored answers | Settings | Outcome, short and long alike |
|---|---|---|
| 0, false, `[]` | as shipped; objective step off; both steps off | graded; one evaluation per question; every question `NOT_FOUND_IN_DOCUMENT` |
| a list with one entry that is not an object | the same three | graded; one evaluation per question; that question `NOT_FOUND_IN_DOCUMENT`, the others `ANSWERED` |

(My first pass did not clear the store between papers and its long
papers "failed": my script, not the product. The table is the second
pass.)

**A stated fact, for the package too (it replaces the sentence "the
entry that is not printed is still sent to the model"):** the entry
that is not printed in the document is sent to the model, inside the
untrusted-text wrapper, on a paper of up to ten questions when no
earlier step claimed anything (the stored list is sent as it is, as
before this row). On a longer paper it is NOT sent: the batch path pairs
answers with questions and the pairing leaves such an entry out. A test
holds the first half (`APaperWithSomeLeftOutThroughTheRealPipeline`).

### Tests and mutants added
| Commit | What |
|---|---|
| `21d78678` | seven tests, red first. Mine, by the method of Verifier 2's probe (the real pipeline, only `AIProcessor.execute_graded_task` replaced). Its Y2 as written states the limit and was green; mine say the opposite and are the red proof: four are red at this commit (the reuse step called directly; a short and a long paper at the AI layer with the objective step off; `grade_engine` to the saved row with it off). The other three are the same papers with the settings as shipped: controls. (The commit's message says "the same four ... are controls"; there are three.) |
| `48b22236` | the cure |
| `c62d682f` | mutant U20: the new condition taken out |
| `b6a29a05` | four tests: a stored 0 or false, short and long, as shipped and with both steps off. Controls. |
| `8e43c2a1` | mutant U21: the pipeline's "not a list is no answers" line taken out (code this row does not change), to show the two both-steps-off controls can fail. **The tip.** |

The per-test reading of all eleven is in `ASSERTIONS_READ.md`, its two
addenda.

A second search (rule 15; the Release Engineer: a pattern-limited
`git grep` over one commit's test files needs no grant outside a full
run): `tree_search2_8e43c2a1.txt`, 50 lines, terms
`_partition_cached|_partition_deterministic|_pair_question_with_answers|is_readable_answer|GRADING_DETERMINISTIC_OBJECTIVE|GRADING_ANSWER_CACHE_ENABLED`.
Hits only under `ai_processor/` (12 modules) and this row's own module.
Eleven of those modules were not in the chain's modules step and were
added (84 labels). No test called the reuse step directly; none pins the
old failure.

### The chain on `8e43c2a1`, 17:40:04 to 17:50:04
On the Release Engineer's grant of 17:39:11 (`chain.status`,
`a8014f8f90e98fce`). Expected sets written before the run
(`expected_kills.py`, `06b089e71c8163db`).

| Step | What | Result |
|---|---|---|
| (r) | the module at `122b28de` | Ran 30 tests in 4.514s, FAILED (failures=5, errors=30): the 21 written tests (`7e721e6139ecde93`) |
| (r2) | the module at `21d78678`, the new tests before the cure | Ran 38 tests in 5.001s, FAILED (errors=4): exactly the four written (`c1cc17f18de65fa0`) |
| (a) | 84 labels | Ran 1231 tests in 290.773s, OK (skipped=1); no `FAIL:` or `ERROR:` line (`1252e80d0b407cc6`) |
| (b) | 21 mutants, each restored and verified | baseline green; 21/21 killed; 238 s (`8793eedb4e79b32f`) |

Load 2.11 at the start, 2.58 to 2.93 through (a), 4.35 at the end of
(b). No kill looks like a timeout: each mutant's run took 8.8 to 12.8 s
and ended with a FAILED line. Every mutant's failing set was the written
one (`expected_kills_8e43c2a1.txt`, `eb3fcb78247bdea4`): U1 3, U2 10,
U3 10, U4 8, U5 6, U6 2, U7 1, U8 1, U9 4, U10 1, U11 1, U12 2, U13 1,
U14 2, U15 2, U16 1, U17 2, U18 3, U19 7, U20 4, U21 2. (U2, U3, U5, U9
and U19 gained the two `grade_engine` tests of the real pipeline.) The
sets of U20 and U21 were written by reading; the run is the first time
they were seen.

State looked at after the chain (17:50): worktree at `8e43c2a1`, clean;
no test or mutant process; no repro or mutant worktree left. The test
databases the runner and the red steps keep: NOT looked for in the
database server.

### The regression on `8e43c2a1`
`c_h165.sh 8e43c2a1`, now FIVE apps: AutoGrader, students, assignments,
billing and ai_processor (two of its grading steps changed),
`--parallel 2`, 17:55:26 to 18:08:50, on the Release Engineer's grant of
17:54:09, in a quiet period. **Ran 4731 tests in 761.989s, OK
(skipped=22)**, exit=0, stalled=0; no `FAIL:` or `ERROR:` line in the
whole raw log (`c_five_apps_p2_8e43c2a1.raw.log`, `bc2ecd7bbff945dd`;
stamped copy `ab4f00e948921f28`). Load 3.69 at the start, 4.98 at the
end (5-minute figure 5.98: something ran beside it that I did not
watch; the Release Engineer notes another session's commit hooks in its
first four seconds). The 22 skipped, from the log, are all opt-in:
tests that make a real paid AI call (`RUN_REAL_AI`), load tests
(`RUN_LOAD_TESTS`), one live network check, and two that cannot fork
inside a parallel worker. None for want of Chromium.

### Limits added by the delta
- **Not tested: the same paper with the second opinion left ON** (it is
  on as shipped). My tests and Verifier 2's switch it off, as the H-154
  harness does: its calls have their own shape and a stand-in provider
  would have to play a second model. By reading, its own walk of the
  answers skips an entry that is not an object.
- A paper with no answers at all (0, false, `[]`, `{}`) costs a paid
  call and is graded all "answer not found": older than this row; a
  row of its own, a product question with the user.
- For rows that change grading, one test that runs the real pipeline
  with only the provider call replaced is now expected (the Senior
  Manager's brief line, from this row). This row has them since
  `21d78678`.

### The credential pattern, second check
`credcheck.sh -v` on this folder alone, 2026-10-07 18:09, after the
Release Engineer's END, archives opened, masked output: no URL with a
password, no encoded URL, no bare made-up password. Assignment-form
names, each judged by what stands BEFORE the name: the same as in the
first check, in the same kinds of file (and now in this file's own
prose, which quotes them), and one new one: `secret:` twice in the two
five-app regression logs, the end of a log line of the ai_processor
app's fetch guard test, "Blocked unsafe fetch_url_content request for
http://internal.test/secret:", a made-up address in a test with no
password in it. None is a credential.

### Files added by the delta
All in `delta_8e43c2a1/`, logs gzipped where large; checksums above are
of the files before gzip.
- `chain.sh.txt` (`6c7a80f0bd60bb54`), `run_chain.sh.txt`,
  `c_h165.sh.txt` (`415602e6aa6c4d96`), `expected_kills.py.txt`
  (`06b089e71c8163db`): the scripts and expected sets as run.
- `chain.status`, `chain_wait.log`, `r_repro_122b28de.log.gz`,
  `expected_r_122b28de.txt`, `r2_repro_21d78678.log.gz`,
  `expected_r2_21d78678.txt`, `a_modules_8e43c2a1.log.gz`,
  `b_mutation_battery_8e43c2a1.log`, `battery_8e43c2a1.tar.gz`,
  `expected_kills_8e43c2a1.txt`: the chain.
- `c_five_apps_p2_8e43c2a1.raw.log.gz`, `.log.gz`, `.load.txt`,
  `iso.status`, `c_h165.out.txt`: the regression.
- `tree_search2_8e43c2a1.txt` (`c094c834268572b7`): the second search.
- One level up: `ASSERTIONS_READ.md` now carries its two addenda
  (`dd47c1968d287bf6`; it was `8e08855268b4cad1` at `bbd01981`), and
  `run_mutants.py` is the runner with U1 to U21. The files of the runs on
  `e42a8d2e` and of the stopped chain are unchanged.
