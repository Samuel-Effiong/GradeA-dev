# H-38: full-suite run (Gate 10 input)

Commit `3a254eb` on `task/teacher-removal`, rebased onto beta `4b902fc`.
Slot granted by Integration & Release; run under the machine lock.

Command: `nice -n 10 flock ~/.machine-fullsuite.lock python manage.py test --settings=settings_worktree --parallel 4 --noinput`
(test DB `test_teacher_removal`). Log: `05_full_suite_3a254eb.log.gz`.

| Result | Value |
|---|---|
| Tests | 4749 |
| Failures | 1 |
| Skipped | 28 |
| Test time | 349.0 s |
| Wall time | 368 s |
| Exit | 1 |

## The one failure is environmental, not H-38

`users.tests_email_domain_rules.ExemptDomainTests.test_nothing_is_exempt_by_default`
asserts `is_exempt_email_domain("qa@yopmail.com")` is False. This machine's
local `.env` sets `EXEMPT_EMAIL_DOMAINS`, which the test reads. Proof by
isolated reruns of that one test on `3a254eb`:

| Environment | Result |
|---|---|
| local `.env` as-is | FAILED (1) |
| `EXEMPT_EMAIL_DOMAINS=` in the process env | OK |

The H-38 diff does not touch email-domain code (its only `users/` changes
are `users/filters.py` and `users/views.py`, enrollment scoping). CI never
sets `EXEMPT_EMAIL_DOMAINS`. The hermetic fix for this test
(`@override_settings(EXEMPT_EMAIL_DOMAINS=[])`) is on `task/h39-network-guard`,
not yet on beta.

No H-38 test failed. None of the known flakes (H-41 redelivery, H-44
pdf_renderer, H-45 redis_hygiene) fired in this run.

## Final full-suite run on the merged tip `acf5f1b` (2026-09-29)

`acf5f1b` = the verified H-38 code (roster fix, funded probes, mypy-stubs
merge) with local beta `197aa46` (H-47 on top of `be78221`) merged in, clean.
Same command, under the machine lock. Log: `06_full_suite_acf5f1b.log.gz`.

| Result | Value |
|---|---|
| Tests | 4791 |
| Failures / errors | 0 / 0 |
| Skipped | 28 |
| Test time | 773.2 s (machine under shared load; the earlier run was 349 s) |
| Exit | 0 |

The environmental `EXEMPT_EMAIL_DOMAINS` failure from the first run is gone:
beta now carries h39's hermetic fix for that test.
