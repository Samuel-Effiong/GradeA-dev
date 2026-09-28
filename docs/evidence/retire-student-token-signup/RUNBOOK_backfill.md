# Runbook: convert pending student invites before the code sign-up is removed

**Who runs it:** the founder, on Railway, against production. Nobody else runs it on production.
**When:** after landing (A) of `task/retire-student-token-signup` is deployed (so no new pending codes are being minted), and **before** landing (B) (which removes `POST /auth/register/student` and `POST /course/student/renew-student-token`) is deployed.
**What it does:** `backfill_pending_student_invites` finds every student that's inactive and still holds an activation code.
- Real address with a pending enrollment: it activates the account with a generated temporary password, emails the student their login credentials, and clears the old code. The enrollment stays PENDING until their first login, as with any new invite.
- Real address, no pending enrollment: it leaves the account inactive and clears the code.
- **Placeholder `@student.local` address:** it clears the code only. The account is not activated and not emailed. *Founder statement (2026-09-28): `@student.local` students were created to be intentionally inaccessible to the student, so there's no teacher notification and no "set a password" follow-up.* Activating them was considered and rejected: it would give the row a usable password that nobody holds, where direct add gives these rows an unusable one. Matching direct add exactly would also mean flipping their enrollments to ENROLLED, which isn't clearly harmless.

It never prints an email, a name or a credential; output is internal ids only. It's safe to rerun: a second run finds nothing.

## 1. Dry run (writes nothing, sends nothing)

```
railway run python manage.py backfill_pending_student_invites --dry-run
```

Expected shape (ids vary):

```
[dry-run] would convert: student <uuid> (pending course <uuid>)
[dry-run] would clear code only (no pending enrollment): student <uuid>
[dry-run] would clear code only (placeholder address): student <uuid>
Backfill (dry run) complete: N converted, M code-only cleared (no pending enrollment), P code-only cleared (placeholder address, left inactive, not emailed). Inactive students still holding a code: N+M+P.
```

**Stop and ask before step 2 if:**
- N is far larger than expected for the pending roster invites (tens to low hundreds is plausible for a term's imports).
- Any line is anything other than the three shapes above, or the command errors.

## 2. Execute (sends real email, changes real accounts)

```
railway run python manage.py backfill_pending_student_invites
```

Expected tail:

```
Backfill complete: N converted, M code-only cleared (no pending enrollment), P code-only cleared (placeholder address, ...). Inactive students still holding a code: 0.
```

N, M and P must match the dry run. **"still holding a code: 0" is the gate for landing (B).**

## 3. Verify

Run the dry run again. It must find nothing:

```
railway run python manage.py backfill_pending_student_invites --dry-run
# -> Backfill (dry run) complete: 0 converted, 0 code-only cleared (...). Inactive students still holding a code: 0.
```

Optional cross-check, read-only, counts only:

```
railway run psql "$DATABASE_PUBLIC_URL" -c "BEGIN TRANSACTION READ ONLY; \
  SELECT is_active, COUNT(*) FROM users_customuser \
  WHERE user_type = 'STUDENT' AND activation_token IS NOT NULL AND activation_token <> '' \
  GROUP BY is_active; ROLLBACK;"
```

**Expected: no rows at all.** The inactive group is what the backfill clears. The active group is also empty, because landing (A) clears the code whenever an existing legacy student is re-invited, and the backfill clears every row it touches. If an active group shows up, it's students who completed the old sign-up before the cutover and still carry a stale code. That's harmless (both old doors only match inactive rows), but report the count.

## 4. Then landing (B)

Only once step 3 shows zero inactive students holding a code: deploy landing (B), which removes the two endpoints. Any student who clicks an old activation link after that gets a 404 from the backend; the frontend pages that sent them there are listed in `FRONTEND_DEAD_PAGES.md`.

## If something goes wrong

- A crash mid-run leaves each student either fully converted or untouched (each conversion is its own transaction). Rerun the command; it resumes with whoever is left.
- The email is queued to Celery (`safe_delay`). If the broker is down, or delivery fails later, the account is NOT rolled back: the student is active with a password they haven't received. Their teacher can reset it, or the student uses "forgot password" (real mailbox only). Check the Celery/MailerSend logs after the run. Any other error while building the email rolls that one student back and stops the command; fix it, then rerun.
