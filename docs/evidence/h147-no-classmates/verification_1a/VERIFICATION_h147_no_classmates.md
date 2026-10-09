# Verification: H-147: a student sees nothing of their classmates @ cc648b44

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-07.
**Branch:** `task/h147-no-classmates` @ **cc648b44** (code and test tip `3ee7ce19`, carried by 0b's base update `787a81fb` onto beta `d7143538`, batch 11 as pushed). For batch 12. No model, no migration (the one path with "migrations" in its name is the evidence's `makemigrations_check_787a81fb.txt`). No file outside `docs/` differs between `787a81fb` and `cc648b44`.

Commits: `96dc7cd2` red tests; `3ee7ce19` the change; `085ea0ee` docs (rule 20's cache modules added to the gate, before any run); `787a81fb` base update; `33a7aace` and `cc648b44` docs only. The evidence is in `docs/evidence/h147-no-classmates/`.

**I ran at cc648b44** (2026-10-07, 15:03:40 to 15:07:30 WAT; hooks ended 15:09:16) on 0b's GRANT of 15:03, from my own detached scratch checkout, serial, each run once, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, `--settings=settings_worktree` (mutants: `settings_worktree_mut`, their own database), every run's output straight to its own file with stdin from `/dev/null`. One chain of the Hardening Engineer's ran beside mine; the one-minute load at each start is in each log (6.86 to 9.94). No timeout, no kill.

Under rule 15 I cite ed's gate (at `787a81fb`) and ed's regression (classrooms and assignments, serial, at `33a7aace`) and repeat neither. Under rule 20 I cite ed's gate, which ran the AutoGrader cache modules.

**Verdict: VERIFIED-WITH-NOTES.** What the evidence lists under "What changes" is true on everything I read and drove. I found no defect. The notes are about what the row does not reach and about comments left behind; none asks for a change before the merge.

## What changes (checked by reading the code at the tip)
- **Roster.** On the course list, the course page and my-courses, a student's `students` is their own entry only, built from their own enrolment (`CourseSerializer._own_entry`), not from the roster with the others removed. The entry keeps its eight keys.
- **Class size.** `student_count` is still sent to a student, as a bare number (the founder's representative's answer of 2026-10-06).
- **Nested assignments.** A student gets the student's assignment serializer (the shape of the assignment list route): no `submission_count`, no scheduling fields, drafts absent; `status` is now the student's own state on the work.
- **Sessions.** A student is sent `school` and `created_by` as null on the session list and the session page.
- **One query.** The course view loads the viewer's own submissions with the assignments (`viewer_submissions`); the assignment serializer reads only the requester's own row from what was loaded.
- **A teacher's answers are as before.**

## What I checked by reading
- **Other routes a student can call.** The assignment routes give a student `AssignmentListStudentSerializer` / `AssignmentDetailStudentSerializer`; the enrolment routes are scoped to the student's own rows; the user route to the student's own row; `my_students`, `student_summary` and the add/remove actions are teacher-only. Of the extra GET actions in `assignments/views.py`, `students/views.py` and `classrooms/views.py`, the ones without a teacher-only or admin-only permission are `my_courses` (read) and `download_pdf` (the assignment's own PDF; I did not read its renderer line by line). In what I read I found no route that sends a student a classmate's name, id, address, state or a count of classmates' work.
- **Every production use of `CourseSerializer`, `SessionSerializer`, `StudentSerializer`, `StudentListSerializer`, `StudentCourseDetailSerializer`** (a search of the tip, tests excluded): the course view, the session view, the enrolment view, and the course-category view. The category view builds `CourseSerializer` without the request on its paged answer; it is registered in no `urls.py` and its relation does not exist (H-6, already on the backlog, which says so).
- **Filters and search.** The course view filters on `session` and `is_active` and searches `name` and `description`; the session view searches `name`. Neither offers a lookup through enrolments, the school or the creator, so the blanked values cannot be confirmed by filtering.
- **The cache.** A student's cached course list and course page are keyed on the student and on each course's roster generation; my-courses on the student and the global generation. A submission write bumps the student's, the course's and the global generation (`students/signals.py`), so the student's own state, new in these answers, is refreshed by the student's own upload (my probe pg drives this).
- **Rule 20, the whole test tree.** I searched every test file outside `classrooms/` and `assignments/` for the course and session routes and serializers: 18 files. Seven of them (AutoGrader cache modules) and the new guard were in ed's gate. Of the ten not in ed's runs (`tests_cache_commit_race`, `tests_cache_commit_race_cost`, `tests_cache_dashboard_2329`, `tests_cache_matrix_measurement`, `tests_probe_h1s3_commit_race`, two billing H-38 modules, two dashboard cache matrices, `users/tests_user_enrollment_filter_oracle`) I read every line the search found: they read the course routes as a teacher, read the enrolment route, or (the measurement module) compare a student's answer with itself; none expects a student to be sent a classmate or a teacher's assignment key. They come with the batch's full run.
- **ed's raw logs, read by me from the commit.** `prefix_base_production_failing_787a81fb.txt.gz`: one Ran line, `Ran 36 tests`, `FAILED (failures=26, errors=1)`, 21 distinct tests. `modules_and_guards_787a81fb.txt.gz`: one Ran line, `Ran 527 tests`, `OK`, 527 "ok" lines, 17 lines of `AutoGrader.tests_cache_bespoke_1114`. `regression_33a7aace.txt.gz`: one Ran line, `Ran 1097 tests`, `OK (skipped=13)`, 13 skip lines. `mutation_results_787a81fb.json`: 13 entries, 13 KILLED; 13 mutant logs.

## What I checked by running (at cc648b44)

The author's tests are route tests of an ENROLLED student and a teacher. My five probes look at what they do not reach. Each was written with the mutant that must fail it, and the one failing method per mutant, named beforehand (`h147_expected_kills.txt`, written 15:01:32, before any run). Rule 19: each probe below was seen green on the tip and red under its mutant; the failing method set of every mutant run is exactly the one written.

| Run | What it shows | Result |
|---|---|---|
| Baseline: my probe module (5), `classrooms.tests_student_sees_no_classmates` (14), `AutoGrader.tests_student_classmates_guard` (7) | green at the tip | `Ran 26 tests in 8.301s`, `OK` |
| V1: the serializer's own enrolment query loses "theirs only" | **pa**: a course NOT loaded by the view (the branch that, by my reading, no route test reaches) gives a student their own entry only | `Ran 5`, `FAILED (failures=1)`: pa |
| V2: session ids blanked for everyone but a teacher | **pb**: a school admin still gets a session's school and creator | `Ran 5`, `FAILED (failures=1)`: pb |
| V3: a student is sent the session ids | **pc**: neither id on the session PAGE (the author tests the list) | `Ran 5`, `FAILED (failures=2)`: pc, its two sub-tests |
| V4: the own entry only from an ENROLLED enrolment | **pd**: a student who COMPLETED the course gets their own entry, with that status, on the three course routes, and nothing of the classmate | `Ran 5`, `FAILED (failures=3)`: pd, its three sub-tests |
| V7: a submission write bumps no cache generation | **pg**: after the student hands a paper in, the CACHED course list, course page and my-courses show SUBMITTED and 2 attempts left (a second read before that is shown to come from the cache) | `Ran 5`, `FAILED (failures=3)`: pg, its three sub-tests |
| V5: `student_summary` loses its permission line (the author's guard module alone) | the author's `test_rule_2_each_refusal_is_where_the_table_says` can fail | `Ran 7`, `FAILED (failures=1)`: that test |
| V6: a new GET action on the course view (the author's guard module alone) | the author's `test_rule_2_every_action_is_classified` can fail | `Ran 7`, `FAILED (failures=1)`: that test |

Every mutant log has "applied Vn", "mutated sha differs: True", "restored_sha256_matches_commit_blob: True", 0 tracked changes after the restore and 0 `__pycache__` directories before and after. Each log holds exactly one Ran line.

V5 and V6 answer the author's request: the two rule-2 guard tests, never seen red in the author's runs, are now each seen red by name.

**Commit hooks** over `d7143538..cc648b44`: exit 0, 18 passed, 0 failed, 7 skipped (no files to check).

## Notes
- **N1. Three comments still describe the old behaviour.** `classrooms/views.py` (`extra_cache_scopes`: "shows a student their classmates"; `my_courses`: "the roster this payload shows (classmates, ...)") and `classrooms/signals.py` (`clear_student_course_cache`: "the only thing a classmate sees change is the roster"). What a classmate's enrolment changes in a student's answer is now the class size alone. The code is right; the comments are not. The author will correct them in a row the Senior Manager names.
- **N2. The serializers treat a reader as a student only when the request says so.** `CourseSerializer` and `SessionSerializer` built without a request give the staff shape. No routed view does that today (the unrouted category view, H-6, would). The new guard classifies sites that name the roster serializers; it does not look for a `CourseSerializer` built without a request. Worth one line in H-6's row.
- **N3. The frontend.** A nested assignment's `status` changes meaning for a student and ten keys go; four arrive. Neither the author nor I can read the frontend. The evidence carries the list for whoever can.
- **N4. What the row leaves, said in the evidence and true as I read it:** the class size still tells a student THAT someone joined or left; cached answers outlive a deployment by up to five minutes; the view still loads the whole roster from the database for a student and the serializer does not send it; the guard does not see a roster built by hand, nor views outside `classrooms/views.py`.
- **N5. Tests not seen red by anyone, and not evidence:** three of the rewritten exposure module that hold what was already true (`test_query_counts_stay_flat_as_the_roster_grows`, `test_school_admin_and_superadmin_cannot_reach_course_payloads`, `test_student_keeps_their_own_email`) and the guard's two scanner self-tests. I did not probe these.
- **N6. Not observed on a live or staging service**, by the author or by me.

## The backlog row
The row at the tip (`docs/HARDENING_BACKLOG.md`, H-147) still reads "In progress ... not yet run or verified". When 0b closes it, it should say what was built: the student's own entry, the bare class size (allowed), the student's own view of nested assignments, the two session ids null; and name N1 to N3 as open.

## Credential check
My probe, mutant file, expected note and eight logs, searched for a URL with anything in the password position and for assignment forms whose name contains PASS, PWD, SECRET, TOKEN or KEY, masked output only: 0 lines in each; no line over 4000 characters. No archive among them.

## Files (in `~/Documents/Projects/GAP-1a-records/`, sha256 prefixes)
- `h147_probe_tests_vf1a_h147_probe.py` af5c3b98242c7011
- `h147_mutants_V.py` 1192b89fc99be716
- `h147_expected_kills.txt` 0a9ccfea76b97205
- `runs/h147_cc648b44.log` 4a3234fcbc2e4f4d
- `runs/h147_mutant_V1_cc648b44.log` fae5161fbcb39e7c
- `runs/h147_mutant_V2_cc648b44.log` cb18d861e77b4bdc
- `runs/h147_mutant_V3_cc648b44.log` 5fa2bf793edc9bfc
- `runs/h147_mutant_V4_cc648b44.log` 81fb464fd7f4e737
- `runs/h147_mutant_V5_cc648b44.log` 42aa27a78aeff149
- `runs/h147_mutant_V6_cc648b44.log` 74047f5b7c76a25f
- `runs/h147_mutant_V7_cc648b44.log` 966700d659d11f60

The runner (`~/Documents/Projects/GAP-1a-scratch/h147_run.sh`, ba4718b9dd514874) and the comparison helper (`h147_expect.sh`, 22a57ea743aca421) were read by 0b before the grant.
