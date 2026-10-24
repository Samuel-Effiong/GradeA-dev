# Stage 3 per-module pass at cc14bb0 (2026-09-30)

The 47 stage 3 cache, probe and regression modules at `cc14bb0` (Design A, the course-roster
scope), run one at a time on the stage 3 worktree, each under
`nice -n 10 timeout -k 60 600 python manage.py test <label> --settings=settings_worktree --noinput -v 2`
(`--keepdb` after the first), with `RACE_COST_ENROLLMENTS=600 RACE_COST_ROWS=200`.
The list is step 4's pass list (`../h1-step4-per-module-8063c44/`) as it exists on stage 3: every
`tests_cache*`/`tests_probe*` module here plus the same regression modules. `tests_no_wildcard_invalidation`
exists on step 4 only, and the two roster modules are new.

`SUMMARY.txt` is the runner's own summary: 46 OK, 1 FAILED, none timed out, 10:44 to 11:04.
The `.log` files are trimmed to one outcome line per test (each file's count matches `ran=`),
with email addresses redacted.

**The one failure, and its fix.** `AutoGrader.tests_cache_matrix_selftest`
`test_an_uninvalidated_write_is_reported_stale` withdrew the student through `QuerySet.update()`
(no signal, no bump) and expected the matrix to report the student's course list STALE. Under
Design A it reads FRESH: the student's list key carries a `crs` scope per course the student can
see, read live, so a withdrawal moves the key by itself. That is the rework working, not a
regression, but it removed the self-test's proof that the matrix can report a student STALE. The
follow-up commit switches the self-test's silent write to a course rename through `.update()`,
which leaves the student's course set alone, so both the student and the teacher again must read
STALE, and FRESH only after the bump. The new behaviour gets its own test,
`test_a_silent_withdrawal_moves_the_students_list_key` (classrooms/tests_cache_course_roster_scope.py).
