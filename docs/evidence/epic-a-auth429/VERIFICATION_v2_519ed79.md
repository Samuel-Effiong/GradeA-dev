# Verification: the auth-lock envelope slice @ 519ed79

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-auth429 @ **519ed79** (code b512ddf / 734ed13, cut from 522f818). It answers v2's S6a N3 (the SM's rulings): the three auth locks answer with the coded envelope **added** to their existing bodies. The three codes' user-facing specs are **pending QA catalogue approval** (staging only, not beta).

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot, from a scratch worktree detached at 519ed79. Rule 15: v2's probes + ed's module + the L2/H-53 budget modules + `AutoGrader.tests_reason_codes` + mutants. ed's users regression (693 OK) is cited.

**Verdict: VERIFIED.**

## Static
`add_coded_envelope(data, code, message, *, code_value)` adds only the envelope keys the body lacks. It never replaces a key, sets `code` only if absent, and never adds `error` (a second text key would change the renderer's message). The reset lock calls it directly. The verify and login locks raise `EnvelopedThrottled` / `EnvelopedAuthenticationFailed`, and `custom_exception_handler` merges the envelope into DRF's own response, so the status, `detail`, `Retry-After` and `WWW-Authenticate` are DRF's.

## Evidence (real routes, real renderer)
| Check | Result |
|---|---|
| v2 probes (`tests_vf2_authlock_probe.py`) + `users.tests_auth_lock_envelope`, `tests_reset_otp_budget`, `tests_verify_email_budget`, `AutoGrader.tests_reason_codes` | **82 OK** |
| **A1 existence oracle**, `/auth/verify` locked: an UNKNOWN address vs a KNOWN unverified account | both **429 VERIFY_LOCKED**; the same `Retry-After` (1799); the same rendered message; the same envelope apart from `reference`. **No oracle** |
| A2 verify lock | 429; the envelope added; the message is the lock's own text ("Too many incorrect codes for this email address…"), not the remediation; `reference == X-Request-ID` |
| A2 reset lock | 429; keeps `code: RESET_LOCKED`, `locked_until`, `retry_after_seconds` and the "For your security…" message; gains `reason_code` etc.; `reference == X-Request-ID` |
| A2/A4 login lock | 401 `ACCOUNT_LOCKED`; keeps `detail`/`code` and **`WWW-Authenticate: Bearer realm="api"`**; the envelope added; `reference == X-Request-ID` |
| A3 unchanged | a plain wrong-code verify (400), a wrong-password login (401) and the per-IP login throttle (429) carry **no** envelope keys |
| v2 mutants (`vf_authlock_mutants.py`) | **2/2 KILLED**: AL1 the helper overwrites existing keys (`test_the_envelope_is_added_never_replacing_a_key`); AL2 the helper adds an `error` text key (5 tests, including v2's oracle probe) |
| ed's gates | prefix 6F/1E on 522f818; 147 OK; 6/6 mutants; users 693 OK; mypy passed (committed) |

The QA catalogue approval of the three user-facing specs stays the stated gate before beta.

Logs: `runs/authlock_run1.log`, `runs/authlock_run2_mutants.log`.
