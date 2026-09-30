# H-56 fix for bundle 3's pdf_cache regression, runs at 7c28742

Every run was under rule 13: `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, with `EXEMPT_EMAIL_DOMAINS=` set empty. Logs are trimmed to one outcome line per test, with emails redacted.

| Run | Tree | Result |
|---|---|---|
| Reproduce first: `assignments.tests_pdf_cache` | `e4f3932` (the tip bundle 3 took), in a disposable worktree | FAILED as required, 1 error: `AttributeError: 'DatabaseDefault' object has no attribute 'isoformat'` |
| Changed modules: `AutoGrader.tests_migration_rollback_defaults` + `assignments.tests_pdf_cache` | `7c28742` | 48 OK |
| Owning-app regression (rule 15): `assignments` | `7c28742` | 613 OK (13 skipped) |

- **Why only `assignments`:** the reader audit found readers only in that app. `users`, `billing` and `dashboard` are covered in full by bundle 3's strict re-run, as 0b proposed.
- **The stopped run:** the stream would also have run `users`. It was stopped at that point, and its partial log is not recorded.
