# H-154: note for the frontend

No request changes. No field is removed or renamed. Nothing changes in
any answer a STUDENT receives. Three things a TEACHER's screens can now
meet, all on a paper whose AI grading reply was wrong in shape (it held a
question twice, or an evaluation for a question that does not exist) and
was corrected before the score was added up.

## 1. The saved arithmetic note has a third status
`feedback.score_calculation_verification.verification_status` was always
`"PASS"`. It can now be `"CORRECTED"`. Then the note also carries:
- `repeated_evaluations_dropped`: a list of
  `{"question_number": 2, "dropped": 1}`;
- `unmatched_evaluations_dropped`: a whole number;
- `correction_note`: one sentence for the teacher.

`individual_scores`, `manual_sum` and `calculation_notes` are as before
and describe the corrected sum.

## 2. A new reason in the review queue
`review_reasons` can hold
`{"type": "ai_reply_corrected", "repeated_questions": [2], "repeated_dropped": 1, "unmatched_dropped": 0}`,
beside the existing types (`answer_not_found`, `grader_disagreement`,
the second-opinion ones). `needs_review` is then true and `review_tier`
is at least `"moderate"`. A screen that lists reasons by type needs a
wording for this one; suggested: "The AI's reply repeated or invented a
question; the lower mark was kept. Please check this paper."

## 3. The score of such a paper
One evaluation per question is counted: of the AI's repeats the lowest,
and an evaluation the system already held (answer key, or reused from an
identical earlier answer) is kept as it is. The total is never above the
maximum.

## Not in this change
- Grades saved before this change are not recalculated. A saved score
  above the maximum stays until the paper is graded again.
- Students are sent none of the above: not the note, not the review
  fields.
