# H-298: deleting a teacher whose courses hold assignments failed

Owner approved the fix for beta on 2026-11-06.

**Cause.** Deleting a teacher cascades to their courses, assignments and
submissions. The assignment and course cache bumps (post_delete receivers)
read `course.teacher`. By then the teacher row is gone, so the read raised
`CustomUser.DoesNotExist`, and the whole delete rolled back.
`getattr(obj, "teacher", None)` does not catch that exception.

**Fix.** Added `AutoGrader.cache_generation.related_or_none`, which returns
None when the related row is gone. `_bump_assignment_scopes` and
`_course_owner_scopes` now use it. The assignment bump takes the teacher's id
from `course.teacher_id`, so the same scopes are still bumped.

**Evidence.**

- 01_red.log: on beta 22eea84b, 2 errors with CustomUser.DoesNotExist.
- 02_green.log: Ran 2, OK.
- 03_plants.log: P1, P2 and P4 were killed. P3 survived (see that log), so
  the students/signals.py change was reverted.
- 04_near.log.gz: classrooms, assignments and every AutoGrader.tests_cache*
  module, `--parallel 2`, under the machine flock: **Ran 1773, OK
  (skipped=13)**. Run with the h284_deps_env venv, because the old
  AutoGrader_env venv lacks whitenoise (required since H-271). The first
  attempt on 2026-11-07 was cut off when the session ended and is not
  counted; this re-run on 2026-11-08 replaces it.
