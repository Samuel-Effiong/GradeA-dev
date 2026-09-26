# Evidence: login-lockout concurrency tests (CI flake, beta 301d915)

**Commit under evidence:** `8f25eee` on `task/fix-overage-concurrency-flake` — test-only
(`users/tests_login_lockout.py`). Logs referenced below are in `docs/evidence/ci_lockout_flake/`.

## The 10 gates

| Gate | Status | Evidence |
|---|---|---|
| 1 Baseline / Regression | **PASS** (users app: see caveat) | CI run 35271899755 is the reproduction (TimeoutError at `tests_login_lockout.py:362`, raised through `:377`). Pre-fix locally, 2 cores + coverage: 3/3 pass at 17.940 / 16.442 / 17.037 s process wall. Post-fix: whole `users` app `Ran 447 tests … OK (skipped=2)`, **obtained under connection starvation** — see Risk 2. Behaviour change recorded as the 4-point record in the `8f25eee` commit message and below |
| 2 Mutation | **PASS** — 2/2 killed | L1 (F() increment → read-modify-write) kills the no-lost-increments test: `7 != 25 : an increment was lost`. L2 (lock gate at `users/serializers.py:414` disabled) kills the LiveServer test: `200 != 401` — the correct password got in. Disposable worktree at `8f25eee`, restores sha256-verified, worktree removed. `mutants_results.json`, `L1.log`, `L2.log` |
| 3 Concurrency | **PASS** | 30 concurrent real HTTP logins (LiveServer) and 25 concurrent APIClient logins; 10/10 consecutive repeats of the LiveServer test pinned to 2 cores under coverage. `margins_postfix_8f25eee.txt` |
| 4 Adversarial | **NOT APPLICABLE** — justification below | |
| 5 Failure / Recovery | **PARTIAL** | The timeout path now fails loudly with context instead of a bare `TimeoutError`. Connection starvation was observed and diagnosed, not measured through. Production failure modes are unchanged by a test-only diff and not re-tested here |
| 6 Stress / Scale | **NOT APPLICABLE** — justification below | |
| 7 Real Infrastructure | **PASS (LOCAL-REAL)** | Real Postgres, a real LiveServer over real sockets, real threads, coverage tracing. Not DEPLOYED-REAL |
| 8 Live / E2E | **NOT APPLICABLE** — justification below | |
| 9 Security / Isolation | **PASS for the property tested** | The lockout is a security control. L2 proves the rewritten burst test still fails if a locked account can log in; the mixed-traffic test still asserts a bystander account is untouched |
| 10 Final gate | **PENDING** | The CI full run triggered by the integrator's push is the release gate for this change. Not yet run |

### NOT APPLICABLE justifications (for the Senior Manager to accept or reject)

- **G4 Adversarial:** the diff changes no endpoint, serializer, permission or production code. The test itself simulates the brute-force attack, and G2's L2 mutant shows it still catches a broken defence; nothing new was exposed to attack.
- **G6 Stress / Scale:** there is no data-volume or query-count surface. The load dimension is simultaneous requests, which is covered under G3.
- **G8 Live / E2E:** nothing in the deployed request path changes. The only effect on the deployed system is which tests CI runs and how they assert.

## The 8 completion answers

1. **What changed.** In `users/tests_login_lockout.py` only: MD5 password hashing on the two concurrency classes; the urlopen timeout became a 120 s hang detector, with a timeout message that says what happened; and the exact "30 attempts counted" assertion was split into two timing-independent tests.
2. **Why it was necessary.** Beta CI failed. 30 concurrent PBKDF2 verifications (1,000,000 iterations each) saturated the single LiveServer on a 4-vCPU runner under coverage, and a request waited past 15 s for a response line. Investigating that showed the exact-count assertion was only ever true because hashing is slow. A locked account is refused **before** its password is checked (`users/serializers.py:414`), so later attempts never increment. The test could fail if the machine was too slow, fail if it was too fast, and pass in between while proving nothing about lost increments.
3. **What was tested.** The pre-fix baseline (3 runs); the post-fix LiveServer test 10/10 pinned to 2 cores under coverage; two mutants; the whole `users` app; and the threshold patch reaching the worker threads (see below).
4. **Gates passed.** 1, 2, 3, 7 (local-real), and 9 for the property tested.
5. **Gates incomplete.** 5 is PARTIAL; 10 is PENDING the post-push CI run; 4, 6 and 8 are N/A subject to acceptance.
6. **Risks remaining.** Listed below.
7. **Exact commit.** `8f25eee67ce13c3eca9f273b11b3363b004f99af`.
8. **Same commit as release?** The integrator cherry-picks it onto the push branch. The pushed SHA will differ, so the post-push CI run on that exact SHA is the gate that counts.

## Behaviour change: the 4-point record

1. **Previous assertion:** a burst of N concurrent wrong passwords leaves `failed_login_attempts == N` and the account locked.
2. **New intended assertions:**
   - (a) With `MAX_LOGIN_ATTEMPTS` patched above N, the count is **exactly** N and the account is **unlocked**. That proves no lost increments, and it proves the patch reached the threads: at the real budget of 5, 25 failures would have locked the account.
   - (b) At the real budget: every response is 401 and carries one of the two rejections the code produces; the account is locked; `MAX_LOGIN_ATTEMPTS <= n <= N`; and the correct password is refused with no token.
3. **Why this is correct:** how many guesses land before the lock is timing-dependent by design. Pinning that number asserted an artefact of the hasher's speed.
4. **Tests that prove it:** `test_concurrent_wrong_passwords_do_not_lose_increments`, `test_a_concurrent_burst_locks_the_account_at_the_real_budget`, `test_concurrent_brute_force_against_live_http_server_is_held`, and `test_mixed_concurrent_traffic_one_account_locks_another_is_unaffected`.

## Measurements

These are process wall-clock times (Django start-up, test database setup and coverage included), one test label per run, `taskset -c 0,1 coverage run`. Before each batch the pool was at 57–59 of 100 connections, and no run logged `too many clients`.

| | Runs | Result | Range |
|---|---|---|---|
| Pre-fix (`301d915`) | 3 | 3/3 pass | 16.442–17.940 s |
| Post-fix (`8f25eee`) | 10 | 10/10 pass | 7.972–11.447 s (median ≈ 8.87 s; worst run 1.29× the median) |

The integrator measured the **test's own runtime** separately with `--durations`: **1.153 s**. That is the right quantity for its "≤ 8.0 s" criterion. The process wall times above measure something else, which caused a brief false alarm and is recorded here so it doesn't recur. A single request can't take longer than the whole test, so the per-request margin against the 120 s hang detector is more than 10×.

**A discarded batch, kept for honesty:** the first post-fix batch of 10 failed 10/10. That was my own assertion bug: I read DRF's `detail` field, but this API wraps errors as `{"success": false, "message": …}` (`users/renderers.py`, `APIJSONRenderer`). I fixed it and re-measured from scratch. The failing batch is preserved in `margins_baseline_and_discarded_batch.txt`.

## Risks remaining

1. **More connections at once.** Without the hashing delay, the burst arrives all at once. That's fine on CI's dedicated Postgres, but on the shared development box it can still hit `max_connections=100` when other sessions hold connections. Starvation shows up as HTTP 500s, which these tests correctly treat as failures.
2. **A different test tolerates HTTP 500s.** The whole-`users` run logged 20 `FATAL: sorry, too many clients already` lines and 5 `Internal Server Error: /api/v1/auth/login` at 10:10:44, yet the suite reported OK. Every lockout test asserts 401 on every response, so the tolerant test is elsewhere. The likely candidates are `users/tests_throttling.py` and `users/tests_throttle_client_identity.py`, which assert on 429 counts. This is the same false-pass family, and the integrator has raised it as its own backlog item. **Not investigated here.**
3. **Project-wide fast hashing is deferred.** The Senior Manager ruled it out for today: it touches 4,234 tests and could weaken auth tests that legitimately exercise hashing. It is scoped as a separate task with the integrator.
