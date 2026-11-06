# H-274 worktree venv: dev requirements added (2026-11-02, SM-approved)

Why: a billing run on 1ec54b6c had 3 import errors. `freezegun` and
`pytest` were missing from the worktree's untracked `.venv`, so
`billing.tests.test_billing_period_anchoring`,
`billing.tests.test_license_service` and one test in `billing.tests.tests`
could not load. `tblib` was also missing, so `--parallel` could not send a
failed test's traceback between processes, and the runner crashed with
`TypeError: cannot pickle 'traceback' object`.

What: `.venv/bin/python -m pip install -r requirements-dev.txt`, pinned
versions only, into the untracked `.venv`. Nothing was committed, and the
main checkout was not touched.

| | Lines | sha256 of `pip freeze --all` |
|---|---|---|
| before | 169 | `85b517c5dd28989571e7ac5ed1353de6e35d42738eea06d7d3bc8a68920be5ad` |
| after | 227 | `8a662347ea29935d61138e4d0ce454bf08ca3219c29b6e11534bdc3b3b5fa944` |

The "after" freeze is committed as `venv_freeze_after.txt`.

- 58 packages were added, including freezegun 1.5.5, pytest 9.1.1 and
  tblib 3.2.2.
- One installed package changed: `types-python-dateutil` 2.9.0.20261029 →
  2.9.0.20250822, the pin in `requirements-dev.txt`. It is a typing stub that
  no test imports.
- `pip check` reports no broken requirements.

Confirmation: `manage.py test --parallel 4 billing` at ec1cc834 gave
`Ran 2315 tests`, `OK` (the serial run before had `Ran 2262`; the
newly importable modules add 53). There was no pickle crash, so the crash
was only the import failures. Log: `billing_parallel_ec1cc834.txt.gz` (`gzip -n -9`, because the
raw log is 616 KB and over the repo's 500 KB added-file limit). sha256 of
the raw log `11fdd50cff91c216ac4f917b9edb2568192234eadc9508ff64a8626d9be81f9e`, of the `.gz`
`97dea982067a4b26a6e4d5f0632a11b3b8b9c34586a4000fc5bfbf1b90d6cbb0`. Check with `gunzip -c … | sha256sum`.
