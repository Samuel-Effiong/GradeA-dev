# Verification: H-97, the Redis test hygiene visits every database (d5)

- **Branch:** task/h97-redis-hygiene-all-databases at **1e407f75**, on task/beta-batch-6 93cb8648 (to be base-updated for bundle 7). The code tip is f1e0e9d7; 1e407f75 adds evidence only.
- **Commits:** 5d8af9f4 and 78399ff9 (tests), a4f379be (the fix: `location_for`), 60415c99 (v2's H-94 note: per-process key names), f1e0e9d7 (v2's finding at the static read: a socket URL keeps its credentials).
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## The defect and the fix, as read
- **Before:** the hygiene code built its 16 "per database" clients with `redis.Redis.from_url(location, db=n)`. Where the URL names a database (the local and the CI layout), redis-py ignores `db=`, so all 16 clients were on the URL's own database. The teardown and the dead-pid sweep never reached databases 1–15, where six test modules write.
- **After:** `location_for(location, db)` puts the database into the URL itself (the path of a redis:// or rediss:// URL, the `db` parameter of a unix:// one). `_clients` keeps its single `from_url` call, so H-73's raw-client registry is unchanged.
- **The sweep's rule is untouched:** SCAN for `gaplus-t*:*`, the anchored pattern `^gaplus-t(\d+):`, dead pids only, never the caller's own pid, UNLINK of the matched keys. No database-wide command.

## Static checks
| Check | Result |
|---|---|
| Production diff | AutoGrader/redis_test_hygiene.py only: `location_for` and one changed line in `_clients` |
| The pattern cannot match other families | v2 checked the compiled pattern on seven shapes: only `gaplus-t<digits>:` matches. The dev server's `gaplus:` prefix, `gaplus-t123abc:`, `gaplus-tnotapid77:`, a prefix not at the start: none match |
| URL shapes | v2 ran `location_for` on ten shapes through redis-py's own parser (a pure function call, no Redis). At bd8b7fa6 nine kept everything but the database and one lost its password (below). At 1e407f75 all ten keep everything but the database |
| v2's finding at the static read | A unix:// URL with credentials in the network location (`unix://:pw@/path`) lost its password. Not the local or CI layout. SM ruling: fixed in this slice (f1e0e9d7, one line), with the shapes in the test and mutant D5 |
| No password printed (SM condition) | The URL-shape test keys its subtests by a label and uses fixed failure messages. None of the password strings appears in any committed evidence file (v2's grep) |
| v2's H-94 note | The sweep test's two bystander keys carry the process id and keep their shapes; the test asserts the second does not parse as a pid prefix (60415c99) |
| Rule 14 | No mock returns a value that reaches a response; the tests talk to the real test Redis |

## d5's gates (read)
| Gate | Tip | Result |
|---|---|---|
| Reproduce-first: the new module on the old code | 5d8af9f4 | Red as expected (6 tests, 16 failures, 15 of them per-database subtests) |
| The three Redis modules at `--parallel 4` under the lock, three times | a4f379be | 36 OK (skipped=2), three times |
| Mutants D1–D4 | a4f379be | 4 of 4 killed |
| The 9 guards plus the repo-wide list | a4f379be | 129 OK |
| Delta (rule 15.4): tests_redis_hygiene_databases + H-73's guard module; mutant D5 | f1e0e9d7 | 29 OK; D5 killed (12 subtest failures in the three socket shapes with credentials) |

- **Rule 17:** run_mutants.py and EVIDENCE.md state `PYTHONDONTWRITEBYTECODE=1` and the `__pycache__` deletion around each mutant; restores are sha-verified.
- **Disclosed by d5:** the first gate (2d64b9fd) was red on H-73's raw-client guard, because the first fix added a second raw-client site; the rework keeps one. That gate's per-mutant logs were deleted by mistake (the summary is kept). A first start attempt ran nothing.

## The one-off sweep of the leftovers (SM ruling: the first run with the fix is the sweep)
- d5's listings (read-only: SCAN and a /proc check): 186 dead-pid test keys in databases 3, 6, 14 and 15 before the first gate's first run; none after. Every other key family in databases 1–15 has the same count before and after.
- **Vezi:** d5's evidence quotes the Vezi manager's written reply that Vezi uses no Redis at all.

## v2 runs (0b's grant, rules 16, 13 and 12, scratch worktree at 1e407f75, `--settings=settings_worktree`)
| Step | Result | File (GAP-v2-handover/runs/) |
|---|---|---|
| d5's read-only listing, before | 0 dead-pid test keys; database 0 held 1,204 keys of 2 live pids (d5's H-89 run) | h97_listing_before.txt |
| The three Redis modules at `--parallel 4`, run 1, under `flock ~/.machine-fullsuite.lock` | **36 OK** (skipped=2) | h97_parallel4_run1_1e407f75.log |
| Run 2 | **36 OK** (skipped=2) | h97_parallel4_run2_1e407f75.log |
| The listing, after | 0 dead-pid test keys | h97_listing_after.txt |
| d5's compare script | **OK**: other families unchanged in databases 1–15; no dead-pid test key left | h97_listing_compare.txt |

- No mutants (d5's D1–D5 are the ones v2 would write), so rule 17 does not apply to v2's runs.
- **What v2's run does not show:** d5's H-89 run was alive at 17:21:21 (h97_other_runs.txt), but v2's parallel runs waited for its lock and started after it had ended and removed its own keys. So v2's sweeps did not run while a live run held keys. That a live run's keys survive another run's sweep rests on d5's own test (`test_another_runs_sweeps_leave_a_live_runs_keys_in_other_databases`), not on v2's run.

## Notes (none blocks)
1. **Only the "URL names a database" layout was run** against a real Redis, by d5 and by v2: it is the only one that exists here and in CI. The other layouts are covered by the parser comparison (19 shapes in d5's test, ten in v2's check).
2. **Until H-97 is merged, every other branch keeps leaking** test keys into databases 1–15 (d5 found one new key 45 minutes after the first sweep). The first run of each branch that has the fix sweeps them.
3. **Old security-evidence keys** (`secreplay…`, `secbill…`, `oauthreplay…`, `verifyreplay…`, in databases 1 and 7–15) are not test-run keys and are not touched. Whether to remove them is a separate decision.
4. **Databases 0, 2, 9 and 10 hold a dev server's `gaplus:` and Celery keys.** The sweep now scans those databases but cannot match those keys; the compare shows them unchanged.
5. **The two skips** in the 36 are tests_redis_test_isolation's two fork tests, which cannot fork from inside a `--parallel` worker. They are unchanged by this slice and run in a serial run.
