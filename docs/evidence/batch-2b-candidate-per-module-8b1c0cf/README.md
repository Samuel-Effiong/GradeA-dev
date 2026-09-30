# Batch-2b candidate: per-module pass at 8b1c0cf (2026-09-30)

The candidate is `task/h1-step4-wildcard-removal`: batch-2a's tip + stage 3's course-roster-scope rework (Design A) + H-1 step 4. It's built by merges only.

**Modules.** 109 modules, one at a time, on the step 4 worktree. The list:
- step 4's 48 modules (`../h1-step4-per-module-8063c44/`);
- the two roster modules;
- every `users` and `classrooms` test module, because retire (A) (batch-2a) changed those apps.

**How each ran.** Under rule 13 and rule 12:

    systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 \
      nice -n 10 timeout -k 60 600 python manage.py test <label> --settings=settings_worktree --noinput -v 2

- `--keepdb` after the first module.
- Environment: `RACE_COST_ENROLLMENTS=600 RACE_COST_ROWS=200 EXEMPT_EMAIL_DOMAINS=` (empty, as in production).

**Timing.** 12:59 to 13:25. The pass was paused once between modules at 0b's request (13:15 to 13:18).

**Result** (`SUMMARY.txt`): 107 OK, 2 FAILED. None timed out, and none hit the memory cap.

**The two failures and their fix.** Both came from batch-2a's retire (A). Under it, an existing student with no sign-in is enrolled PENDING, with fresh credentials. That is what `test_first_import_of_never_signed_in_students_is_two_bumps_per_row` pins.
- `AutoGrader.tests_cache_matrix_selftest`, 8 of 8: the fixture's enrolment came back PENDING, not ENROLLED.
- `classrooms.tests_cache_course_roster_scope`, 1 of 12: the enrolment cost pin read `{"SET": 16, "INCRBY": 16}`. The PENDING path also saves the account.

`018351f` gives both factories the `last_login` a real sign-in records. That's the same fix as the H-25 fixtures (`877c900`, which 1a verified). `rerun_018351f.log`: both modules, 20 tests, OK.

The `.log` files are trimmed to one outcome line per test (each file's count matches `ran=`), with email addresses redacted.
