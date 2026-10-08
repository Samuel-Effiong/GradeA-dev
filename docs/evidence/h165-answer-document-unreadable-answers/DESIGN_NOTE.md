# H-165: the answer document builder must not raise on stored answers it cannot print

Design note, d5, 2026-10-07. For the Senior Manager's ruling before any
test is written; Verifier 2 verifies. MEDIUM, first row of batch 13.
Reading only: nothing here was run. Line numbers are beta `d7143538`.

## The fault, in one paragraph
`student_submission_to_html` (`students/services.py`) walks
`submission.answers` as a list of objects and calls `.get` on each. On a
non-empty value of any other shape (an object, a string, a number, a
list holding a string) it raises. The column takes any JSON. Beta's two
writers cannot store such a shape today, **only because this builder
raises inside them before they save**. The live service's "edit by text"
path saves first and builds after, so such rows can exist on production.
For such a row on beta's code: a student's read of the paper before
release answers 500 on every read (H-130 rebuilds per read); anyone's
read answers 500 when the stored document is empty; grading fails at its
save, after the paid call (refunded).

## What changes

### 1. The builder never raises on the shape of `answers`
One small pure helper decides what can be printed:

- a list: its entries that are objects are printed as today; an entry
  that is not an object is left out;
- anything else that is not empty (object, string, number, `true`):
  nothing is printed from it;
- empty values (`[]`, `{}`, `""`, `null`, `0`, `false`): as today,
  nothing is printed and nothing is said.

When anything was left out, the "Student Responses" section ends with
**one fixed line of ours**, the same on every path:

> Some of this submission's answers could not be displayed.

and when nothing at all could be printed from a non-empty value:

> This submission's answers could not be displayed.

**What each reader sees.** The line is fixed text written in the code,
never text from the row, the model or an exception. It says nothing
about grading, scores, review or errors, so the student's view before
release (the header in its ungraded form, as H-130 has it) tells nothing
it did not tell before. A teacher sees the same line under the ordinary
header. A document for a row whose answers are a list of objects is
**byte for byte what it is today**; a test holds that.

### 2. The writers refuse what the builder used to refuse for them
This is the part that is easy to miss. Today a list holding a
non-object entry is kept out of the database by the builder raising in
the upload (about line 1015) and in the answers update (about 1253).
Once the builder is tolerant, both would start storing such lists. So
both writers' existing check ("not a list: refuse") is widened to
"not a list of objects: refuse", with the same error and inside the same
refund scope where there is one. Nothing a writer accepts today is
refused after the change, and nothing it refuses today is accepted.

### 3. Grading sends such a paper to the teacher
Grading already pairs questions with answers by skipping entries that
are not objects, so for such a paper every question is graded "answer
not found" and the paper already gets the `answer_not_found` reason,
then the save fails in the builder. (That is for an object, a string or
a list. A stored number or `true` cannot be walked at all, and the
pairing itself raises before the paid call; I read the pairing
(`_pair_question_with_answers`) and not every step before it, so the
tests will show which it is, and the pairing gets the same helper if it
is needed.) After the change the save succeeds,
and `_populate_and_save_grade` adds **a reason of its own**:
`{"type": "answers_unreadable", "left_out": <count or "all">}`, in the
review queue beside the others, tier as `answer_not_found` has it. No
model change: one more entry in `review_reasons`. A student is sent no
review field (H-127), before or after release.

### 4. A log line
Ids and the kind of value only (`submission=<id> answers=<kind>
left_out=<n>`), never the value. **Where:** not in the builder, which
runs on every read of an unreleased paper by its student and would log
on each. I propose it in the three places that STORE a document or a
grade for such a row: grading's save, the read that rebuilds an empty
stored document, and (when score printing lands) the manual grade.

## What the formatter is sent
The formatter is sent the grading result, not the document
(`grading_result_for_formatter`), so it is sent nothing new: for such a
paper the result says each answer was not found, as it would today if
the save did not fail. The new review reason is not part of what it is
sent. The fixed line is never sent to a model.

## Rule 20: cached answers
Three cached answers carry the document: the submission detail (staff
and student), and the assignment detail's
`student_submission_raw_input` for a student. For ordinary rows no
payload changes. For an affected row a 500 becomes a 200 with the line.
No key changes: the document still follows the row, whose saves bump the
reader. The AutoGrader app's cache tests are in the regression, and the
existing test modules of the routes this row touches are in the chain's
modules step (`students.tests`, the H-130 modules, the assignment detail
tests), their fixtures read first: several use `answers={}` and one uses
`{"q1": "2"}`.

## Limits I already see
- A stored document made from unreadable answers stays as stored (for
  staff, and for the student after release) if the answers are later
  repaired by hand; only the unreleased student's view follows the row.
- The fix prints and flags; it does not repair the answers. The paper's
  text is whatever the row holds.
- Production is not changed by this row until it is promoted; the
  package line and the count file are the bridge.

## Questions for your ruling
1. **Should grading spend a paid call on a paper with no printable
   answer at all?** Today it does (and then fails, refunded). After this
   row it would be charged, graded zero and flagged. The alternative is
   to refuse grading such a paper up front with a plain message and no
   charge. I lean to the refusal for "nothing printable" and to
   grade-and-flag for "some left out".
2. The log line's place, as proposed in 4, or in the builder after all.
3. The tier of `answers_unreadable`: as `answer_not_found`, or lower.
4. The two sentences' wording.

## Tests, first, as their own commit (each new one seen red)
- the helper and the builder for each shape: object, string, number,
  list of strings, a mixed list, and the ordinary list byte for byte;
  the empty values;
- an affected row through each route that fails today: the student's
  unreleased submission and assignment reads, a staff read with an empty
  stored document, grading's save (reason present, grade saved);
- each writer refuses a list holding a non-object entry, and on the
  upload the charge is refunded;
- one mutant per condition, expected sets written before any run, every
  test read against every mutant and against its fixture's real values.

## Rulings (Senior Manager, 2026-10-07), added 16:11
The design above is approved. Answers to the four questions, and one
gap found afterwards:

1. **Nothing printable: refused for grading up front.** No paid call, no
   charge; the paper is put in the review queue with
   `answers_unreadable` (`"all"`). The refusal happens before the grading
   claim, so nothing of a grade and no claim is left on the row. In bulk
   grading each paper is its own task, so the refusal is that paper's own
   failure and the rest go on. "Some left out" is graded and flagged.
2. **No log line in the builder.** One where a document or a grade is
   stored for such a row. (I also log at the refusal itself, a fourth
   place, since nothing else would tell an operator; yours to strike.)
3. **Tier:** as `answer_not_found` (critical).
4. **The two sentences** as written above.

**The gap, and ruling (d).** The manual-grade route refuses a paper that
was never graded ("has not been graded yet", and it needs a saved
maximum). So for a paper refused up front a hand grade is NOT a way
round, and the route is not changed in this row. The way round is
readable answers: a new upload, or the edit by text, each of which
replaces the stored value with a list of objects. The teacher's sentence
on the refusal says exactly that:

> This submission's answers could not be read. Upload the paper again or
> re-enter its answers, then grade it.

Tests hold that each of the two cures works on such a row and that
grading then goes through, and that the refusal makes no paid call.
Whether a teacher may grade by hand a paper the AI never graded is a
product question the Senior Manager takes to the user.

**A paper that WAS graded before its answers became unreadable** (a
staff edit in the admin): it has a saved grading result and a maximum,
so the manual-grade route accepts it. On beta today that route does not
build the document, so it works. Once score printing lands, the route
builds the document inside the guard ruled for that row; with this row
underneath, the builder does not raise at all, so the grade is saved and
the stored document is rebuilt with the fixed line. That is the one case
where a hand grade remains the way round.

**The grading pairing** for a stored number or `true`: such a value is
"nothing printable" and is refused before the pairing is reached, so the
pairing is not changed.
