# Verification: H-127 and H-128: what a student is sent of a saved grading result @ d027ac91

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-06.
**Branch:** `task/h127-student-feedback-whitelist` @ **d027ac91** (production code tip `595e323e`; test tip `1d6824e7`), on `task/beta-batch-10` `8bbf44f9` (batch 9 as pushed). For batch 10. H-127 MEDIUM, H-128 LOW. No model change, no migration, no setting.
- H-127: three student routes return the student projection (`9792251b`, `91960a9f`, `ac8cf2c0`); the formatter is not sent the second-opinion block (`3a3901d4`); a student is sent a projection of the formatted grade (`93db0af5`); only plain values pass under an allowed name (`595e323e`); a repository-wide guard (`14fbae09`, `a7216ae3`). Each with its tests first.
- H-128: a failed second opinion saves one of six codes (`53182e4d`).
- `1d6824e7`: two tests added after my first runs, for the gap I found (below). Test only.
- `f43e0f23`: 0b's base update onto `8bbf44f9`, a plain merge. Everything else up to the tip is evidence (docs only).

The evidence is in `docs/evidence/h127-student-feedback-whitelist/`.

**I ran at two tips**, each time in 0b's slot, from my own detached scratch checkout, serial, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`; under rule 18 the output went straight to a file with stdin from `/dev/null`. Each run had its own test database, created and destroyed by Django: every log has its "Destroying test database" line, and 0b listed the server's test databases after the first runs and found none of mine.
- **At `f43e0f23`** (15:24 to 15:26 WAT): my probes with the author's routes module and guard, and my mutants Y14 and Y18.
- **At `1d6824e7`** (15:39 to 15:41 WAT): the author's routes module alone, and Y14 again.
- **The first runs stand for the production code:** between `f43e0f23` and the tip the only change outside `docs/` is the two added tests, in one test module.

Under rule 15 I cite ed's gates and ed's regression (assignments, dashboard, students and ai_processor, serial, 2059 OK, skipped=22, at `f43e0f23`) and repeat none of them.

**Verdict: VERIFIED-WITH-NOTES.** The change does what the evidence says on everything I drove, through the real routes and with the real formatting task writing the row. Two things I found before the hand-over were folded in by the SM's rulings (the formatted grade; values nested under an allowed name), and one gap in the tests, not in the code, is closed by two committed tests that my run shows catch it. Nothing is required before the merge. The notes say what H-127 does not do; N1 and N2 are the ones a reader must know.

## What changes
- **A student's assignment page and dashboard list** returned the whole saved grading result of a released grade; **the student's submission list** returned the teacher's review-queue fields, released or not, and could be filtered and ordered by them. All three now give the student projection: the list sends the five review fields as false or nothing, and the three review-queue queries answer 403 for a student.
- **The formatted grade** on the student's own submission page was returned as stored. It is the formatter's output, and the formatter's prompt asks for advice to the teacher in it. Now the stored text is read back, only the student's sections are kept, and the same text form is returned; text that cannot be read back as a dictionary is shown as nothing.
- **Under an allowed name only plain values pass**: text, a number, true or false, nothing, or a list of those.
- **The formatter** is no longer sent the second-opinion block.
- **H-128:** a failed second opinion saved the error's own text (a credit balance, a refusal reason, the provider's error body); it now saves one of six codes and the text goes to the log.
- **A guard** fails on a new raw read of the three columns, a new serializer that lists a guarded column, or a review-queue filter a student is not refused.

## How this item came to its present shape
- My pre-read (by reading, before any run) found that the formatted grade reached a student whole. The SM ruled it into H-127 in a narrow form: project what the student is sent; do not change what the formatter is sent or its prompt.
- My read of that fold found that a dictionary nested under an allowed name was copied whole. The SM ruled that in too (`595e323e`).
- Three smaller points of the pre-read: a dead method that would have returned the raw column is removed; the spaced ordering form is in the author's test; the answer-upload route is unchanged and stated (N3).

## What I checked by running (at f43e0f23)
The author's tests set a row by hand and ask one route. My probes add the real writer, every student route at once, and the reader on texts that are hard to read.

| Probe | Result |
|---|---|
| **W1: the real writer.** The real formatting task saves what the formatter returned (the AI call replaced by a dictionary with teacher content in every place the prompt allows and some it does not, and with awkward text). | The row holds **1500 characters of Python-form text**; JSON's reader refuses it. The student's page shows 1138 characters: **exactly the projection**, in the same text form, with quotes of both kinds, a backslash, LaTeX, a new line, a tab, HTML and non-ASCII text intact, and none of 23 teacher markers. The teacher's page shows the whole. The numbers the task corrects from the stored grade were corrected before the save. |
| **W2: a dictionary under an allowed name** (inside the strengths list; as the student's recommendations; as a question's narrative; as the score statement). | **Dropped** in all four. |
| **W3: a flag restated inside a sentence to the student** (the per-question narrative). | **Shown.** This is the stated limit, observed (N1). |
| **W4: the reader is bounded.** Fifteen stored texts under the size limit, tried in a child process so that a crash would be seen. | No crash. Each ends in nothing or in a projection, the slowest in 0.07 s. What the reader raised: a syntax error for 10,000 nested brackets or braces, a 400,000-digit number and an unclosed string; **MemoryError** for 400,000 minus signs or "not"s in a row; **TypeError** for a list used as a dictionary's key or held in a set; nothing for the rest. One character over the limit is not read at all. Through the student's page the minus-sign text gives a normal response and nothing. |
| **W5: every student route at once**, with teacher markers in the saved feedback, the older feedback column, the review columns and the formatted grade: the submission, the list (three ways), the assignment, the assignment list, the dashboard list. | **None of 23 markers in any of the 7 responses**, for a released grade and for an unreleased one. The list's five review fields are false or nothing. |
| **W6: the ordering refusal in every spelling Django REST framework accepts.** | A student is refused 12 spellings (spaces or a tab around a term, a leading or trailing comma, the term in any position) and 3 forms of the filters. Four spellings the framework does not accept as that ordering are answered normally. The teacher is not refused. |

## The gap I found, now closed
- **My mutant Y14:** the formatted-grade reader treats only syntax and value errors as "nothing". The SM's ruling is that every failure reads as nothing; the code does that (it catches every exception).
- **At `f43e0f23` it survived all of the author's tests** and was killed only by my W4 (`Ran 50 tests`, failures=1), which I had written down beforehand as the possible outcome. The author's deeply-nested text raises a syntax error, so it never reached the wide catch. Under the narrowed code the two kinds of text in W4 that raise something else would answer the student's page with a server error.
- **The SM ruled a fold** (2026-10-06): committed tests with those two texts through the student's page.
- **The tests, `1d6824e7`:** `StudentFormattedGradeTest.test_text_whose_reading_fails_with_a_type_error_is_shown_as_nothing` (a list used as a dictionary's key) and `StudentFormattedGradeTest.test_text_whose_reading_runs_out_of_memory_is_shown_as_nothing` (400,000 minus signs before a number). Each asks the student's page and requires a normal response, nothing for the formatted grade and no teacher marker. Test only: +22 lines in one file, nothing existing changed.
- **My run at `1d6824e7` shows they catch Y14.** The author's routes module unmutated: **31 tests OK**. Under Y14: `Ran 31 tests`, **FAILED (failures=2)**, and the two failing tests are those two, each with "500 != 200": under the narrowed code the student's page answers a server error. The other 29 passed. That is exactly what I wrote down before the run and before the tests were committed (`h127_expected_kills_delta.txt`, file time 15:28:39; the commit is stamped 15:36:37).

## Evidence
| Check | Result |
|---|---|
| **Run** @ f43e0f23: my probes + `students.tests_student_feedback_routes` + `AutoGrader.tests_student_feedback_guard` | **50 tests OK** (3 s; wall 15 s): my 7 and the author's 43. Load before: 2.27. |
| **Y14** @ f43e0f23 | `Ran 50 tests`, failures=1: my W4 only. Load before: 1.95. See above. |
| **My mutant Y18** @ f43e0f23 (the ordering refusal does not strip the spaces around a term; not in the author's battery) | **KILLED** (`Ran 50 tests`, failures=7, the sub-cases of two tests): my W6 and the author's `test_a_student_cannot_filter_on_the_review_queue`. Exactly the two I wrote down. Load before: 1.81. |
| **Run** @ 1d6824e7: the author's routes module | **31 tests OK** (4 s; wall 33 s). Load before: 2.15. |
| **Y14** @ 1d6824e7 | **KILLED** by the two named tests (`Ran 31 tests in 2.259s`, failures=2). Load before: 4.50; no test here has a wall-clock limit. |
| ed's gates (cited) | The routes module once at `1d6824e7`: 31 OK. Reproduce-first on the old production files: red, exactly the 28 tests named beforehand. Modules and guards: 469 OK at `bbd2b53d`, 473 OK at `8610d16e` after the nested-value fold. Mutants: 32 of 32 at `bbd2b53d`; the nine on the projection module again at `8610d16e`, two of them new; 34 in all on the final code, each with the failing tests named beforehand. The regression above. 0b's run of H-124's guard on the merged tree: 22 OK. |
| The battery is on the final test modules, except the two added tests | The guard module last changed at `a7216ae3`, and the formatter and error-code test modules before it; all are in the battery's tip `bbd2b53d`. The routes module gained the nested-value tests at `5ca8f909`, after which the nine mutants on the projection module ran again at `8610d16e`; the other 25 are on files that commit pair does not change, and the module only gained tests. It then gained the two tests of `1d6824e7`, which none of the author's mutants is aimed at; my Y14 is their mutant. |
| No production change after my first runs | `git diff f43e0f23 d027ac91` outside `docs/` is the one test module, +22 lines. |
| The base update `f43e0f23` | A plain merge; nothing is on neither parent. Against `8bbf44f9` the branch differs outside `docs/` in 12 files: 8 production files and 4 test modules (the same 12 at the tip). |
| Hooks | `pre-commit run --from-ref 8bbf44f9 --to-ref f43e0f23` passes, and again to `1d6824e7` (the range; I did not run each commit separately). |
| Rule 14 | No MagicMock in my probe; the one replaced call returns a plain dictionary. |

**Rule 17.** Every run had `PYTHONDONTWRITEBYTECODE=1`, and each mutant was applied with `python -B`. `__pycache__` under `AutoGrader/`, `students/`, `assignments/` and `dashboard/` was deleted before each baseline, before each mutant and after each restore (the logs show 0 directories each time). After each mutant the restored file matched the commit blob's sha256, and no tracked file was changed.

**The form of my logs.** Each holds exactly one "Ran" line and one OK or FAILED line. After them come the lines written to standard output, which reaches a file only at exit (Django's own and the lines my probes print), the "Destroying test database" line, and my footer: exit status, wall time, load and the restore checks.

**The credential check on my own files** (the widened form, values not printed): the record, the probe, the two mutants and the notes written before the runs hold no URL with anything in the password position, no assignment form and no percent-encoded form. The five logs hold no URL form. Two of them (the first baseline and Y18) hold one assignment-form match each: the words "as a key" before a colon, in the name my probe prints for one of its hard texts. Not a value.

## Notes
**N1 (what a student can still read; stated in the evidence, and I agree it is outside this item).**
- **A sentence written to the student can restate a review flag.** The formatter is still sent everything except the second-opinion block, and its prompt tells it to surface every flag for the teacher. H-127 removes the sections written for the teacher; it cannot remove a flag restated inside a sentence to the student. W3 shows one being shown. Row H-131 holds the product question.
- **Keeping `narrative` per question is sound** in my reading (the SM asked me to weigh it): the prompt defines it as a third-person description of what the student's own response covered and lacked. It carries the same risk as every other sentence to the student, no more.
- **Rows formatted before H-127** may restate the second opinion in such a sentence, because the formatter was sent it then. Old rows are not rewritten.
- **`overall_performance_analysis` in the feedback is copied whole**, by decision, pinned by a test: its real keys are not known without reading real rows. The grading schema today asks for student-facing content there.

**N2 (the founder's rule that a student must not be able to tell a grade exists before release is NOT met by H-127).** The evidence says so plainly and lists seven tells; it is row H-133. One of them I reported: the student's course final grade is computed from every graded row, released or not (row H-130).

**N3 (one student route still answers with the teacher's serializer).** The answer upload (`upload_answers`, students only) responds with the teacher's detail serializer and no request. It is safe today only because an upload is refused once a row is graded, under the row lock, and nothing clears that mark; so the row it returns is always ungraded. The guard cannot see it. Not changed, because it would change the response's shape for the frontend; stated in the evidence. I did not ask for a ruling on it.

**N4 (for the deployment and the frontend).**
- **Cached responses outlive the change:** up to 15 minutes for the student dashboard's list and 5 for the submission routes, unless the deployment clears the cache.
- **A student page that sends `needs_review`, `review_tier` or an ordering by `review_severity` now gets 403.** Nobody has read the frontend for it.
- **A formatted grade that is not a dictionary in Python's text form is shown to a student as nothing**, where it used to be shown as stored: plain words, JSON with null or true, text over 500,000 characters. Whether any stored row is like that is not known; nobody has looked at stored values.
- **A teacher's `second_opinion.error` is now a code**; rows saved earlier keep their text.
- Students keep the text form they have always received (Python's, not JSON); storing real JSON is row H-132.

**N5 (H-128, by reading and by the author's tests; I ran no probe of my own on it).** The code is taken from the first failure of a known kind in the chain of explicit causes, else "other"; it never saves any of the exception's text. A failure chained only implicitly falls to "other", the safe side.

**N6 (the guard's limits; in its docstring, mine among them).** It reads Python source by name. It does not see which caller reaches a teacher-shaped serializer (N3), a review column read as an attribute into a hand-built response, a list serializer built without a request (its mask then does not apply), a column read through the ORM by name, or a template. The route tests and my W5 cover today's routes; the guard does not replace them.

**N7 (not observed anywhere live).** Everything here is from the code and from test runs on this machine. No stored row, staging or live response was looked at, by the author or by me.

**N8 (rollback).** Code only; no step. After a rollback the three routes and the formatted grade return what they returned before, and a failed second opinion saves its text again.

Logs: `runs/h127_f43e0f23.log`, `runs/h127_mutant_Y14_f43e0f23.log`, `runs/h127_mutant_Y18_f43e0f23.log`, `runs/h127_delta_1d6824e7.log`, `runs/h127_delta_mutant_Y14_1d6824e7.log`. Probe: `h127_probe_tests_vf1a_h127_probe.py`. Mutants: `h127_mutant_Y14.py`, `h127_mutant_Y18.py`. Written before the runs: `h127_expected_kills.txt` (15:13:22), `h127_expected_kills_delta.txt` (15:28:39).
