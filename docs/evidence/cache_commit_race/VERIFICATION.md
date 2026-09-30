# H-25 cache commit-race: independent verification (@233ebe7, on beta e7e4bdf, for batch-2)
I didn't author H-25.

**Rebase integrity.** The non-docs delta has the same `git patch-id --stable` before and after the rebase (4b902fc..bd2f016 and e7e4bdf..c5d1a6e~1 are both b4d0b26bf41e). `AutoGrader/cache_generation.py` is the identical blob (45992d1013) at bd2f016 and 233ebe7, and it's unchanged between 4b902fc and e7e4bdf. c5d1a6e is test-only typing.

**Code (15 production lines).** `bump_many` bumps immediately, then, when `in_atomic_block`, registers `transaction.on_commit(lambda: _bump_now(unique))`. This is correct across the cases: autocommit bumps once, after the write has committed; commit gives a second bump that orphans anything cached in the pre-commit window; a rollback or a savepoint rollback discards the callback; nested savepoints re-bump once at the outer commit. Every stage-3 fan-out goes through `bump_many`, so it inherits the protection. `bump_generation` is not covered, but it has 0 production callers outside cache_generation.py; that's logged as H-52 per the SM.

**Tests** (my detached checkout of 233ebe7): `AutoGrader.tests_cache_commit_race` **14/14**.

**Gate 4: I re-ran the Security Lead's replay on the rebased tip, as their own EVIDENCE requires after a rebase.** Harness `cache-race-gate4/tests_redteam_cache_commit_race.py` with its `settings_ccrprobe.py`, unmodified (Redis DB 10, own-prefix keys, never flushed; 0 leftover keys afterwards), with a unique test DB, 5 rounds:
| Tree | Stale after removal | Detail |
|---|---|---|
| beta e7e4bdf (control) | **5 of 5** | every round: `post=[200 ×5]`, `still_enrolled_in_db=False`, i.e. a removed student is still served a cached 200 |
| H-25 233ebe7 | **0 of 5** | every round: `post=[404 ×5]`; an entry was cached in the window (`student_cache_keys=1`) but never served |
This matches the Security Lead's original run on bd2f016 (4/5 vs 0/5). **Gate 4 PASS on the rebased tip.** Note: beta is live, so the control shows this revocation bypass is live today, and H-25 closes it.

**My mutants** (independent of yours; each restore sha-verified):
| Mutant | Result |
|---|---|
| a: on_commit re-bump removed | KILLED (8 tests, including test_a_read_in_the_pre_commit_window_is_not_served_stale) |
| b: re-bump only the first scope | KILLED (6 assertion failures, including test_only_the_writers_scopes_move_and_the_commit_exactly_repeats_them; plus 6 errors from slicing a non-sequence) |
| c: `in_atomic_block` check inverted | KILLED (9 tests, including test_outside_a_transaction_a_bump_happens_once) |
Your re-run battery (07_mutation_battery_rebased.log: control 14/14; a 8, b 1, c 9 failures, crash safety now measured) agrees.

**Gates:** 2 PASS, 4 PASS (re-run by me on 233ebe7), 8 PARTIAL with the founder's written sign-off (unchanged). Gate 1/10 full suite: covered by the batch-2 run.
Verdict: VERIFIED. Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
