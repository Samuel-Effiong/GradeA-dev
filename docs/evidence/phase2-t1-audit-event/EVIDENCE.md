# T1 evidence — Epic A AuditEvent schema + emitter

Worktree: `Grade-Automator-Plus-phase2-t1-audit-event`, branch
`task/phase2-t1-audit-event`, rebased onto beta `b441a29`, this evidence at
commit `b9aea98` ("T1 rebuild: school_id, per-action metadata allow-list,
real trace_id wiring"). Supersedes the prior evidence in this directory
(`dc72f45`, based on the pre-rebase `fba1294`), which predates three fixes
made reviewing this work against
`docs/phase2/architecture/04_epic_a_implementation_plan.md`:

1. **`license_id` → `school_id`.** `LicenseSubscription` is 1:many per
   school across renewals; a School Admin's audit history would have
   fragmented on renewal. Every other tenancy boundary in the codebase
   scopes by `school_id`.
2. **Metadata allow-list is now per-action** (`METADATA_ALLOWLIST` in
   `audit/metadata.py`), not one global key pool — a key valid for one
   action is no longer automatically valid for every other action.
   `sanitise()` still does the generic shape/PII-shape/size checks;
   `sanitise_metadata_for_action()` layers the narrower per-action gate on
   top, applied to `metadata` only (`before`/`after` keep the global pool —
   neither has a real call site yet to size a per-action list against).
3. **`trace_id` now actually resolves from
   `AutoGrader.request_context.get_request_id()`** — the id already
   propagated across the web request and every Celery hop it dispatches —
   falling back to an explicit `audit.context.trace_context()` if one is
   open, then a fresh `uuid4()`. The reused branch's `trace_id` previously
   read only from a disconnected, never-populated scaffold contextvar
   (`audit.context`'s own), so every real call site would have received a
   random id unrelated to the one that actually ties a request/task/log
   line together — silently breaking FR-A-03/NFR-OBS-01/X-5. Found and
   fixed during this build, approved by the Senior Manager (see plan §0.3a).

## 1. Test suite

`python manage.py test audit --settings=settings_worktree --noinput`

- Found 119 test(s)
- Ran 119 tests in ~7s
- **OK**

119 = the original 106 + 13 new: 8 for the per-action metadata allow-list
(`tests_metadata.py`, including that every key on every action's list is
also in the shared `ALLOWED_KEYS` pool, and that an action with no call site
yet accepts no metadata at all), 4 for the `trace_id` / `request_context`
fallback (`tests_emitter.py` — explicit context wins, the request's own id
is used when none is open, a non-UUID-shaped inbound id is replaced not
trusted, a fresh id is minted with neither available), and 1 proving the
per-action metadata gate is wired at the real `emit()` call path, not just
in the underlying helper function in isolation.

## 2. Mutation testing

Expanded from 53 to **57 mutants** (`mutate.py`) to cover the two areas of
new logic the original battery never touched: the `trace_id` resolution
order (E4/E4b/E4c) and the per-action metadata gate (D13/D14). Applied one
at a time from collision-safe copies, restore verified by md5 against the
pre-mutation original after every single mutant (never `git checkout`) —
confirmed programmatically (`all(m["restored_md5_ok"] for m in results)` on
the run's own JSON output), not just by eye.

**Result: 57 / 57 KILLED**, in two runs:

- **51/57** in one clean `--keepdb` pass (`final_run.log`, `mutation_log.jsonl`,
  `mutation_results.json`) covering every emitter/context/metadata/enum
  mutant plus the two model-level mutants that don't touch the migration
  (M1, M2).
- **6/57** (M3–M8, the migration-file mutants) re-verified separately
  against a **fresh** test database per mutant (`migration_mutation_run.log`,
  `mutate_migration.py`): `--keepdb` reuses the already-built schema, so a
  migration-file mutation never reaches the database and those 6 show as
  false SURVIVED under `--keepdb` — a script limitation, not a code gap.
  **Flag this for whoever re-runs this battery next**, alongside the other
  finding below, so neither is re-chased blind.

**Second finding worth flagging**: the first attempt at the full 57-mutant
battery showed 14 apparent survivors (the 6 above plus 8 code-level ones:
E14, E16–E19, E24, D3, D13). All 8 code-level ones were re-verified in
isolation once a concurrent full-suite run on the shared host had finished,
and all killed cleanly — the same connection-contention signature as the
full-regression run below (§3), just against a single small app instead of
the whole suite. One of the 8, E14/D13, also had a genuine test-target
issue independent of contention: the per-action gate was only exercised by
a `tests_metadata.py` call to `sanitise_metadata_for_action()` directly,
never through `emit()` — fixed by adding the `emit()`-level test described
in §1, which now correctly kills both mutants.

Categories: E1–E27 (emitter build/validation/trace-id logic), C1–C3
(context/trace id), D1–D14 (metadata allow-list/sanitise, generic +
per-action), M1–M8 (model: append-only, no-FK, indexes, columns,
constraints), N1–N3 (enums: retention-class vocabulary, action verbs).

## 3. Regression — full suite

`python manage.py test --keepdb -v 1` (every app, no labels)

- Ran 4670 tests in 2551.123s
- **OK (skipped=26)**
- exit=0

A first attempt at this full regression, run concurrently with another
session's own full-suite run on the same shared Postgres/Redis instance,
came back with 628 errors — all `OperationalError: connection to server was
lost` / `kombu.exceptions.OperationalError` / `redis.exceptions.
ConnectionError` signatures, i.e. connection-pool and broker contention
from two full suites competing for the same 100-connection cap and Redis
instance, not real failures. Confirmed: zero actual assertion failures
anywhere touching this work; the only "audit"-named hits were
`dashboard.tests_dashboard_audit_fixes` and
`billing.tests.test_audit_identity_migration` (pre-existing, unrelated
files that happen to share the word "audit"), and even those only failed at
`setUpClass` (connection-lost), never on an assertion. Discarded as noise,
not reported as a result. Re-run alone once the host was confirmed clear:
the clean `4670/4670 OK` above.

## 4. Conclusion

Schema, taxonomy, emitter and correlation-id wiring match the frozen data
model (`03a_data_model.md` §2.1), the requirements (FR-A-02, FR-A-03,
FR-A-04, FR-A-11, A6, X-4, X-5), and the corrections/decisions recorded in
`04_epic_a_implementation_plan.md` §0 (0.1, 0.3, 0.3a, 0.4, 0.5). No
regressions anywhere in the codebase. Nothing outside `audit/` was modified.

Post-commit sha256 (from `git show b9aea98:<path>`, not the working copy —
pre-commit hooks can rewrite a file after it's written, so a pre-commit hash
is not evidence of what actually landed):

```text
176286f4e24f6456e3e25972225b9b6217db8dfb01ccf1406e9f62c917320556  audit/emitter.py
194e679ab10cae840865772f1b9a0e8ffe2fe7ce7a29d3f3a74c085b2c5cc12e  audit/context.py
5b7ce955eab9814d727902e1658be72e75d9773d00bf63a4f2d617989c2ff05c  audit/metadata.py
f09850e5ceaec9d61c96fff22081f8809c47c278e3772dbda5809f6a06bf689c  audit/models.py
8840cbf5871b21dd31ae11a63b74a4760543e79bc68a4f5090a1797982a8c64b  audit/apps.py
39c9f9de164ddbb5ab628f0dc6cbcda810af50e970b14dd5f3bf90a9c0ef830b  audit/enums.py
8ed2b8aebb1d74de49fd109b53bf99d0987b9133b27949918b023cd7c514e856  audit/migrations/0001_initial.py
```
