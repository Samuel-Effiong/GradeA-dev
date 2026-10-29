# Epic B0 notes (Release Builder, a0)

## R2 on beta f5d2d577 — WIP 6f0cc829 cherry-picked at 97c381ef

### Survey correction (SM ruling)

The survey's 2a claim "get_ai_model_function is already deleted (H-206 did it)"
was wrong. The survey was run against the r2 tree (which carries the WIP's
deletion), not against beta. On beta f5d2d577 the function is at
`ai_processor/services.py:1276`
(`git grep -n "def get_ai_model_function" f5d2d577`). The WIP cherry-pick
deleted it, breaking 3 pre-existing tests (see below).

### call_record.py decorator (ruling #2 safety check)

The `@recorded_call` decorator (`ai_processor/call_record.py:346-382`) wraps
`execute_graded_task`. Before the function body runs it does:

- `call_record.py:351`: checks `enabled()` — reads a Django setting, no DB
- `call_record.py:353`: creates `_State()` — pure Python, no I/O
- `call_record.py:354`: sets a ContextVar token — no I/O
- `call_record.py:355`: records `time.monotonic()` — no I/O
- `call_record.py:363`: calls `function(*args, **kwargs)` — enters the body

The `record()` function (DB write via `write_row` → `AICallRecord.objects.create`)
runs only AFTER `function()` returns (:373) or raises (:368).

**No DB write, no provider call, no credit read or write before B0's guard
fires.** The guard at `execute_graded_task:5502` runs as the first statement
of the body, before all I/O.

### Survey 2a: model-call invariants (checked on r2 97c381ef, corrected)

| Claim | Status | Evidence |
|---|---|---|
| `OpenAI(` constructed only in services.py | TRUE | services.py:1179 only (non-test, non-benchmark). error_messages.py:131 imports exception classes only. |
| `self.client` used only in `__init__` and `__ai_model` | TRUE | services.py:1179 (init), 1259 (__ai_model). |
| `__ai_model` called only from `execute_graded_task` | TRUE | Two sites: services.py:5612 and 5654, both inside execute_graded_task (starts :5480). |
| `get_ai_model_function` exists on beta | TRUE | beta f5d2d577:ai_processor/services.py:1276. **B0 WIP deletes it (FR-B-01 option a).** |
| Benchmark harness only reassigns client | TRUE | isolation_run8/isolation_harness.py:129 assigns `ai_services.ai_processor.client = OpenAI(...)`. No chat.completions calls, no execute_graded_task calls. |
| Batch 20 / H-206 / H-256 alternative paths | NONE | chat.completions appears only at services.py:1259 (inside __ai_model). call_record.py wraps execute_graded_task but introduces no provider path. |

### Survey 2b: credit writers

| Writer | File:line | Guarded? | Guard |
|---|---|---|---|
| `CreditBucket.consume_credits` → self.save() | models.py:1243-1244 | YES | pre_save + assert_not_zero_credit(WALLET_CONSUME) at models.py:925 |
| `CreditLedger.record()` (×29 sites) | services.py, license_service.py, disputes.py | YES | _guard_save in immutable.py:202 (pre_save) |
| `CreditLedger.objects.bulk_create()` | models.py:1055, services.py:1567 | YES | AppendOnlyQuerySet.bulk_create at immutable.py:145 |
| `CreditUsageLog.objects.bulk_create()` | models.py:1054 | YES | Same immutable.py:145 |
| Refund: bucket.used_credits -= amount; bucket.save() | services.py:1534-1535 | YES | pre_save on CreditBucket |
| Reversal: bucket.used_credits ±= N; bucket.save() | credit_reversal.py:369-370, 474-475 | YES | pre_save on CreditBucket |
| Dispute: bucket.used_credits ±= N; bucket.save() | disputes.py:427-428, 594-597 | YES | pre_save on CreditBucket |
| CreditUsageLog.objects.filter().update(is_refunded=True) | services.py:1568 | NO — flag only | QuerySet.update bypasses pre_save. Sets is_refunded, no credit movement. |
| CreditWallet.objects.filter().update(overage_blocks_used=F+N) | services.py:1631, license_service.py:3144, 4387 | NO — counter only | Not used_credits/total_credits. Each followed by CreditLedger.record(). |
| CreditWallet.objects.filter().update(stripe_customer_id=...) | stripe_live_qa_scenarios.py:176,310; live_qa/scenarios_deep.py:171 | N/A | Live QA scripts only, not credit-touching. |

**No writer of used_credits or total_credits bypasses all guards.**

### Survey 2c: entry guard and call sites

Guard at services.py:5502 — first executable statement after the docstring,
before prompt_version check (:5503), ensure_task_not_cancelled (:5510), access
check, estimate, and provider call.

17 call sites of `self.execute_graded_task(` in services.py:
1310, 1369, 1513, 1690, 2170, 2432, 2621, 2891, 3612, 3891, 4924, 5337, 5444,
5839, 5992, 6039, 6103. No external callers.

### Survey 2d: plan §9 open items

| §9 | Status | Evidence |
|---|---|---|
| 9.1 client attribute | Open. Option (a) approved. AST test passes. |
| 9.2 Benchmark harness | **Closed.** isolation_harness.py:129 only reassigns client. |
| 9.3 Every CreditBucket writer | **Closed.** Survey 2b: no unguarded writer found. |
| 9.4 SUPER_ADMIN also blocked | Open (design choice). Guard fires before the branch at :5612. |
| 9.5 Exception not BaseException | Open (design choice). Per-operation tests assert at boundary. |
| 9.6 Async bulk copy | Open. E2 decides. |
| 9.7 Celery non-propagation | Open. Thread test passes; Celery test to be added. |
| 9.8 Test-helper location | **Closed.** billing/testing/zero_credit.py exists; python tests naming hook passes. |
| 9.9 Gate 10 | Open. Applying Gates 1–9. |
| 9.10 audit.metrics | **Closed.** audit/metrics.py on beta (Epic A). WIP metric tests pass. |

### Survey 2e: WIP test run (development, not gate)

**Ran 36, FAILED (errors=5).** 31 passed, 5 errored.

All 5 errors: `TypeError: missing 'prompt_version'` — beta added prompt_version
as a required kwarg, WIP tests don't pass it.

| # | Failing test | Fragment |
|---|---|---|
| 1 | ViolationTests.test_execute_graded_task_raises_before_any_provider_call | TypeError: missing 'prompt_version' |
| 2 | ViolationTests.test_execute_graded_task_super_admin_unmetered_branch_also_raises | same |
| 3 | ViolationTests.test_refund_inside_a_scope_raises_and_changes_nothing | same |
| 4 | ViolationTests.test_same_calls_succeed_outside_a_scope | same |
| 5 | ViolationTests.test_zero_credit_block_inside_a_metered_pipeline | same |

---

## Pre-existing tests affected (3)

B0 deletes `get_ai_model_function` (FR-B-01). Three pre-existing tests call it.

| # | File:line | Test id | Old call | Error |
|---|---|---|---|---|
| 1 | ai_processor/tests_h256_provider_timeout.py:100 | `ProviderTimeoutTests.test_every_try_carries_the_timeout` | `processor.get_ai_model_function()(**args)` | `AttributeError: 'AIProcessor' object has no attribute 'get_ai_model_function'` |
| 2 | ai_processor/tests_provider_block.py:106 | `ProviderBlockTests.*` (via `call()` helper at :106) | `processor.get_ai_model_function()(**arguments)` | `AttributeError: 'AIProcessor' object has no attribute 'get_ai_model_function'` |
| 3 | ai_processor/tests_reading_pipeline.py:613 | `TheMarksAreMadeAtTheProviderCallTest.*` (via `_call()` at :613) | `services.ai_processor.get_ai_model_function()` | `AttributeError: 'AIProcessor' object has no attribute 'get_ai_model_function'` |

**Fix:** replace `get_ai_model_function()` with `_AIProcessor__ai_model` (the
name-mangled private method). These tests test the provider call itself
(timeouts, ZDR block, reading-pipeline marks), so calling below
execute_graded_task is correct.

### Break run

Checkout the 3 files at f5d2d577 (working-tree only; restore after). Run:
- `ai_processor.tests_h256_provider_timeout`
- `ai_processor.tests_provider_block`
- `ai_processor.tests_reading_pipeline.TheMarksAreMadeAtTheProviderCallTest`

Against code at tip.

- **FAILED** with exactly 3 AttributeError failures for `get_ai_model_function`.
- No other errors.

#### STOP: break-run mismatch (SM ruling at c1f21b1f)

**Observed:** Ran 37, FAILED (errors=64). All 64 are
`AttributeError: 'AIProcessor' object has no attribute 'get_ai_model_function'`.

Breakdown:
- tests_h256_provider_timeout: 4 errors (3 ProviderTimeoutTests + 1 RequestShapeTest;
  all use `ask()` which calls `get_ai_model_function`)
- tests_provider_block: 52 errors (TheHelper 1, TheSwitch 1,
  TheRequestAsksForZeroRetentionOnEveryPath 2 + 48 subTests (8 paths x 6))
- tests_reading_pipeline.TheMarksAreMadeAtTheProviderCallTest: 8 errors
  (all use `_call()` which calls `get_ai_model_function`)

**Cause:** "3" was the number of files, not the number of tests that call through
the deleted escape hatch.

**SM ruling:** ACCEPTED as an error in the expectation; the code is not at fault.
Continue the gate. From this point, write every expectation as exact test ids
(or test id + subTest path) with the error line, not as a count.

---

### Commit trailer slip

Commits 9442c0fb, 1cfb7dfa, c1f21b1f use `Co-Authored-By: Claude Opus 4.6`
instead of the standing rule `Co-Authored-By: Claude Opus 5.5`. SM ruling:
do not rewrite those commits; use 5.5 from the next commit on.

---

## Gate expectations

### Seam check (12 files)

`git diff --name-only f5d2d577..HEAD -- ':(exclude)docs/*'` must yield:

1. `ai_processor/services.py`
2. `ai_processor/tests_metering_chokepoint.py` (new)
3. `ai_processor/tests_h256_provider_timeout.py` (pre-existing)
4. `ai_processor/tests_provider_block.py` (pre-existing)
5. `ai_processor/tests_reading_pipeline.py` (pre-existing)
6. `billing/immutable.py`
7. `billing/models.py`
8. `billing/testing/__init__.py` (new)
9. `billing/testing/zero_credit.py` (new)
10. `billing/tests/test_zero_credit_registry.py` (new)
11. `billing/tests/test_zero_credit_scope.py` (new)
12. `billing/zero_credit.py` (new)

### Red run (revised after test commit 1cfb7dfa — 43 tests)

Wholesale-revert 3 production files to f5d2d577 (working-tree only; restore
after). This removes all 5 guard sites and restores `get_ai_model_function`.
Keep all test files and `billing/zero_credit.py` at tip.

```
git show f5d2d577:ai_processor/services.py > ai_processor/services.py
git show f5d2d577:billing/immutable.py > billing/immutable.py
git show f5d2d577:billing/models.py > billing/models.py
```

**Tests that FAIL (19):**

| # | Test id | Fragment |
|---|---|---|
| 1 | ViolationTests.test_execute_graded_task_raises_before_any_provider_call | `ZeroCreditViolation not raised` |
| 2 | ViolationTests.test_execute_graded_task_super_admin_unmetered_branch_also_raises | `ZeroCreditViolation not raised` |
| 3 | ViolationTests.test_wallet_consume_raises | `ZeroCreditViolation not raised` |
| 4 | ViolationTests.test_ledger_record_raises | `ZeroCreditViolation not raised` |
| 5 | ViolationTests.test_ledger_objects_create_raises | `ZeroCreditViolation not raised` |
| 6 | ViolationTests.test_ledger_bulk_create_raises | `ZeroCreditViolation not raised` |
| 7 | ViolationTests.test_usage_log_record_raises | `ZeroCreditViolation not raised` |
| 8 | ViolationTests.test_bucket_balance_write_raises_and_bookkeeping_save_does_not | `ZeroCreditViolation not raised` |
| 9 | ViolationTests.test_allow_unsafe_mutation_does_not_lift_the_scope | `ZeroCreditViolation not raised` |
| 10 | ViolationTests.test_bulk_create_update_conflicts_form_raises_the_scope_error_first | `ZeroCreditViolation not raised` |
| 11 | ViolationTests.test_metric_emitted_once_per_violation | `ZeroCreditViolation not raised` |
| 12 | ViolationTests.test_violation_still_raises_when_metric_and_logging_helpers_fail | `ZeroCreditViolation not raised` |
| 13 | ViolationTests.test_swallowing_the_violation_does_not_relax_the_scope | `ZeroCreditViolation not raised` |
| 14 | ViolationTests.test_refund_inside_a_scope_raises_and_changes_nothing | `ZeroCreditViolation not raised` |
| 15 | ViolationTests.test_zero_credit_block_inside_a_metered_pipeline | `ZeroCreditViolation not raised` |
| 16 | ThreadIsolationTests.test_scope_in_one_thread_does_not_leak_to_another | `scoped_raised` 0 != 200 |
| 17 | ThreadIsolationTests.test_n_threads_each_in_own_scope_all_raise | `raised_counts` 0 != 50 |
| 18 | AdversarialTests.test_charge_after_nested_scope_exits_succeeds | `ZeroCreditViolation not raised` |
| 19 | MeteringChokepointTests.test_escape_hatch_is_gone | `True is not false` (beta has `get_ai_model_function`) |

**Tests that PASS (24):**

| # | Test id | Why it survives |
|---|---|---|
| 1 | ScopeMechanicsTests.test_unknown_operation_is_rejected | scope mechanics in zero_credit.py (at tip) |
| 2 | ScopeMechanicsTests.test_register_makes_a_name_usable_and_is_idempotent | scope mechanics |
| 3 | ScopeMechanicsTests.test_register_rejects_blank_names | scope mechanics |
| 4 | ScopeMechanicsTests.test_scope_state_is_restored_on_normal_exit | scope mechanics |
| 5 | ScopeMechanicsTests.test_scope_state_is_restored_when_the_block_raises | scope mechanics |
| 6 | ScopeMechanicsTests.test_nested_scopes_stack_and_unwind | scope mechanics |
| 7 | ScopeMechanicsTests.test_decorator_enters_the_scope_on_every_call | scope mechanics |
| 8 | ScopeMechanicsTests.test_scope_is_not_inherited_by_a_new_thread | scope mechanics |
| 9 | ViolationTests.test_same_calls_succeed_outside_a_scope | ops outside scope, no guards needed |
| 10 | OperationHelperTests.test_a_correct_zero_credit_operation_passes | helper checks scope entry + no provider + no credit change; op does none |
| 11 | OperationHelperTests.test_an_operation_that_never_enters_the_scope_fails | helper checks _entry_recorder, no guard |
| 12 | OperationHelperTests.test_an_operation_that_charges_outside_a_scope_fails | consume_credits outside scope changes credit state either way; helper detects |
| 13 | OperationHelperTests.test_an_unregistered_operation_fails | registry check, no guard |
| 14 | CeleryNonPropagationTests.test_scope_does_not_propagate_across_contextvar_boundary | in_zero_credit_scope() only |
| 15 | CeleryNonPropagationTests.test_scope_not_inherited_by_thread_simulating_celery_worker | in_zero_credit_scope() only |
| 16 | AdversarialTests.test_charge_after_all_scopes_exit_succeeds | consume_credits after scope exit succeeds either way |
| 17 | PerformanceMeasurementTests.test_guard_check_overhead | assert_not_zero_credit in zero_credit.py (at tip) |
| 18 | MeteringChokepointTests.test_provider_client_is_only_built_in_services | AST scan, structure unchanged |
| 19 | MeteringChokepointTests.test_self_client_is_only_used_inside___ai_model | AST scan |
| 20 | MeteringChokepointTests.test___ai_model_is_called_only_from_execute_graded_task_twice | AST scan (beta has same 2 call sites) |
| 21 | MeteringChokepointTests.test_no_other_module_reaches_the_private_model_method | AST scan (excludes services.py) |
| 22 | MeteringChokepointTests.test_only_the_benchmark_harness_assigns_to_client_outside_the_class | AST scan |
| 23 | BehaviouralSpyTests.test_provider_call_only_reachable_through_execute_graded_task | patches __ai_model, calls execute_graded_task; beta path same |
| 24 | ZeroCreditRegistryTests.test_every_registered_operation_has_a_helper_test | scans zero_credit.py (at tip) |

**Totals:** 19 FAIL + 24 PASS = 43.

Corrections from original NOTES:
- Removed #17 `OperationHelperTests.test_an_operation_that_charges_outside_a_scope_fails` from FAIL (PASS: helper detects credit change regardless of guards).
- Added `MeteringChokepointTests.test_escape_hatch_is_gone` to FAIL (wholesale services.py revert restores the escape hatch).
- Added 6 new tests from 1cfb7dfa: 2 FAIL (n_threads, nested_scope), 4 PASS (celery x2, all_scopes_exit, guard_overhead, behavioural spy).

#### STOP: red-run fragment mismatch (SM ruling at 8a993b39)

**Observed:** Ran 43, FAILED (failures=18, errors=1). 19 FAIL + 24 PASS.
All 19 test ids match. All 24 PASS ids match. One fragment mismatch:

- **Test:** ViolationTests.test_bulk_create_update_conflicts_form_raises_the_scope_error_first
- **Predicted:** FAIL with `ZeroCreditViolation not raised`
- **Actual:** ERROR with `ImmutableRecordError: CreditLedger is append-only; bulk_create(update_conflicts=True) would rewrite existing rows.`
- **Cause:** the zero-credit guard (immutable.py:146 on tip) fires before the append-only guard (immutable.py:137 on beta). Removing the zero-credit guard exposes the append-only guard, which raises `ImmutableRecordError` instead. ERROR is the correct outcome for a test whose purpose is guard ordering.

**SM ruling:** ACCEPTED. The test is red for exactly the reason it was written.
Lesson: when a test's purpose is ordering ("raises X first"), predict the
exception the next guard down will raise, not just "X not raised".

### Green run

Expected: **Ran 43, OK**.

### Near suite

Labels: `billing`, `ai_processor`, `assignments`.

DiscoverRunner count at tip (after test commit 1cfb7dfa): **4614**.
Expected: **Ran 4614, OK** (skipped count may vary).

### Makemigrations

`python manage.py makemigrations --check --dry-run` must exit 0. No schema
changes.

### Mutant battery (M1–M12)

Each mutant reverts or weakens one protection. The named test must FAIL.

| # | Mutant | Anchor | Killing test(s) | Expected fragment |
|---|---|---|---|---|
| M1 | Remove entry guard in execute_graded_task | `assert_not_zero_credit(MODEL_CALL)` in execute_graded_task (services.py:5502) | test_execute_graded_task_raises_before_any_provider_call | `ZeroCreditViolation not raised` |
| M2 | Move that guard to after __ai_model | Move the assert_not_zero_credit line to after `response = self.__ai_model(...)` at :5654 | test_execute_graded_task_raises_before_any_provider_call | provider.assert_not_called `AssertionError: Expected call count 0` |
| M3 | Remove consume_credits guard | `assert_not_zero_credit(WALLET_CONSUME)` in CreditWallet.consume_credits (models.py:925) | test_wallet_consume_raises | `ZeroCreditViolation not raised` |
| M4 | Skip check before the adding early return in _guard_save | `assert_not_zero_credit(...)` block in _guard_save (immutable.py:201-204) | test_ledger_record_raises, test_ledger_objects_create_raises, test_usage_log_record_raises | `ZeroCreditViolation not raised` |
| M5 | Remove the bulk_create guard | `assert_not_zero_credit(BULK_INSERT)` in AppendOnlyQuerySet.bulk_create (immutable.py:145) | test_ledger_bulk_create_raises | `ZeroCreditViolation not raised` |
| M6 | Scope does not reset in finally | Remove `_active_scopes.reset(token)` from zero_credit_scope (zero_credit.py:119) | test_scope_state_is_restored_when_the_block_raises | `assertIsNone` fails (scope still set) |
| M7 | Scope replaced by set/unset instead of stack | Replace `_active_scopes.set(... + (operation,))` with `_active_scopes.set((operation,))` (zero_credit.py:115) | test_nested_scopes_stack_and_unwind | inner scope unwind shows None instead of OP |
| M8 | Registry accepts unknown names | Remove the `if operation not in ZERO_CREDIT_OPERATIONS` check (zero_credit.py:107-111) | test_unknown_operation_is_rejected | `ValueError not raised` |
| M9 | Re-add get_ai_model_function | Re-add `def get_ai_model_function(self): return self.__ai_model` at services.py:1276 (beta definition) | test_escape_hatch_is_gone | `assertTrue` fails (hasattr returns True) |
| M10 | Add a stray self.client.chat reference | Add `self.client.chat.completions` reference in a new method in AIProcessor | test_self_client_is_only_used_inside___ai_model | offenders list not empty |
| M11 | Metric emit raises and is not caught | Remove the `except Exception` around audit_metrics.count in _report (zero_credit.py:171-172) | test_violation_still_raises_when_metric_and_logging_helpers_fail | `RuntimeError: metrics down` (instead of ZeroCreditViolation) |
| M12 | Remove CreditBucket pre_save guard | Remove guard_bucket_save or disconnect the pre_save.connect (models.py:1695-1697) | test_bucket_balance_write_raises_and_bookkeeping_save_does_not | `ZeroCreditViolation not raised` |

---

## Tests to add (SM rulings 5–6)

### Gate 3 expansion: 200 rounds, N-thread isolation

Increase ThreadIsolationTests.ROUNDS from 100 to 200. Add a second test:
N threads (8), each in its own scope, each doing consume_credits. All must
raise. Zero cross-thread interference.

### Celery non-propagation (§9.7)

Assert that `in_zero_credit_scope()` returns None inside a task body dispatched
from inside a scope. Use the ContextVar boundary (task_prerun restores only the
request id, not arbitrary ContextVars), not a real broker.

### Gate 4 gaps

- Charge after nested scope exits (new test).
- Swallowed violation + second charge (existing: test_swallowing_the_violation_does_not_relax_the_scope).
- Dispatch case (the Celery non-propagation test above).

### §4.5 behavioural spy

Patch `_AIProcessor__ai_model` and call one method per feature family. Every
call passes through execute_graded_task (spy on it).

Feature families (from the 17 call sites):
- grade_assignment (:1310)
- extract_answer (:1369)
- extract_assignment (:1513)
- formatted_grade (:1690)
- generate_assignment (:2170)
- student_summary (:2432)
- weekly_course_summary (:2621)

### Gate 6: performance measurement

100k iterations of `assert_not_zero_credit("MODEL_CALL")` outside a scope,
measured in ns per call. Record baseline and with-guard times. Measurement only,
no pass/fail threshold.
