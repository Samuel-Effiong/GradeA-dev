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
| Changed module `AutoGrader.tests_cache_invalidation_coverage` | see log | `r2_changed_module.txt` |
| Mutation P1–P2, S1–S5 (own DB, dropped) | see log | `r2_mutation_log.txt`, `r2_mutation_results.json` |
