# Step (b): merge beta `abeda10` into phase2/epic-a `19b2072`. Resolution advice from ed (Security)

0b performs the merge (founder decision); ed supplies the resolution. Nothing here is committed on any branch. Prepared 2026-09-30 against **phase2/epic-a `19b2072`** (S5 R3 and the S1b follow-up merged). No test was run by ed; the gates are 0b's run.

## 1. The four conflicted files
| File | Resolution |
|---|---|
| `AutoGrader/settings.py` | 0b's mechanical union: S1b's `FAILED_AUTH_*` block, a blank line, then H-53's `VERIFY_EMAIL_MAX_FAILURES` / `VERIFY_EMAIL_LOCK_SECONDS`. ed agrees. |
| `classrooms/views.py` | 0b's union: keep `from audit.emitter import emit` and `from audit.enums import AuditAction, AuditOutcome`, plus beta's multi-line `cache_generation` import with `SCOPE_COURSE`. ed agrees. |
| `docs/HARDENING_BACKLOG.md` | 0b's union: beta's refreshed H-39 row (CLOSED `be78221` / `3856e40`), then Epic A's H-40 row. ed agrees. |
| `users/views.py` | **Take `files/users/views.py` whole.** It is the conflicted merge of `abeda10` into `8de4376` (`users/views.py` is identical on `19b2072`), with both hunks resolved in `AuthViewSet.verify` as below. It has no other change. |

## 2. `/auth/verify` after the merge (SM ruling: one event per attempt; H-53's behaviour stays)
| Attempt | Answer (H-53, unchanged) | Audit event (AUTH_LOGIN, `auth_method=email_verification`) |
|---|---|---|
| email or token missing | 400 | FAILURE `CODE_MISSING` (Epic A's, unchanged; spends no budget) |
| wrong code, or unknown email | 400 | FAILURE `INVALID_CODE`, target = the account or None |
| expired code | 400 | FAILURE `CODE_EXPIRED`, target = the account |
| the guess that spends the budget (wrong or expired) | 400 | the same FAILURE event, plus `metadata.lock_triggered: true` (as reset_password's L2) |
| locked, or over budget in a race (a correct code included) | 429 `Retry-After` | **DENIED `VERIFY_LOCKED`**, target = the account or None |
| success | 202 | SUCCESS (Epic A's `sign_in_succeeded`, unchanged); `clear_verify_failures` (H-53) kept |

S1b's caps apply through the emitter. `lock_triggered` is already on AUTH_LOGIN's allow-list (`audit/metadata.py`, from the 2a merge).

## 3. `patches/merge-b.patch` (apply after the merge commit's conflicts are resolved: `git apply patches/merge-b.patch`)
Unified diff, `a/` / `b/` paths, 11 hunks. Checked with `patch -p1 --dry-run` against `19b2072`'s six files plus the merged (= beta's) guard file; the result is byte-identical to the built files. All the files pass black 25.1 (line-length 88), isort `--profile=black` and flake8 with the hook's arguments. mypy was not run on the scratch copies; the commit hook runs it.
- `audit/enums.py`: `ReasonCode.VERIFY_LOCKED`, after `RESET_LOCKED`.
- `AutoGrader/reason_codes.py`: `VERIFY_LOCKED` in `AUDIT_ONLY_CODES` (otherwise S6a's emitter refuses the event and a locked attempt leaves nothing).
- `audit/emitter.py` `_failed_auth_cap_scope` (**SM ruling on the no-account DENIED**): a DENIED with no target returns `(None, True)`: no floor, the global cap. Before, `admit(None, global_cap=False)` always wrote, so a spray at locked **unknown** addresses (H-53 locks per address, known or not) would have been the one uncapped path. A known account's DENIED is unchanged (floor + per-target, no global). There is no per-target bucket for an unknown address, because cap keys hold an account id, never an email. The docstring says so.
- `audit/failed_auth_cap.py`: the module docstring (the follow-up's wording on `19b2072`) gains the no-account rule. No code change, so its raw cache writes stay at **2** (lines 85 and 89).
- `audit/tests_failed_auth_cap.py`: `test_a_no_account_denial_spray_stops_at_the_caps_a_known_floor_does_not` (SM's pin). At floor 5, target 30 and global 6, 10 no-account DENIEDs give 6 rows + 1 DENIED global summary with no target. Then a known account's first 5 DENIEDs are all written, with the global cap spent.
- `users/tests_auth_audit_doors.py` `VerifyEmailDoorTests`:
  - `verify()` takes an `ip`, so the per-IP 5/hour throttle never answers in place of the budget. `spend_the_budget()` checks one event per attempt, with `lock_triggered` on the last only.
  - New tests: the budget-spending guess; a locked attempt with the **right** code gives 429, one DENIED `VERIFY_LOCKED` naming the account, and the account is still unverified; a locked unknown address gives DENIED with no account and no email stored; **the catalogue-membership test** (`CODE_MISSING`, `INVALID_CODE`, `CODE_EXPIRED`, `VERIFY_LOCKED` ∈ `AUDIT_ONLY_CODES`).
- `AutoGrader/tests_cache_invalidation_coverage.py` (the merged file is beta's batch-2b guard): `NON_RESPONSE_CACHE_WRITES` gains `"audit/failed_auth_cap.py": (2, ...)`. ed ran the guard's own `raw_cache_writes()` over the merged tree: `audit/failed_auth_cap.py` is the only new file (2 writes; `cache.incr` isn't counted); every other count equals beta's table. The key prefixes can't collide: `audit:failed_auth:*` versus H-53's `verify_email:{attempts,locked}:*`.

**Epic A tests moved to H-53's 429:** none needed.
- Every other test file that calls `/auth/verify` (`users.tests_auth_input_validation`, `tests_open_signup`, `tests_throttling`, `tests_verify_email_preset_password_characterization`, `classrooms.test_school_admin_otp_deadend`) is identical on `19b2072` and `abeda10`, so it already passes with H-53 on beta.
- Epic-only callers: `audit.tests_route_coverage` calls `emit_anonymous_refusal` directly, not the view. S1b's `CappedDoorThroughTheMiddlewareTests` sends 3 wrong codes, under the budget of 5, and its expected events are unchanged. The doors tests clear their LocMem cache per test.

## 4. Gates (rule 15)
**Changed modules:**
- `users.tests_auth_audit_doors`
- `audit.tests_failed_auth_cap`
- `audit.tests_emitter`
- `audit.tests_route_coverage`
- `AutoGrader.tests_reason_codes` (its completeness scanner picks up `"VERIFY_LOCKED"` from `sign_in_failed`)
- `AutoGrader.tests_cache_invalidation_coverage` (its Redis classes need real Redis)
- `users.tests_verify_email_budget` (beta's H-53 tests, now with audit events)
- `users.tests_throttling`
- `users.tests_auth_input_validation`

**Regression:** ONE owning app, `users`.

**Mutants** worth running (anchors unique in the post-patch files):
- `return target_id, target_id is None` → `return target_id, False` (the no-account spray is uncapped again);
- remove the `sign_in_failed(... "VERIFY_LOCKED", denied=True)` call (a locked attempt leaves no event);
- `denied=True` → `denied=False`;
- `{"lock_triggered": True} if lock_triggered else None` → `None`;
- `ReasonCode.VERIFY_LOCKED,` removed from `AUDIT_ONLY_CODES`.

## 5. Provenance
`build.py` rebuilds everything from the base files with asserted unique anchors: `python3 build.py <scratch mergeb dir> <this dir>`. Base files: `git show 19b2072:<path>`; the guard is `git show abeda10:AutoGrader/tests_cache_invalidation_coverage.py`; `users_views.py` is the conflicted file from `git merge-tree --write-tree 8de4376 abeda10`. `SHA256SUMS` covers the two deliverables.
