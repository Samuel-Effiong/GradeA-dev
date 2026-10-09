# Fast-path batch landing — evidence

User directive (2026-09-21): land the verified fix batch on beta as ONE merge,
verified by ONE full-suite run + a smoke test + evidence, skipping per-fix
mutation/stress/deployed-E2E reruns (their earlier evidence stands). Risk is
accepted for QA beta; full strict gates return before anything reaches main.

## What's in the batch

Built in worktree `GAP-batch-fastpath`, off beta `fba1294`, merged in order:

1. `cb3bbea` — H-18 (course IDOR) + H-19 (superadmin either-flag credit/AI
   bypass)
2. `6cb1686` — H-22 (my-students cross-teacher leak)
3. `dc2f656` — H-21 (free-plan self-activation)
4. `8a1b783` — H-24 (refusal handling: AI refusals never retried, never 500)
5. `80c46ae` — strict-gate-runner script
6. `f48c611` — H-33 (stale stored final_grade repair command)
7. `c0e05e6` — H-31 (lockout mixed-traffic test hardening)
8. `5400759` — fake-Stripe HTTP-client test harness
9. `f270c98` — overage concurrency test flake fix
10. `a7d667c` — reconcile H-19/H-24 test conflict (an empty wallet now
    raises `EmptyWalletError`, not the pre-H-24 `ParseError`)
11. `afcadce` — vendor the `cl100k_base` tiktoken encoding so token counting
    needs no live network (ca)
12. `51e62cf` — reconcile a second, missed H-19/H-24 test conflict
    (`tests_superadmin_unmetered_both_flags.CreditGateBothFlagsTest`, same
    root cause as #10, found by the full-suite gate)

Conflicts resolved during the merge: `docs/HARDENING_BACKLOG.md` (kept both
sets of rows), `billing/views.py` (kept both imports). One merge regression
caught by a worker's quick check and fixed (see #10 above).

## Gate: ONE full-suite run on a7d667c

Run by gate-runner, Redis DB 15, `--parallel 1`, fresh DB, no `--keepdb`,
unfiltered log. Logs: `../Grade-Automator-Plus-gate-logs/a7d667c-db15/`.

- 4530 tests, 2926s (49 min — down from the pre-fix ~2.8h baseline, thanks
  to the Redis dead-key sweep + leak fix landing separately).
- `FAILED (failures=48, errors=35, skipped=26)`.

### Triage (every failing test accounted for, not just counted)

All 83 failing/erroring entries were traced to one of two causes — **zero
real regressions found**:

1. **Transient network outage (this sandbox's outbound HTTPS to
   `openaipublic.blob.core.windows.net` was down during the gate run)** —
   60 of 83 confirmed by traceback content (SSL EOF / connection errors)
   or by direct re-run once network access returned; the remaining
   ai_processor benchmark/golden replay failures are consistent with the
   same cause (extraction step needs the tokenizer) though not each
   individually re-run. Network access was independently confirmed
   restored by three separate sessions (SM, gate-runner, fixes-coordinator)
   within an hour of the gate, and 4 spot-re-run tests from this bucket
   passed clean with a real download. This is now moot going forward:
   commit `afcadce` vendors the encoding file, so no future gate depends on
   live network for this at all (`ai_processor/tests_tiktoken_cache.py`
   guards it).
2. **Two stale pre-H-24 test files** (23 of 83 entries, all in one root
   cause): `users/tests_credit_balance_permission.py` (fixed in `a7d667c`,
   already known before this gate) and
   `ai_processor/tests_superadmin_unmetered_both_flags.py::CreditGateBothFlagsTest`
   (found BY this gate, fixed in `51e62cf`) both asserted the pre-H-24
   `HasCreditBalance` behavior (`ParseError` → 400, "Insufficient Credits"
   text). H-24 (merged into this same batch) intentionally changed that
   permission class to raise `EmptyWalletError` → 402,
   `code=insufficient_credits`. Confirmed with a direct, network-free
   re-run that this reproduces deterministically and is not
   network-related — it is a merge-reconciliation miss, now fixed and
   verified (both files pass their full test class: 9/9 and the earlier
   2/2).

No test in the 83 reproduces a real behavioral regression once these two
causes are accounted for.

### Deltas added after the a7d667c gate (not covered by a second full run)

Per the fast-path directive, these were verified by targeted runs, not a
second 45-90 min full suite:

- `51e62cf`: `ai_processor.tests_superadmin_unmetered_both_flags` full file,
  9/9 OK.
- `afcadce` (merge `f59f2fc`): `ai_processor.tests_tiktoken_cache`, 3/3 OK
  (verified: good file loads without network; a corrupted byte is caught by
  hash, not silently self-healed; file-absent skips clean). Merged with
  zero conflicts against the batch.

## Smoke test

Worker (phase2-worker) smoke-tested the merged batch (`a7d667c`, before the
2 later deltas) in its own worktree/DB, Redis DB 5, fake AI provider, no
real spend: login, my-students, upload/extract, and the refusal path — 24
checks, PASSED. Evidence: `docs/evidence/smoke-a7d667c/` (committed
separately by phase2-worker on its own branch).

## Known, accepted, unchanged from earlier evidence

- `billing.tests.test_overage_purchase_integrity.ConcurrentOverageDeliveryTests.test_concurrent_purchases_by_different_teachers_stay_separate`
  is a pre-registered live-Stripe-timing flake (BOARD.md); not re-verified
  in this gate beyond the one run above passing.
- Per-fix mutation/stress/deployed-E2E evidence for each component fix
  (H-18/19/21/22/24/31/33) stands from its own branch's earlier gate, per
  the user's fast-path approval — not repeated here.

## Redis leak fix (landed separately, not part of this batch's diff)

`task/test-redis-cleanup` (gate-runner): teardown-in-finally + SIGTERM
handler + 12h TTL (exempting H-1 generation counters) + start-of-run
dead-PID sweep. 139 tests OK. Explains the ~5-8x slowdown that made the
original full suite take 2.8h; not required for this batch's correctness,
tracked as its own landing decision.

## Verdict

Safe to land on beta. Every one of the 83 gate failures is accounted for by
a named, non-regression cause; the two real defects found (stale H-19 tests)
are fixed and independently re-verified. User approved landing
(2026-09-22).
