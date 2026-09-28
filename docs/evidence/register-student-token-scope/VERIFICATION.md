# H-47: Verification

Branch `task/register-student-token-scope` @ eb32c2a: cb4bec1 + 3b31d00 (budget alerting and Retry-After) + a --no-ff merge of local beta be78221. 06a24c7 on top is docs-only (the OAuth evidence line), not part of H-47.

## Merge
Re-merging 3b31d00 with be78221 myself gives tree `33ef348…`, identical to eb32c2a. No evil merge. users/views.py auto-merged (the OAuth fix touches it too), so I tested the combination: H-47 + h47_exposure_sql + google_auth + verify_email_preset_password_characterization + auth_input_validation + auth_endpoints + email_domain_rules + network_guard + classrooms.tests gives **179 OK, 3 skipped** (network-gated) in my own detached checkout.

## Code read
- Both doors are scoped to `user_type=STUDENT` (register_student lookup and renew_student_activation). A teacher's or school admin's code gets the same 400 as an unknown code on both.
- The budget check sits before the `try`, so the catch-all can't turn Throttled into a 500. It runs after DRF's per-IP throttle and BEFORE the token is tested, so once the budget is spent even a correct code is refused. That's the property that actually bounds guesses.
- 3b31d00: the ERROR `register_student.budget_exhausted` fires on `count == LIMIT`, and atomic incr means exactly one request sees that value, so it fires once per window. Refused requests never call record, so it can't re-fire. `Retry-After = window - (now % window)` (min 1) is exactly the time to the next bucket boundary, consistent with the bucketed key. The expired-code 200 + renewal_url path is unchanged apart from counting as a failure, as EVIDENCE §3 says and per the SM's ruling.
- Logs carry reason, count, limit and window only. No token, email or IP.

## Your M7 equivalence argument: confirmed on the production backend
Production uses django_redis, but every H-47 test overrides to LocMem. I re-ran RegisterStudentGlobalFailureBudgetTests against the suite's real Redis backend (a throwaway subclass, never committed): all 6 pass. A direct probe shows `cache.add(k, 0, timeout=30)` followed by 5× `incr` leaves value 5 and **TTL 30**, so incr preserves the TTL on Redis, and `record_register_student_failure` counts 1, 2, 3 with a live TTL. The argument holds in production. One nuance: on Redis, the window-reset test passes only because of the bucket in the key, since a patched `time.time` doesn't expire a server-side TTL. So the bucket isn't purely redundant in the test; it's what the test observes.

## My mutation battery (8 mutants, independent of yours; every restore sha-verified against eb32c2a)
| Mutant | Result |
|---|---|
| V1 register lookup unscoped | KILLED (teacher takeover, teacher same-400, school-admin tests) |
| V2 renew lookup unscoped | KILLED (renew teacher-code same-400) |
| V3 budget check moved inside the try | KILLED (4 budget tests, including 429 + Retry-After) |
| V4 budget checked only on no-match (a correct code would slip through while spent) | KILLED (the "even a valid code gets 429" test plus 3 others) |
| V5 ERROR fires on `>=` instead of `==` | **SURVIVED** (see note 1) |
| V6 refusal WARNING not logged | KILLED |
| V7 no Retry-After | KILLED |
| V8 a success recorded as a failure | KILLED (a-success-does-not-count) |
Your own log: 8/9 killed, M7 equivalent (argued, and now empirically backed on Redis, above).

## Notes (non-blocking)
1. **V5:** "ERROR once per window" isn't pinned. Sequentially the pre-check stops the count at LIMIT, so `==` and `>=` behave identically; they only differ under concurrent overshoot. A cheap test: call `record_register_student_failure()` directly LIMIT+3 times, then assert exactly one `register_student.budget_exhausted` record.
2. **Legitimate failures spend the same budget.** At 100/hour, a mass onboarding (term start, a big roster) with ordinary mistypes can pause all student sign-ups with no attacker involved. The new ERROR alert makes that visible; size REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT against the expected legit failure rate.
3. **Bucket boundary:** aligned buckets allow up to 2×LIMIT failures in a short span straddling a boundary (100 at :59:59 plus 100 at :00:00), so the worst case in any sliding hour is 200. That's still a hard bound.
4. **The renew door** (`renew-student-token`, AllowAny + per-IP RegisterThrottle) isn't counted in the global budget. It's still a guessing oracle for live student codes. A hit rotates the victim's code instead of completing the account, so there's no takeover, but an @student.local student then loses their code (the new one is emailed to no mailbox). Consider counting renew failures in the same budget. This is pre-existing and adjacent, not a regression.
5. The 429 text says "Your invitation is still valid". That isn't true for a code that expires during the pause. Minor wording.
6. The full suite on eb32c2a is still owed (you're running it).

## Verdict: VERIFIED-WITH-NOTES
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.

## Re-check of the pre-landing delta (ad3bab2): VERIFIED-WITH-NOTES
- The renew door (`handle_expired_token`) checks the shared budget BEFORE the serializer and the lookup, and outside the try, so it answers 429 with Retry-After. The refusal log carries `door=renew`. Every `EnrollmentError` is recorded as `renew_refused`.
- V5 is pinned: `test_the_exhausted_error_fires_once_per_window_not_per_failure`, killed by harness M10. The 429 no longer claims the invitation is still valid.
- Re-ran in my own detached checkout of ad3bab2: `tests_register_student_token_scope` + `auth_input_validation` + `classrooms.tests` = 58 OK.
- My own mutant R3 (the renew budget check moved AFTER the lookup, into the failure branch, so a real code would be rotated while spent) is KILLED by `test_renew_is_refused_once_the_budget_is_spent_and_rotates_nothing`. Restore sha-verified.
- Harness log checked: 11/12 killed, with M7 equivalent (confirmed on Redis earlier). The new M11/M12 kill against the right tests.
- Minor, non-blocking: `renew_student_activation` raises two different `EnrollmentError` messages, "Invalid token or user not found." and "No pending enrollment found for this user.", and the view returns `str(exc)`. The second one confirms a real student code. It gives no amplification, since each renew guess now costs the same budget as a register guess, and at register a correct guess completes the account anyway. But making both messages identical would remove the oracle for free.
- Notes 2 and 3 from the first pass stand: legitimate mistypes spend the budget, and the bucket boundary allows up to 2×LIMIT failures in a short span.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
