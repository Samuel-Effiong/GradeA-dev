# T1 evidence — Epic A AuditEvent schema + emitter

Worktree: `Grade-Automator-Plus-phase2-t1-audit-event`, branch `task/phase2-t1-audit-event`, based on beta `fba1294`.

## 1. Test suite

`python manage.py test audit --settings=settings_worktree --noinput`

- Found 106 test(s)
- Ran 106 tests in 6.169s
- **OK**

Covers: `tests_schema.py` (columns/nullability, no FKs, indexes, append-only,
student data minimisation, closed vocabularies), `tests_emitter.py` (build/
validation/rejection/never-raises/trace-id/retention-class/request-field
behaviour), `tests_enums.py` (vocabulary pins), `tests_metadata.py`
(allow-list sanitiser).

## 2. Mutation testing

53 mutants generated across the module (`mutate.py`), applied one at a time
from collision-safe copies, restore verified by md5 against
`checksums_md5_at_mutation.txt` after each run (never `git checkout`).

Categories: E1–E27 (emitter build/validation logic), C1–C3 (context/trace id),
D1–D12 (metadata allow-list/sanitise), M1–M8 (model: append-only, no-FK,
indexes, columns), N1–N3 (enums: retention-class vocabulary, action verbs).

**Result: 53 / 53 KILLED.** Full per-mutant log: `mutation_log.jsonl.txt`,
`mutation_run.txt`. Restore checksums: `checksums_md5_at_mutation.txt`.

## 3. Regression — full suite

`python manage.py test audit users classrooms assignments students billing --settings=settings_worktree --noinput`

- Found 2989 test(s)
- Ran 2989 tests in 1312.048s
- **OK (skipped=17)**
- exit=0

No failures. Noise observed in the log (pre-existing, not caused by `audit`):
`Free trial plan not found` warnings, one `SubmissionAlreadyGradedError`
traceback inside a test that exercises that exact error path, and one
Chromium PDF-renderer relaunch message from the PDF export tests. None of
these are failures — the suite reports `OK`.

## 4. Conclusion

Schema, emitter, and vocabularies match the frozen data model
(`03a_data_model.md` §2.1) and requirements (FR-A-02, FR-A-04, FR-A-11, A6,
X-4, X-5). No regressions in `audit`, `users`, `classrooms`, `assignments`,
`students`, or `billing`. Nothing outside `audit/` was modified except the
`AutoGrader/settings.py` `INSTALLED_APPS` entry.
