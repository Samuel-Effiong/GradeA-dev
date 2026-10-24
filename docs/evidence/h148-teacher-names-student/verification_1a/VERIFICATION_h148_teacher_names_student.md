# Verification: H-148: the teacher names a student when adding them by email @ 19f5c872

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-07.
**Branch:** `task/h148-teacher-names-student` @ **19f5c872**, the final tip. Stacked on H-147's `787a81fb` (carried in by 0b's merge `356bdd34`); it merges after H-147. For batch 12. No model, no migration.

Commits over `787a81fb`: `512656d1`, `6d9fdbb1`, `24a947cf` tests first; `466f85e6` the change; `356bdd34` the merge of H-147's branch; `56ce7ec5`, `117feddc` docs only; `1d97842d` tests only (the follow-up to my finding, below); `d7be0241`, `19f5c872` docs only. No production file differs between `356bdd34` and `19f5c872`; between `117feddc` and `19f5c872` the only files outside `docs/` are the two billing test files. The evidence is in `docs/evidence/h148-teacher-names-student/`.

**I ran at 19f5c872** (2026-10-07) in 0b's slots, from my own detached scratch checkout, serial, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, `--settings=settings_worktree` (mutants: `settings_worktree_mut`), every run's output straight to its own file with stdin from `/dev/null`. Two attempts: the first (15:49:52) stopped at a red baseline through a fault of my probe and ran no mutant; the second (16:00:32 to 16:02:32; hooks ended 16:03:53) ran as written. Both are told below. The one-minute load at each start is in each log (3.44 to 6.69). No timeout, no kill.

Under rule 15 I cite ed's gate (at `356bdd34`), ed's regression (classrooms and users, serial, at `56ce7ec5`) and ed's follow-up run (at `d7be0241`) and repeat none of them. Under rule 20 I cite ed's gate and follow-up, both of which ran the AutoGrader cache module.

**Verdict: VERIFIED-WITH-NOTES** at 19f5c872. At the first tip handed to me, `117feddc`, I found by reading that five calls in two billing test modules still added a student with an email alone; that is corrected, tests only, and shown red before and green after by the author. What the evidence lists under "What changes" is true on everything I read and drove. The notes are edges for the Senior Manager to rule on and the backlog row's wording; none asks for a change before the merge.

## What changes (checked by reading the code at the tip)
- **The add-by-email form** (`POST course/<id>/students`) requires `first_name` and `last_name`, at least two letters each; `middle_name` is optional. An email alone is refused.
- **A new account** carries the typed name.
- **An address that already has a student account:** the typed name fills an account that has no first and no last name, and never replaces a stored one. The answer says which name stands (`student_name`, `typed_name_used`).
- **The class-list import** fills the same way.
- **The one-exact-name-per-course rule** applies to the typed or filled name. The whole add is one transaction, so a refusal undoes the fill and leaves no account behind.
- **Three invitation emails** lose the sentence that promised a forced password change.

## My finding at 117feddc, and what was done
- **Found by reading, before any run of mine:** `billing/tests/test_h38_teacher_removal.py` (lines 118, 204, 245, 286 at `117feddc`) and `billing/tests/test_h38_part2_removed_teacher_routes.py` (line 145) posted `{"email": ...}` to the route and expected 200 (four) or 403/404 (line 204). With the names required the form answers 400. Neither module was in the author's gate (one billing module, `test_license_service`) or regression (classrooms and users). The author had searched for the route's name; these modules write the path by hand.
- **The Senior Manager ruled** a whole-tree search, a tests-only correction, and one run over every module the search finds plus the rule 20 module.
- **`1d97842d`**, read by me: the five calls send `add_by_email(...)`; one import in each file; one comment; no assertion changed. The removed teacher's test (line 204) now sends a complete form, so what refuses it is the course lookup.
- **The author's search**, read by me (`route_callers_search.py.txt` and its output): seven terms over every tracked `.py` file; the raw-path term is any `students` before a quote. It finds 17 test files that name the route or its path. My own search (the route's name, the raw path and the single-add url, with the lines after each hit) found no caller outside those 17 files and the same five calls without a name.
- **The follow-up's logs, read by me from the commit.** `followup_before_d7be0241.txt.gz`: one Ran line, `Ran 81 tests`, `FAILED (failures=80)`; I compared the set of failing tests with `followup_red_written.txt`: the same 80. `followup_after_d7be0241.txt.gz`: one Ran line, `Ran 393 tests`, `OK`, 81 lines of the two billing modules and 17 of `AutoGrader.tests_cache_bespoke_1114`.
- **Said by the author and true:** the red run does not show the removed teacher's own post answered 400 (that test is red there through its class's setUp). The 400 is a reading of the code, the author's and mine: the view validates the form (`is_valid`) before it looks the course up.
- **The place the author names to attack:** another test that expects a refusal the form now pre-empts. I looked at the 16 places in the tree where a refusal is expected within eight lines after a post made with `add_by_email` (two billing, the rest classrooms). Each sends the helper's complete form. Going by the lines the search printed and the modules they are in (the removed teacher's and another teacher's course, the cross-school rule, the placeholder address, an address already enrolled), each is refused by a check that comes before the name is used. I did not read each of those tests through, nor every assertion of those files; all of them are in the follow-up's green run.

## What I checked by reading, beside that
- **The fill** (`classrooms/services/enrollment.py`): only with `fill_empty_name`, only when both typed names are there, only when the account has no first and no last name after stripping. It sits after the three refusals (already enrolled, not a student or another school, switched off), so none of them depends on the typed name.
- **The 14 test files the author changed first:** the diff from `787a81fb` shows only the body of each call replaced by `add_by_email(...)` and one import per file. No assertion changed.
- **ed's gate and regression logs, read by me from the commit.** `prefix_base_production_failing_356bdd34.txt.gz`: one Ran line, `Ran 39 tests`, `FAILED (failures=22, errors=4)`. `modules_and_guards_356bdd34.txt.gz`: one Ran line, `Ran 865 tests`, `OK (skipped=3)`, 17 lines of `AutoGrader.tests_cache_bespoke_1114`. `regression_56ce7ec5.txt.gz`: one Ran line, `Ran 1127 tests`, `OK (skipped=4)`. `mutation_results_356bdd34.json`: 26 entries, 26 KILLED; 26 mutant logs.

## What I checked by running (at 19f5c872)

The author's 25 tests of the form and 26 mutants cover the rule case by case. My four probes are edges the author named as worth attacking and did not test.

**First attempt, 15:49:52 to 15:50:15, stopped at a red baseline:** `Ran 29 tests in 2.492s`, `FAILED (errors=3)`. The three were my own qa, qb and qc, each a `KeyError` on `typed_name_used` or `student_name`: my probe read the rendered body (`response.json()`), and this project's renderer wraps every answer. It was my probe's fault and said nothing about the row; in qa the status and the filled name had already passed. The author's 25 tests and my qd were "ok". I released the slot at once and ran no mutant. The probe as it ran, its log and the note written before it are kept (files below).

**Second attempt.** The only change to the probe: the answer is read from `response.data` in five places, as the author's tests read it, and a paragraph in its docstring. The mutants and the expected sets were unchanged (`h148_expected_kills.txt`, written 15:51:00, before the runs). Rule 19: each probe was seen green on the tip and red under its own mutant, and each mutant failed exactly the one method written.

| Run | What it shows | Result |
|---|---|---|
| Baseline: my probe module (4), `classrooms.tests_teacher_names_student_on_add` (25) | green at the tip | `Ran 29 tests in 3.394s`, `OK` |
| X1: a stored name of spaces counts as a name | **qa**: an account whose stored first and last name are only spaces is filled with the typed name, and the answer says the typed name was used | `Ran 4`, `FAILED (failures=1)`: qa |
| X2: the fill does not write the middle name | **qb**: an account with ONLY a middle name counts as nameless; it is filled, and its middle name is replaced by the typed one (blank when none is typed). See N2 | `Ran 4`, `FAILED (failures=1)`: qb |
| X3: the fill does not ask whether the account has a name | **qc**: two teachers add the same nameless account one after the other, in two courses: the first teacher's name stands, and the second is answered with it and `typed_name_used` false | `Ran 4`, `FAILED (failures=1)`: qc |
| X4: the name clash compares the first name exactly | **qd**: a nameless account added under a classmate's name typed in another case is refused (400), stays nameless and is not in the course | `Ran 4`, `FAILED (failures=1)`: qd |

Every mutant log has "applied", "mutated sha differs: True", "restored_sha256_matches_commit_blob: True" and 0 tracked changes after the restore. Each log holds exactly one Ran line.

**Commit hooks** over `787a81fb..19f5c872`: exit 0, 19 passed, 0 failed, 6 skipped (no files to check).

## Notes
- **N1. The backlog row's opening is the thing the author has since corrected.** The row at the tip (`docs/HARDENING_BACKLOG.md`, H-148) opens with "A student can change their own first and last name". The author's correction (in the hand-over and the proposal): the account edit has refused that since July; what was missing was any way for a teacher to name a student added by email. When 0b closes the row it should say what was built (the form requires the name; the fill rule; the import; the three emails) and that the student-rename claim was wrong.
- **N2. An account with only a middle name is treated as nameless, and the fill replaces that middle name**, with a blank when the teacher types none. qb shows it; it does not say it is right. "Half a name is a name" is the rule for a first or a last name; whether a middle name alone should stand, or be kept when the teacher types none, is for the Senior Manager. Such an account is unusual.
- **N3. Two adds at the same moment.** The add locks the course row, not the student's account. Two teachers adding the same nameless account in two courses at once can both find it nameless and both fill it; the later write stands. The rule "a stored name is never replaced" holds one after the other (qc) and not across that race. Read in the code, not run. A conditional write, or a lock on the account, would close it.
- **N4. Who can name whom.** Any teacher who knows the address of a nameless student account that belongs to no school can add it to a course and so give it a name, for good, for every teacher who has that student. Accounts tied to another school are refused before the fill. This follows from the rule as decided; I name it so it is a decision and not a surprise.
- **N5. The answer shows the stored name of an existing account** to the teacher who added it. The teacher's roster shows the same name once the add succeeds, so nothing new is told.
- **N6. The form is checked before the course is looked up.** A teacher posting to a course that is not theirs with an incomplete form is told about the form (400) and not about the course (404). Nothing is told about the course either way; it is why the removed teacher's test needed a complete form.
- **N7. Breaking for the current page:** an add by email without the two names is refused. Neither the author nor I can read the frontend; the evidence has the lines. A genuine one-letter name is refused (H-151, open).
- **N8. A student who is already nameless and already enrolled is not cured here** (the author says so; H-153 is to be the cure).
- **N9. Not observed on a live or staging service**, by the author or by me.

## Credential check
My two probe versions, mutant file, two expected notes and six logs, searched for a URL with anything in the password position and for assignment forms whose name contains PASS, PWD, SECRET, TOKEN or KEY, masked output only: 0 lines in each but one. The first attempt's log has three matching lines (617, 626, 635): each is Python's exception name `KeyError:` followed by the name of the answer's key my probe asked for (`typed_name_used`, `student_name`); the pattern matches the letters KEY in the exception's name. They are not credentials. No line over 4000 characters; no trailing whitespace. No archive among them.

## Files (in `~/Documents/Projects/GAP-1a-records/`, sha256 prefixes)
Second attempt:
- `h148_probe_tests_vf1a_h148_probe.py` 9dd66e023ecf6016
- `h148_mutants_X.py` 9ea78f6c67602960
- `h148_expected_kills.txt` af952add81684873
- `runs/h148_19f5c872.log` 20890d8da3707034
- `runs/h148_mutant_X1_19f5c872.log` 778f684e509f34ca
- `runs/h148_mutant_X2_19f5c872.log` 91135e91a8bec964
- `runs/h148_mutant_X3_19f5c872.log` a479be39d8da940f
- `runs/h148_mutant_X4_19f5c872.log` 9c375ae089645581

First attempt, kept as it ran:
- `h148_probe_v1_as_run_15h49.py` fb9af9cc3cf32ad8
- `h148_expected_kills_first_attempt.txt` 496abc9dd834075b
- `runs/h148_19f5c872_first_attempt_15h49.log` 274d9a3e3e9c35c0

The runner (`~/Documents/Projects/GAP-1a-scratch/h148_run.sh`, 3b32a9081b199706) and the comparison helper (`h148_expect.sh`, 43292243f1e7c3c1) were read by 0b before each grant.
