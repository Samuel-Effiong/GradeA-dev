# H-39 network guard — Verification

Branch `task/h39-network-guard` @ 816ddd1, off beta 4b902fc.

## Re-run myself
- `AutoGrader.tests_network_guard` + `users.tests_email_domain_rules`: **35/35 passed** (8 dedicated guard tests, matches claim).
- Confirmed the guard is real infrastructure, not decorative: `AutoGrader/settings.py` sets `TEST_RUNNER = "AutoGrader.redis_test_runner.RedisHygieneRunner"`, which wraps `super().run_tests()` in `block_real_network_calls()` — every test run actually goes through it. Grepped the whole tree: `network_guard` is imported only by the test runner and its own test file, never by production code, matching its docstring's claim.
- Code read: `_guarded_connect`/`_guarded_connect_ex` only intercept `AF_INET`/`AF_INET6`; AF_UNIX (Postgres socket) is never touched — confirmed via `test_unix_sockets_are_never_touched` and by reading the family check directly. The nested-context restore logic saves/restores whatever was installed on entry (not hardcoded originals), so a nested `with` can't disable the outer guard — read this carefully since it's the one subtle part of the design, and it's correct.
- Confirmed the hermetic fix for the widely-cited `test_nothing_is_exempt_by_default` environmental failure: `@override_settings(EXEMPT_EMAIL_DOMAINS=[])` added directly on that test. This is the fix every other branch I verified today (token-epoch, l2, patch-password, h3, epic-a-land, h5, h14) cited as "the known environmental failure" — confirmed it actually neutralizes it.

## Full suite run
Ran the full `--parallel 4` suite myself (flock-wrapped): **4677 tests, 1 failure, 28 skipped.** The known `EXEMPT_EMAIL_DOMAINS` failure is GONE (this branch's own fix), but a different, unrelated test failed: `AutoGrader.tests_redis_hygiene.SweepTests.test_delete_own_keys_takes_only_that_exact_prefix` (`AssertionError: 1 != 2`).
This branch's diff does not touch `tests_redis_hygiene.py` or `redis_test_hygiene.py` at all. Re-ran the failing test alone 3/3 clean, and the whole module under `--parallel 4` 2/2 clean — only shows up under the full 4677-test load. Per the SM: accepted as noted, logged as **H-45** in `docs/HARDENING_BACKLOG.md` (committed `b0a0bd8` on task/register-landed-verify), not blocking this verdict.

## Verdict: VERIFIED-WITH-NOTES
The guard itself, its wiring, and the hermetic email-domain fix are all correct and independently confirmed. The one full-suite failure is logged as H-45 and accepted per the SM, not blocking.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
