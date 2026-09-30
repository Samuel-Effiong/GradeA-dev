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
| Changed modules: this guard + all repo-wide guards (incl. H-65's `tests_beat_locks`/`tests_beat_health`) | see log | `changed_modules.txt` |
| The gap: 2e9dcb0's guard with beat_locks gaining a raw GETSET | expected GREEN | `gap_old_guard_green_with_raw_write.txt` |
| Mutation P1–P2 (production gains a raw write), S1–S5 (the scanner loses a rule) | see log | `mutation_log.txt`, `mutation_results.json` |
