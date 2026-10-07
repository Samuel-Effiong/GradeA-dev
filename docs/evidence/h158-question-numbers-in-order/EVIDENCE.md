# H-158: an AI reply's question numbers are made 1..N before anything is saved

**Severity:** MEDIUM. **Author:** d5. **Branch:**
`task/h158-question-numbers-in-order`, on `220f9cd6`. **Verifier:** v2.
**Batch:** 13. Three production files (`assignments/services.py`,
`views.py`, `serializers.py`), one test module, a mutation runner. No
migration, no model change, no setting. **Does it change grading?** It
changes no grading code. It changes what grading is handed: the numbers
of an assignment's questions. One test runs the real grading pipeline on
it (below).

The design note (`DESIGN_NOTE.md`, beside this file) has the fault, the
design and every ruling of the Senior Manager. This file has what was
built, searched, run and seen.

## The fault, shown
Grading pairs a question with the student's answer BY the question's
number, and nothing made the numbers of an AI reply distinct. Seen by
calling the real grading pipeline directly, and now held by a test: a
paper of three objective questions which the model numbered 1, 1, 2 was
graded as a paper of TWO questions, out of 20 instead of 30. The
question the pairing lost was simply not graded.

Both chunked extraction paths already re-indexed their merged questions
1..N (`ai_processor/services.py`); the single-call extraction, the
generation from a prompt and the save of a stored draft took the model's
word.

## What was built
| Where | What |
|---|---|
| `assignments/services.py` | `number_questions_in_order`: a new list in which each entry that is an object carries its position, from 1; the model's own numbers by new number, as text of at most 32 characters (null where the model gave none); how many numbers changed. The input is not changed. |
| `extract_assignment_data` | numbers the questions straight after the reply is trimmed: before the stored AI copy, before the document, before the serializer. The stored AI copy (`ai_raw_payload`) gains one key, `model_question_numbers`. One WARNING line when a number changed: user id, path, counts. |
| `assignments/views.py` | the generated draft and the save of a stored draft are numbered the same way; a log line; NOTHING new stored: a draft's snapshot is sent to the client, and a generated assignment has no paper whose numbering the key would record. |
| `assignments/serializers.py` | `AssignmentSerializer.validate` refuses a repeated number: "Two questions have the same number (N). Each question needs its own number." A last defence: no AI path reaches it any more. |

**A second thing it cures, said because it touches H-159:** a number the
serializer's integer field refuses (`"2a"`, null, true, 2.5: seen by
calling the field) used to cost the teacher the save AFTER the paid
call. Such a number is now replaced like any other before the
serializer. A test holds it for `"2a"`.

**Who is sent the new key:** nobody. `ai_raw_payload` is named by one
serializer, `AssignmentSerializer`, where it is write-only
(`extra_kwargs`). By my reading of the serializer files of the assignments, students,
classrooms, dashboard, billing and users apps at `7f5d1743`: no other
serializer names the field and none uses `"__all__"`. A draft's snapshot,
which IS sent to the client, never carries the key (a test, and mutant
N15). The Django admin shows a staff user the model's fields as it
always showed `ai_raw_payload`; the key is inside it there.

**Who can still send a repeated number to the serializer:**
nobody, by my reading at `7f5d1743`. `AssignmentSerializer(` is built
in four places, all of them AI paths that now number first:
`update_assignment_from_extraction` (`assignments/services.py`: create
and edit by text, sync and background), the upload
(`assignments/file_uploads.py`), the generated draft's own check and the
draft save (`assignments/views.py`). A teacher's POST, PUT and PATCH on
the assignment routes are given `AssignmentTextSerializer`
(`get_serializer_class`), which takes text, not questions. Read from the
code, not shown by a run.

## Commits
| Commit | What |
|---|---|
| `7be08a58` | tests (red), 29 tests |
| `7d7b1220` | the change |
| `af1c5b8c` | mutation runner, N1 to N18 |
| `7f5d1743` | one more test: the real grading pipeline grades three questions, not two. The tip the gates ran on. |

## The search (rules 15 and 20)
One pattern-limited `git grep` over the test files of `af1c5b8c`, git
objects only (the Release Engineer: no grant needed outside a full run),
`tree_search_af1c5b8c.txt`, 253 lines, seven sets of terms: the stored AI
copy and the new key; the extraction entry points; generation and the
draft save; upload; `AssignmentSerializer(`; the override flag; this
row's own names. A second, narrower one over the modules that drive the
AI assignment paths for fixtures or assertions on a question number
other than 1: two hits, both a second question numbered 2.
- **No test pins** the stored AI copy's exact content, nor an AI reply's
  numbers other than in order.
- The chain's modules step takes the 19 existing modules that drive
  these paths (45 labels with the guard list). Left to the app
  regression: the cache-matrix and scale modules. In no step: the
  `tests_real_*` modules, which make paid calls and are opt-in.

## Every assertion read against the real value
`ASSERTIONS_READ.md`, beside this file, written before any run: per
test, what it inspects and in what form, how that form was seen, what
decides each negative check, which mutants fail it; and the checks no
mutant decides, listed as not evidence.

## Gates
All on the Release Engineer's grants; 6G cap, sleep inhibited, timeout
1800, output to files, own databases, rules 17 and 18. Expected failing
sets written before any run (`expected_kills.py`, `4a01bd2172383efe`).

### A false start of mine, told
The chain was granted at 18:09:36. I held its start for the machine's
load (8.5, another project's tests). At 18:13:06 the Release Engineer
sent a HOLD (a HIGH row went first). I started at 18:16:05 without
having read it, in a command whose own check had printed that a test
process was running, and stopped the run myself at 18:16:27 through the
script's own stop path. Step (r) only, 22 seconds, no Ran line: not a
result of any kind. Its four files are in `stopped_by_me_7f5d1743.tar.gz`.
The Senior Manager ruled that the chain's one run was still owed.

### The chain, 18:19:49 to 18:25:41, on `7f5d1743`
On the Release Engineer's "GO H-158" (`chain.status`, `2268bbab2cca6b1c`).

| Step | What | Result |
|---|---|---|
| (r) | the final test file on the code of the red commit `7be08a58` (one test was written after it) | Ran 30 tests in 2.046s, FAILED (failures=14, errors=19; subtests counted): the 24 written tests (`cd672019f33b3b07`) |
| (a) | the module, 19 modules of the paths touched, the guard list: 45 labels | Ran 790 tests in 185.987s, OK; no `FAIL:` or `ERROR:` line (`1444c075c4d7225e`) |
| (b) | 18 mutants, each restored and verified | baseline green; 18/18 killed; 121 s (`32ec0952b8363dd6`) |

Load 2.14 at the start, 2.90 to 1.87 through (a), 2.80 at the end. No
kill looks like a timeout: each mutant's run took 5.3 to 6.8 s and ended
with a FAILED line. Every mutant's failing set was the written one
(`expected_kills_7f5d1743.txt`, `6f24528d2ecde79b`):

| | What the mutant does | Tests failing (written beforehand, and as run) |
|---|---|---|
| N1 | extraction keeps the model's numbers | 8 |
| N2 | the key is not stored | 6 |
| N3 | extraction never logs | 1 |
| N4 | extraction always logs | 1 |
| N5 | the log line carries the questions | 1 |
| N6 | every number counts as changed | 7 |
| N7 | 2.0 counts as already in its place | 1 |
| N8 | the kept text is not bounded | 1 |
| N9 | the input's own objects are numbered | 1 |
| N10 | an entry that is not an object takes a position | 1 |
| N11 | a value that is not a list is walked | 1 |
| N12 | generation is not numbered | 3 |
| N13 | the draft save is not numbered | 1 |
| N14 | no last defence | 2 |
| N15 | the model's numbers ride in the draft | 1 |
| N16 | the override comparison reads the whole stored copy (code this row does not change) | 1 |
| N17 | a question with no number keeps the text "None" | 1 |
| N18 | the draft paths never log | 2 |

What the run was the first to show (written by reading only): the
routes' status codes for these replies, the stored form of the key on a
saved row, the serializer's error list, and all eighteen sets.

State looked at after the chain (18:25): worktree at `7f5d1743`, clean;
no test or mutant process; no repro or mutant worktree left. The test
databases the runner and the red step keep: NOT looked for in the
database server.

### The regression
`c_h158.sh 7f5d1743`: AutoGrader, assignments, students and billing,
`--parallel 2`, 19:11:50 to 19:22:02, on the Release Engineer's grant of
19:09:21, in a quiet period. **Ran 3867 tests in 587.040s, OK
(skipped=16)**, exit=0, stalled=0; no `FAIL:` or `ERROR:` line in the
whole raw log (`c_four_apps_p2_7f5d1743.raw.log`, `ee6f8448cbb469e0`;
stamped copy `a9b18be6359e4a4f`, load file `5e7e8332276ea57f`). Load
3.71 at the start, 7.11 at the end (something else began near the end; I
did not watch it). The 16 skipped are the opt-in ones, as in the four-app
runs of the other rows.

**A refused attempt, told:** at 19:11:22 the same script refused to start
("1-minute load average 4.07 is over 4"; the other project's tests). I
sent the Release Engineer "STARTED" before reading the script's output
and corrected it within a minute; the attempt left one load line (kept in
`stopped_by_me_7f5d1743.tar.gz`, folder `refused_19_11_22`). The run
above started at 19:11:50.

## How a student's answers map after the numbers change
(The design note has the reasoning; these are the statements.)
- A new assignment made by upload or generation: the document is built
  from the questions after numbering, so printed and stored numbers
  agree.
- A paper created or edited by TEXT keeps the teacher's own numbering in
  its document while the stored questions are 1..N. That was already so
  for a long paper and whenever the model obeyed its prompt.
- **For the package too:** an assignment that already has submissions
  and is extracted again can leave stored answers under numbers no
  question has any more; at the next grading those questions are graded
  "answer not found" and the paper goes to the review queue. Nothing is
  graded wrongly in silence. No stored answer is rewritten by this row.
  The same happens today whenever the model numbers such a paper
  differently on a second reading.

## Limits, stated
- **A paper whose own numbering repeats** (Section A 1, Section B 1) is
  stored as 1..N, while a student's sheet says "B 1". The answer reader
  maps by number, then position, then content, so whether that
  student's answer reaches the right question rests on the model; no
  test of ours can show it without a paid call. Follow-up row H-171
  (LOW until shown otherwise): the reader is given each question's own
  label from the new key; decided after one live check with a sectioned
  paper, approved by the user, planned in
  `SECTIONED_PAPER_CHECK_PLAN.md`, not yet run.
- **A paper whose numbers are distinct integers but not 1..N (for
  example 5 to 10)**, raised by Verifier 2: before this row the
  assignment kept its own numbers and answer extraction relabelled the
  model's answers to them; now the questions are 1..N and a sheet that
  says "5." must be mapped to stored question 1 by the answers prompt,
  which decides by number, then position, then content. That rests on the
  model; no test of ours shows it; nothing reads `model_question_numbers`
  at answer extraction. The Senior Manager ruled "always 1..N" because
  both chunked paths already do so for every long paper. A non-integer
  label such as "1(a)" was never storable (the question number is an
  integer field), so such a paper cost the teacher the save, after the
  paid call, before this row.
- A question's text that refers to another by number is not rewritten.
- The key is kept on the three extraction paths only.
- H-172 (LOW, its own row, not cured here): the "teacher overrode the
  AI" flag never sets on rows made by today's code; the comparison needs
  `ai_generated` true AND a stored copy, which no path writes together.
  One test here holds that the new key would not change that comparison
  on a row that has both.
- Not run against a browser, the frontend, or a real model.

## The credential pattern
`credcheck.sh -v` on this folder alone, 2026-10-07 19:27, after the
Release Engineer's END, archives opened, masked output: no URL with a
password, no encoded URL, no bare made-up password. Assignment-form
names, each judged by what stands before it: `Key:`, `key:` and `PASS:`
in the two regression logs (a query plan printed by a test, a line of
prose, an older probe's own line); `KeyError:` in the red step's log and
in the mutants' logs (the tests' own errors); `passed:` in the expected
file (the comparison's wording); `key:` in this file's prose;
`snapshot_keys=` in the search output (a line of a test that names a
variable). None is a credential.

## Files
Logs gzipped where large; checksums above are of the files before gzip.
- `DESIGN_NOTE.md`, `ASSERTIONS_READ.md` (`646306bc40b13b46`),
  `SECTIONED_PAPER_CHECK_PLAN.md`: the design, the per-test reading, the
  plan of the one paid check (not run).
- `run_mutants.py`: the runner (N1 to N18). `chain.sh.txt`
  (`178d5cc4177f4ed2`), `run_chain.sh.txt`, `c_h158.sh.txt`
  (`4b4b27e03f86b56b`), `expected_kills.py.txt` (`4a01bd2172383efe`).
- `chain.status` (`2268bbab2cca6b1c`), `chain_wait.log`,
  `r_repro_7be08a58.log.gz`, `expected_r_7be08a58.txt`,
  `a_modules_7f5d1743.log.gz`, `b_mutation_battery_7f5d1743.log`,
  `battery_7f5d1743.tar.gz`, `expected_kills_7f5d1743.txt`: the chain.
- `c_four_apps_p2_7f5d1743.raw.log.gz`, `.log.gz`, `.load.txt`,
  `iso.status`, `c_h158.out.txt`: the regression.
- `tree_search_af1c5b8c.txt` (`38e80caece4d1396` as committed: the commit hook removed trailing blank lines; `b88ee0bd97322596` as made): the search.
- `stopped_by_me_7f5d1743.tar.gz`: the false start and the refused
  attempt.
