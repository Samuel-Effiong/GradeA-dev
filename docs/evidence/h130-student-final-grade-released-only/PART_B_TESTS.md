# H-130, part B: what each test assumes about existing behaviour, and where that is in the code

Written 2026-10-06 after the second stopped chain, on the SM's order,
before asking for the slot again. Three faults in these tests had been
found by runs (a PENDING student refused the assignment route; a guard
test that broke the source it parsed; an assertion that two renderings of
a graded document are equal). For each test: the existing behaviour it
relies on, where I READ that behaviour (file and line at the tip, not
from memory), and which run first showed it red and green.

Runs so far (all on 0b's grant, logs kept):
- **C1**, first early chain, 15:37, tests of 1c5313ff on 1c5313ff: stopped;
  its part B results are not used (faulty fixture).
- **C2 (r2)**, 16:44, the tip's tests (c20e9a4a) on the FIRST fix's code
  564a903c: the red proof.
- **C2 (a)**, 16:44, tip c20e9a4a: the 21 part B tests of that tip ran;
  20 ok and 1 FAIL, read test by test from the raw log
  (`second_chain_c20e9a4a/a_modules_c20e9a4a.log`; the log interleaves application log lines
  with the results, so each test's own "ok" or "FAIL" line was taken).
- No mutant has run yet. "Mutants only" means no commit shows the test
  red; it is there to fail under a mutant.

File and line references are to the tip `9a9d6cb2` (`5d195a9e` plus one
commit). That commit lengthens one comment in
`students/views.py` by nine lines, so every reference into that file
below line 326 was re-derived from the file as it now is.

## The fixture (`AnswerDocumentBase.setUp`)
| It assumes | Read at |
|---|---|
| Enrolling by e-mail leaves the enrolment PENDING | `classrooms/services/enrollment.py:254` (`enroll_student_by_email`), `:318` and `:380` (created PENDING) |
| Only ENROLLED and COMPLETED students may read a course's assignments | `classrooms/models.py:232` (`COURSE_ACCESS_ENROLLMENT_STATUSES`); `assignments/views.py:339` to `:344` (the student's queryset) |
| A real student becomes ENROLLED by this same update at login | `classrooms/services/enrollment.py:420` (`activate_pending_enrollments_on_login`), `:431` (v2 read it too) |
| The upload engine makes the row with the score column's default and no grading time, builds the document from that unsaved row and stores it | `students/services.py:994` to `:1000` (the row), `:1003` (the document); `students/models.py:50` to `:53` (`default=0.00`) |
| The engine needs only the extraction and the notification e-mail replaced | `students/services.py` `upload_answers_engine`; same two patches as `students/tests.py:361` to `:369` |
| The header prints nothing for a score of zero (float or decimal), "Not graded yet" for no grading time | `students/services.py:74` to `:75` (`safe`), `:103`, `:105` |

## The read helpers
| It assumes | Read at |
|---|---|
| A student's GET of their submission is served by the student serializer and cached per reader and submission | `students/views.py:316` (`retrieve`), `:336` to `:342` (key on the reader's generation), `:364`, `:373` |
| That route rebuilds and stores the document when the column is empty | `students/views.py:346` |
| A student's GET of an assignment is served by `AssignmentDetailStudentSerializer`, which finds the student's own submission | `assignments/views.py:361`; `assignments/serializers.py:335` to `:343` (`_get_submission`) |
| Each helper asserts 200 and reads the document by its key, so a refusal cannot pass as a result | the test file itself |

## The tests
| Test | Existing behaviour it relies on | Read at | First red | First green |
|---|---|---|---|---|
| `AStudentWhoIsOnlyInvited...test_the_assignment_route_answers_not_found` | a PENDING student is refused | as in the fixture table | never (pins a refusal) | C2 (a) |
| `ASubmitted...test_the_upload_engine_stored_a_document` | the engine stores a document; a new row is ungraded and unreleased | `students/services.py:1003`; `students/models.py` (`is_published` default False) | never | C2 (a) |
| `ASubmitted...test_on_the_students_own_submission` | none beyond the fixture: the document built from the unsaved row at upload equals the one built from the stored row on a read | `students/services.py:994` to `:1003` against `:87` (default score), `:101` (the date printed as a day) | C2 (r2) | C2 (a) |
| `ASubmitted...test_on_the_students_view_of_the_assignment` | the same, on the assignment route | the same | C2 (r2) | C2 (a) |
| `GradingChanges...test_on_the_students_own_submission` | grading sets a score and a grading time and stores a rebuilt document | `students/services.py:350`, `:383`, `:392`, `:486`, `:492` | C2 (r2) | C2 (a) |
| `GradingChanges...test_on_the_students_view_of_the_assignment` | the same | the same | C2 (r2) | C2 (a) |
| `GradingChanges...test_a_regrade_changes_nothing_either` | a graded row can be graded again by the same function | `classrooms/tests_final_grade_zero_score.py:111` (`grade_by_ai`), used so by its own regrade test | C2 (r2) | C2 (a) |
| `GradingChanges...test_a_grade_of_zero_changes_nothing_either` | a zero prints as nothing, whether a float in memory or a decimal from the database, so the graded document differs from the submitted one by the grading date only | `students/services.py:74` to `:75`, `:105`; `:377` (the score is a float at grading) | expected at (r2); not run yet (added after C2) | not run yet |
| `GradingChanges...test_a_half_graded_row_reads_the_same_too` | a row can hold a score with no grading time, or the reverse (written straight to the table here) | columns are independent: `students/models.py:50`, `:78`; the publish route itself guards against such rows, `students/views.py:1321` to `:1332` | C2 (r2) | C2 (a) |
| `GradingChanges...test_a_graded_row_whose_stored_document_was_lost` | the read route rebuilds and stores a document for an empty column, from the row as it is (graded) | `students/views.py:346` to `:360` | C2 (r2) | first assertion: C2 (a). **Second assertion was WRONG in C2 (a)** and is corrected: I had assumed the rebuilt document equals the one grading stored; grading prints a float score ("7.0", `students/services.py:377`), the rebuild a decimal from the database ("7.00"). It now requires only that a document was stored and that it is not the ungraded form. Not run since. |
| `WhatIsGivenUp...test_a_rename_shows_when_it_happens...` | the header prints the assignment's current title | `students/services.py:92` (title), read from `submission.assignment` each time | never (pins a stated cost); mutants | C2 (a) |
| `WhatIsGivenUp...test_the_same_on_the_students_view_of_the_assignment` | the same, on the assignment route | the same | never; mutants | C2 (a) |
| `NothingElse...test_grading_stores_a_different_document_and_a_students_read_leaves_it` | grading stores a document that differs from the submitted one; a student's GET writes nothing when the column is filled | `students/services.py:486`, `:492`; `students/views.py:346` (rebuild only when empty) | never; mutants | C2 (a) |
| `NothingElse...test_the_teacher_reads_the_graded_document_before_release` | a teacher's GET of the submission returns the stored column | `students/views.py:368` (the staff serializer), its `raw_input` is the plain column | never | C2 (a) |
| `NothingElse...test_after_release_the_student_reads_what_the_teacher_reads` | the single release route releases a graded row and invalidates the student's cached responses | `students/views.py:1319`, `:1339`, `:1348`; `students/signals.py:39`, `:27` (the student's scope) | never; mutants | C2 (a) |
| `NothingElse...test_a_row_with_no_stored_document_is_served_as_it_is` | the assignment route does not rebuild an empty document itself | `assignments/serializers.py` `get_student_submission_raw_input` (no rebuild there) | C2 (r2) | C2 (a) |
| `TheCached...test_a_response_cached_before_grading_is_not_the_graded_form_after_it` | saving a submission invalidates the student's cached responses | `students/signals.py:88` to `:90` (the save receiver), `:27` | never; mutants | C2 (a) |
| `TheCached...test_a_response_cached_before_release_is_not_served_after_it` | the single release route invalidates, although it writes with a queryset update | `students/views.py:1339`, `:1348` | never; mutants | C2 (a) |
| `TheCached...test_nor_after_the_release_of_the_whole_assignment` | "release all" invalidates every released student's cached responses, on both routes, although it writes with a queryset update | `assignments/views.py:1729`, `:1747`; `students/signals.py:52`, `:69` | never; mutants | C2 (a) (this was my least sure prediction; it held) |
| `EveryReader...test_no_serializer_carries_the_document_unnamed` | four serializers of a submission list `raw_input` | read by parsing `students/serializers.py`, `assignments/serializers.py`, `classrooms/serializers.py`, `dashboard/serializers.py` on the merged tree: the four named | never | C2 (a) |
| `EveryReader...test_a_student_facing_serializer_does_not_return_the_stored_column` | DRF exposes a declared method field in `.fields` | `students/serializers.py` (the declared `raw_input`) | never; mutant B9 | C2 (a) |
| `EveryReader...test_the_students_assignment_serializer_does_not_read_the_stored_column` | `inspect.getsource` of a module-level class parses as it is | the class is at module level, `assignments/serializers.py:528` | never; mutant B8 | C2 (a) (it errored in C1, with `cleandoc`) |

## Added after v2's line-by-line read of 5d195a9e (the fourth)
v2 read the whole test file against this list and found one statement the
code does not hold: the guard's dictionary called
`StudentSubmissionDetailSerializer` "staff only", and two other
serializers "returned from a write route". This list had no row for what
that dictionary SAYS; the guard test checks its names only. Read now:

| Statement in `READERS_OF_THE_DOCUMENT` | Read at |
|---|---|
| `StudentSubmissionDetailSerializer`: staff, AND the student's own upload route answers with it | built at `students/views.py:368` (retrieve, staff branch), `:564` (**`upload_answers`, permission `IsStudent`, `:520`**), `:801` (grade), `:1160`, `:1350`, `:1401` (teacher routes) |
| `StudentSubmissionSerializer` and `StudentSubmissionUpdateSerializer`: no route builds a response from them | no `StudentSubmissionSerializer(` or `StudentSubmissionUpdateSerializer(` call in `students/views.py`; the first is the fallback of `get_serializer_class` (`:415`); upload-async answers with task data (`:676`), the raw-text edit with the list serializer (`:710`), update-async with task data (`:771`) |
| `StudentSubmissionDetailStudentVersionSerializer`: a student reads it, through the helper | `students/views.py:364`; `students/serializers.py` `get_raw_input` |

| Test | Existing behaviour it relies on | Read at | First red | First green |
|---|---|---|---|---|
| `TheUploadRoutesOwnAnswer.test_a_first_upload_answers_with_the_submitted_document` | the upload route needs an ENROLLED student, a funded teacher, one file; it answers 201 with the staff serializer; file handling and extraction can be replaced as an existing green test replaces them | `students/views.py:139` to `:153` (`_assignment_open_to_student`), `:520`, `:564` to `:566`; `users/permissions.py:8` to `:40` and `:57` to `:67` (the teacher's wallet, found from `assignment_id`); `students/tests_post_grading_submission_lock.py:504` to `:529` (the same patches, 201) | expected at (r2), by its last line; not run yet | not run yet |
| `TheUploadRoutesOwnAnswer.test_an_upload_on_a_graded_unreleased_row_is_refused_with_no_document` | a graded row refuses the student's upload before any answer is built: 409 with one key, "error" | `students/services.py:1098` (`ensure_student_may_submit`), `:1050`, `:1067` to `:1069`; `students/views.py:111` to `:119` (`_submission_closed_response`), `:545` to `:548` | never (pins a refusal this row rests on) | not run yet |
| `WhatIsGivenUp...test_a_rename_the_teacher_saves_refreshes_the_students_cached_document` | saving an assignment bumps every enrolled student's cache generation | `assignments/signals.py:93` (`_bump_assignment_scopes`), `:120` to `:128`, `:173` to `:174` (the save receiver) | never; mutants B3, B7, B9 | not run yet |
| the fixture's due date | the header prints the due date as it comes from the database, at upload and on a read | `students/services.py:93`; the fixture reloads the assignment after setting it | n/a | not run yet |

Also corrected with these: the comment above the read route's cache key
(`students/views.py:318` onward), which said a retitled assignment
"provably does not change this response". For a student before release
it now does. Comment only; the view's code is unchanged.

**What the upload answer rests on, until a separate row changes it:** the
student's upload route answers with the staff serializer, stored document
included. That is safe only because the upload refuses a graded or
being-graded row first. The SM has given the change of that answer to a
new row for the Security Engineer; the refusal is pinned here meanwhile.

## The SM's question: does the "7.0" / "7.00" printing touch the student's form?
By reading, no; and one run agrees for the first case.
- **The ungraded form does not print the row's score at all.** It prints
  the column's default (`students/services.py:87`), and `safe` prints
  nothing for a zero, float or decimal (`:74` to `:75`).
- **A row whose score is zero after grading:** the stored graded document
  prints nothing for the score either, so it differs from the submitted
  one by the grading date only. The student, before release, gets the
  ungraded form, which has neither. The new test
  `test_a_grade_of_zero_changes_nothing_either` reads it on both routes.
  Not run yet.
- **The document stored at upload against the one rebuilt on a read, for
  a submitted row:** at upload the score is the default as a float, on a
  read it is the default as a decimal; both print nothing. The submitted
  date is printed as a day (`:101`) from a timezone-aware time in both
  cases. The two `ASubmitted...` tests compare exactly these two
  documents, on both routes, and were green in C2 (a).
- **Where it does show:** only in a document that prints a non-zero
  score, which a student receives only after release, and then it is the
  stored one. Row H-139.
