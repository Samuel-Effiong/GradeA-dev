# Verification: H-73, the raw Redis client guard (ed), round 3, v2's F1/F2 fix

- **Branch:** task/h73-raw-redis-client-guard at **8bab946**:
  - code 95593ae;
  - gates at 4e2708d = 2db3809 + 0b's base update onto H-65 adf8fe0, which contains 9887b25.
- **Verifier:** v2 (independent), 2026-09-30.
- **Verdict:** **VERIFIED**
- **Scope:** test-only (AutoGrader/tests_cache_invalidation_coverage.py, `RawRedisClientTests` + `_RawClientScan`), bounded per the SM's ruling. Dynamic `getattr` and string dispatch are a documented limit, not a finding.

## What was checked
No v2 test run: the guard run is ed's (rule 15; 0b). All checks below are static. The scanner was pulled out of the guard module with `ast` and run with no Django and no DB.

| Check | Result | Evidence |
|---|---|---|
| `git show --remerge-diff 4e2708d` | **Empty**: a clean auto-merge, no hand edits | – |
| Added-line survival (`vf_merge_survival.py 4e2708d`) | base 2e9dcb0, sides 2db3809 / adf8fe0, **0 lines lost** | – |
| 4e2708d..8bab946 | Evidence files only (EVIDENCE.md, r3 logs, JSON) | `git diff --name-only` |
| The merged guard vs 95593ae (statically passed earlier) | Byte-identical. beat_locks.py is byte-identical to e5a94d0. | cmp |
| v2 shape harness on the merged guard | Every F1 shape counts. beat_locks + pipeline getex + lock: (1, 5) → **(1, 7)**. F2: `from P import M`, `import M as m` and `import P.M` each give the new module **(1, 1)**. | h73_static/results_r3_4e2708d.txt |
| Repo-wide scan of the merged tree vs RAW_CLIENT_USERS | **MATCH**: beat_locks (1, 5), cache_generation (1, 4), redis_test_hygiene (1, 1), testing/beat_locks (1, 1) | same |
| RAW_READ_METHODS | Reads only, no overlap with RAW_WRITE_METHODS. getex, lock and register_script are write names. | – |
| ed's gate (read, not repeated) | 9 guards + tests_beat_locks: **111 OK** at 4e2708d. Mutants: **22/22 killed** by named tests, no survivors. P3/P4 (v2 F1 on the real beat_locks) and P5 (v2 F2 in beat_health) are killed by `test_every_raw_client_user_is_listed`; F2a–c by the cross-module test; F1a–i by the rule-1/rule-2 tests. | r3_changed_modules.txt, r3_mutation_log.txt |

## What the harness still misses (none is a finding)
- **`caches["default"].client…` and `from django.core.cache import cache as dc`.** Documented in `_RawClientScan`'s limits. A `caches[...]` form that goes through `get_client()` is still caught as an acquisition.
- **A client passed into another module's helper.** The helper module isn't counted and its caller counts (1, 0). This is documented ("a client passed into another module as an argument").
- **A module-attribute factory scanned alone** (`scan_raw_client` without `raw_client_uses`). This is expected: the resolution happens across modules, and at module level it is caught.

## Notes (none blocks)
1. **Disclosed deviation:** the mutation step ran under `timeout 3000` against rule 12's 1800 cap, and finished in 10m13s. 0b allowed it. EVIDENCE records it, along with the new practice (split into batches of 1800 s or less, or ask the SM).
2. **Counts by name include non-Redis calls:** beat_locks' `threading.Event.set()` and cache_generation's two `cache.incr()`. The RAW_CLIENT_USERS reasons say so. An edit there fails loudly, which is the safe direction.
