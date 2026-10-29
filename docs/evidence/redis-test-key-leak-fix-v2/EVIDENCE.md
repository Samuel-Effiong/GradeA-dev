# Redis test-key leak fix (v2) — evidence

Commit: `d50aa74` on `task/redis-test-cleanup-v2` (base: `beta` at
`5375baf`), worktree `GAP-redis-cleanup`. Not landed, not pushed.

This supersedes the never-landed `task/test-redis-cleanup` branch
(`002be62`/`d4166eb`, base `a7d667c`), which went stale as beta moved on. It
is a fresh diagnosis and fresh port, not a merge of that branch: the old
commits were read only as a reference for the approach.

## Re-verified before porting anything

1. **The leak still reproduces on current beta.** A single, successful,
   unmodified `python manage.py test classrooms` run (isolated Postgres +
   Redis, no other process touching either) left **333** `gaplus-t<pid>:...`
   keys behind — mostly H-1 generation counters (`cachegen:usr:...`,
   `cachegen:crs:...`, `cachegen:sch:...`), plus Celery/kombu binding keys
   and a couple of presence keys. Confirmed with a plain Redis `SCAN` before
   and after the run, not by trusting the old diagnosis.
2. **The H-1 generation-counter exemption still applies as originally
   written.** `AutoGrader/cache_generation.py`'s `GENERATION_KEY_PREFIX =
   "cachegen"` is unchanged since the original fix was written, and the
   legacy wildcard `delete_pattern` invalidation this project is migrating
   away from (H-1) is still the live mechanism in production code
   (`assignments/signals.py`, `classrooms/signals.py`, `users/signals.py`,
   etc. all still call it) — the H-1 stage-3 wildcard-removal work
   (`task/h1-stage3-wildcard-removal`) has not landed on beta. So an expired
   counter would still read back as `DEFAULT_GENERATION` and revive every
   superseded cache entry, exactly the failure the exemption exists to
   prevent. Nothing about this exemption needed to change.

## Fix (this commit)

Same design as the original, ported file-for-file where the surrounding
code hadn't moved:

- `AutoGrader/redis_test_hygiene.py`: `sweep_dead_prefixes()`,
  `delete_own_keys(pid)`, a SIGTERM handler, and the
  `redis_test_hygiene()` context manager wiring them — **plus one addition
  not in the original** (see "Gap found" below): a second
  `sweep_dead_prefixes()` call at teardown, not just at start.
- `AutoGrader/redis_test_runner.py`: `RedisHygieneRunner(DiscoverRunner)`.
  Byte-identical to the original (`002be62`) — confirmed by matching
  sha256.
- `AutoGrader/settings.py`: `TEST_RUNNER =
  "AutoGrader.redis_test_runner.RedisHygieneRunner"`, inserted at the same
  point the original patch used (just before the `DJANGO_REDIS_SCAN_ITERSIZE`
  comment) — that surrounding code was untouched by everything that's
  landed on beta since.
- `AutoGrader/test_cache.py`: `PrefixScopedRedisCache` gets the same
  `TEST_KEY_TTL_SECONDS` (12h) clamp on `set`/`add`/`set_many`, exempting
  `cachegen:...` keys, merged into the class's *current* form — this file
  had since grown a `key_prefix` property (per-process pid resolution for
  `--parallel`, unrelated to this fix) that didn't exist when `002be62` was
  written. The clamp logic itself is untouched; only where it was spliced
  in changed.
- `AutoGrader/tests_redis_hygiene.py`: ported unchanged (byte-identical to
  `002be62`, confirmed by matching sha256) and passes as-is against current
  beta with no modifications needed.

## Gap found during re-verification (not in the original fix)

The H-9 fork-isolation self-tests
(`AutoGrader/tests_redis_test_isolation.py`'s `_fork_broker_worker`/
`_fork_cache_worker`) spawn **child processes** that write their own
`gaplus-t<childpid>:...` keys (a deliberate part of proving per-process
isolation) and exit mid-run. Neither original layer reaches those keys:
the start-of-run sweep already ran before the children existed, and
teardown's `delete_own_keys()` only removes the *current* process's own
prefix, not a child's. This predates this fix — it was never covered by
the original `002be62` either.

Reproduced directly: after a full `AutoGrader` app-suite run with the
fix ported as-is (no second sweep), 5 keys were left behind — Celery/kombu
queue keys from the fork-isolation test's dead child pids, carrying no TTL
of their own (`TTL == -1`), so they would never disappear on their own.

Closed with a second `sweep_dead_prefixes()` call in the `finally` block,
after `delete_own_keys()`. Safe to repeat: it only ever touches a prefix
whose owning pid is confirmed dead, so it can't reach a live sibling run's
keys. Re-ran the same `AutoGrader` suite after adding it: **0** keys left
behind.

## Proof

- `AutoGrader.tests_redis_hygiene` (12 tests, unmodified from the original):
  `Ran 12 tests in 17.128s — OK`.
- `python manage.py test classrooms` (the reproduction module) with the fix
  in place: `OK`, **0** `gaplus-t*` keys left behind (down from 333 before
  the fix).
- `python manage.py test AutoGrader` (364 tests, includes the fork-isolation
  self-tests that surfaced the gap above): `OK`, **0** keys left behind
  after adding the end-of-run sweep (was 5 without it).
- `python manage.py test students billing users assignments`: exit code 0
  (all passing — Django's runner exits non-zero on any failure), **0** keys
  left behind.
- `pre-commit` on the changed files: check-yaml, end-of-file-fixer,
  trailing-whitespace, check-ast, docstring-first, large-files,
  merge-conflicts, case-conflicts, builtin-type-constructor,
  BOM, detect-private-key, debug-statements, black, mypy, flake8, isort,
  bandit, detect-secrets — all passed on first attempt.

## Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis), on
this worktree, branch `task/redis-test-cleanup-v2` off `beta` at `5375baf`.

`python manage.py test --settings=settings_worktree --parallel 4 --noinput`

- Ran 4597 tests in 286.809s (~4.8 min)
- **OK (skipped=28)**
- 0 `FAIL`/`ERROR` lines anywhere in the log (grepped, not just the final
  summary line)
- Post-run scan: **0** `gaplus-t*` keys left in the isolated Redis.

Tail of the full run: `full_regression_tail.txt`.

## Wall-clock, for context (not a controlled A/B)

The original evidence doc measured the *effect* of a one-time manual key
cleanup (`billing.tests`: 2,492s → 316s, 7.9x) but never measured a
full-suite run on the actually-fixed tree. This run — 4597 tests in 287s,
on a Redis that started and ended empty — is the first full-suite number
recorded against this fix. It is not a controlled before/after on identical
hardware/load (the ~2.8h figure this whole effort traces back to was
measured on a since-changed tree, with the shared Redis in an unknown leaked
state at the time), so this number should be read as "the fixed tree runs
fast and leaves no residue," not as a multiplier over the old figure.

## Conclusion

The leak reproduces on current beta exactly as originally diagnosed, the
H-1 exemption is still correct and required, and the ported fix (plus the
one addition this re-verification surfaced) leaves zero `gaplus-t*` keys
behind across every suite it was run against, including the specific
self-tests that exercise forked child processes. Not yet landed — reported
to the Senior Manager for independent re-verification before merging to
beta.

## File hashes (post-commit, from the committed blob)

```
$ git show d50aa74:<path> | sha256sum
AutoGrader/redis_test_hygiene.py   885783e22db460c032ec4e8769c008f27d8c14f0650ccea740cd16fb188a197a
AutoGrader/redis_test_runner.py    bdc75d3f5e80222e759479080eb46b4dabbd7eee7daf1981796b9250e2fb7f27
AutoGrader/tests_redis_hygiene.py  1b5bce0bef5587f08962d54b60f1b5f7cfc97147d986a88719f357d5a0fdd2a7
AutoGrader/settings.py             9a979d3e49f066036af55af1ea7e284f33e5df37eb503bd51e797c4ea29446b6
AutoGrader/test_cache.py           3a8620ae1b7fad749f86e5b9ad992d80b6561d2528797996b5369f5da87a7b2e
```

Note: `redis_test_runner.py` and `tests_redis_hygiene.py` hash identically
to the original `002be62` commit — confirmation that those two files were
ported byte-for-byte, unchanged.
