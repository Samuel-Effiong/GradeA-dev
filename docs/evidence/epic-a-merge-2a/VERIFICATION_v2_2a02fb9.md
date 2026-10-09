# Verification: batch-2a → Epic A merge @ 2a02fb9

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **L2 read:** 1a, whose verdict is CORRECT on the reset_password part.
**Branch:** task/epic-a-merge-2a @ **2a02fb9**:
- merge commit e549c9d (parents 3bbafdd = phase2/epic-a, 755aa27 = batch-2a);
- plus 2a02fb9, which adds a one-line test tiebreak and EVIDENCE.

**Date:** 2026-09-30.
Every run was wrapped: 6G memory cap (`MemorySwapMax=0`), `nice -n 10`, `timeout -k 60 1800`, `RACE_COST 600/200`, `EXEMPT_EMAIL_DOMAINS=`. The slot was 0b's. The scratch worktree was detached at the sha under test.

**Verdict: VERIFIED-WITH-NOTES.**
- The resolution follows the SM's rulings.
- The merge has no evil hunks.
- S1's invariant holds on the merged tip.

## Merge cleanliness (`git show --remerge-diff e549c9d`)
The only changes beyond git's auto-merge are:
1. **`users/views.py` reset_password**, the single conflict:
   - locked → `sign_in_failed(RESET_LOCKED, denied)` + `_reset_locked_response` (429);
   - a wrong guess that spends the budget → one `sign_in_failed(INVALID_CODE, extra_metadata={"lock_triggered": True})` + 429;
   - an ordinary wrong guess → `sign_in_failed(INVALID_CODE)` + 400.
2. **`users/auth_audit.py`**: `sign_in_failed(..., extra_metadata=None)`, merged into the metadata.
3. **`audit/metadata.py`**: `lock_triggered` in `ALLOWED_KEYS` and in AUTH_LOGIN's allow-list.
4. **`users/tests_auth_audit_doors.py`**: the locked test pins 429, and a new spend-guess test.

Items 2–4 are what the ruling needs; there is nothing else.

2a02fb9 on top of e549c9d changes one test line (`order_by("occurred_at", "pk")`) plus EVIDENCE.md.

**Auto-merged areas checked:**
- `AuditMiddleware` is still last in `MIDDLEWARE`.
- The login serializer still uses `failure_actor` and the target's `school_id`.
- `audit/context.py`, `middleware.py` and `emitter.py` are byte-identical to the verified S1 4333e0a.
- batch-2a's only new view-level 429 is reset_password. That route is anonymous and has named events, so S1's "429 is not recorded" exemption is unaffected.

## Evidence
| Check | Result |
|---|---|
| S1 labels + v2/1a probes + `users.tests_reset_otp_budget` @ e549c9d | **170 OK**. The V1 probe: add_teachers → teacher's CREDIT_TRANSACTION + STATE_CHANGE naming the school admin |
| Resolution mutants (`vf_merge2a_mutants.py`, 10) @ e549c9d | **10/10 KILLED**: locked with no event; locked back to 400; spend guess with no event, no flag, two events, recorded as DENIED RESET_LOCKED, or answered 400; plain wrong guess with no event; allow-list key dropped; `extra_metadata` ignored |
| S1 R2 mutants on the merged tip (M1, M8 V1 actor dropped, M11) @ e549c9d | **3/3 KILLED** |
| Regression @ **2a02fb9**: `audit users AutoGrader` | 1268 OK (skipped=4), including v2's 7 probe tests |
| Regression @ 2a02fb9: `classrooms students assignments` | 1246 OK (skipped=14) |
| Regression @ 2a02fb9: `billing` | 1670 OK |
| Whole-repo `pre-commit run mypy --all-files` @ 2a02fb9 | Passed |
| `makemigrations --check --dry-run` @ 2a02fb9 | No changes detected |

Every mutant restore was hash-checked. The only change between e549c9d and 2a02fb9 in code under test is the one test line, which the 2a02fb9 regression covers.

## Notes
- **N1. The tiebreak is deterministic, but it is not insertion order.** `AuditEvent.pk` is `uuid4`, so if two events ever shared `occurred_at`, ordering by pk would not restore their creation order. In practice there are no ties: `occurred_at` is Python `timezone.now()` (microseconds), and each attempt is a separate request with password hashing between them. A sturdier form, not required: assert that exactly one event has `lock_triggered`, and that it is the one created by the 5th request.
- **N2. Out of scope for this slice.** The full-suite Gate 10 and the staging refresh are 0b's after the merge into phase2/epic-a.
