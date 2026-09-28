# H-47 — `/auth/register/student` completed any pending account by its code alone (HIGH)

Branch `task/register-student-token-scope`, off beta `4b902fc`. Reproduce-first `f4fe530`, fix `f538205` (+ the evidence commit that carries this file). Written by the Security Engineer. Severity HIGH per the SM, 2026-09-28. The SM confirmed the same lookup is on `origin/main`, so it's **live in production**.

**Status: fix (a) BUILT on the branch, local gates pass. NOT landed, NOT deployed. Independent verification and the full-repository gate are owed. Fixes (b)/(c) (per-email lookup or longer student codes) wait for the founder.**

## 1. The bug

`AuthViewSet.register_student` (`POST /auth/register/student`, `AllowAny`, `RegisterThrottle` 10/hour per IP) found its row with:

```python
CustomUser.objects.filter(activation_token=token, is_active=False).first()
```

That's no email and no `user_type`. It then set **the caller's** password, first/last name and profile image, set `is_active=True` and stamped `email_verified_at`.

The same `activation_token` column also holds:

| Holder | Code | Lives |
|---|---|---|
| Self-registered teacher (`/auth/register` → `send_user_activation_email`) | 6 digits | 15 min |
| Roster-imported student (`classrooms/services/roster_import.py`) | 6 digits | 24 h |
| Renewed student (`CustomUser.renew_activation_token`) | 6 digits | 24 h |
| Direct-invite student | `secrets.token_urlsafe(32)` | — |
| School admin (`resend_school_admin_invitation`) | `secrets.token_urlsafe(32)` | 7 days |

So:
1. **Cross-flow takeover.** A pending teacher's 6-digit verification code completed that teacher's account through the student door, with a password the caller chose. That's a working, verified login on someone else's address.
2. **Pool brute force.** A guess isn't tested against one email but against *every* live 6-digit code at once, so success per guess is (live codes) / 1,000,000. The only limit is per-IP. The `ACTIVATION_TOKEN_VALIDITY` comment in `users/models.py` sized the 24-hour window for brute force "for a known email". This door needs no email. A hit takes over a roster student too: their pending enrollments are promoted, and an `@student.local` student can't then recover by password reset.

Reproduced on beta `4b902fc` (`f4fe530`): `test_a_pending_teacher_row_cannot_be_completed_through_the_student_door` **fails**. The caller's password logs in (`200`), and the teacher row is active and verified. The student positive control passes.

## 2. The fix (option (a) + the SM's two add-ons)

| Change | Where |
|---|---|
| Lookup scoped to `user_type=STUDENT` | `users/views.py` `register_student` |
| Same scope on the **renew** door. It had the identical token-only lookup, and a teacher's code there reached `renew_activation_token()`'s `ValueError` and answered **500**, confirming the code was real | `classrooms/services/enrollment.py` `renew_student_activation` |
| **Global** failure budget: failed attempts (no match, or expired) are counted per window across all IPs. Once the limit is reached, every caller gets `429` until the window rolls over. Defaults 100/hour, set by `REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT` / `REGISTER_STUDENT_FAILURE_WINDOW_SECONDS`. Checked **before** the view's `try`, whose catch-all would otherwise turn `Throttled` into a 500. Successes don't count | `users/throttling.py`, `users/views.py`, `AutoGrader/settings.py` |
| Each failure logged at WARNING with `reason` and `window_failures` only (no token, email or IP) | `users/throttling.py` |
| The `429` says why and when: *"Student registration is paused for a short while because of too many invalid activation codes. Your invitation is still valid; please try again later."* DRF appends "Expected available in N seconds." and sets `Retry-After` to the seconds left in the window | `users/views.py` |
| Alerting: the failure that exhausts the budget logs **once per window at ERROR** (`event=register_student.budget_exhausted`, with limit, window and retry-after). Every refused request logs at WARNING (`event=register_student.budget_refusal`). Neither carries a token, email or IP | `users/throttling.py` |

A teacher's or school admin's code now gets the byte-identical `400` an unknown code gets, on both doors.

**Trade-off to know about:** the global budget turns a guessing run into a pause of student sign-ups for the rest of the window. Anyone can trip it deliberately with 100 bad codes an hour. That's the price of bounding total guesses independent of IP count. The limit and window are env-tunable.

## 3. Deliberately NOT changed (decision needed)

**Expired vs wrong code.** A code matching an expired student row answers `200` with `renewal_url` and `expired_token`. A wrong code answers `400`. So the difference confirms "this guess matched a real, expired row". It's left as is because:
- the frontend's renew UI keys on that `200` + `renewal_url` (pinned by `users/tests_auth_input_validation.py`: `test_expired_activation_reports_renewal_rather_than_registering`, `test_null_activation_expires_is_handled_not_crashed`), so unifying the two is a frontend contract change;
- the confirmation is worth little: an expired code can't complete an account, and posting it to the renew door **rotates** it (a new code is generated and emailed), so a confirmed guess is dead on arrival;
- expired attempts now count against the global budget like any other failure.

**SM ruling (2026-09-28): keep it, do not unify.** The SM confirmed from `send_token_renewal_emails` that a renewed code goes only to `student.email` and the student's teacher, never back to whoever called the renew endpoint. A guesser who confirms an expired match and renews it just rotates the code to a mailbox they don't control. The oracle yields nothing usable, and the frontend contract stays as it is.

**Options (b)/(c)** (email + code, or longer student codes like `K7P2-9QXM-4D`) close the pool attack itself. They wait for the founder. The SM noted that the student completion form never asks for an email and that roster students often have placeholder `@student.local` addresses.

## 4. Gates

| Gate | Result | Evidence |
|---|---|---|
| 1 Baseline / regression | Reproduce-first fails on `4b902fc` (above). With the fix: `users.tests_register_student_token_scope` (12) + `users.tests_auth_input_validation` + `users.tests_auth_endpoints` + `classrooms.tests` → **92/92 OK**, including the existing expired-code and renew tests unchanged. `users.tests_h47_exposure_sql` 2/2 (the exposure SQL runs on the real schema). Full suite: **pending slot** | test runs; `f4fe530` |
| 2 Mutation | 9 mutants, **8 killed, 1 equivalent**. Killed: M1 register lookup unscoped, M2 renew lookup unscoped, M3 budget never checked, M4 no-match not counted, M5 expired not counted, M6 `>=`→`>`, M8 refusal raised as a non-`Throttled` error (→500), M9 token leaked into the log record. **M7** (fixed counter key, so the bucket never changes) survives and is **equivalent**: the key is created with `cache.add(..., timeout=window)` and `incr` preserves that TTL (LocMem and django-redis both), so a fixed key still expires and resets after one window. The bucket-in-the-key is a second, redundant reset. The window-reset test advances `time.time`, which expires the TTL too | `mutate.py` (every anchor asserted unique), `mutation_log.txt`, `mutation_results.json` |
| 3 Concurrency | Not threaded-tested. The budget uses `cache.add` + atomic `cache.incr`, so concurrent failures can't lose counts; a race can let at most a handful of requests past the limit at the boundary, which doesn't change the bound materially | — |
| 4 Adversarial | Cross-flow takeover replayed through the real endpoint and real login (reproduce test). The distributed-guessing bound is exercised with the per-IP throttle removed, so the global budget alone must stop it | `RegisterStudentGlobalFailureBudgetTests` |
| 5 Failure | The budget refusal is raised before the transaction and the view's catch-all (mutant M8 proves a non-`Throttled` exception there becomes a 500 the tests catch) | M8 |
| 7 Real infrastructure | Real PostgreSQL; LocMem cache in tests (atomic `incr` semantics match Redis for the paths used) | — |
| 8 Live / E2E | LOCAL-REAL only | — |
| 9 Security / isolation | Teacher and school-admin codes refused on both doors with the same body as an unknown code; student completion unchanged; logs carry no token/email | tests |
| 10 Full-repository | **Pending** | — |

## 5. Production exposure

`prod_exposure_query.sql`, read-only (`BEGIN TRANSACTION READ ONLY … ROLLBACK`), selects ids, types and timestamps only, no emails. **The founder runs it.** The database can't say *which* endpoint activated a row: `/auth/verify` and `register_student` both clear the token and stamp `email_verified_at`. So query 1 lists candidates (non-student, self-registered, active, verified), and the proof is in the HTTP logs: a `POST /auth/register/student` 200 within a second or two of a candidate's `email_verified_at` is a takeover. Query 2 gives the live code pool by type, which is today's exposure. Guessing runs show as bursts of 400s on that path in the access logs; beta itself logged nothing per failure before this fix. `users/tests_h47_exposure_sql.py` runs both SELECTs against the real schema and checks they pick out the right rows and no personal columns.

## 6. Open

1. Gate 10 (full suite).
2. Independent verification (Verification Engineer).
3. Founder: (b)/(c), and whether to unify expired vs wrong code (frontend change).
4. Founder: run `prod_exposure_query.sql` and correlate with HTTP logs.
