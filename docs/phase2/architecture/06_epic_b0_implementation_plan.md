# 06 — Epic B0 Implementation Plan (zero-credit scope; remove the unused model escape hatch)

**Status:** plan for review. Nothing here is implemented.
**Companion to:** [05_epics_b_to_i_roadmap.md](05_epics_b_to_i_roadmap.md) (§2.4 B0/B1 split, §7 plan contents),
[01a_requirements_specification.md](01a_requirements_specification.md) (FR-B-01, FR-B-10, NFR-CRD-03, NFR-TST-03/06/07),
[03_architecture.md](03_architecture.md) (§2.2, X-8),
[04_epic_a_implementation_plan.md](04_epic_a_implementation_plan.md) (style, audit conventions).
**Size:** S (about 2–3 working days, one engineer).
**Code facts are from beta at 4b902fc.** Audit code (`audit/`) is referenced from
`task/epic-a-land` (c75efcc), which is not on beta yet. Anything I did not confirm is
marked **[UNVERIFIED]** and collected in §9.

B0 adds no behaviour a user can see. It delivers the guard that C, E and F need
before they can claim "provably zero-credit" (FR-B-10), and it closes the one
gap in FR-B-01. It does **not** touch reservation, `AIJob`, estimation or
dispersal; those are B1.

---

## 0. Corrections to the prior documents

Checked against beta while drafting.

### 0.1 Line references in 03_architecture §2.2 are stale

03_architecture cites `services.py:4137` and `:4217` for the two `__ai_model`
call sites, and `:4205` for the balance check. On beta they are at
`ai_processor/services.py:4467` (the unmetered `SUPER_ADMIN` branch) and
`:4546` (the metered path), with the check-then-execute balance test just
above at `:4531–4538`. `execute_graded_task` starts at `:4350`; `__ai_model` is
at `:662`. The structure the document describes is correct; only the numbers
moved. This plan uses the beta numbers.

### 0.2 FR-B-01 is true, but `client` is a wider escape hatch than `get_ai_model_function()`

- `get_ai_model_function()` (`ai_processor/services.py:729–730`) returns the
  name-mangled `__ai_model`. **No caller exists anywhere in `*.py`** (grep of the
  whole tree, 2026-09-26). Deleting it is safe.
- But `AIProcessor.client` (`services.py:657`) is a **public** attribute holding
  the OpenRouter client. `client.chat.completions.create` is reachable from any
  module that imports the `ai_processor` singleton, with no metering. Today it is
  referenced only inside `__ai_model` (`:685`, `:708`) and assigned once outside
  the class by a benchmark harness
  (`ai_processor/benchmark/isolation_run8/isolation_harness.py:129`), which swaps
  in its own `OpenAI(...)` with a 300s timeout and 3 retries. **Verified
  (2026-09-26, read of the whole `isolation_run8/` directory):** the harness only
  reassigns `client`; nothing under it calls `client.chat.completions`,
  `__ai_model` or `execute_graded_task` directly. The AST test's allow-list for
  assignments to `.client` outside `AIProcessor` is therefore exactly this one
  path. Side finding: production constructs the client with no timeout or retry
  cap (the harness docstring says a half-open connection hung a run for 2 hours);
  not a B0 matter, reported to the Feature Lead.
- Only `services.py` imports and constructs `OpenAI` in non-test code
  (`AutoGrader/error_messages.py:96` imports exception classes only).

**Decision (Feature Lead, 2026-09-26): option (a) approved.**  *(Original framing:)* the requirement wording is
"no component reaches a model provider directly". Removing the function alone
leaves `client` open. Options: **(a) delete the function and add a test that
`.client` is used only inside `__ai_model` (AST-checked); leave the attribute
name alone**, because renaming it to `_client` breaks the benchmark harness and
is churn unrelated to metering; or (b) also rename to `_client` and update the
harness. I recommend (a).

### 0.3 The provider call happens *before* the charge

In `execute_graded_task` the order is: access check → estimate → balance check →
`__ai_model(...)` (network call, `:4546`) → `transaction.atomic(): wallet.consume_credits(...)`
(`:4560`). So a guard placed only at `consume_credits` or at the ledger write would
fire **after** the provider has already been called and paid for. For a
zero-credit operation that would be the worst outcome: real provider cost, no
charge, then an exception. **The guard must sit at `execute_graded_task` entry**
(before any of that), and the ledger-level guards are the second line.

The `SUPER_ADMIN` branch (`:4467`) calls the provider with no ledger write at all.
An entry guard also covers it; a ledger-only guard would not.

### 0.4 The existing append-only seam is the right place for the ledger guard

`billing/immutable.py` already gives one place per write shape:

| Write shape | Existing seam | Notes |
|---|---|---|
| `CreditLedger.record()` / `CreditUsageLog.record()` / `.objects.create()` / `.save()` | `pre_save` receiver `_guard_save` (`immutable.py:~188`) | Insert path returns early today (`instance._state.adding`); the zero-credit check goes **before** that early return |
| `.objects.bulk_create(...)` | `AppendOnlyQuerySet.bulk_create` (`immutable.py:~118`) | Used on the spend path (`billing/models.py:1039–1040`) and the refund path (`billing/services.py:1389`) |
| `CreditBucket.consume_credits` | `CreditBucket.save(update_fields=["used_credits", ...])` (`models.py:~1213`) | Not an append-only model. See §3.3 |

This matters because 04 §0.6 already found that `record()` alone is an
incomplete chokepoint for credit transactions: `bulk_create` bypasses `save()`.
The same trap applies here, so the guard hooks the queryset method too.

### 0.5 Tension for Epic E to resolve, not B0

NFR-PRF-08 says bulk copy returns `202` with a job id. 03a §4.5 says
"Bulk copy is synchronous and zero-credit". If bulk copy becomes an async task, the scope
must be entered **inside the task body**; a `ContextVar` does not cross the Celery
hop (§3.4). B0 supplies a decorator that works for both, and the E2 plan must
pick one.

---

## 1. Scope

### In scope

1. `billing/zero_credit.py`: the scope, its exception, guard functions, and a
   registry of declared zero-credit operations.
2. Guard call sites: `execute_graded_task` entry, `CreditWallet.consume_credits`
   entry, the `CreditLedger`/`CreditUsageLog` insert seam, and
   `AppendOnlyQuerySet.bulk_create`.
3. Delete `AIProcessor.get_ai_model_function()` (FR-B-01) and add the
   "no other call path" test.
4. Test helpers so each later epic can write its per-operation test in a few lines.
5. The recorded mutation matrix (NFR-TST-07).

### Not in scope (deliberately)

- Wrapping any real operation. None of the FR-B-10 operations exist yet except tag
  creation on the old `Topic` model, which C1 replaces. Each owning epic wraps its
  own operation and adds its own test. B0 ships **no production consumer**, so it
  is dark by construction (NFR-MNT-05).
- Reservation, `AIJob`, estimation, dispersal: B1.
- Any grant, purchase, expiry or webhook path. Those are outside any scope and are
  unaffected.
- Renaming `client` (§0.2 option (b)).

---

## 2. Build order

1. `billing/zero_credit.py` + unit tests (§3, §6.1). No other file changes.
2. Guard hooks in the four call sites (§3.3), each with its own mutation test.
3. Delete `get_ai_model_function()`; add the AST test (§4).
4. Registry and helper (`billing/testing/zero_credit.py`) (§5).
5. Gate evidence: mutation matrix, concurrency and adversarial runs, full-suite
   baseline (§7).

Steps 1 → 2 are a hard sequence. Step 3 is independent and can be a separate
commit; it can land first if review of 1–2 is slow.

---

## 3. Design

### 3.1 API

```python
# billing/zero_credit.py

class ZeroCreditViolation(Exception):
    """A credit-touching write or a model call was attempted inside a
    zero-credit scope. Carries `operation` and `attempted` (a stable code)."""

@contextlib.contextmanager
def zero_credit_scope(operation: str): ...

def zero_credit(operation: str):
    """Decorator form. Enters the scope on each call, so it also works as the
    first line of a Celery task body."""

def in_zero_credit_scope() -> str | None:
    """Current innermost operation name, or None."""

def assert_not_zero_credit(attempted: str) -> None:
    """Called by the guard sites. Raises ZeroCreditViolation if a scope is open."""
```

`attempted` is a code, not free text: `MODEL_CALL`, `WALLET_CONSUME`,
`LEDGER_INSERT`, `USAGE_LOG_INSERT`, `BULK_INSERT`, `BUCKET_MUTATION`.

### 3.2 State: a `ContextVar`, nestable, always restored

Same idiom as `billing/refunds.py` (`_active_task_ids`), for the same reason:
`ai_processor` is a process-wide singleton, so instance state would leak across
requests. The var holds a tuple of operation names (innermost last). Entering pushes,
`finally` resets via the token, so an exception inside the block, or a
`ZeroCreditViolation` itself, always restores the previous state. Nested scopes are
allowed; an outer scope is never weakened by an inner one (the guard is "any scope
open").

### 3.3 Guard call sites

| Site | File (beta) | Guard | `attempted` |
|---|---|---|---|
| `AIProcessor.execute_graded_task` | `ai_processor/services.py:4350`, first statement, **before** `ensure_task_not_cancelled` | `assert_not_zero_credit("MODEL_CALL")` | `MODEL_CALL` |
| `CreditWallet.consume_credits` | `billing/models.py:864`, first statement inside the method (before the row lock) | same | `WALLET_CONSUME` |
| `CreditLedger` / `CreditUsageLog` insert | `billing/immutable.py` `_guard_save`, checked before the `adding` early return | same | `LEDGER_INSERT` / `USAGE_LOG_INSERT` |
| `AppendOnlyQuerySet.bulk_create` | `billing/immutable.py` | same, based on `self.model` | `BULK_INSERT` |

`CreditBucket` balance changes: `CreditBucket.consume_credits`
(`models.py:1213`) writes `used_credits` by `save(update_fields=…)`. Every path that
moves a bucket also writes ledger rows, so the ledger guards catch it, but only
**[UNVERIFIED]**: I did not enumerate every `used_credits`/`total_credits` writer.
`QuerySet.update()` on `CreditBucket` bypasses both `save` and `pre_save`
(`billing/services.py:1038` and `credit_reversal.py:321` lock buckets). Step 2
therefore includes an audit: list every writer of `CreditBucket.used_credits` /
`total_credits`, and either show each also writes a ledger row or add a
`BUCKET_MUTATION` guard on `CreditBucket`'s `pre_save`. Any writer found that does
neither is a finding for the Feature Lead, not something B0 silently fixes.

### 3.4 What the scope does not cover, stated up front

- **Celery hops.** A `ContextVar` set in a request is **not** propagated into a task
  it dispatches (`AutoGrader/celery_signals.py` restores the request id only). A
  zero-credit operation that runs async must enter the scope in the task body. The
  decorator exists for that. A test asserts the non-propagation so nobody assumes
  otherwise.
- **Raw SQL, `QuerySet.update()`, `_raw_delete()`, other processes.** Same limits as
  the append-only guard's own docstring (`immutable.py`).
- **`allow_unsafe_mutation()`** does not disable this guard. It is a different
  concern (row immutability); a data-repair session must not silently run inside a
  zero-credit scope.

### 3.5 On violation

1. Raise `ZeroCreditViolation` (an `Exception`, not `BaseException`, so Celery and
   Django handle it normally and `billing_refund_scope` treats it as a failed block).
2. `logger.error` with `operation`, `attempted`, and the `trace_id` if present.
   No student or user identifiers beyond ids (FR-A-04).
3. Emit `credit_ledger_anomaly` with `tags={"kind": "zero_credit_violation"}` via
   `audit.metrics.count` (exists on `task/epic-a-land`; the metric is documented
   there as "any occurrence: page immediately (P0)"). Import lazily and swallow
   import or emit failure so a metrics problem never masks the violation. On beta
   without Epic A, the import fails and only the log remains; the test covers both.

The exception is **not** swallowed anywhere by B0. Existing broad `except
Exception` blocks in callers (e.g. the pipelines around `services.py:1139`) may
catch it; the per-operation tests therefore assert on the ledger and the exception at
the operation boundary, not just "no exception escaped".

### 3.6 Interaction with `billing_refund_scope`

Orthogonal. A zero-credit operation makes no charge, so the refund scope has
nothing to reclaim. If both are open (a zero-credit step inside a metered pipeline),
the zero-credit scope only forbids charges **while it is open**; the outer
pipeline's charges outside it are untouched. A test covers a zero-credit block
nested inside a metered pipeline and the reverse (a metered call attempted inside
the inner block raises, and the outer refund scope still refunds earlier charges).

---

## 4. FR-B-01: remove the escape hatch, prove no other path

**Change:** delete `AIProcessor.get_ai_model_function` (`ai_processor/services.py:729–730`).
Nothing calls it.

**Test** (`ai_processor/tests_metering_chokepoint.py`), static, parses the source
with `ast`:

1. `get_ai_model_function` does not exist on `AIProcessor`.
2. In non-test, non-benchmark `*.py` files, `OpenAI(` is constructed and
   `chat.completions` is referenced only in `ai_processor/services.py`.
3. Inside `services.py`, `self.client` is referenced only in `__ai_model`.
4. `self.__ai_model` (mangled `_AIProcessor__ai_model`) is called from exactly two
   places, both inside `execute_graded_task`.
5. A behavioural spy: patch `_AIProcessor__ai_model` with a recording fake and call
   one method per feature family; assert every call passes through
   `execute_graded_task` (spy on it). **[UNVERIFIED]** which methods are the right
   representatives; pick from the ~10 `execute_graded_task` call sites listed by
   grep (`services.py:765, 823, 966, 1142, 1609, 1835, 2009, 2601, 2871, 3820,
   4210, 4316, 4674, 4826, 4872, 4935`).

Mutation (§6.2): re-adding the function, or adding a stray `self.client.chat…` call,
fails the specific test.

Excluded from the scan by explicit path list (not by glob), with the reason in a
comment: `ai_processor/benchmark/`, tests, `docs/`.

---

## 5. What later epics get: registry and test helper

- `ZERO_CREDIT_OPERATIONS`: a module-level set of declared operation names in
  `billing/zero_credit.py`. `zero_credit_scope(operation)` rejects a name not in the
  set, so a typo cannot pass a test with an unregistered scope. Each epic adds its
  names in its own change (from FR-B-10 / QA-B-02: `assignment_copy`,
  `assignment_bulk_copy`, `library_copy_in`, `library_copy_out`, `lesson_copy`,
  `lesson_upload`, `feedback_edit`, `tag_*`).
  B0 registers one name only, `"_selftest"`, used by its own tests.
- `billing/testing/zero_credit.py::assert_operation_is_zero_credit(operation, run)`:
  snapshots ledger, usage-log and bucket state and provider-call count, runs the
  callable, asserts all unchanged, and asserts the operation ran inside a scope by
  spying on `zero_credit_scope`. This is the "test per operation" of FR-B-10, one line
  in each epic.
- A meta-test `billing/tests/test_zero_credit_registry.py`: every registered name
  other than `_selftest` appears in at least one call to the helper, found by
  AST-scanning the test tree. It starts trivially green and bites when C/E/F register
  names without a test.

---

## 6. Test plan, mapped to acceptance criteria

### 6.1 Acceptance mapping

| Requirement | Test |
|---|---|
| FR-B-01 no other call path | §4 tests 1–5 |
| FR-B-10 "raises if any ledger write occurs" | scope open, then each of: `CreditLedger.record`, `CreditUsageLog.record`, `.objects.create`, `.objects.bulk_create` (both models), `wallet.consume_credits`, `SubscriptionService.refund_credits`, `execute_graded_task` (teacher, student, and `SUPER_ADMIN` branches) → each raises `ZeroCreditViolation` with the right `attempted`, and **no row is written and no provider call is made** (assert on `_AIProcessor__ai_model` call count = 0 and ledger count unchanged) |
| FR-B-10 "a mutation removing the scope fails the test" | §6.2 matrix; plus a test using the helper on a deliberately mis-wrapped fake operation that charges without entering the scope: the helper's snapshot assertion fails |
| NFR-CRD-03 "no zero-credit path can bill" | same tests |
| Nesting / restore | exception inside block, violation inside block, nested scopes: state restored to the previous value each time |
| Outside a scope | the same calls succeed unchanged: full existing `billing/`, `ai_processor/`, `assignments/` upload-billing suites green (Gate 1) |
| Celery non-propagation (§3.4) | dispatch a task with `CELERY_TASK_ALWAYS_EAGER` off from inside a scope using the real broker path **[UNVERIFIED: whether the test settings allow it; else assert on `task_prerun` restoring only the request id]** |
| Metric (§3.5) | with `audit.metrics.count` patched, one violation emits exactly one `credit_ledger_anomaly` with `kind=zero_credit_violation`; with the module missing or raising, the violation still raises |

### 6.2 Mutation matrix (NFR-TST-07)

Each row removes or weakens one protection; the named test must fail. Counts recorded
in the evidence doc.

| # | Mutation | Test that must fail |
|---|---|---|
| M1 | Remove entry guard in `execute_graded_task` | model-call-in-scope test (provider count > 0) |
| M2 | Move that guard to after `__ai_model` | same (provider called once) |
| M3 | Remove `consume_credits` guard | wallet-consume test |
| M4 | Skip check before the `adding` early return in `_guard_save` | `record`/`create` tests |
| M5 | Remove the `bulk_create` guard | bulk-create tests, refund test |
| M6 | Scope does not reset in `finally` | exception-restores-state test |
| M7 | Scope replaced by set/unset instead of stack | nesting test |
| M8 | Registry accepts unknown names | registry test |
| M9 | Re-add `get_ai_model_function` | §4 test 1 |
| M10 | Add a stray `self.client.chat…` call | §4 test 3 |
| M11 | Metric emit raises and is not caught | metric-failure test |

### 6.3 Gate-specific tests

- **Concurrency (Gate 3, NFR-TST-06):** real threads, barrier-synchronised, real
  Postgres. Thread A inside a scope, thread B doing a metered `consume_credits` at
  the same instant. A raises, B succeeds and its ledger row exists; repeated 200×
  to catch a leak of scope state across threads. Also N threads each in their own
  scope. The 200× loop must show zero cross-thread interference.
- **Real infrastructure (Gate 7):** the FR-B-10 tests above run on real Postgres,
  not SQLite or mocks; the ledger-count assertions use the real table.
- **Adversarial (Gate 4):** written from the bypass side: `update_conflicts=True`
  bulk insert, `objects.create` instead of `record`, nested scope exit followed by a
  charge, re-raising and swallowing `ZeroCreditViolation` in a broad `except` inside
  the scope and then attempting a second charge, and a scope entered in a request
  followed by a Celery dispatch (documented non-propagation).

---

## 7. The six items from 05 §7

**1. Audit actions added.** None. B0 adds no `AuditAction` and no `retention_class`
mapping. The violation surfaces as the existing `credit_ledger_anomaly` metric.
(The vocabulary gaps in 05 §2.5 are the Feature Lead's audit-vocabulary task.)

**2. Student deletion/export walk (NFR-CMP-04).** No new tables, no new
student-linked data. Nothing to add.

**3. `10 Gates.md` applicability.**

| Gate | Applies | Basis |
|---|---|---|
| 1 Baseline / regression | Yes | Full `billing/`, `ai_processor/`, `assignments/` suites before and after; guard sites are on the spend path |
| 2 Mutation | Yes | §6.2, 11 mutations |
| 3 Concurrency | Yes | §6.3, scope isolation across threads |
| 4 Adversarial | Yes | §6.3 bypass attempts |
| 5 Failure / recovery | Partly | Exceptions inside scopes, metric failure never masks the violation. PostgreSQL/Redis/Stripe/provider outages are **N/A**: B0 adds no I/O of its own |
| 6 Stress / scale | Partly | Measure the added cost per ledger insert and per `execute_graded_task` call (a `ContextVar.get()`): 100k iterations before/after, report ns per call. Large-batch scale is N/A |
| 7 Real infrastructure | Yes | Real Postgres for all ledger tests; Redis and Celery are N/A except the non-propagation check |
| 8 Live / end-to-end | **N/A** | No endpoint, worker or deployed behaviour changes; nothing consumes the scope yet. First applicable in C1/E2 |
| 9 Security / isolation | Partly | No new role or tenant surface. The guard is not an authorisation control. Billing isolation is unaffected (asserted by the existing `test_credit_endpoint_tenant_isolation.py` and `test_billing_transaction_tenant_isolation.py` staying green). Role/school/teacher/student isolation matrices: **N/A**, nothing added |
| 10 | **Not defined** | The file's title says "10-Gate" but it defines Gates 1–9 only (**verified**: it ends at Gate 9). I applied 1–9 and flag the gap in §9 |

**4. Migrations.** None. No schema change; nothing to classify under
`docs/MIGRATIONS.md`, no contract step.

**5. Feature flag / dark shipping (NFR-MNT-05).** Not needed. The scope has no
production consumer until C1/E2/F/D wrap operations, and each of those ships behind
its own flag. B0's only always-on effects are a `ContextVar` read at the guard sites
and the deleted function. Both are exercised by the full existing suite (Gate 1).

**6. Role-by-resource matrix rows (NFR-SEC-01).** None added: B0 introduces no
resource or endpoint. Recorded here so the absence is deliberate.

---

## 8. Files touched

| File | Change |
|---|---|
| `billing/zero_credit.py` | new |
| `billing/testing/__init__.py`, `billing/testing/zero_credit.py` | new (test helper; **[UNVERIFIED]** whether a non-test-named module in the app is picked up by the "python tests naming" hook or the test runner; the fallback is `billing/tests/zero_credit_helpers.py`) |
| `billing/immutable.py` | guard in `_guard_save` and `AppendOnlyQuerySet.bulk_create` |
| `billing/models.py` | guard at `CreditWallet.consume_credits` entry |
| `ai_processor/services.py` | guard at `execute_graded_task` entry; delete `get_ai_model_function` |
| `billing/tests/test_zero_credit_scope.py`, `test_zero_credit_registry.py` | new |
| `ai_processor/tests_metering_chokepoint.py` | new |
| `docs/evidence/epic-b0/` | evidence doc |

`ai_processor/services.py` is a 5,189-line file also touched by other streams;
B0's edit there is two small hunks. Rebase before merge.

---

## 9. Open items and unverified claims

1. **`client` attribute** (§0.2): option (a) vs (b). Default (a).
2. ~~**Benchmark harness** (§0.2)~~ **Resolved 2026-09-26**: harness only reassigns `client`; see §0.2.
3. **Every writer of `CreditBucket.used_credits` / `total_credits`** (§3.3): audit
   in step 2; any writer that neither writes a ledger row nor passes through
   `save()` is reported, not fixed, by B0.
4. **Guard also blocks the unmetered `SUPER_ADMIN` model call** (§0.3). This is
   stricter than "no ledger write". I chose it because a zero-credit operation
   should never reach a model at all. **Consequence for Epic D:** if lesson upload
   needs an AI extraction call (BE-D-03 says upload is zero-credit; whether upload
   parses via a model is **[UNVERIFIED]**), that call cannot be inside the scope.
   D's plan must resolve it.
5. **`ZeroCreditViolation` as `Exception`**: can be swallowed by a broad `except`
   in a caller. Mitigated by tests asserting at the operation boundary; a
   `BaseException` subclass was rejected because it would bypass Celery and
   Django error handling.
6. **Async bulk copy** (§0.5): E2 decides sync vs 202 and where the scope is
   entered.
7. **Celery non-propagation test** (§6.1) may be limited by test settings.
8. **Test-helper location** (§8).
9. **Gate 10**: `10 Gates.md` defines Gates 1–9; the team rule refers to the file as
   "10 Gates". I applied 1–9 and flag the mismatch.
10. **`audit.metrics` absent on beta**: the metric emit is a soft dependency until
    Epic A merges. B0 must not merge ahead of Epic A unless the lazy-import
    fallback is accepted.
11. **Effort** (S, 2–3 days) excludes independent verification.
