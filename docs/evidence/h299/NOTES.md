# H-299: audit events written inside a zero-credit scope are dropped

Branch `task/h299-zero-credit-audit`, cut from `origin/staging` 8aafa47dd (SM, 2026-11-08). Worktree `Grade-Automator-Plus-h299`, test DB `test_h299`. A staging bug fix, so it targets staging (Phase 2). No push.

## 1. The bug

`billing/immutable.py` `_guard_save` runs `assert_not_zero_credit` on every insert into a model registered with `register_append_only_guards`.

- A model missing from `_INSERT_ATTEMPT` falls back to `LEDGER_INSERT`.
- `AuditEvent` is registered with these guards (audit/apps.py, since Epic A T1 60a1e20da) and is not in `_INSERT_ATTEMPT`.
- So inside a `zero_credit_scope`, `AuditEvent.objects.create` raises `ZeroCreditViolation(attempted=LEDGER_INSERT)`.
- `audit.emitter.emit` swallows the exception (FR-A-11) and returns None.

`AppendOnlyQuerySet.bulk_create` has the same problem through `assert_not_zero_credit(BULK_INSERT)`, for every append-only model.

The check itself dates from B0 r2 (34d5f6179) and is on staging.

## 2. Survey (sent to the SM before the fix; SM approved the design 2026-11-08)

- **S-1.** On staging, beta and task/epic-b1a, no production code opens a zero-credit scope. The only production user is task/epic-c1, in `tags/services.py` (6 scope sites).
  - C1 works around the bug by emitting after the scope closes, inside the same transaction.
  - So no event is lost on staging today. The bug blocks C1 and future zero-credit epics.
- **S-2.** It is not silent. Each dropped audit row also raises false alarms:
  - `_report` logs an ERROR `Zero-credit violation: operation=… attempted=LEDGER_INSERT`;
  - it counts `credit_ledger_anomaly{kind=zero_credit_violation}`;
  - the emitter then raises `_alert("store_failed")` and counts `audit_emit_failures_total`.
  So one lost audit row turns into a false credit-ledger anomaly. The SM called this the most important finding.
- **S-3.** The bulk path (immutable.py:145) refuses AuditEvent bulk inserts as `BULK_INSERT`. Nothing bulk-inserts AuditEvent in production today.
- **S-4.** C1's `_audit` docstring describes the bug as current behavior. The SM routes that to Lane 2. `tags/` is not touched here.
- `audit/emitter.py:211` is the only production AuditEvent writer.
- No existing test pins the refusal, so no pre-existing test is edited.

## 3. The fix (approved design)

In `billing/immutable.py`:
- `_NOT_CREDIT_MOVING = frozenset({"auditevent"})`, plus a helper `_moves_credit(model)`, which is `model._meta.model_name not in _NOT_CREDIT_MOVING`.
- `_guard_save` runs the zero-credit check on insert only `if _moves_credit(sender)`.
- `AppendOnlyQuerySet.bulk_create` runs `assert_not_zero_credit(BULK_INSERT)` only `if _moves_credit(self.model)`.

What does not change:
- `_INSERT_ATTEMPT` and its `LEDGER_INSERT` fallback, so an append-only model nobody has listed is still treated as a credit write. An exemption list, not an allowlist, so a future credit table is guarded by default.
- Row immutability for every model, AuditEvent included: update and delete are still refused, and `mutable_fields` is unchanged.
- CreditLedger and CreditUsageLog behavior inside and outside a scope.
- The module docstring gains a short paragraph on the exemption.

There is no new user-facing wording and no migration.

## 4. Tests: new file `billing/tests/test_zero_credit_audit.py`

All tests use the registered operation `_selftest`.

| Id | Test | Red (8aafa47dd + new file) | Green |
|---|---|---|---|
| Z-1 | `emit()` inside a scope returns the event, and the row is stored (`AuditEvent` count +1, the pk can be fetched) | FAIL (`unexpectedly None`) | PASS |
| Z-2a | that emit logs no `Zero-credit violation` ERROR on `billing.zero_credit` (`assertNoLogs`) | FAIL | PASS |
| Z-2b | that emit counts no `credit_ledger_anomaly` and no `audit_emit_failures_total` (`audit.metrics.count` patched) | FAIL | PASS |
| Z-3 | `AuditEvent.objects.bulk_create([row])` inside a scope stores the row | ERROR (`ZeroCreditViolation` … `BULK_INSERT`) | PASS |
| Z-4a | control: `CreditLedger.objects.create` inside a scope raises `LEDGER_INSERT`, nothing written | PASS | PASS |
| Z-4b | control: `CreditUsageLog.record` inside a scope raises `USAGE_LOG_INSERT` | PASS | PASS |
| Z-4c | control: `CreditLedger.objects.bulk_create` inside a scope raises `BULK_INSERT` | PASS | PASS |
| Z-4d | control: `_guard_save` for an unlisted append-only model (a stand-in sender `futurecredittable`) inside a scope raises `LEDGER_INSERT` | PASS | PASS |
| Z-4e | control: inside a scope, an AuditEvent update (`QuerySet.update`, `.save()` of a changed field) and delete still raise `ImmutableRecordError` | PASS | PASS |
| Z-5 | inside `transaction.atomic()` and a scope, the emitted row exists before the block ends; after the atomic block rolls back, it is gone | FAIL (the in-block check, `unexpectedly None`) | PASS |

Z-4a to Z-4c repeat parts of B0's `ViolationTests` on purpose, so this file stands on its own. The B0 tests run in the near run.

## 5. Runs, expected

Every targeted set is the new file + G-1 (`ai_processor.tests_metering_chokepoint`, `ai_processor.tests_call_record_static`) + `students.tests_reading_label_static` + `AutoGrader.tests_h271_static_files`. This is staging's G-1/static, so its count is recorded at the run.

1. **Red** on 8aafa47dd + the test file: `FAILED (failures=4, errors=1)`: Z-1, Z-2a, Z-2b and Z-5 FAIL, and Z-3 ERRORs. All the rest pass: Z-4a–e and every G-1/static test.
2. **Green** on the fix: OK, the same count.
3. **Plants**, each on the committed fix, each restored and checked by sha256. Each runs the targeted set; HP-3 also runs `billing.tests.test_zero_credit_scope`.

| Plant | Change in `billing/immutable.py` | Expected kill |
|---|---|---|
| HP-1 | `_guard_save`: the `_moves_credit(sender)` condition removed (save path exempts nothing) | Z-1, Z-2a, Z-2b, Z-5 (Z-3 still passes: it is the bulk path) |
| HP-2 | `bulk_create`: `if _moves_credit(self.model):` becomes `if True:` | Z-3 only (ERROR `BULK_INSERT`) |
| HP-3 | `_moves_credit` always returns False (every model exempt) | Z-4a, Z-4b, Z-4c, Z-4d. In `test_zero_credit_scope`, the ViolationTests ledger/usage/bulk tests: `test_ledger_record_raises`, `test_ledger_objects_create_raises`, `test_usage_log_record_raises`, `test_ledger_bulk_create_raises`, `test_bulk_create_update_conflicts_form_raises_the_scope_error_first`, `test_allow_unsafe_mutation_does_not_lift_the_scope`. Any further B0 failures are recorded as observed. |
| HP-4 | `_moves_credit` becomes an allowlist: `model._meta.model_name in _INSERT_ATTEMPT` (an unlisted model is unguarded) | Z-4d only |

4. **Near run**, under `flock ~/.machine-fullsuite.lock` with h284_deps_env: `billing audit` (the B0 guard tests `test_zero_credit_scope`, `test_zero_credit_registry` and `test_holds_zero_credit` are in billing) + G-1/static, `--parallel 4`. Expected OK; the count is recorded at the run.

Any mismatch is a STOP, reported to the SM.

## 6. Results (2026-11-08; expectations b5fe1a5a, tests c97b380a, fix efc35ddf)

| Run | Code | Result | Matches? | Log |
|---|---|---|---|---|
| Red: new file + G-1 + static | c97b380a (staging 8aafa47d + tests) | Ran 68 (10 + 58), FAILED (failures=4, errors=1). FAIL: Z-1 `unexpectedly None`; Z-2a `Unexpected logs found: ['ERROR:billing.zero_credit:Zero-credit violation: operation=_selftest attempted=LEDGER_INSERT …']`; Z-2b (alarm metrics counted); Z-5 `unexpectedly None`. ERROR: Z-3 `ZeroCreditViolation … BULK_INSERT`. | yes | logs/red_c97b380a.log |
| Green, the same set | WIP | Ran 68, OK | yes | logs/green_WIP.log |
| Fix commit | efc35ddf | black added one blank line on the first attempt; re-staged (format only) | — | — |
| Green, the same set | efc35ddf | Ran 68, OK | yes | logs/green_efc35ddf.log |
| Near run, under `flock ~/.machine-fullsuite.lock`, h284_deps_env, `--parallel 4`: `billing audit` + G-1/static | efc35ddf | Ran 2924, OK (skipped=2) | yes (count recorded at the run, as declared) | logs/near_efc35ddf.log.gz |

**Plants on efc35ddf.** Runner `plant_h299.py`, one plant per command. For each plant it:
- checks the base sha256 (3c3e3060…);
- requires the anchor text to occur exactly once;
- runs the targeted set (HP-3 adds `billing.tests.test_zero_credit_scope`);
- restores the file in a `finally` block and checks the sha256.

Every plant was restored to 3c3e3060…. G-1 and static stayed green in every plant.

| Plant | Result | Matches? | Log |
|---|---|---|---|
| HP-1 | Ran 68, FAILED (failures=4): Z-1, Z-2a, Z-2b, Z-5. Z-3 passed. | yes | logs/plant_HP-1.log |
| HP-2 | Ran 68, FAILED (errors=1): Z-3 | yes | logs/plant_HP-2.log |
| HP-3 | Ran 103, FAILED (failures=9, errors=1). FAIL: Z-4a, Z-4b, Z-4c, Z-4d; B0 `test_ledger_record_raises`, `test_ledger_objects_create_raises`, `test_usage_log_record_raises`, `test_ledger_bulk_create_raises`, `test_allow_unsafe_mutation_does_not_lift_the_scope`. ERROR: B0 `test_bulk_create_update_conflicts_form_raises_the_scope_error_first`. No other B0 failures. | yes. The kill set is exactly the declared 10. The declaration named no FAIL/ERROR form; the update_conflicts test ERRORs because, with the scope check gone, the call reaches `ImmutableRecordError`, which its `assertRaises(ZeroCreditViolation)` does not catch. | logs/plant_HP-3.log |
| HP-4 | Ran 68, FAILED (failures=1): Z-4d | yes | logs/plant_HP-4.log |

No plant survived. H-299 is fixed on `task/h299-zero-credit-audit`, pending the SM's review. Not pushed.
