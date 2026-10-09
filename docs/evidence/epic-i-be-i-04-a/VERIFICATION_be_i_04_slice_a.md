# Verification record: BE-I-04 slice A (the settings version and the six label columns)

Verifier: the Next-stage Checker (reserve engineer 0c), independent of the author.
Author: the Next-stage Builder. Branch `task/epic-i-be-i-04-a`, frozen tip **58326e45**
(b0da8237 as gated, plus one evidence-only commit), base `phase2/epic-a` 9a581258.
Written 2026-10-06. Clock times are WAT, read from the clock or from the logs.

## Verdict

**VERIFIED-WITH-NOTES at 58326e45** (2026-10-06 15:36 WAT), with five items required before the
merge (section "Required before the merge"). None of the five is a fault in the code the slice
ships: they are one test to adopt, two declarations in the evidence, one line in the example
environment file, and the full-suite log to commit. I check that one delta and nothing else.

## What I did

| | What | Result |
|---|---|---|
| Reading | The whole diff 9a581258..58326e45, both new test modules, the evidence, the runner, every log | Section "By reading" |
| Run, step 1 | The two changed test modules once at 58326e45 (rule 15: nothing wider repeated) | Ran 42 tests, OK |
| Run, step 2 | Eight probes of my own, written before any run | Ran 8 tests, OK |
| Run, step 3 | Twelve mutants of my own, expected failing test named before any run | 12 KILLED, 0 SURVIVED, 0 BROKEN |
| Full suite | The Release Engineer's one run on 58326e45; I read the raw log myself | Ran 6442 tests, OK (skipped=30), no FAIL or ERROR |
| Pattern check | Credential pattern check over the 51 changed files and inside the 18 gzipped logs | No URL with a password position filled; no literal secret assignment |

Not repeated (rule 15): the author's 400-test run, the author's 27 mutants, the full suite.
Not done: a whole-tree credential scan (the changed files and their archives were checked).

## By reading

Checked and correct:
- 58326e45 differs from b0da8237 only under `docs/evidence/`. Between the red run's tip 80adc937
  and b0da8237, outside the evidence: the temperature wording in one docstring, one sentence in
  document 05, one test line.
- All 15 gzipped logs match `gzipped_logs_sha256.txt`. `modules_and_guards.txt` holds its own
  "Ran 400 tests" and "OK" lines, sha256 as stated, and no skipped test.
- Each of the 27 mutant logs has its own "Ran" line, its expected test among the FAIL/ERROR
  lines and no module that failed to load. The runner follows rules 17 and 18. The tests were
  last changed at b0da8237 and the battery ran at b0da8237.
- Run 1 (red, 80adc937) is disclosed with its logs; run 2 is a whole gate, not a partial re-run.
- Six columns, each NOT NULL with Django default and database default "unlabelled", no index;
  migration 0031 is six AddField and nothing else.
- Admin: the six names are in `readonly_fields`.
- Temperature: the module and the evidence say it is to be added in slice B by the Senior
  Manager's ruling, and say the release does not stand in for it. Not in the version in this
  slice, as declared.
- The founder's sentence is in the help text of all six columns, the comment block in
  `students/models.py`, the docstrings of both new modules, and dated notes in documents 03a
  (two places), 05 and 07. It says "filled from these fields".

Findings:
- **R1. Step 0 is a red proof for 12 of the 42 new tests, not for all of them.** Step 0 takes
  the model, the admin and the settings back to the base but keeps the two new modules, so the
  other 30 tests pass there. Tests-first would have shown all 42 red. For those 30 the red proof
  is the mutant battery, and before my run eleven tests had been red in no run at all. My
  mutants V1 to V12 were aimed at them: all killed (below). After my run two tests have still
  never been seen red: `test_the_placeholder_fits_every_column` and
  `test_the_word_matches_the_labels` (each a one-line comparison of constants).
  So: the tests guard what they claim, but that is shown by the two batteries together, not by
  step 0. The Senior Manager has made tests-first an order for slices B and C.
- **R2. One difference from the accepted design note is not declared.** The note (section 6,
  item 2) puts the founder's sentence in the docstring of `_populate_and_save_grade`. The slice
  does not touch `students/services.py`; the docstring of `students/grading_label.py` stands
  in. Ruled by the Senior Manager: owed in slice C, and to be declared in this slice's evidence.
- **R3. No committed test shows that a row that already exists reads the placeholder after the
  migration.** The committed tests read the column default from the schema. My probe P3 does it
  with a real row and passes. Ruled by the Senior Manager: the author adopts it as a committed
  test before the merge.
- **R4. The "nothing the frontend receives changes" tests are narrow.** One greps five named
  serializer files for the column names; the other checks "all fields" only in
  `students.serializers`. True today (probe P5 checked all eight submission serializers in six
  apps and two real responses). A note, not a fault.
- **R5. The gate script is not in the evidence** and EVIDENCE.md gives neither its path nor its
  hash (`mutate.py` is committed). It is `~/Documents/Projects/GAP-builder-scripts/run_be_i_04_a_gate.sh`.
- **R6. `GRADING_RELEASE_ID` is named in no example environment file.**

## My run

One slot from the Release Engineer, 2026-10-06 15:07:15 to 15:11:05, in my own detached
checkout of 58326e45 (`~/Documents/Projects/GAP-0c-scratch/be-i-04-a`), never the author's
worktree. Serial, 6G scope, rules 12, 13, 16, 17, 18: every run and every mutant's inner run
wrote straight to its own file; nothing was piped. Load 9.79 13.38 12.02 at the start, 5.36
8.89 10.50 at the end; no test of mine asserts on the wall clock. The source was as committed
after the probes and after the mutants; both of my test databases were dropped at the end.

**A failed start, disclosed.** A first grant at 14:08 ended after one second with no test run
and no "Ran" line: my scratch checkout had no `.env` link (made with a plain
`git worktree add`; the team's worktree script makes that link), so Django could not start.
No database was made. I added the link, proved the checkout starts with `manage.py check`
("System check identified no issues" with both settings files), and ran once on a new grant.
Just before the 14:08 start I had added two lines to my script so that it stops when step 1 or
2 is not green; the Release Engineer read them before granting. Nothing was re-run after a
result.

### Step 1: the two changed modules

`ai_processor.tests_grading_config`, `students.tests_grading_label_fields`: exit 0,
"Ran 42 tests in 0.098s", "OK". The author's result holds on my checkout.

### Step 2: probes (expected results written before the run; all as expected)

| Probe | What | Result |
|---|---|---|
| P1 | The model as it stood at 0030 inserts a submission without naming the six columns (what older code does after a code-only rollback) | The row reads "unlabelled" in all six |
| P2 | A superuser opens the admin change form of a submission | No form field and no input for any of the six. See the limit below |
| P3 | students at 0030, a graded row inserted, migrate to 0031, then back to 0030 | After 0031 the row reads "unlabelled" in all six and its score is unchanged; the reverse runs and the score is still there |
| P4 | `sqlmigrate students 0031` | Six `ALTER TABLE ... ADD COLUMN ... DEFAULT 'unlabelled' NOT NULL`; no UPDATE, no CREATE INDEX, no DROP DEFAULT |
| P5 | Every ModelSerializer of StudentSubmission in students, assignments, classrooms, dashboard, billing, users; and the teacher's detail and list responses | 8 serializers, none with a label field, "__all__" or exclude; both responses 200 and neither names a label column or the placeholder |
| P6 | The settings version from two fresh interpreters with different PYTHONHASHSEED | Equal (both the same `cfg:` code) |
| P7 | An unsaved instance, then save, a partial save, a whole-row save | No DatabaseDefault object on the instance; the row reads "unlabelled" throughout |

Limit of P2, stated: the POST I built came back 200 and did not save (the marker field I
changed was unchanged afterwards), so the POST half proves nothing either way. What P2 proves
is that the change form has no field and no input for the six columns. P6's two subprocesses
each print one line captured by `subprocess`; they are not test runs.

### Step 3: mutants (expected failing test written in the runner before the run)

| Mutant | What is broken | Result | Expected test, found among the failing |
|---|---|---|---|
| V1 | a submission serializer exposes every field | KILLED, Ran 17 | `test_no_serializer_of_the_submission_exposes_all_fields` |
| V2 | the release is no longer stripped | KILLED, Ran 25 | `test_a_release_from_the_host_is_recorded_as_given` |
| V3 | the saved-answer lifetime is hashed into the version | KILLED, Ran 25 | `test_the_saved_answer_switch_and_lifetime_do_not_change_it` |
| V4 | a reading can be assigned to | KILLED, Ran 25 | `test_a_reading_cannot_be_assigned_to` |
| V5 | a word a grading writes equals the placeholder | KILLED, Ran 17 | `test_no_grading_word_is_the_placeholder` |
| V6 | one setting's value is left out of the hash | KILLED, Ran 25 | `test_each_grade_shaping_setting_changes_the_version` |
| V7 | one code constant's value is left out of the hash | KILLED, Ran 25 | `test_each_code_constant_changes_the_version` |
| V8 | document 03a no longer says "first form of the grading record" | KILLED, Ran 17 | `test_each_document_says_first_form_and_names_the_later_table` |
| V9 | the first two label names swap places | KILLED, Ran 17 | `test_there_are_exactly_these_six` |
| V10 | a backup-used word is longer than its column | KILLED, Ran 17 | `test_every_word_a_grading_can_write_fits_its_column` |
| V11 | a listed code constant does not exist in the grading service | KILLED, Ran 25 | `test_every_listed_constant_exists_in_the_grading_service` |
| V12 | the version is not the same twice for the same reading | KILLED, Ran 25 | `test_the_same_settings_give_the_same_version_twice` |

All twelve inner runs exited 1, each with its own "Ran" line and no load failure. One thing my
prediction got wrong: I wrote that V5 should also fail `test_the_word_matches_the_labels` as a
by-product. That test is in the other module, which V5's inner run does not include, so it was
not run under V5 and has still not been seen red.

## The full suite (the Release Engineer's run, read by me, not repeated)

`~/Documents/Projects/GAP-0b-runs/gate10_slice_a_a1.log`, 8,553,294 bytes, sha256
`b16ab45546617954b6f16d6a332e8bd16e59b5b8359bef508378f0bc96705733` (computed by me; equal to
the Release Engineer's). One run at `--parallel 4` on 58326e45, 15:27:33 to 15:35:51, exit 0.
- Exactly one "Ran" line: "Ran 6442 tests in 471.359s"; result line "OK (skipped=30)"; after it
  only the five "Destroying test database" lines.
- FAIL or ERROR headers in the whole log: 0 (counted over the full file, not the tail).
- The 30 skips, by their own reasons: 12 real AI calls (opt-in), 9 load tests, 4 network,
  1 Redis, 2 audit benchmarks, 2 that cannot fork inside a parallel worker. None is a test of
  this slice.
- Both new test modules are in the run. Per the summary: watchdog never fired; makemigrations
  clean; load 2.20 at the start, 7.03 at the end.
This is the regression of every app that reads `StudentSubmission` (rule 15 addendum).

## Required before the merge (one delta; I then check the delta only)

1. Adopt probe P3 as a committed test: a row made at 0030 reads "unlabelled" in all six columns
   after 0031, and the migration reverses (R3).
2. Declare in EVIDENCE.md, under the differences from the design note, that the founder's
   sentence in the docstring of `_populate_and_save_grade` is owed in slice C (R2).
3. Put the gate script's path and sha256 into EVIDENCE.md, or commit the script (R5).
4. Name `GRADING_RELEASE_ID` in the example environment file, empty, with one line saying what
   feeds it is not yet known (R6).
5. Commit the full-suite log into the evidence (evidence-only commit on top of 58326e45; the
   committed copy must unpack to the sha256 above) and add its result to EVIDENCE.md's "Still
   owed" section.

Nothing to fix in the tests from my mutants: none survived.

## Notes (not required)

- R1 and R4 above.
- The delta's new test (item 1) needs its own red proof: either the test's commit first with a
  red run, or one mutant.

## Files of this verification (kept outside the repository)

Under `~/Documents/Projects/`; sha256 of each:

```
41f288cc8452274bef234812e930b7355e165c8ee40ea6ebf912e18b7b34bde2  GAP-0c-runs/be-i-04-a/logs/console.txt
519f0faa9897f1731fceb43d6abd5539d08a91ffe119eeeee1a31c47ddb5d4c2  GAP-0c-runs/be-i-04-a/logs/1_changed_modules.txt
7c209c597eac889f84db4e96619ecf2c5b1b0939adb3eaa8dae872310d24920e  GAP-0c-runs/be-i-04-a/logs/2_probes.txt
77261c281d5fb79d8eb6018ae29d4acc64bbd093ab51e0cc052e3c4e11f5ffcc  GAP-0c-runs/be-i-04-a/logs/3_mutation_log.txt
a3cf2c54e111e32a7c203c3d54f2ce55038330f1df813dc867d7db1a98b12536  GAP-0c-runs/be-i-04-a/logs/mutants/mutation_results_0c.json
9409c6944fd3dcbcf291b75f88fa501fd0c1403bd40e44108e65056e6797a0d5  GAP-0c-runs/be-i-04-a/tests_0c_probe_be_i_04_a.py
8ace99b8ced1b821c2d5c18bb566a3e62e4ee4d5de0cd02b5bc33495fc8185f2  GAP-0c-runs/be-i-04-a/mutate_0c.py
fc8cbcc2757c593006df6fabd3df0ca97de2de9f8f51adc142a3b9658cc758d8  GAP-0c-runs/be-i-04-a/run_0c_verify.sh
87d1fdda1bf84f6f313e0e527b80b3a0a6c953668d7a281dfc1708d8c0b7afcc  GAP-0c-runs/be-i-04-a/logs/attempt1_setup_fault_1408/console.txt
08dde0594722a2799473b9ea6484c31bf10b6bfbd9b5c9fc73f6fff094bc596b  GAP-0c-runs/be-i-04-a/logs/attempt1_setup_fault_1408/1_changed_modules.txt
b7cf7e0b922326c5b86bf8a310f1a8ab2a0646f394ec53bf5417199fea4491af  GAP-0c-runs/be-i-04-a/logs/credscan_changed_files.txt
```
