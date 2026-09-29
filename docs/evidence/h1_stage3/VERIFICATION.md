# H-1 Stage 3, steps 1–3 (targeted invalidation; the wildcards stay): Verification

Code verified at 65202ee. The gate target 168d57e = 65202ee + be0bdbc (docs: first-gate record) + the renewal-anchor test fix 3802d64, which is byte-identical and separately VERIFIED. Evidence tip dd2dc6a is docs-only on 168d57e.

## Code and tests (at 65202ee)
- **Merges:** my re-merges reproduce 0ca0404 (tree a3bbcde) and 65202ee (tree 1e3a2eb) exactly. The first merge's message wrongly says 4b902fc; the parent is be78221, as the evidence records.
- **Scope:** 13 production files (signals ×5 apps, 4 data-repair commands, small edits in assignments/views.py, dashboard/views.py and students/services.py); the rest is tests. No wildcard call is removed. One call site is added: `invalidate_submission_caches_bulk` (students/signals.py:110) reuses the pre-existing `SUBMISSION_CACHE_PATTERNS`. **Step 4's removal inventory must include it.**
- **Premises pre-checked:** no CreditBucket write bypasses save()/create() (P5); no production code sets Assignment.teacher (G1).
- **Tests in my own detached checkout:** `tests_cache*` 250 + `tests_probe*` 4 = **254 OK**.
- **My independent mutants** (the matrix disables the legacy wildcards; each restore sha-verified): G2 (summary key unversioned) is killed by the withdrawal-revokes-cached-access test; G4 (no school-admin fan-out) by the school-move test; P1 (claim not invalidated) by the claim and retry tests; P5 (CreditBucket receiver no-op) by the credit-grant test.
- **§8 status-summary:** one base parametrised by `legacy_wildcards_live`. FRESH with the wildcards live (production), STALE with them off, plus an eviction check. There's no live bug; it's a pinned **step-4 prerequisite** (version this family first and flip those tests).

## §6 strict gate: reconciled against the committed files, not the relay
- The first attempt (be0bdbc) is recorded honestly: run 1 had 4774 tests with 1 FAIL, the date-triggered twelve-renewals test, and run 2 never started. The cause is now fixed and verified (3802d64).
- The re-run (`strict_gate.py run 168d57e h1-stage3-gate10-rerun --runs 2`): `summary.json` says verdict PASS on commit 168d57e8…, no verdict reasons, both runs `ran 4775, result OK, skipped 26, failures []`, and per-test tallies consistent (4749 ok + 26 skipped). `suspend_events: 0`, sleep inhibitor confirmed, and base beta 197aa46 unchanged start to end. The committed run logs themselves contain `Ran 4775 tests … OK (skipped=26)` for r1 and r2.
- **All 24 entries in RAW_LOG_SHA256SUMS.txt match**, hashed from the committed (gunzipped) blobs.
- Gate 5 now points to the Redis-outage probe and the Celery claim→fail→re-claim test, as requested.
- Gate 8 is recorded as **PARTIAL** (real routes, services, Postgres and Redis; no live gunicorn or deployed run). That's honest, and the landing implication is for the SM (see my note to them).

## Verdict: VERIFIED (steps 1–3)
H-1 stays OPEN: step 4 (wildcard removal) is founder-approved for a separate branch, with two prerequisites recorded above.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
