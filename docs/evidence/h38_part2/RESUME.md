# H-38 RESUME — shutdown checkpoint 2026-09-27

Branch `task/teacher-removal` @ `56099ce` (worktree
`Grade-Automator-Plus-teacher-removal`), on beta `4b902fc`. Working tree is
clean — nothing uncommitted, nothing to recover from stash.

## State

- Part 1 (`ad93df2`) and part 2 (`56099ce`) both committed. All 33 reproduce
  probes (`billing/tests/test_h38_part2_removed_teacher_routes.py`) plus the
  17 part-1 tests plus the sweep guard
  (`classrooms/tests_teacher_access_sweep.py`) pass together: 55/55, see
  `h38_p2_run2.log` in the session scratchpad (not committed).
- Site-by-site mutation testing (`mutate2.py`, session scratchpad, not
  committed) had 20 planned mutants covering every rewritten call site.
  Only 4 ran before it crashed on a string-match assertion — **all 4
  KILLED**: `A1_assignments_list_qs`, `A2a_upload_course`,
  `A2b_upload_async_course`, `A2c_generate_course`. Results file:
  `mutation2_results.txt` in scratchpad.
- Crash cause: mutant `A3_draft_save` (assignments/views.py, the AI-draft
  save's `teacher_course_access_q` filter) — the script's expected string
  doesn't match after `black` reformatted that block on commit. Not a bug in
  the fix, a bug in the mutation harness's string match.
- Git state was verified clean (`git status --short`, matches `56099ce`)
  before this note was written — the crashed mutant never got applied by
  `open(f,"w")`, so nothing needed reverting.

## Next command on resume

1. Reread the current `assignments/views.py` around the AI-draft-save
   filter (`git show 56099ce -- assignments/views.py` or just open the
   file) and fix `mutate2.py`'s `A3_draft_save` tuple's `new` string to match
   the actual committed text.
2. Rerun the remaining 16 mutants (A4, T1, S1-S3, C1-C6, D1-D4) — the script
   takes mutant names as extra argv to run a subset, e.g.:
   `python <scratchpad>/mutate2.py <scratchpad> A3_draft_save A4_pdf_teacher_view T1_upload_task S1_assignment_taught_by S2_submissions_qs S3_my_students_qs C1_remove_student C2_my_students_exists C3_submissions_prefetch C4_studentcourse_qs C5_topic_qs C6_topic_create_validator D1_dash_course D2_dash_assignment D3_dash_course_teacher D4_ai_context`
   (script and scratchpad path are session-local; if unavailable, rewrite
   from the mutant table in the command history / this note's git blame).
3. Confirm all 20 KILLED, then: rebase `task/teacher-removal` onto current
   beta (last checked 141 commits behind at `4b902fc`; check how far it's
   moved since), re-run the full 55-test set plus a `classrooms users
   assignments students dashboard` regression, update
   `docs/evidence/h38_teacher_removal/` and `docs/evidence/h38_part2/` with
   final gate status, then request the full-suite slot from Integration &
   Release Lead (H-38 has priority behind authz-oauth per the SM).
4. Outstanding from before shutdown: SM has the ownership decision (LEAVE,
   already implemented) and asked for a `HARDENING_BACKLOG.md` entry for
   H-38 and a new H-38-F1 (orphaned-course dashboard/reassignment follow-up)
   — **not yet written**, do that as part of step 3's evidence update.
5. `billing/tests/test_h38_part2_removed_teacher_routes.py` was copied
   read-only from the Hardening Engineer's worktree
   (`Grade-Automator-Plus-h38-part2-repro` @ `713ad49`) and then edited here
   (assertion loosened for the credit-gate status-code variance, one
   before/after fix for the student-course patch check). Their worktree is
   independent and untouched.

## Not run at shutdown

No test server or long-running process of mine was left running in this
worktree; the last background test run completed before this note was
written. Nothing to stop.
