# Cache generation bump inside a transaction: the commit race

Verification evidence for the fix on branch `task/cache-commit-race`, written
against the 10-gate doctrine
(`~/.claude/senior-manager/grade-automator-plus/DOCTRINE.md`).

- Gated commit: **PENDING** (filled in after the two strict gate runs)
- Base commit: `b744c9f` (beta at the time of branching)
- Logs: `docs/evidence/cache_commit_race/`
- Role: h1-stage3-cache. This change is **separate from H-1 Stage 3** at the
  Senior Manager's direction, and lands first.

## READ THIS FIRST — what is not verified

- **Gate 8 (live / end-to-end) is PARTIAL.** Under the risk tiering in H8,
  a cache-invalidation change is **environment-sensitive**, so Gate 8 PASS
  needs DEPLOYED-REAL evidence and LOCAL-REAL is PARTIAL. The race window is
  the few milliseconds between a bump and its commit, and nothing reachable
  over HTTPS can force a read into it, so the mechanism is proven LOCAL-REAL.
  The Senior Manager has accepted a written PARTIAL here. A deployed
  probabilistic replay on the QA beta after landing can raise it later; it is
  not claimed now.
- **Gate 4 (adversarial) depends on an independent session.** The Attacker
  session (grade-automator-plus-04) holds the revocation replay. Its result is
  recorded here when it lands.
- Under hardening rule H1.3, this change is in the environment-sensitive
  class, so a gate left PARTIAL needs **the user's explicit written sign-off**
  before it lands. The Senior Manager's approval does not substitute for it.
  That sign-off is **not yet given**.
- **Gate 6's timing figures are not final.** The first measurement ran while
  five other suites were running (load average 20.9), so its wall-clock and
  p50/p95 numbers were discarded rather than reported. Counts, query numbers
  and generation arithmetic from that run are load-independent and hold.

## 1. What changed

`AutoGrader/cache_generation.py`, in `bump_many`: when a bump happens inside an
open transaction, the same counters are bumped a second time from
`transaction.on_commit`.

```python
bumped = _bump_now(unique)
if transaction.get_connection().in_atomic_block:
    transaction.on_commit(lambda: _bump_now(unique))
return bumped
```

`_bump_now` is the old body of `bump_many` (pipelined bump, with the
per-counter fallback), extracted so it can run twice.

## 2. Why it was necessary

Cache freshness uses generation counters: a cached response is stored under a
key that includes the generation of every scope it depends on, and a write
"invalidates" by bumping those generations.

The bump runs from `post_save` / `post_delete`, which Django runs **inside**
the caller's transaction. So for any write in `transaction.atomic`:

1. the writer saves a row and bumps the generation — the bump reaches Redis
   immediately, the row is not committed yet;
2. a concurrent reader computes the **new** key and reads the **old**
   committed rows, then caches that response under the new key;
3. the writer commits. Nothing bumps again, so the poisoned entry stays
   authoritative until its 5-minute TTL expires.

For an enrollment this serves a stale course; for a revocation it can keep
serving access that was just removed. The affected paths are every service
using `transaction.atomic`, including `enroll_student_by_email` and
`remove_student_from_course`.

Reproduced before the fix: `01_reproduce_on_unfixed_beta.log` (see §4, Gate 1).

## 3. The 10-gate table

| Gate | Status | Evidence |
| --- | --- | --- |
| 1 Baseline / Regression | PARTIAL | reproduce-first done: 8/13 fail on `b744c9f`, 13/13 pass on the fix; the full strict suite (twice) is still pending |
| 2 Mutation | PASS | `04_mutation_battery.log`: 3/3 mutants killed, sha256-verified restores, control 10/10 |
| 3 Concurrency | PASS | 20 writers + 20 readers, 10 rounds, real threads/Postgres/Redis; `02_after_fix.log` |
| 4 Adversarial | PENDING | independent Attacker session replay of the revocation path |
| 5 Failure / Recovery | PASS | Redis refused and timing out, at the first bump and at commit; `02_after_fix.log` |
| 6 Stress / Scale | PARTIAL | counts, query growth and generation arithmetic proven at 600/6,000 and 200/2,000; the timing pass awaits an unloaded machine; `05_double_bump_cost.log` |
| 7 Real Infrastructure | PASS (LOCAL-REAL) | every test runs on real Postgres + real Redis; no mocked cache backend |
| 8 Live / E2E | PARTIAL | the pre-commit window cannot be forced over HTTPS; mechanism proven LOCAL-REAL |
| 9 Security / Isolation | PASS | the post-commit bump touches only the writer's own scopes; `02_after_fix.log` |
| 10 Final Production Gate | PENDING | two consecutive clean strict runs on the exact commit |

Statuses marked PENDING are not claims. They are filled from committed logs
before this document is offered for review.

## 4. Gate by gate

### Gate 1 — Baseline / Regression

Reproduce-first, as H2.1 requires: the test file was written and run against
the **unfixed** base commit `b744c9f` with production code untouched.

`01_reproduce_on_unfixed_beta.log`: **8 of 13 tests FAILED** on `b744c9f` with
the final test file, the tree carrying nothing but that file (the log records
the sha256 of `cache_generation.py` on the tree and at the base commit —
identical — and a `git status` showing only the untracked test file):

- both forced interleavings: the cached course stayed stale after the commit;
- three generation-arithmetic tests;
- the isolation test: the commit did not repeat the writer's scopes;
- the failure-recovery test, for both the refused connection and the timeout:
  with the in-transaction bump failed, nothing invalidated afterwards.

The remaining tests pass before and after by design: they asserted bounds the
old code already met (a rolled-back transaction must not bump again; with both
bumps failing, staleness is TTL-bounded either way). They are stated here as
non-discriminating rather than counted as evidence.

The same file passes 13/13 after the fix (`02_after_fix.log`).

No existing test was modified or weakened by this change; the diff adds a test
file and extracts a helper.

### Gate 2 — Mutation

Every guard the diff adds, with its mutant (`04_mutation_battery.log`, run in a
disposable detached worktree with its own database):

| Guard in the diff | Mutant | Result |
| --- | --- | --- |
| the post-commit bump exists at all | a: `if False` — never queue it | KILLED, 5 tests failed |
| it is queued **only** inside a transaction | b: `if True` — queue it always | KILLED, 1 test failed |
| the immediate bump still happens inside a transaction | c: skip the immediate bump when atomic (the Senior Manager's option B) | KILLED, 5 tests failed |

Each mutant was restored from the commit's blob and verified by sha256
(`3fca9ec5…`); all three matched. The unmutated control run passed 10/10 and
the worktree was removed with a clean status.

### Gate 3 — Concurrency

`ConcurrentEnrollmentRaceLoadTests`: 20 enrolling threads and 20 reading
threads per round, 10 rounds, real threads against real Postgres and Redis.
Every round asserts the exact enrollment count and that the cached course
equals the uncached truth. Every thread closes its own connection in `finally`
and `run_threads` asserts no thread is still alive after the join.

The forced-interleaving tests do not rely on timing: the writer's bump releases
the reader and the writer holds its transaction open until the reader has
cached what it saw.

### Gate 4 — Adversarial

Held by the independent Attacker session, per H5.1. The strongest atomic
revocation path was handed over: `DELETE course/<pk>/student/<student_id>` →
`remove_student_from_course`, which deletes the enrollment inside
`transaction.atomic`. The autocommit paths (withdrawal PATCH, school-move
PATCH, admin bulk deactivation) are not exposed to this race and serve as
negative controls.

### Gate 5 — Failure / Recovery

Injection is at the `django_redis` client **class**, because Django's cache
object is per thread and an instance patch never reaches the writer thread.
Each case records the resulting state:

| Failure, and when | Result |
| --- | --- |
| Redis refuses the connection at the commit bump, after the reader cached | the write still commits; the poisoned entry keeps its TTL (0 < ttl ≤ 300s) and the read is fresh once it expires |
| Redis refuses the writer's **first** bump, recovered before commit | the post-commit bump does the invalidation; the cached read is fresh immediately |
| Redis refuses **both** bumps | the write still commits; staleness is bounded by the TTL, then fresh |
| the same three cases with a Redis **timeout** instead | identical |
| transaction rolled back | no post-commit bump (the callback is discarded) |
| inner savepoint rolled back | a bump made inside it is not repeated; the outer bump still fires on commit |

No case leaves an unbounded stale entry, and no case fails the write. This is
the intended trade: a bump that cannot reach Redis degrades to the 5-minute
TTL, exactly as the pre-existing invalidation does, and never loses the write.

### Gate 6 — Stress / Scale

`AutoGrader/tests_cache_commit_race_cost.py`, figures in
`05_double_bump_cost.log`. Two shapes, each measured with the fix off and on,
at two sizes 10× apart (H7.2):

- **Production's shape.** `import_roster` commits **per row** and caps a file
  at 2,000 rows (`MAX_ROWS`), so no production path enrolls 6,000 students in
  one transaction. Measured at 200 and 2,000 rows through the real service.
- **The synthetic worst case** the Senior Manager asked for: 600 and 6,000
  enrollments in a single `transaction.atomic`.

Figures: **NOT YET MEASURED ON A QUIET MACHINE.** The pass on 2026-09-17 ran at
load average 20.9 against five other suites, and its wall-clock and p50/p95
numbers are discarded rather than reported. The counts it produced are
load-independent and stand: one extra Redis round trip and ten extra commands
per in-transaction bump, the commit phase sending exactly what the open
transaction sent (2,000 round trips / 20,000 commands for a 2,000-row import;
6,000 / 60,000 for 6,000 in one transaction). The timing pass is booked for a
quiet exclusive window.

Two defects in this harness were found and fixed rather than worked around,
both recorded because H1.4 makes every figure traceable:

1. the fixtures reused student first names across the two sizes, and
   `StudentCourse.save`'s `full_clean` rejects a duplicate exact name in a
   course, so rows failed inside the 2,000-row pass. Names are now unique per
   size (`e6da8a2`).
2. query counts came from `connection.queries`, a deque capped at 9,000
   entries, so at 6,000 enrollments it stopped growing and reported 0 against
   1,796. Counting now goes through `connection.execute_wrapper` (`0bbd8d1`).
   This is the single failure in `03_autograder_app_after_fix.log`.

The fix issues no database queries, so query counts are identical with it off
and on (6.00 per enrollment at both smoke sizes), and per-row counts do not
grow with the batch.

### Gate 7 — Real Infrastructure

LOCAL-REAL for every result in this document: real PostgreSQL and the real
Redis backend, no mocked cache. The only patched infrastructure is the
deliberate failure injection in Gate 5 and the legacy wildcard deletes, which
are disabled in these tests so that they measure the generation mechanism
rather than a SCAN-and-delete of the whole keyspace.

### Gate 8 — Live / E2E

PARTIAL, accepted in writing by the Senior Manager, and still subject to H1.3
(the user's sign-off). What the deployed environment could show is that
enrollment and revocation work, which is not in question. What it cannot show
is a read landing inside the pre-commit window, because that window is not
externally addressable. The Attacker session's probabilistic replay against
the running app is the closest available evidence and is recorded under
Gate 4.

### Gate 9 — Security / Isolation

`PostCommitBumpIsolationTests`: with a home school and another school, a
same-school different-teacher course and a foreign course, an enrollment in one
course inside a transaction moves **only** the writer's own scopes (the
enrolled student, the course teacher, the course, the school), and the commit
repeats exactly those, each by twice its in-transaction change. Every foreign
counter — the same-school teacher, the other-school teacher, an unrelated
student, both foreign courses, the other school — is unchanged, measured both
inside the transaction and after the commit.

The fix changes no key, no scope and no read path, so no cache-isolation
boundary moves; this test exists to prove that.

### Gate 10 — Final Production Gate

Two consecutive clean strict runs on the exact commit, per H10.2, booked with
the Senior Manager. Procedure: detached worktree with identical before/after
fingerprints, a fresh uniquely named database with no `--keepdb`, unfiltered
log with line count and sha256, `systemd-inhibit`, and the Postgres checks
afterwards. Evidence is committed docs-only.

## 5. The eight completion answers

1. **What changed.** `bump_many` queues a second bump of the same counters via
   `transaction.on_commit` when it runs inside an open transaction; its old
   body is extracted as `_bump_now`. Plus two test files.
2. **Why it was necessary.** A bump inside a transaction lands before the
   commit, so a concurrent read can cache pre-commit data under the
   post-bump key, where nothing invalidates it for 5 minutes. For a revocation
   that means access can outlive its removal.
3. **What was tested.** Forced pre-commit interleaving (plain and nested with a
   rolled-back savepoint); exact generation arithmetic for commit, rollback,
   inner-savepoint rollback and nested savepoints; Redis refused and timing out
   at each bump step; 20×10 concurrent enrollments against real infrastructure;
   tenant isolation of the post-commit bump; and the cost of the second bump at
   two scales in both the real and the synthetic write shape.
4. **Which gates passed.** 2, 3, 5, 7, 9 (7 as LOCAL-REAL).
5. **Which gates remain incomplete.** 1 is PARTIAL until the two strict runs,
   which also close 10; 4 is pending the independent Attacker replay; 6 is
   PARTIAL until its timings are remeasured on an unloaded machine; **8 is
   PARTIAL and stays PARTIAL for this landing.**
6. **What risks remain.** (a) Every in-transaction bump costs a second Redis
   round trip; the commit phase of a 6,000-write transaction replays 6,000 of
   them (figures in Gate 6). They are not deduplicated across callbacks — a
   possible later optimisation, deliberately out of scope here. (b) If Redis is
   unavailable for both bumps, staleness is bounded by the TTL, not removed.
   (c) The window can only be closed for writes that go through Django's
   transaction machinery; a raw SQL write outside the ORM would still bump
   nothing.
7. **What exact commit contains the verified implementation.** PENDING.
8. **Is the verified commit the one intended for release.** To be confirmed
   after the gate: the gated SHA must equal the landed SHA, and
   `git rev-parse beta` must equal it after landing.

## 6. Log index

| File | Contents |
| --- | --- |
| `01_reproduce_on_unfixed_beta.log` | the race on `b744c9f`, production code unchanged |
| `02_after_fix.log` | the same tests on the fix |
| `03_autograder_app_after_fix.log` | the whole `AutoGrader` app suite; the single failure is the harness defect described in Gate 6, fixed in `0bbd8d1` |
| `scripts/` | the scripts that produced the logs (mutation battery, reproduce, strict gate) |
| `04_mutation_battery.log` | mutants a/b/c plus control, disposable worktree |
| `05_double_bump_cost.log` | Gate 6 cost measurement |
