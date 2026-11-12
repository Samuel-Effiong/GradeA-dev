# R1 review flags (beta bug fix) - notes

Brief R1 from the SM (2026-11-12), owner-approved plan
team/PLAN_review_flags_2026-11-12.md. Base origin/beta f975ba9d0, branch
task/review-flags. Every run: --settings=settings_worktree --keepdb
--noinput --parallel 4, under flock ~/.machine-fullsuite.lock, venv
~/Documents/Virtualenvs/review_flags_env.

## Rulings (SM, 2026-11-12, after the survey checkpoint)

- Q1: clear only rows whose every non-"resolved" entry is
  grader_disagreement or second_opinion_unavailable.
- Q2: rows that stay are re-ranked from the remaining reasons by the
  grading code (students/review_state.py, imported by 0037).
- Q3: new gradings write no disagreement entries; the serializer strips
  them from old rows.
- Q4 (OWNER): the `second_opinion` block leaves every teacher payload; it
  stays stored in feedback.
- Q5: level on the teacher's extraction_flag / answers_unreadable /
  answer_not_found reasons; the teacher's "Needs review" (flagged_for_review)
  is also true for a whole-paper-unreadable paper; students unchanged.
- Q6: level on upload_mismatch and review_flag for teacher and student.

## Notes

- R5 side effect (accepted by SM): grading_eval's teacher_alignment gets no
  new disagreement labels, as disagreements are no longer queued for a
  teacher to confirm or override.
- R6 (accepted by SM): 0037 is a queryset update; it writes no audit
  (GRADE_CHANGE) rows.
- answers_unreadable (SM follow-up ruling): a count (some stored answers
  unreadable) is level "question", left_out "all" (the whole paper) is
  "assignment"; both set the teacher's flagged_for_review (rule 2). These
  are stored entries that cannot be printed, not reader flags, so no
  READING_PROBLEM flag covers them. Students unchanged.

## Runs

- G1 guards60 on the base f975ba9d0: Ran 1087, OK (skipped=3).
- R1 red on the base: Ran 29, failures=10, errors=12.
- R1 green: Ran 29, OK.
- N1 near (students assignments ai_processor users guards60): Ran 4975,
  failures=31, errors=10, skipped=26. These are tests that pinned the old
  behavior (disagreement flags, the second_opinion block, flags without
  level), plus ai_processor.tests_call_record_static's migration test,
  which fails only on --keepdb parallel clones made before 0037 existed
  (passes serially).
- RR1 (the failing classes + R1): Ran 109, one test of mine fixed, then
  serial Ran 13, OK.
