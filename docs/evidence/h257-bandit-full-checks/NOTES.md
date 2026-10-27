# H-257 notes (Hardening Engineer, 4d)

- **The row** (batch 29 row B): Bandit runs every check, not only B101 and B102. Each finding is fixed, or marked
  `# nosec BXXX` with a one-line reason on its line. A blanket skip is allowed only for a check that is noise
  across the codebase, and must be named.
- **Base:** H-256's tip `2a6e3eb7` (stacking accepted by the Senior Manager; the bundle merges H-256 at
  `2a6e3eb7`, then H-257). On `31a86456` the same run also lists H-256's own bug (B113, below).
- **Branch** `task/h257-bandit-full-checks`: tests `08b8ff48`, code `e7f1bb1c`, docs and gate pack (this folder).

## Before

- **The config:** `pyproject.toml` had `tests = ['B101', 'B102']`, so Bandit ran two of its checks.
- **On 31a86456**, with the limit removed, `pre-commit run bandit --all-files` (bandit 1.8.6) found **57 issues:
  High 1, Medium 1, Low 55**.
  - The Medium was B113 at `users/views.py:2200`, the Google token exchange with no timeout: H-256's bug.
  - With every check on, Bandit would have caught it.
- **On 2a6e3eb7** (H-256 in): 56 issues. Outputs: `bandit_all_checks_31a86456.txt` and
  `bandit_all_checks_2a6e3eb7.txt`.
- **With the limit in place,** the hook passed on both bases (`bandit_limited_2a6e3eb7.txt`).

| Test | Count | One example | What was done |
|---|---|---|---|
| B324 hashlib (High) | 1 | `users/mixins.py:47`, the MD5 that names a list's cache entry | **fixed**: `usedforsecurity=False`; the digest is unchanged (`users.tests_h257_cache_key_digest` pins it) |
| B106 hardcoded_password_funcarg | 19 | `classrooms/test_views.py:42` `password="password123"` | 16 test fixtures, plus the OTP test's token string, are left out by the **test-module exclude**. Per-line nosec: `isolation_harness.py:152` (a throwaway benchmark account), `scenarios_fast.py:90` (Stripe's test payment method id), `backfill_pending_student_invites.py:94` (an `activation_token` filter) |
| B603 subprocess_without_shell_equals_true | 11 | `scripts/check_migration_safety.py:87` | 5 are nosec (fixed argument lists, no shell); 6 are in `scripts/test_strict_gate.py`, a test module now excluded |
| B607 start_process_with_partial_path | 6 | `scripts/check_migration_safety.py:87` (`git` from PATH) | 3 are nosec, 3 are in the excluded test module |
| B404 import subprocess | 6 | `scripts/credscan.py:72` | **the one blanket skip** (`skips = ['B404']`): it flags every import of subprocess, while the calls are judged by B603, B606 and B607, which stay on |
| B110 try_except_pass | 5 | `ai_processor/services.py:333` | nosec on the `pass` line; the except lines and their `noqa: BLE001` comments are unchanged |
| B112 try_except_continue | 3 | `users/services.py:34` | nosec on the `continue` line |
| B105 hardcoded_password_string | 3 | `users/views.py:2193`, the token URL | nosec: a URL; `'[secret]'` (the scrubber's replacement text); `'\\frac'` (a LaTeX command) |
| B606 start_process_with_no_shell | 1 | `scripts/strict_gate.py:1175` | nosec: the gate re-executes itself under systemd-inhibit |
| B311 random | 1 | `billing/live_qa/chaos.py:297` | nosec: seeded fault injection for live QA |

## What changes

- **`pyproject.toml`:** the `tests` limit is removed, and `skips = ['B404']` is added with its reason.
- **`.pre-commit-config.yaml`:** the bandit hook's exclude is now
  `tests/|examples/|docs/|setup.py|(^|/)tests?_[^/]*\.py$`. That is the tests/ directories plus files named
  test_*.py or tests_*.py (SM ruling 3).
  - It newly excludes 414 of the 1241 tracked `.py` files: 403 named tests_*.py and 11 named test_*.py.
  - All 414 are test modules or test helpers. Two helpers hold no tests: `ai_processor/tests_reading_pinned.py`
    (settings pinned for tests) and `classrooms/tests_support_add_by_email.py` (a fixture helper). No production
    file is excluded.
  - It covers 26 of the 56 findings: all 16 `password123` fixture lines; the OTP test's
    `'original-7-day-token'`, in a test module, so excluded rather than nosec'd; and the 9 subprocess findings
    plus 1 B404 in `scripts/test_strict_gate.py`, that script's self-test module.
- **`users/mixins.py`:** `hashlib.md5(query_params.encode(), usedforsecurity=False)`.
- **21 lines carry `# nosec BXXX  # reason`.**
  - **Separate ids with spaces:** bandit 1.8.6 parses `B603,B607` as one match and keeps the last id only (P7
    below shows it).
  - **Reasons after a second `#`:** bandit reads every word up to the next `#` as a test name. The repository's
    older style, `# nosec B603 B607 - git, ...` in `scripts/credscan.py`, makes Bandit warn on each word. Those
    warnings are left as they were.
  - **Placement:** Bandit honours a nosec anywhere in the statement's line range. So B110 and B112 sit on the
    `pass`/`continue` line, and every marked line stays within black's 88 columns.
- **At the tip** the hook passes on every file with 0 issues.

## Tests

- **`users.tests_h257_cache_key_digest`** (2 tests) is a control, green before and after.
  - The list key for `{"search": "x", "page": "2"}` must be `widgets:user_id__7:query__6b28336181f78eb555d53247f909c6b5`.
    That digest was computed before the change.
  - The order of the query string must not change the key.
- **The seam** (`seam_check.py`):
  - every changed Python file has the base's syntax tree (comments only), except `users/mixins.py`, which differs
    by the one keyword;
  - the two config files change only as written.
  - It passes at the tip.

## The red run (written before the gate)

- **Setup:** the tree as at 2a6e3eb7 with only pyproject's `tests` limit removed.
- **Expected:** `pre-commit run bandit --all-files` exits 1 with exactly 56 issues: B105=3 B106=19 B110=5 B112=3
  B311=1 B324=1 B404=6 B603=11 B606=1 B607=6.

## Planted findings (7; expected issues written before any run)

- **How each plant runs:** `pre-commit run bandit --files <file>` on one planted change; the hook must list
  exactly the written issues.

| Id | Planted | Hook run on | Expected |
|---|---|---|---|
| P1 | the md5 without usedforsecurity | `users/mixins.py` | B324 x1 |
| P2 | the Google post without its timeout | `users/views.py` | B113 x1 |
| P3 | the nosec marker removed from `SECRET = "[secret]"` | `AutoGrader/log_scrubbing.py` | B105 x1 |
| P4 | the test-module pattern removed from the exclude (the exclude is read) | `classrooms/test_views.py` | B106 x11 |
| P5 | a scratch file `h257_planted_scratch.py` with `password = "hunter2-planted"`, deleted after | the scratch file | B105 x1 |
| P6 | the B404 skip removed (the skip is read) | `scripts/credscan.py` | B404 x1 |
| P7 | `# nosec B603 B607` written `B603,B607` | `scripts/check_migration_safety.py` | B603 x1 |

## Gate

- **Script:** `run_h257_gate.sh.txt`.
- **Order:**
  1. the red run (56);
  2. the seam;
  3. the hook at the tip (Passed, 0 issues);
  4. own + three cache modules + the 21 guards;
  5. the 7 plants.
