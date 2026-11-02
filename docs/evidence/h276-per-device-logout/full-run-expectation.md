# H-276 full regression: expectation (committed before the run)

Commit: this one (code identical to 768c16ab). Command: `python manage.py test --settings=settings_worktree --noinput`
(the whole suite; test DB test_AutoGrader_h276).
Baseline: beta f5d2d577 full run = Ran 8587, OK, skipped=30 (Batch 31 evidence).
Change: +11 tests (users/tests_h276_per_device_logout.py). One existing test renamed and inverted, count unchanged.
Expected: Ran 8598, OK, skipped=30. Anything else = STOP.
Mutation: 9/9 killed (M-3 after the refresh-guard test), all restores sha256 OK.
