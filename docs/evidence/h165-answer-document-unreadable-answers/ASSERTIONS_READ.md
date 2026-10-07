# H-165: every assertion of the module, read against the real value

d5, 2026-10-07, after the chain on `2b7e812e` stopped at step (a) and
before any run of the corrected tip. The Senior Manager's condition:
every assertion of every test in `students/tests_answers_unreadable.py`
(31 tests; 30 at the red commit), positive and negative, read against the
actual form of what it inspects.

## The forms, and how each was seen
| What a test inspects | Its real form | Seen how |
|---|---|---|
| the builder's return (`student_submission_to_html`) | an HTML string; the fixed line is written with `escape(line, quote=False)`, which leaves the apostrophe as it is | direct call before the first run (the checksums); and the stopped run: every builder test green |
| a stored document (`row.raw_input`, `self.stored()`) | a string: the JSON text of a document, made by `html_to_prosemirror_text`. JSON does not escape an apostrophe | direct call of the converter on HTML I built (16:45); and the stopped run: the positive checks on it (R3, S1) green |
| a route's document (`response.data["raw_input"]`, `["student_submission_raw_input"]`) | the same string, under its key | the stopped run: R1, R2, R3 green |
| the whole answer of a route, printed (`str(response.data)`) | Python's printed form of a dictionary: it ESCAPES an apostrophe inside a string that also holds a double quote | the stopped run's failure; direct call (16:45): the line is NOT found in the printed form, IS found by the key |
| captured log lines (`"\n".join(logs.output)`) | plain strings, `%s`-formatted: a UUID as text, `dict`/`list`, `all`/`1` | the stopped run: R3, S1 green |
| a row's columns (`answers`, `review_reasons`, `needs_review`, `score`, ...) | Python values read back from the database | compared with `==` to values of the same kind |
| `response.data` of the grade route's refusal | a dictionary, before rendering (not the wrapped body) | the stopped run: G7 green |

The words looked for in a printed form after the correction
(`answers_unreadable`, `review_reasons`, `the-answer-text-xyz`) hold only
ASCII letters, digits, `-` and `_`; neither Python's printed form nor
JSON changes those (direct call, 16:45), and the test asserts it of each
word before the check.

**The one check on `str(response.data)` that escaping could hide** was
R4's positive check for the line. Fixed at `069eccf9`: read from
`response.data["raw_input"]`. No other positive check is on a printed
form. No value looked for anywhere in the module holds a quote, a
backslash or a non-ASCII letter except the two fixed lines and the
refusal sentence (an apostrophe each); those three are only ever looked
for in a plain string or compared with `==`.

## Per test
"Red under" names the mutants of the expected file that fail the test;
"control" means it is green with or without the change and is NOT
evidence of this row.

| Test | What it inspects, in what form | Negative checks: what decides them | Read against the real value | Red under |
|---|---|---|---|---|
| A.test_a_list_of_objects_byte_for_byte | builder's HTML; two checksums taken on 220f9cd6 | none | yes | U18 |
| A.test_an_empty_value_byte_for_byte | builder's HTML; checksum | none | yes | U17, U18 |
| A.test_neither_line_is_part_of_the_other | two constants of the test file | both checks are on constants | yes | control, never red |
| B.test_nothing_printable_says_so_in_one_fixed_line | builder's HTML, six shapes, each asserted truthy first | `SOME` not in: decided by U3 (it IS there). telltale not in: decided by U19 (new) | yes | U1, U2, U3, U19 |
| B.test_some_left_out_prints_the_rest_and_says_so | builder's HTML | `NOTHING` not in: U3. telltale not in: U19 | yes | U2, U3, U19 |
| B.test_the_form_a_student_reads_before_release_has_the_same_line | builder's HTML, both forms | `graded != ungraded`: the two forms differ in the header (A's two different checksums show it for an ordinary row); no mutant makes them equal: that one check is a control | yes | U2, U3 (by the positive checks) |
| B.test_what_can_be_printed_is_decided_in_one_place | the helper's return, tuples compared with `==` | none | yes | U1, U17 |
| R.test_the_fixture_is_what_it_says | the row's `answers`; the stored document (string) | none | yes | control |
| R.test_the_student_reads_the_unreleased_paper | the route's document by its key (string) | telltale not in: U19 | yes | U2, U3, U19 |
| R.test_the_student_reads_it_on_the_assignment | the assignment route's document by its key (string) | telltale not in: U19 | yes | U2, U3, U19 |
| R.test_a_read_that_rebuilds_an_empty_stored_document | the route's document, the stored document, log lines | telltale not in the LOG: U12 puts it there | yes | U2, U3, U11, U12 |
| R.test_a_student_is_not_told_more_than_the_line | CORRECTED. the line by the document's key; the review keys as keys of the answer; three words in the printed form | the row is GIVEN a review reason first and the test asserts it is there. telltale: U19. The review keys and `answers_unreadable` not being sent is H-127's ground (the student's serializer has no review field): no mutant of this row sends them, so those checks are a control | yes | U2, U3, U19 |
| G.test_no_paid_call_is_made | the replaced grader's call record | `assert_not_called`: U4 calls it | yes | U4 |
| G.test_the_teacher_is_told_what_to_do | `str(error)` compared with `==` to the sentence; `is_user_facing_error` | none | yes | U4, U15 |
| G.test_the_paper_is_put_in_the_review_queue | the row's review columns; `needs_review` asserted False before | `review_severity` is not None: before the refusal it is None on this row (never graded, never flagged); the refusal always sets it with the other three, so no mutant of mine fails this one line alone | yes | U4, U6, U8 |
| G.test_nothing_of_a_grade_is_written_and_no_claim_is_left | the row before and after | `graded_at` is None: U4 grades the paper and sets it | yes | U4 |
| G.test_refused_twice_the_reason_stands_once | the row's `review_reasons` with `==` | none | yes | U4, U7 |
| G.test_every_such_shape_is_refused | six shapes through `grade_engine` | `assert_not_called`: U4 | yes | U1, U4 |
| G.test_the_grade_route_answers_400_with_that_sentence | status; `response.data` with `==` (a dictionary, unrendered) | `assert_not_called`: U4 | yes | U4, U15 |
| W.test_a_new_upload | the row's `answers` with `==`; the stored document; the graded row | `NOTHING` not in the stored document: the stored document never held it on this path, so this line is a control. `review_reasons` is None, `needs_review` False: a control here | yes | U5 (control of the cure; U5 shows it can fail) |
| W.test_the_edit_by_text | as above | as above | yes | U5 |
| W.test_a_refusal_before_the_cure_does_not_stay_on_the_paper | as above, after a refusal; `needs_review` asserted True before the cure | `needs_review` False and `review_reasons` None after: decided by the True asserted before; no mutant keeps the flag through a grading, so not seen red on those two lines | yes | U4, U5, U6 |
| S.test_the_grade_is_saved_and_the_paper_flagged | the row; the stored document (string); log lines | telltale not in the LOG: U12 | yes | U2, U3, U9, U10, U12, U16 |
| S.test_it_is_not_refused | the replaced grader's call record; `graded_at` | `graded_at` is not None: U5 refuses and leaves it None | yes | U5 |
| S.test_the_reason_stands_beside_the_others | the reasons' types, a list compared with `==` | none | yes | U9 |
| O.test_grading_leaves_no_such_reason | the row; the stored document (string) | `SOME` not in: U18 prints it. `NOTHING` not in, `review_reasons` None, `needs_review` False: no mutant decides these three | yes | U18 |
| X.test_the_upload_refuses_a_list_that_is_not_all_objects | the error's text; the row's `answers` with `==` | none | yes | U13 |
| X.test_the_edit_refuses_a_list_that_is_not_all_objects | as above | none | yes | U14 |
| X.test_the_edits_refusal_refunds_the_charge | the replaced refund's call record; the row | none | yes | U14 |
| X.test_what_was_refused_is_still_refused | as X1, shapes refused before this row | none | yes | control |
| X.test_what_was_accepted_is_still_accepted | the row's `answers` with `==` | none | yes | control |

## Checks that no mutant decides (not evidence, said plainly)
- A.test_neither_line_is_part_of_the_other; R's fixture control; X's two
  controls.
- B3's `graded != ungraded`.
- G3's `review_severity is not None`.
- W's and O's "no review reason / no flag / `NOTHING` not in the stored
  document" lines (O's `SOME` line IS decided, by U18).
- R4's review-key checks: the student's serializer has never sent a
  review field (H-127); the row now holds a reason so that the check
  has something to find, but no mutant of this row sends it.

## What the reading changed
- R4's positive check (the fault that stopped the chain).
- R4's negative checks had nothing to decide (the row held no review
  reason; "Traceback" in a 200 answer could never be there): the row is
  given a reason, the keys are looked for as keys, "Traceback" is gone.
- No mutant made any "the answer text is not in the document" check
  fail: U19 added (the line carries the stored value after the fixed
  text), expected `{B1, B2, R1, R2, R4}`, written before any run.
- No other expected set changed.
