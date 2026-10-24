# Section 4 (assignments) — verification evidence

Preserved record of the verification run for Section 4 of
`docs/CODEBASE_AUDIT_SECTIONS.md`.

**This lives in the repo deliberately.** The prior session's evidence was
held only in a scratchpad directory, which was wiped between sessions and
had to be re-established from scratch. Evidence that cannot survive a
session restart is not evidence.

**This is NOT the definitive release gate.** It is a snapshot of a dirty,
multi-session working tree. The definitive gate must be a fresh run from
the final merged, committed state — see §9.

---

## 1. Tree under test

| | |
|---|---|
| Branch | `append-only-audit-tables` |
| HEAD commit | `8a208f6ed98bcb493c12c2f30eb0b42c34d4d06f` |
| HEAD subject | *Harden auth and billing, and add the tests that prove it* |
| HEAD date | 2026-09-05T19:31:36+01:00 |
| Working tree | **DIRTY** — 47 tracked-modified, 1 staged, 38 untracked |
| **Fingerprint** | **`58208b0b2a133edacbf79498065b89db045042cd17da1e8aa7fe6f26b04ad396`** |

The fingerprint is `sha256( git diff HEAD ‖ sorted sha256 of every
untracked file )`. It exists because HEAD alone does not identify this
state: three sessions had uncommitted work in the tree.

> **Do not inherit this fingerprint.** Any later evidence report must
> compute and state its own. Reproduce with:
> ```sh
> { git diff HEAD; git ls-files --others --exclude-standard -z \
>     | sort -z | xargs -0 -r sha256sum; } | sha256sum
> ```

### 1a. The tree moved AFTER this run — what is and is not covered

The whole-repository run executed roughly 09:05–09:25 on 2026-09-10. Two
files inside Section 4's own scope were then modified by other sessions
**later the same day**, so they are **outside** the evidence below:

| File | Modified | Change | Owner |
|---|---|---|---|
| `assignments/pdf_renderer.py` | 15:41 | Added `PDFRendererUnavailable` + `_gevent_patched()` guard, refusing to start Chromium in a gevent-patched process | not this session |
| `assignments/signals.py` | 15:46 | H-1 stage 2 — `bump_many` cache-generation scopes (COURSE/USER/SCHOOL) on assignment save | H-1 stream |

Tree fingerprint at the time of writing this record:
`37afa169472dbb8f08c891ae3017d785754658ec54dd1754bf8cbb9512c94107`.

**Re-checked after those changes:** `assignments` alone — **521 tests, OK,
10 skipped**. So the later work does not break Section 4, but it is not
covered by the mutation or whole-repository evidence in §5–§6.

## 2. Infrastructure — asserted, not assumed

Probed at the start of the run, not inferred from settings:

| Component | Version | Proof |
|---|---|---|
| PostgreSQL | **18.6** (Ubuntu 18.6-0ubuntu0.26.04.1) | `connection.ensure_connection()` + `SELECT version()` |
| Redis | **8.0.5** | `cache.set`/`cache.get` round-trip asserted; `delete_pattern` capability confirmed |

Engine `django.db.backends.postgresql`; cache backend
`django_redis.cache.RedisCache`. No LocMem, no SQLite, no mocked backends.

## 3. Static gates

| Gate | Result |
|---|---|
| `makemigrations --check --dry-run` | **No changes detected** (exit 0) |
| `manage.py check` | **No issues (0 silenced)** (exit 0) |
| `pre-commit run --all-files` | **Clean, repo-wide** |

`manage.py check --deploy` reports 65 items. All are local-environment
settings — `DEBUG`, `SECURE_SSL_REDIRECT`, HSTS, cookie flags, plus
drf-spectacular serializer-inference warnings — driven by the local
`.env`. None is a code defect in any section, and none is treated as a
gate failure here.

## 4. Test scope and results

### 4a. Whole repository — fresh database

Stale gate DB explicitly `DROP`ed first, then rebuilt from migrations.
Command: `manage.py test --noinput --parallel 1` under a settings shim
pinning `DATABASES["default"]["TEST"]["NAME"] = "test_gate_303c0ac6"`.

> **3,632 tests — 1 failure, 12 skipped. Exit code 1.**
>
> This is **NOT** a passing repository gate and must not be described as
> one.

- **Failures in `assignments`: 0.** Grepping the run log for
  `assignments.` among `FAIL:`/`ERROR:` lines returns zero.
- The single failure:
  `billing.tests.test_overage_refund_lifecycle.SchoolRefundAttributionTests.test_a_won_chargeback_unblocks_every_teacher_it_blocked`
  — `AssertionError: 500 != 0 : teacher A still owes for a chargeback that was won`.

**Ownership of that failure.** `billing/tests/test_overage_refund_lifecycle.py`
is **untracked** (never committed) and was last written at **09:13 on
2026-09-10 — while this run was executing**. It belongs to the concurrent
billing session's in-flight work on `billing/credit_reversal.py`. It is
not a Section 4 regression, and this session did not modify it.

> This attribution is provisional. It stands **only if the final merged
> run confirms it** (§9).

### 4b. Section plus siblings — after the H-2 teardown fix

Command: `manage.py test assignments users classrooms students --keepdb
--parallel 1`.

> **1,347 tests — OK, 12 skipped. Exit code 0.**

### 4c. Section alone, after the later third-party changes in §1a

> **521 tests — OK, 10 skipped. Exit code 0.**

## 5. Connection leaks / teardown (H-2)

The whole-repository run reached
`Destroying test database for alias 'default'...` with **no** *"database
is being accessed by other users"* error. The non-zero exit in §4a was
purely the billing assertion, **not** a teardown or connection leak.

Section 4 owns four threaded `TransactionTestCase` classes:
`ConcurrentAccessRevocationTest`, `PrerenderConcurrentDispatchTest`,
`AssignmentReadPathLoadTest`, `RealRendererOverloadTest`. Their worker
threads already closed their own connections, which is only half the fix —
the main thread's connection, and any a worker died before releasing,
could still outlive the test. All four now also carry:

```python
def tearDown(self):
    connections.close_all()
    super().tearDown()
```

matching the `ThreadSafeTransactionTestCase` pattern established in
`classrooms`. Confirmation run after that change: §4b, exit code 0.

**H-2 is NOT closed globally.** It stays open until the final merged run
proves: fresh DB create/destroy succeeds; no leaked connections; exit code
0; and no unrelated infrastructure teardown failure.

## 6. Mutation evidence

Re-established against fingerprint `58208b0b…` (the previous session's
records were unverifiable after the scratchpad wipe). Method: remove the
protection, run the relevant suite, then **byte-compare the file against
its pre-mutation backup** to prove the revert.

| Protection removed | Result |
|---|---|
| Enrollment rule — PENDING/WITHDRAWN denied course content | **6 failures** |
| PDF renderer load shedding | **2 failures + 2 errors** |
| Chromium dead-browser detection | **5 errors** |
| Chromium mid-render retry | **1 error** |
| Per-assignment PDF cache invalidation | **2 failures** |

All five confirmed load-bearing. All touched files verified byte-identical
to backup afterwards.

*Method note:* the first attempt at this harness silently mis-matched an
anchor string and produced blank results, and its "reverted cleanly" check
compared against `HEAD` — which legitimately differs, since the tree
carries this section's real work. Both faults were corrected before the
table above was produced. A revert check must compare against a
**pre-mutation backup**, never against HEAD.

## 7. Known-open items for this section

| Item | Status |
|---|---|
| Schema extension (`assignments/schema.py`) | **OPEN — API-contract decision required.** See `docs/decisions/ASSIGNMENT_LIST_SCHEMA_CONTRACT.md`. Implementation deliberately untouched; behaviour pinned by `assignments/tests_schema_extension.py`. |
| Real-AI extraction cost/latency | Observed ~150s and ~33k credits for one real extraction. Deliberately **not** asserted in the normal suite; the real call is opt-in via `RUN_REAL_AI=1`. Tracked as production economics, not a test. |
| H-2 | Addressed within this section's threaded tests; **not** globally closed. |

## 8. Caveat on concurrency

The working tree was being modified by other sessions **during** the
whole-repository run, and again afterwards (§1a). Every number here is a
snapshot tied to a stated fingerprint. None of it is a permanent
repository-wide guarantee.

## 9. What the definitive release gate must be

One fresh, clean, repository-wide run from the **final merged, committed**
state — not this dirty multi-session snapshot — producing:

1. fresh database created and destroyed successfully;
2. `makemigrations --check` clean;
3. `manage.py check` clean;
4. real PostgreSQL and real Redis, asserted;
5. complete test suite;
6. **exit code 0**;
7. no leaked connections;
8. `pre-commit run --all-files` clean;
9. its own fingerprint recorded.

Only that run is definitive release evidence.
