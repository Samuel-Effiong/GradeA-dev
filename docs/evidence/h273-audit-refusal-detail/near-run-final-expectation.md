# H-273 final near run: expectation (committed before the run)

Commit: this commit (code identical to d8b78e86).
Command: python manage.py test audit users classrooms billing students assignments AutoGrader --settings=settings_worktree --noinput
(settings_worktree = AutoGrader.settings + test DB name test_AutoGrader_h273.)

Baseline 7-app count at f5d2d577: 6690 (near-run-step5.md).
New tests since: audit/tests_h273_query_filters.py 32, audit/tests_h273_request_detail.py 30.
Expected: Ran 6752, OK, skipped=20. Any other count or any failure = STOP.

Mutation: full battery 25/27 killed on ff2cba75 (logs beside this file). The 2 survivors
(M-16, M-18) were test gaps; tests added in d8b78e86 and re-run: both killed. Final 27/27,
all restores sha256 OK.
