# H-165: the answer document builder does not raise on stored answers it cannot print

**Severity:** MEDIUM. **Author:** d5. **Branch:**
`task/h165-answer-document-unreadable-answers`, on `220f9cd6` (batch 12
with H-150). **Verifier:** v2. **Batch:** 13, first row.
Four production files, one test module, a mutation runner. No migration,
no model change, no setting. **A payload changes for affected rows only**
(rule 20): a 500 becomes a 200 whose document carries one fixed line.

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
