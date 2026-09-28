# H-39 resume note (shutdown checkpoint 2026-09-27)

STEP: network_guard.py + tests committed (5e9409e on task/h39-network-guard,
mypy/flake8/black green, 8 dedicated tests pass). Hermetic fix for
users.tests_email_domain_rules.test_nothing_is_exempt_by_default is in the
same commit. Full-suite proof under the guard has NOT been captured (earlier
run was killed for the 2-suite cap; not restarted since).

NEXT COMMAND (from this worktree):
    python manage.py test --settings=settings_worktree --parallel 4 --noinput

BLOCKED ON: nothing — just needs a queue slot after resume.
