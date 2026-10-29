# Batch 29: one strict full run, green (after one stop on the hooks)

Recorded by Release Builder (a0), 2026-10-27. The Senior Manager built the batch, granted the run and wrote the one
integration commit. Release Builder (a0) installed the bundle's pins, ran the hooks, stopped on the red hook, started
the run on the fixed tip and read its result. Gate 1 (the package read), the merge to beta and the push are the Senior
Manager's.

**What batch 29 is:** beta `b199eed6` (the privacy document, second edition) plus five `--no-ff` merges and one
integration commit, in this order:

| Tip | What it carries |
|---|---|
| `94a16e19` | H-255 "Stripe error text" at its gate tip `675b6d4d` |
| `2a98006e` | H-256 "timeouts on outside calls" at its gate tip `2a6e3eb7` |
| `4a01fe20` | H-258 "pip-audit in CI" at its gate tip `9263e9f8` |
| `3cf18ce7` | H-259 "Stripe log text" at its gate tip `2658ca13` |
| `2d424a48` | H-257 "Bandit full checks" at its gate tip `3e72357a` |
| `299d917e` | the Senior Manager's integration commit (see "The stop on `2d424a48`") |

- H-260 is not in this batch.
- The only conflicts in the five merges were in `docs/HARDENING_BACKLOG.md`'s table. Every row was kept.
- Each row's own gate is recorded in its own folder under `docs/evidence/`: `h255-stripe-error-text`,
  `h256-outside-call-timeouts`, `h257-bandit-full-checks`, `h258-pip-audit-ci`, `h259-stripe-log-text` (each with its
  `NOTES.md`, its gate folder, and the script, mutant and seam files the row used).

## The pins: installed before the run, on purpose

`requirements.txt` in this bundle moves five pins. They were installed into the shared `AutoGrader_env` from this
worktree before the run, on the Senior Manager's instruction, so that the suite would run on the versions the bundle
ships.

- `pip install -r requirements.txt`: exit 0, `pip_install.log.gz` beside this file (45,409 bytes unpacked).
- The dry run first said: "Would install Authlib-1.6.12 Django-5.2.17 brotli-1.2.1 pyasn1-0.6.4 tornado-6.5.9".
- `pins_before_after.txt` is the `diff` of `pip freeze` before and after: exactly five changes and nothing else —
  Authlib 1.6.3 → 1.6.12, brotli 1.2.0 → 1.2.1, Django 5.2.6 → 5.2.17, pyasn1 0.6.3 → 0.6.4, tornado 6.5.4 → 6.5.9.
- `pip check` after the install was clean.
- The venv stays on these pins. Restoring the old ones (`pip install -r pins_before.txt`, kept in
  `GAP-a0-runs/b29/`) is the Senior Manager's call, not the Builder's.
- No row's gate ran on these pins: this run is their first. Eleven Django patch releases are the single largest
  untested thing in the batch; the script's own `makemigrations --check --dry-run` on 5.2.17 said "No changes
  detected", and mypy passed.

## The stop on `2d424a48`: the hooks were red, and the Builder did not fix it

The hooks were run on the merge tip `2d424a48` before any suite. `pre-commit run --all-files` exited 1:
`hook_table_2d424a48.txt` has 26 hooks, 24 Passed, 1 Skipped ("check for broken symlinks", no files to check), and
**bandit Failed**. `git status --porcelain` was empty before and after; no hook changed a file. The suite was not
started. The block, as Bandit printed it:

```
>> Issue: [B603:subprocess_without_shell_equals_true] subprocess call - check for execution of untrusted input.
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   Location: ./scripts/pip_audit_gate.py:121:11
120	    print(f"pip-audit gate: {len(ids)} accepted advisories from {IGNORE_FILE.name}")
121	    return subprocess.run(audit_command(ids), cwd=ROOT).returncode
Code scanned: Total lines of code: 106314; Total lines skipped (#nosec): 2; skipped by #nosec BXXX: 28
Total issues (by severity): Low: 1, Medium: 0, High: 0
```

**It is a cross-row red, and neither row could have seen it alone:**

- H-257 (`3e72357a`) rewrote `pyproject.toml`'s `[tool.bandit]` from `tests = ['B101','B102']` (two checks only) to
  `skips = ['B404']` (every check but one), and widened the hook's exclude to the test files. B603 is on from then on.
- H-258 (`9263e9f8`, commit `28133025`) added `scripts/pip_audit_gate.py`, whose `subprocess.run` is the only call
  B603 finds in the whole tree.
- H-257's gate ran before `pip_audit_gate.py` existed; H-258's gate ran under the two-check configuration. Each row
  was green on its own branch. Only the merge of both has the call and the check together.

**The Senior Manager's fix, `299d917e`** (3 lines in 1 file, `scripts/pip_audit_gate.py`): the call carries its
marker and its reason —

```python
return subprocess.run(  # nosec B603  # fixed argv built above, no shell
    audit_command(ids), cwd=ROOT
).returncode
```

The argv is built by `audit_command(ids)` in the same file from the pinned version and the accepted-advisory ids; no
shell is used and nothing from outside the repository reaches it.

**Hooks on `299d917e`:** run again by the Builder (11:24 to 11:26). `pre-commit run --all-files` exited 0.
`hook_table_299d917e.txt`: 26 hooks, 25 Passed, 1 Skipped (the same symlink hook), none Failed. `git status
--porcelain` empty before and after; the tree was unchanged.

## The run on `299d917e`: green

- Against beta `b199eed6`, 237 files changed (+8796, −116). 46 of them are outside `docs/`.
- No migration: `git diff --name-only b199eed6..299d917e` names no file under a `migrations/` folder, and
  `makemigrations --check --dry-run` said "No changes detected".
- The code files outside `docs/` and outside the test modules:
  - repository root and tooling: `.github/workflows/audit.yml`, `.pip-audit-ignore.toml`, `.pre-commit-config.yaml`,
    `.secrets.baseline`, `pyproject.toml`, `requirements.txt`;
  - `AutoGrader/`: `error_messages.py`, `log_scrubbing.py`, `settings.py`, `testing/beat_locks.py`;
  - `ai_processor/`: `benchmark/answers/mutations.py`, `benchmark/history.py`,
    `benchmark/isolation_run8/isolation_harness.py`, `evidence.py`, `services.py`, `tasks.py`;
  - `assignments/pdf_renderer.py`;
  - `billing/`: `imports.py`, `license_stripe_mutation.py`, `license_views.py`, `live_qa/chaos.py`,
    `live_qa/scenarios_fast.py`, `overage_pricing.py`, `payment_method_views.py`, `qa_time_travel.py`,
    `receipts.py`, `stripe_service.py`, `tasks.py`;
  - `classrooms/`: `management/commands/backfill_pending_student_invites.py`, `serializers.py`;
  - `scripts/`: `check_migration_safety.py`, `pip_audit_gate.py`, `strict_gate.py`;
  - `users/`: `mixins.py`, `services.py`, `throttling.py`, `views.py`.

**Command:** `strict_bundle.sh 299d917e b29 Grade-Automator-Plus-beta-batch-29 task/beta-batch-29`.

- A copy is beside this file (sha256 `2c167d16…`). It is batch 28's script (sha256 `2b64776f…`) with only its log
  folder changed (`b28` to `b29`); `diff` of the two shows that one line (line 13) and nothing else.
- Everything else is the same: rules 12, 13, 16, 17 and 18, the shared lock, the 3600 s timeout, the 300 s stall
  watchdog, the load check after the pre-checks, the cap of available memory − 2 GB, and the 1.5 GB guard.
- No drill settings. No `PLAYWRIGHT_*` variable was set for the run; the platform override was used for the
  Chromium install only, in batch 27. `~/.cache/ms-playwright` still held `chromium-1208`,
  `chromium_headless_shell-1208` and `ffmpeg-1011`, checked before the start.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `299d917e` | 2026-10-27 11:26:35 to 11:37:43 (632 s of tests; 668 s wall) | **Ran 8436 tests, OK (skipped=30)**, exit 0 |

- FAIL/ERROR headers across the whole log: 0. "... FAIL" and "... ERROR" result lines: 0. The one line that starts
  with "ERROR" is a test's docstring ("ERROR (our environment is wrong) must be distinguishable from ... ok").
- No suspend. The stall watchdog never fired, and the memory guard never acted.

**Tests per app** (they sum to 8436): ai_processor 1425, assignments 774, audit 377, AutoGrader 786, billing 2255,
classrooms 640, dashboard 430, students 867, users 882.

- Against batch 28 (8376), that is +60, the number counted before the start by syntax tree (`b199eed6` to
  `2d424a48`; `6db6c4b6` to `b199eed6` changes no test file). `299d917e` touches one non-test file.
- Test for test, the log has 62 names batch 28's log does not, and 2 of batch 28's are gone. The 2 are renames
  inside modules the batch changes, not losses:
  - `AutoGrader.tests_error_messages.DescribeStripeErrorTest.test_uses_stripe_user_message_when_present` →
    `test_uses_a_card_errors_user_message` (H-255);
  - `billing.tests.test_overage_price_drift.PriceAuthorityTests.test_the_refusal_names_both_numbers_so_it_can_be_acted_on`
    → `test_the_refusal_detail_names_both_numbers_so_it_can_be_acted_on` (H-255).
- The 60 new tests are in the modules named before the run, with the counts named before the run:
  `AutoGrader.tests_pip_audit_gate` 12 (H-258), `ai_processor.tests_h256_provider_timeout` 5,
  `users.tests_h256_google_exchange_timeout` 3, `billing.tests.test_h256_stripe_network_retries` 2 (H-256),
  `billing.tests.test_h255_stripe_error_text` 13 (H-255), `billing.tests.test_h259_stripe_log_text` 23 (H-259),
  `users.tests_h257_cache_key_digest` 2 (H-257).
- All 62 passed. No test in batch 28's log is missing from this one for any other reason. For the tests with a
  docstring, the "ok" is on the next line.

**Skips:** 30, the same set as batch 28, test for test (opt-in real AI 12, load tests 9, network or Redis opt-in 5,
the two in-worker fork tests, the two benchmarks). No skip names Chromium: the renderer tests all ran, as in batch 28.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran: 24 tests, counted by their own test lines, and none failed.
H-257 changes how the cache key is hashed (`md5(..., usedforsecurity=False)`) but not the key text, which is what
these 24 pin; `users.tests_h257_cache_key_digest`'s 2 new tests pin it from the other side.

**The cross-row risk, named before the run.** `GAP-a0-runs/b29/cross_row_risk_named_before_run.txt` names five. Four
were as expected; the fifth is the hook red above, which the file names and which stopped the first start.

1. **The pins.** As expected: "No changes detected" on Django 5.2.17, mypy green, and no test anywhere in the suite
   failed on the new versions. This is the only evidence that the five pins are safe for this code.
2. **H-256's timeouts.** `blocked_outbound=0`, as in batch 28. The log has 0 `BlockedNetworkCallError` raises and
   names `oauth2.googleapis.com` 0 times. No older test that asserts a call's kwargs exactly broke on the added
   timeout argument.
   - `users/views.py`'s "Google OAuth token exchange failed" ERROR line appears 7 times, up from 4 (an eighth match
     is a test's docstring). The 3 new ones are H-256's own tests' — `ReadTimeout`, `ConnectTimeout` and a
     `ConnectionError` raised by their stand-ins, logged in the three lines that precede
     `users.tests_h256_google_exchange_timeout`'s three results. The other 4 are batch 28's, from
     `users.tests_auth_audit_doors` and `users.tests_google_auth`.
   - H-256 also changed what that line says: batch 28 logged the bare sentence, this run logs
     `error=<class> frames=<call chain>` after it. That is the shape
     `test_a_timeout_is_logged_by_its_class_only` pins.
3. **H-257's hashing.** Rule 20 green, unchanged (above).
4. **H-255 and H-259 together.** Both rewrite Stripe error and log text in the same files; each was gated without
   the other. All 13 of H-255's and all 23 of H-259's tests passed in the same run, and no older billing test
   failed: `billing` ran 2255, none red.
5. **H-258's tests offline.** Its 12 tests read `requirements.txt`, `.pip-audit-ignore.toml` and the workflow from
   disk with a fixed TODAY (2026-10-27) and made no network call: 12 passed, `blocked_outbound=0`.

**Load and memory:**

- Load 7.49 6.23 5.16 at the pre-start check (11:26:22), with 14,707 MB available. The script's own check after its
  pre-checks read 6.19 6.01 5.10, under 8, so the start did not wait.
- The load at the end was 8.15 8.52 6.64. This is the highest end-of-run load of the recent batches; the run still
  came in at 668 s wall.
- 14,487 MB available at the start, so a 12G cap. The lowest available during the run was 12,259 MB.

**The log:** `strict_b29.log.xz` (packed with `xz -9e`; 477,880 bytes, under the hook's 500 KB limit of 512,000
bytes).

- 5,779,954 bytes unpacked, sha256 `a78a3dcac4b6265a8815fb288ede73a96bdadab07e300eb625b249de5e864e1e`.
- Unpacking it was checked to give the identical bytes.
- `strict_b29.summary.txt` is the script's summary.
- The log's own timestamps (`[2026-10-27 10:2x:…]`) are in UTC, one hour behind WAT.

**Not a measure: the counts of worker log lines.** "API request refused" (H-227's WARNING) appears on 978 lines here
against 1,319 in batch 28, and "Free trial plan not found" on 7,009 against 6,991. How many of these worker lines
reach the log varies by hundreds from run to run on the same tip (batch 27 ran the same tip twice: 1,438 and 2,131).
"API Exception" is 14, as in batch 28. Tracebacks are 551, down 10. None of these numbers is a result of this batch,
and none should be read as one.

**Not shown by this run:**

- the pip-audit gate against the real advisory database (H-258): its 12 tests read files from disk, and
  `.github/workflows/audit.yml` runs the real thing only in CI;
- Bandit's full check set on a real pull request (H-257): the hooks ran it here on the whole tree, which is how the
  stop above was found, but CI's own run is not in this record;
- the real Stripe API: every H-255 and H-259 test uses a stand-in, so what this run shows is the text our code
  writes, not what Stripe sends;
- real Google sign-in, real AI calls and the load tests (opt-in, skipped as in batch 28);
- the PDF renderer on the production image: the laptop's Chromium is Playwright's unofficial ubuntu24.04 fallback
  build on Ubuntu 26.04, while the image installs its own (`Dockerfile:51`, `--with-deps`);
- the five pins under anything but this suite: no live traffic, no production image build;
- anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
