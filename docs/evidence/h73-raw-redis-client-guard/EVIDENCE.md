# H-73: the raw-cache-write guard now sees the raw Redis client

Branch `task/h73-raw-redis-client-guard` off d5's `task/h65-beat-locks` tip
`2e9dcb0` (H-65 on `8de3078`), so it merges after H-65. Author: ed
(Security). Verifier: v2. **Test only**: the guard module changes, and no
production code does.

## The gap

Layer 2 of `AutoGrader/tests_cache_invalidation_coverage.py` counts only
calls on a name `cache` (`cache.set/add/get_or_set/set_many`). A write
through the raw django-redis client was invisible to it: no versioning
check, no `NON_RESPONSE_CACHE_WRITES` entry, nothing. That covers
`get_redis_connection()`, `cache.client.get_client()`, the `cache.client`
wrapper, and redis-py's `Redis.from_url`. The gap step below shows it:
on the old guard, `beat_locks` gaining a raw `GETSET` stays green.

## The fix

A second scan, `raw_client_uses()`, feeds `RawRedisClientTests`. It counts,
per non-test module:
- **acquisitions:** calls to `get_redis_connection`, `get_client`, `Redis`,
  `StrictRedis`, `from_url` or `ConnectionPool`, plus calls to a factory
  imported from another module;
- **writes:** calls of a Redis write command (`set`, `setex`, `incr`,
  `eval`, `delete`, `unlink`, `hset`, … the full list is
  `RAW_WRITE_METHODS`; reads and `pipeline.execute()` are not counted) on a
  client expression.

A client expression is any of:
- a source or factory call (a factory is a local function that returns or
  **yields** a client, or one imported from a module that defines one);
- `cache.client` / `cache._cache`;
- a client's `pipeline()`;
- a name bound to a client in the same function, by assignment, as the
  target of a `for` over a factory, or as the **parameter** of a local
  function called with a client.

`RAW_CLIENT_USERS` lists each module with its (acquisitions, writes) and a
reason. It is compared both ways, so a new user fails and a stale entry
fails. Every entry must give a reason.

| Module | Acq. | Writes | Why |
|---|---|---|---|
| AutoGrader/beat_locks.py | 1 | 4 | H-65 locks: SET NX PX, Lua release/extend, the fail-closed probe |
| AutoGrader/cache_generation.py | 1 | 2 | the pipelined generation bump (SET NX + INCR) |
| AutoGrader/redis_test_hygiene.py | 1 | 1 | test-only key hygiene (unlinks gaplus-t<pid>: prefixes) |
| AutoGrader/testing/beat_locks.py | 1 | 1 | test-only: clears beat-lock keys before each test |

- **What the table shows.** The scan found `redis_test_hygiene.py`, which
  the row didn't name: its clients come from a generator and reach `unlink`
  through a helper's parameter. That is why yields, `for` targets and
  parameters are followed.
- **Pre-existing, not new.** `cache_generation.py`'s raw pipeline predates
  H-65 and was just as invisible.

Self-tests:
- **Shapes the scanner must catch** (acquisitions, writes): direct, bound
  name, `cache.client.get_client`, the `cache.client` wrapper, pipeline, a
  local factory, redis-py, and yielded + looped + passed-on.
- **An imported factory** counts in the importing module.
- **Shapes it must ignore:** the cache API, a read, a scan, another client
  (OpenAI), and a same-named parameter in an unrelated function.
- **Every listed module** is really found by the scan.

## Gates

This is a test-only change, so the run is the changed modules, the gap step
and mutation; there is no regression (as ruled for H-55). The script follows
0b's rules of 2026-09-30: mutation on its own DB (`..._mut`, dropped after),
`--verbosity 2`, timestamped output, and a `pg_stat_activity` snapshot.

| Gate | Result | Log |
|---|---|---|
### Round 1 (d7ab949)

| Gate | Result | Log |
|---|---|---|
| Changed modules: this guard + all repo-wide guards (incl. H-65's `tests_beat_locks`/`tests_beat_health`) | 93 tests, **1 failure, inherited from H-65** (see below). Every H-73 test passed | `r1_changed_modules.txt` |
| The gap: 2e9dcb0's guard with beat_locks gaining a raw GETSET | GREEN as expected (exit 0): the old guard can't see the raw write | `gap_old_guard_green_with_raw_write.txt` |
| Mutation P1–P2 (production gains a raw write), S1–S5 (the scanner loses a rule), own DB, dropped | 6/7 killed by named tests; **S5 survived** | `r1_mutation_log.txt`, `r1_mutation_results.json` |

**The inherited guard failure.** `AutoGrader.tests_no_wildcard_invalidation.test_no_production_code_calls_a_wildcard_cache_operation`
fails with `{'AutoGrader/testing/beat_locks.py: scan_iter': [36]} != {}`.
The file and its `scan_iter` come from H-65 (2e9dcb0, d5), H-73's base.
H-73's diff against 2e9dcb0 touches only `AutoGrader/tests_cache_invalidation_coverage.py`.
The cause: that guard's `production_python_files()` counts
`AutoGrader/testing/` as production code, and its `ALLOWED` list has
`redis_test_hygiene.py` but not `testing/beat_locks.py`. The fix belongs
to d5 (reported to d5, 0b and the SM). H-73 cannot pass addendum 2's
guard step until it lands. 0b will update H-73's base onto d5's fixed
H-65 tip before the merge.

**Script defect, disclosed.** Round 1's stop check (`grep -q " OK"`) also
matched the migrations' `Applying ... OK` lines, so the script went on to
the gap and mutation steps after the failure. Those steps don't depend on
the guard, so their results stand. Round 2 checks for a final `OK` and no
`FAILED`.

**S5 (import resolution disabled) survived.** No live module imports a
raw-client factory, and `test_an_imported_factory_counts_in_the_importing_module`
hands the imported set to `scan_raw_client` directly, so nothing exercised
`raw_client_uses()`'s cross-module resolution. The fix is test-only,
at debd2ab:
- `raw_client_uses(files=None)` takes an optional list of `(rel, path)` pairs.
- `test_a_factory_imported_from_another_module_is_found_across_modules`
  builds three fixture modules. An aliased import of a factory counts;
  the same name imported from a module that doesn't define it does not.

### Round 2 (debd2ab, test-only: 0b granted the changed module + the 7 mutants, no regression)

| Gate | Result | Log |
|---|---|---|
| Changed module `AutoGrader.tests_cache_invalidation_coverage` (run at a721a83 = debd2ab + round-1 docs) | 17 tests OK (18.7 s) | `r2_changed_module.txt` |
| Mutation P1–P2, S1–S5 (own DB `test_h73_raw_redis_client_guard_mut`, dropped) | **7/7 killed** by named tests; S5 is killed by `test_a_factory_imported_from_another_module_is_found_across_modules`; source clean afterwards | `r2_mutation_log.txt`, `r2_mutation_results.json` |

**Still owed: the guard step on the new base.** d5 fixed H-65 at e5a94d0
(exact-key delete, no `scan_iter`). H-73's scanner, run statically over
e5a94d0's files, still counts 1/4, 1/2, 1/1, 1/1, matching
`RAW_CLIENT_USERS` exactly. 0b will update H-73's base onto H-65's tip
after bundle 4 is merged into it. Then all the repo-wide guards run again
in their own targeted slot, before the merge (addendum 2).

## Round 3: v2's static findings F1 and F2 (SM ruling: fix in H-73 before the verdict)

v2's static review at debd2ab: `~/Documents/Projects/GAP-v2-handover/FINDING_h73_static_debd2ab.md`.
- **F1:** a listed module could gain raw writes and keep its count. v2's example on the real beat_locks: a GETEX on a `with`-bound pipeline, plus a `lock()`, left it at (1, 4).
- **F2:** `from AutoGrader import beat_locks; beat_locks._redis().set(...)` in a new module counted nothing.

The SM bounded the scope: receiver-agnostic write counting, a read allow-list, and module-attribute factory resolution. Dynamic `getattr` and string dispatch are documented limits.

### What changed (test-only: `AutoGrader/tests_cache_invalidation_coverage.py`)

The scanner is now `_RawClientScan`. `scan_raw_client`, `raw_client_factories` and `raw_client_uses` keep their signatures; `scan_raw_client` gains an optional `module_factories`. A write is counted by either of two rules, and once when both apply:
1. **By name, whatever the receiver.** In a module with an acquisition, every call named in `RAW_WRITE_METHODS` counts. The list gained v2's missing commands (getex, setbit, zremrangebyscore, zpopmin, sinterstore, hincrbyfloat, smove, linsert, lmove, rpoplpush, blpop, pfadd, xtrim, restore, `lock`, `register_script` and their relatives). `append` and `copy` left the by-name list, because `list.append` / `dict.copy` are everywhere; rule 2 still counts them on a client the data flow follows.
2. **The read allow-list.** On a client the data flow follows, any method not in `RAW_READ_METHODS` / `RAW_PLUMBING_METHODS` / `RAW_CLIENT_SOURCES` counts, including a command redis-py adds later. The data flow now also follows:
   - with-as, annotated assignments and walrus;
   - a factory that returns a bound name;
   - `self.<attr>`;
   - a keyword argument to a local function;
   - a closure, looked up through the enclosing functions.

**F2:** `raw_client_uses` now resolves `from P import M`, `import M as m` and `import P.M`, followed by `M.factory()`. Each counts as an acquisition, and as a client for rule 2.

**Documented limits** (in `_RawClientScan`'s docstring): dynamic `getattr`; string dispatch (except `execute_command`, which rule 1 counts); a client kept in a container, or on a non-`self` attribute, in a module with no acquisition; a client passed into another module; the django-redis wrapper under another name (`caches["x"].client`, `cache as c`); star-imports.

**`RAW_CLIENT_USERS` counts.** Rule 1 counts by name, so two listed modules now pin a larger number. The reasons say which calls:
- `beat_locks.py`: (1, 4) → **(1, 5)**. The 5th is the heartbeat's `threading.Event.set()`.
- `cache_generation.py`: (1, 2) → **(1, 4)**. Two cache-API `cache.incr()` calls.
- `redis_test_hygiene.py` (1, 1) and `testing/beat_locks.py` (1, 1) are unchanged.

The counts are the same at 2e9dcb0 and at d5's e5a94d0.

### Tests

- **New:** `test_a_write_name_counts_whatever_it_is_called_on` (rule 1: a client in a container; a parameter of an unrelated function).
- **New:** `test_any_non_read_call_on_a_client_is_a_write` (rule 2). One case per binding form, each using `vf_cmd`, a method in no list. Only the data flow can count it, so each form has a case no other rule masks: with-as, annotated, walrus, a factory returning a bound name, self attribute, keyword, closure, for over a factory, positional argument.
- **New:** `test_a_listed_module_gaining_raw_writes_changes_its_count`. v2's F1 appended to the real `AutoGrader/beat_locks.py` raises the count by exactly 2.
- **Extended:** the cross-module test gains F2's three import forms.
- **Changed:** the ignore test's "name in another function" case moved to rule 1, since it is now counted. In its place: "no raw client in the module" and "reads and plumbing".

### Static checks before the run (pure `ast`, no Django or DB, no slot, as v2's review)

- **`RawRedisClientTests` through a unittest stub:** 10/10 OK on this tree and on an e5a94d0 snapshot. The live counts are identical on both.
- **v2's `h73_shapes.py` / `h73_real.py`** (copies with only the new names added to their extraction set):
  - every F1 shape and command is counted;
  - beat_locks + pipeline/lock goes (1, 5) → **(1, 7)**;
  - `newmod_attr` (F2) → (1, 1).
  - Still MISS, as documented limits: `caches['default'].client` and the `cache as dc` alias, in a module with no acquisition. The standalone "module-attribute factory" shape needs `raw_client_uses`, which resolves it (the `newmod_attr` row).
- **Kill prediction for all 22 mutants** with the same stub, on an e5a94d0 copy: **22/22 killed**, each by a named test (`r3_static_kill_prediction.txt`; harness: `static_harness.py`). It predicts the gate; it is not the gate.

### Round 3 gates: ONE run on the new base (after 0b merges H-65 9887b25 into H-73)

| Gate | Result | Log |
|---|---|---|
| Changed module + all 9 beta-line guards | see log | `r3_changed_modules.txt` |
| Mutation: P1–P5 (production gains a write: GETSET, raw `cache.client`, pipeline GETEX, lock, F2 module-attribute), S1–S5, F1a–F1i, F2a–F2c (own DB, dropped) | see log | `r3_mutation_log.txt`, `r3_mutation_results.json` |

No regression: the change is test-only (rule 15).
