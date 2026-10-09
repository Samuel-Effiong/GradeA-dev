# Evidence — BE-A-04 PII cleanup (§0.5a)

Backfilled evidence doc: this work landed as `d960176` (worktree
`Grade-Automator-Plus-epic-a-pii-cleanup`, branch
`task/epic-a-pii-cleanup`) on 2026-09-23, and was merged into
`integration/epic-a` at `148d32c` (the credit-audit merge point every
later Epic A piece built on). It shipped without a `docs/evidence/`
write-up at the time; this document records what was already built and
already passed, per the Senior Manager's §10 acceptance-criteria pass —
no code changes accompany it.

## 1. What was built

Per the Epic A implementation plan §0.5a (FR-A-04 / NFR-CMP-02: no student
PII in logs):

### 1.1 Fixed the 11 confirmed leaks (+ 2 same-pattern ones found while editing)

All 13 sites logged `.id` instead of a name/email, across 9 files:

| File | What changed |
|---|---|
| `assignments/tasks.py` | `print(f"...{submission.student.get_full_name}")` → `logger.info("...%s", submission.id)` |
| `classrooms/services/roster_import.py` | `logger.error("...%s %s", row.first_name, row.last_name, ...)` → logs `row_index`/`course.id` instead |
| `AutoGrader/tasks.py` | 3 sites logging `recipient_list` (raw email addresses) → logs `len(recipient_list)`; **+1 extra**: the same file's *return-value strings* echoed the raw list back to the caller, fixed identically (not in the original 11-site list) |
| `users/serializers.py` | `logger.exception("...%s", getattr(user, "email", None))` → `user.id` |
| `users/mailerlite_service.py` | 1 originally-listed site (`logger.error(... user.email ...)`) + **1 extra found while editing** (`logger.info(... user.email ...)` two lines above it, same file, same pattern) → both now log `user.id` |
| `users/signals.py` | 2 sites (`logger.debug`/`logger.error`) logging `user.email` → `user.id` |
| `classrooms/serializers.py` | `logger.exception("...%s for school %s", user_email, school_name)` → logs `user.id`/`school.id` (captured before the closure so the retry-safe `transaction.on_commit` callback doesn't hold a stale ORM reference) |
| `billing/license_service.py` | `logger.info("Created CreditWallet for teacher %s", teacher.email)` → `teacher.id` |
| `billing/management/commands/backfill.py` | `print(f"FAILED: {user_sub.id} (user {user_sub.user.email})...")` → `user_sub.user_id` (avoids an extra query too) |

13 sites total (11 confirmed + 2 same-pattern), 9 files — matches the
commit message exactly.

### 1.2 CI lint rule: `scripts/check_no_pii_in_logs.py`

AST-based (not grep — this codebase's real leaks were routinely split
across multiple lines, which a same-line grep pattern would miss). Fails
CI if any `logger.*`/`print` call anywhere in the repo passes
`.email`/`.first_name`/`.last_name`/`.get_full_name()` directly as an
argument. Wired into `.pre-commit-config.yaml` as a new local hook
(`check-no-pii-in-logs`, `files: \.py$`, excludes `migrations/`).

**Baseline, not a blanket ban**: `scripts/pii_log_baseline.txt`
grandfathers 9 pre-existing files (`billing/access_control.py`,
`billing/license_service.py`, `billing/management/commands/backfill.py`,
`billing/qa_time_travel.py`, `billing/services.py`,
`billing/stripe_service.py`, `billing/tasks.py`, `billing/views.py`,
`users/signals.py`) covering ~95 pre-existing same-pattern instances
outside the 13 confirmed-and-fixed sites — independently verified as true
positives, all logging a **school-side actor's** email (teacher, admin,
subscription owner), not a student's, at mostly-INFO level. Fixing them
was out of this PR's scope; tracked separately as **H-23** in the
hardening backlog. A file leaves the baseline once every violation inside
it is fixed (same per-file burn-down convention this repo already uses
for flake8-eradicate/E800).

### 1.3 Sentry `before_send` scrubber: `AutoGrader/sentry_scrubbing.py`

Defense-in-depth, not a substitute for the lint rule: `send_default_pii=
False` (already set) only suppresses Sentry's *automatic* user/request
context — it does nothing about a log message's own string content, and
`LoggingIntegration(event_level="ERROR")` turns every `logger.error`/
`.exception` call into a Sentry event regardless. `scrub_pii_before_send`
regex-scrubs email-shaped strings (`_EMAIL_RE`) from the two places that
content actually lands in an outgoing event: `logentry.message`/
`.formatted`/`.params`, and `exception.values[].value` plus each
stacktrace frame's `vars`. Wired into `AutoGrader/settings.py`'s
`sentry_sdk.init(before_send=scrub_pii_before_send, ...)`. Never raises —
wrapped in its own `try/except`, same "a logging failure must never fail
the caller" posture as the audit emitter (FR-A-11).

### 1.4 Scope decision, documented not coded: `ai_processor/services.py`

`docs/decisions/AI_PROCESSOR_EXCEPTION_LOGGING_POLICY.md` records (without
code changes) that `ai_processor/services.py`'s ~15+ broad-except
`exc_info=e`/`str(e)` sites, plus 3 confirmed downstream inheritors in
`students/views.py` (lines 538/668/781), are out of this PR's scope
because that file was being modified concurrently by the parallel
audit-emitter PR (`grade-automator-plus-88`) — touching the same file's
exception handling in both PRs at once risked a merge collision or a
silent double-fix. States the rule a fast-follow PR must implement
against, and what the fast-follow needs to do to close the gap. Also
rules out `assignments/views.py:867,1420,1835` and
`assignments/services.py:640,772` as out-of-scope (assignment content,
not student submission data — not FR-A-04's target).

## 2. Test suite

- `AutoGrader/tests_sentry_scrubbing.py` (new, 5 tests): scrubs an email
  from `logentry.message`; scrubs from `exception.value` and stacktrace
  frame vars; an event with no PII is left byte-identical; a malformed
  event (missing/wrong-typed keys) never raises; missing keys anywhere in
  the expected shape are handled gracefully.
- `scripts/test_check_no_pii_in_logs.py` (new, 6 tests): flags a direct
  `.email` argument; flags a `.get_full_name()` call; flags a violation
  split across a multiline call (the case a grep rule would miss); does
  **not** flag an `.id` argument; does not flag an unrelated call; a
  baselined file's known violations are not reported as new.
- `AutoGrader/tests.py` / `AutoGrader/tests_send_email_impl.py` (2 tests
  updated, not new): two pre-existing tests were pinned to the old leaky
  `f"...to {recipient_list}"` return-string format and updated to match
  the new `f"...to {len(recipient_list)} recipient(s)"` format — a
  necessary consequence of §1.1's `AutoGrader/tasks.py` fix, not new
  coverage.

`python manage.py test AutoGrader.tests_sentry_scrubbing scripts.test_check_no_pii_in_logs AutoGrader.tests AutoGrader.tests_send_email_impl --settings=settings_worktree --noinput -v 2`
(re-run now, from the committed tree, for this backfill)

- Found 24 test(s)
- **OK**

Also re-ran the lint script itself standalone against the current tree:

`python scripts/check_no_pii_in_logs.py`
- `OK: no new PII-in-logs violations.` (exit 0)

And the wider touched-app suite:

`python manage.py test assignments.tasks classrooms users billing.license_service billing.management --settings=settings_worktree --noinput -v 1`
- Ran 798 tests
- **OK (skipped=4)**

## 3. Mutation testing

Not run as a standalone battery for this PR at landing time. The SM's
independent regression check at landing time covered correctness; this
backfill did not invent a new mutation script after the fact, since doing
so risks producing a mutation record that doesn't reflect what was
actually verified when the code was written. Flagging this explicitly as
the one gap relative to the other Epic A evidence docs' pattern (all of
which include a mutation section) — the 24 dedicated tests above (11
scrubber/lint tests plus baseline handling) do directly assert the
scrubber's and lint rule's own logic via table-driven true/false cases,
which is the same protection a small mutation battery on those two
self-contained modules would add.

## 4. Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis), on
this worktree rebased onto `integration/epic-a` `ee75da7` (current HEAD,
carries every Epic A piece landed since, including this one at its
original `d960176`).

`python manage.py test --settings=settings_worktree --parallel 4 --noinput`

- Ran 4756 tests in 513.454s (~8.6 min)
- **OK (skipped=28)**
- 0 `FAIL`/`ERROR` lines anywhere in the log (grepped, not just the final
  summary line)

Tail of the full run: `full_regression_tail.txt`.

## 5. Conclusion

All 13 confirmed-and-fixed PII-in-logs sites remain fixed on the current
`integration/epic-a` tree, the CI lint rule and its baseline are in place
and passing, the Sentry scrubber is wired and tested, and the documented
`ai_processor/services.py` scope decision (H-23-adjacent, tracked
separately) still stands unfixed as recorded. No regressions anywhere in
the codebase.

Post-commit sha256 (from `git show d960176:<path>`, the commit as it
actually landed — pre-commit hooks can rewrite a file after it's written,
so the working copy is not authoritative for what was committed):

```text
9c953a322802874707b95581ce9355c1fc4b89d3d9dfb73cf059b4fc92f5bd7d  AutoGrader/sentry_scrubbing.py
a8dce43cd59aae0779ae755de457d4d239a4aedebbdc379e554c17ec9fe56773  AutoGrader/settings.py
9c84fa82df55e546592c5c058a8bc3d9f875646b024e1d3274fe7601503af2b5  AutoGrader/tasks.py
569a09e921701bca7ce7886e1f9e753cabf050ed9f8af4237272d7b9d0beb992  AutoGrader/tests.py
bf38027abdd4272be8fecc0c4d0951997e1a97cc481a67cc673e79e5c43610a2  AutoGrader/tests_send_email_impl.py
b8b5e99d8e96241e565d96dfadf49aea77f7b0421bfadf3cf0795b283cc162c6  AutoGrader/tests_sentry_scrubbing.py
1078e4b3ac56f020286df5edd2ea0154b2530830f0fc5ebaa64de2afaae9de21  assignments/tasks.py
75900b63d06c6e20e9d04e95a6742ed097b4a3293c3cd40347c385516fce6934  billing/license_service.py
b6aece67b4118076d3b8dfd30794d90be047d9066b9bc5fb3150336a3b153473  billing/management/commands/backfill.py
b1c920f4e2b38efc5eefcc0de44c0e693812c7b0010ca50d0599d389eae5e37a  classrooms/serializers.py
d985366a136503e8f04888208267f4275257103e1e76e2cc0df8cd57099800be  classrooms/services/roster_import.py
b92596825af3470660bfc55e3a7112f78d0b4309979eca7527da47de44fff8c7  docs/decisions/AI_PROCESSOR_EXCEPTION_LOGGING_POLICY.md
d2e3524ab0aa9874240248df1b583f0f790fcc0b64dd188a1e7b885a39e9f0a1  scripts/check_no_pii_in_logs.py
b63703abb41fe211436dfa5ed8af08370c5d296ec388992d32398db743848983  scripts/pii_log_baseline.txt
11b3e029d7a8ba8edc903db54e0e12ab258f1dea0cde7d69d6d286bbeac612b2  scripts/test_check_no_pii_in_logs.py
51ab51df063c956e2c4a3ee05a2ce024f32575498397494cf072388139d59cef  users/mailerlite_service.py
25abd766c55f250097d49c1350e30d49dfc121fdcdd144d10c8d69175f2195f9  users/serializers.py
da78621aa82b321ef75273e4f9db11c48913a8fa0a7964d97c94e0c94434bd25  users/signals.py
2651a785fbf7fc9ae5ed3a78552e669ac8ceb9eae904db6e37c0c17dad566d25  .pre-commit-config.yaml
```

Tree fingerprint (`git cat-file -p <commit>`'s own `tree` line — the
content actually recorded at each commit, independent of this backfill):

```text
d960176d8c1783f42a3a6cd017e11276d5631c9b  tree a0fd45d78b2f49d2ed48763cedcee7090c246df9
148d32c7249c3cc2d8937f9265bf7b64b94b2e9c  tree d4a613c463f7cd4c9ff74861ba095a924d37554e  (merge point onto integration/epic-a)
```
