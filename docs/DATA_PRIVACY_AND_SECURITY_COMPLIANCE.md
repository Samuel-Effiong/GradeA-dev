# Data Privacy & Security Compliance Write-Up — Grade Automator Plus

*Prepared from a review of the actual codebase as of 2026-09-01, with §3 added 2026-09-03. This document describes what is genuinely implemented today, not a target state. Where something is a gap or a weak spot, it's called out plainly rather than glossed over — an honest write-up is more useful for compliance purposes than an optimistic one.*

For a deeper engineering-level reference, see [docs/backend/security-and-tenancy.md](backend/security-and-tenancy.md).

---

## 1. Who this covers and why it matters

Grade Automator Plus handles data covered by **FERPA** (the U.S. student-records privacy law) because it stores student names, submissions, and grades on behalf of schools and teachers. It also processes payments through Stripe, which brings light **PCI DSS** obligations (though card numbers themselves never touch our servers — see §7).

---

## 2. Who can see what (access control)

The app has four roles: **student, teacher, school admin, and super admin** (our own staff). There's no single "tenant ID" switch — instead, every screen and API endpoint is written to only fetch the data that role is allowed to see. A teacher's query for students only ever returns their own students; a school admin's queries are scoped to their own school; students only ever see their own submissions and assignments that have actually been published.

Login uses signed tokens (JWT) that expire after 1 day, with a separate refresh token good for 2 days that's invalidated and re-issued every time it's used. Changing your password immediately logs out every other device. Sensitive actions — the six-digit email verification code, password reset requests, login attempts — are rate-limited so they can't be brute-forced from outside. **Important: until 3 September 2026 these caps were not actually working. §3 explains what happened and what it could have allowed.**

**Honest limitation:** if someone steals a valid access token, there's no way to revoke it early — it's simply good for up to 24 hours. There's also no multi-factor authentication. An account now locks for 15 minutes after 5 failed login attempts in a row (on top of the existing IP-based rate limiting), so password-guessing against one account is bounded even from many IPs at once. The remaining gaps are not unusual for a product at this stage, but are worth knowing if a school's IT department asks.

---

## 3. Stopping automated attacks (rate limiting)

**What this is.** We put a cap on how many times the same visitor can try
a sensitive action — signing in, asking for a code by email, or creating
an account. Without a cap, someone can keep trying automatically,
thousands of times, until something works.

The caps in place today:

| Action | Cap |
| --- | --- |
| Signing in | 10 per minute |
| Asking for an emailed code | 5 per hour |
| Creating an account | 10 per hour |
| Signing in with Google | 20 per hour |

**What went wrong.** These caps existed, but until 3 September 2026 they
were not working. A configuration error meant the system saw almost
every request as coming from a different new visitor, so the count never
built up and the cap was never reached. In practice there was no limit
at all.

**How we know.** We tested our trial environment before and after. Before
the fix, attempts kept being allowed well past the cap. After it,
attempts are correctly refused once the cap is reached — including when
someone tries to disguise where they are connecting from.

**What this could have allowed.** While it was broken, someone on the
internet could have:

- kept guessing passwords — though a separate protection did work
  throughout: an account locks for 15 minutes after 5 wrong passwords;
- kept guessing the six-digit codes we email out for account activation
  and student invitations;
- created accounts, or triggered password-reset emails, in bulk.

The codes are the biggest concern. Six digits is a million
combinations, which is quick to work through if nothing stops you
retrying. Student invitation codes are the most exposed, because a
guess is checked against every invitation waiting to be accepted rather
than one named person.

**Was it actually exploited?** We do not know. Nobody has reviewed the
records from that period yet. We should not tell a school "no impact"
until someone has.

**Where it stands.** Fixed and confirmed on our trial environment. **Not
yet live for customers.** Until it is, everything above still applies to
the live service.

## 4. How data is protected in storage and in transit

- **In transit:** in production, cookies are marked secure, HSTS is enabled (so browsers refuse to downgrade to plain HTTP), and the app sits behind a TLS-terminating proxy. One setting worth double-checking on the live server: the code's default is to *not* force HTTP→HTTPS redirects (it's an opt-in environment variable, left off by default to avoid a redirect loop if the proxy isn't configured correctly first). This is a config question to verify against the live deployment, not the code.
- **At rest:** the database itself relies on the hosting provider's standard encryption at rest. On top of that, only one thing gets extra, field-level encryption in the app itself: Google sign-in tokens. Student names, grades, and submission text are stored as ordinary database fields — protected by infrastructure-level encryption and access control, but not individually encrypted by the application.
- **Cross-origin access (CORS):** the list of websites allowed to call our API is a hardcoded allowlist of our own domains — not "anyone can call this," which was a real problem in an earlier version of the code and has since been fixed and locked down.
- **SQL injection:** the app uses Django's ORM everywhere instead of hand-written SQL, which is the standard, effective defense against SQL injection. No raw, user-influenced SQL was found anywhere in the codebase.

---

## 5. Financial records and audit trails

Credit and billing records (`CreditLedger`, `CreditUsageLog`) were recently hardened to be **append-only at the application level** — the code actively blocks any code path (including cascading deletes) from editing or deleting a financial record after it's written, with one narrow, intentional exception for marking a transaction as refunded.

**Important nuance for a compliance write-up:** this is an *application-level* guarantee, not a database-level one. It stops accidental or careless mutation from our own application code. It would **not** stop someone with direct database access (e.g., a raw SQL console) from altering history. The code's own documentation says this explicitly — it should be described as "protected against application mistakes," not as a cryptographically tamper-proof audit log, unless a database-level trigger is added later. Stripe webhook events are also kept permanently and never deleted, which supports reconciling billing history independently of our own ledger.

---

## 6. Data retention and deletion ("right to be forgotten")

Deleting a user account is a genuine, permanent delete — performed only by super admins — and it cascades to remove that person's related records (submissions, enrollments, activity history). The one deliberate exception is billing history, which is kept even after an account is deleted, specifically to preserve financial records.

**Gaps to be aware of:** there is currently no self-service "export my data" or "delete my account" flow for a user to trigger themselves, and no documented retention schedule (e.g., "student submissions are purged after N years"). If a school asks for a formal data-retention or data-subject-rights policy, that's a policy the business needs to write and then have engineering implement — it doesn't exist as a feature today.

---

## 7. Payments

Card details are handled by Stripe directly — our servers never receive or store raw card numbers, which keeps PCI DSS scope minimal (we only handle Stripe's tokenized references). Stripe API keys are pulled from environment configuration, never hardcoded, and the app automatically uses Stripe's test keys outside of production so live payment data can't leak into a development environment by accident.

---

## 8. Secrets and configuration

API keys, database credentials, and encryption keys all come from environment variables — none are hardcoded in the source code. The app is written to fail to start rather than fall back to an insecure default if a required secret (like the JWT signing key) is missing. Debug mode, which would leak stack traces and internal details, is hard-wired off in production and only turns on in local development.

One thing worth a quick manual check rather than a code review: local environment files (`.env`, `QA.env`, `live.env`) exist in the project directory. It's worth confirming none of these are accidentally tracked in git history — a one-time `git log --all --full-history -- .env` style check is cheap insurance.

---

## 9. File uploads

Uploaded submissions (PDFs, images) are capped at 50MB, and the actual file content is parsed and validated (not just trusted by filename) before being processed — a malformed or malicious file fails during parsing rather than being accepted blindly. Files are stored via Cloudinary in production rather than on the app server's own disk.

---

## 10. Summary: what to tell a school, and what to fix first

**Safe to say today:**
- Role-based access control keeps students, teachers, school admins, and staff scoped to their own data.
- Card payment data never touches our servers.
- Standard injection defenses (ORM, no raw SQL) are in place.
- CORS/CSRF/allowed-hosts are locked to known domains, not wide open.
- Financial records can't be silently edited by application bugs.
- Caps on repeated sign-in attempts, emailed codes and sign-ups — **only once the fix in §3 goes live for customers. It is not live yet.**

**Should be disclosed to schools now, or fixed before claiming otherwise:**
- No field-level encryption of student PII/grades — only Google OAuth tokens are encrypted at the field level.
- No self-service data export or account deletion for end users.
- Access tokens can't be revoked early if stolen (24-hour exposure window).
- **The caps described in §3 were not working at all until 3 September 2026, and the fix is not yet live for customers.** Until it is, do not describe sign-in, emailed codes or sign-up as protected against repeated automated attempts. Nobody has yet checked whether the gap was used.

**What to fix first, in order:**
1. Make the §3 fix live for customers — it is only on our trial environment today.
2. Check the records from the affected period to see whether the gap was used.
3. Make the six-digit student invitation code longer and harder to guess. The caps reduce this risk but do not remove it.

**Confirmed manually (2026-09-01):**
- `SECURE_SSL_REDIRECT` is turned on in the live production environment.
- `.env` / `QA.env` / `live.env` have never been committed to git history.

**Tested on the trial environment (3 September 2026):**
- Before the fix: sign-in and password-reset attempts kept being allowed
  well past their caps. The caps were doing nothing.
- After the fix: attempts are refused once the cap is reached, and stay
  refused when someone tries to disguise where they are connecting from.
- Not yet tested on the live service, because the fix is not live there.
