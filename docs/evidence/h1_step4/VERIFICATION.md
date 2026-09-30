# H-1 step 4 (wildcard removal): code-review verification @a86354b
Stacked on steps 1–3 (dd2dc6a). To be rebased onto beta after 1–3 land; the full suite and strict gate follow then and are NOT covered here.

## 1. ConcurrentAccessRevocationTest relabel: confirmed empirically, not just argued
The change labels each request "late" by the flag state when it's ISSUED rather than when it COMPLETES. I read the whole test: `revoke()` saves WITHDRAWN (autocommit), then `cache.clear()`, then sets the flag, so a request issued after the flag has its `get_object()` access check strictly after the commit and the clear. I then instrumented `AssignmentViewSet.get_object` to record the flag state at the moment of the access check, and ran the race **40 times** (12 threads × 6 requests + revoker, student re-enrolled each iteration; throwaway test in a detached checkout, never committed). Of 2880 requests, 324 got 200:
- 5xx: **0**
- 200 whose access check ran AFTER the flag (a real leak): **0**
- 200 issued after the flag (failures under the new label): **0**
- 200 issued BEFORE the flag but completed after it (false alarms under the OLD label): **27**
So the old completion-time label gives false failures at a real rate once the wildcard SCANs no longer slow the gap between commit and flag. The new label matches the assertion's meaning and hides no leak.
Scope caveat (pre-existing, unchanged): because `revoke()` clears the whole cache, this test exercises the DB access race only, never cache staleness.

## 2. Commit race (H-25): neither fixed nor worsened
With the wildcards gone, generation bumps are the only invalidation, so I checked whether any removed wildcard clear ran AFTER commit (which would have masked the commit race). None did. Every removed `delete_cache_patterns` call was in a synchronous signal receiver or `invalidate_*` helper, running in-transaction beside the bump. `batched_cache_invalidation` (the only deferring mechanism) had no production caller: its only references were its own unit tests and one comment. The commit race is therefore the same before and after step 4. Its fix, task/cache-commit-race (bd2f016), is **not** on beta 197aa46 and not in this branch; see note (a).

## 3. Removal completeness and the guard
- A non-test search for delete_pattern / iter_keys / scan_iter / keys(…) / SCAN / KEYS / delete_cache_patterns / batched_cache_invalidation finds only the allowlisted `assignments/pdf_cache.py:175` exact per-assignment prefix clear, plus comments and the `DJANGO_REDIS_SCAN_ITERSIZE` setting that the PDF clear still uses.
- The BatchUploadSession receiver deletion is justified: no serializer or cached view renders it (checked).
- Status-summary (ffe90cc): both keys are now `versioned_key` on `usr(student)`, like their siblings, and the `?course=` access check stays BEFORE the cache lookup.
- My mutants (each restore sha-verified):
  | Mutant | Result |
  |---|---|
  | S1: status-summary `:all` key back to raw | KILLED by all 12 `*_is_fresh` status-summary tests |
  | Guard (a): `cache.delete_pattern("studentadmins:*")` added in dashboard/views.py | KILLED (test_no_production_code_calls_a_wildcard_cache_operation) |
  | Guard (b): `get_redis_connection().scan_iter("user*")` added in users/signals.py | KILLED (same test) |
  | Guard (c): PDF clear widened to `f"{CACHE_KEY_PREFIX}:*"` | KILLED (test_the_pdf_clear_stays_an_exact_prefix) |
  | Guard (d): `cache.clear()` added in users/signals.py | **SURVIVED**, see note (b) |

## Notes
(a) **Sequencing (for the SM):** with the wildcards gone, generation bumps are the sole invalidation. The commit race they're exposed to (a read between the in-transaction bump and the commit re-caches pre-commit data under the new generation) is unchanged by step 4, but H-25 is its fix and is not landed. Recommend H-25 lands before or with step 4.
(b) **Guard gap:** whole-cache wipes (`cache.clear()`, which django-redis implements as FLUSHDB, and `flushdb()`/`flushall()`/raw FLUSHDB/FLUSHALL) are broader than any wildcard but aren't flagged. Production has 0 such calls today, so adding `clear`, `flushdb` and `flushall` to the scanner (plus FLUSHDB/FLUSHALL to RAW_COMMANDS) is free.
(c) The wallet licence-plan-change STALE for 3 viewers is pre-existing (same with the wildcards on and off), so it's H-37 and not a step-4 regression.
(d) Owed after the rebase: beta merge, whole-repo mypy, full suite and strict gate. I'll do a quick look at the rebased tip.

Verdict: VERIFIED-WITH-NOTES (code review).
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
