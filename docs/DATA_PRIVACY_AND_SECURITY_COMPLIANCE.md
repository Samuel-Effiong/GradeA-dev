# Data Privacy & Security Compliance Write-Up — Grade Automator Plus

*Prepared from a review of the actual codebase as of 2026-09-01, with §3 added 2026-09-03. This document describes what is genuinely implemented today, not a target state. Where something is a gap or a weak spot, it's called out plainly rather than glossed over — an honest write-up is more useful for compliance purposes than an optimistic one.*

For a deeper engineering-level reference, see [docs/backend/security-and-tenancy.md](backend/security-and-tenancy.md).

---

## 1. Who this covers and why it matters

Grade Automator Plus handles data covered by **FERPA** (the U.S. student-records privacy law) because it stores student names, submissions, and grades on behalf of schools and teachers. It also processes payments through Stripe, which brings light **PCI DSS** obligations (though card numbers themselves never touch our servers — see §7).

---

## 2. Who can see what (access control)

The app has four roles: **student, teacher, school admin, and super admin** (our own staff). There's no single "tenant ID" switch — instead, every screen and API endpoint is written to only fetch the data that role is allowed to see. A teacher's query for students only ever returns their own students; a school admin's queries are scoped to their own school; students only ever see their own submissions and assignments that have actually been published.

Login uses signed tokens (JWT) that expire after 1 day, with a separate refresh token good for 2 days that's invalidated and re-issued every time it's used. Changing your password immediately logs out every other device. Sensitive actions — the six-digit email verification code, password reset requests, login attempts — are rate-limited so they can't be brute-forced from outside. **This document previously said that without qualification; on 2026-09-03 those limits were found not to be working at all, and were fixed. See §3, which explains what happened and what it exposed.**

**Honest limitation:** if someone steals a valid access token, there's no way to revoke it early — it's simply good for up to 24 hours. There's also no multi-factor authentication. An account now locks for 15 minutes after 5 failed login attempts in a row (on top of the existing IP-based rate limiting), so password-guessing against one account is bounded even from many IPs at once. The remaining gaps are not unusual for a product at this stage, but are worth knowing if a school's IT department asks.

---

## 3. Stopping automated attacks (rate limiting)

### What this is, in plain terms

"Rate limiting" means capping how many times the same person can hit a
sensitive endpoint in a given period. Without it, an attacker can simply
try again in a loop — thousands of times a minute — until something
works. It is the main defence against guessing passwords, guessing the
six-digit codes we email out, and mass-creating fake accounts.

### The limits now in force

| Action | Limit per internet address |
| --- | --- |
| Log in | 10 per minute |
| Request an email-verification code | 5 per hour |
| Request a password-reset code | 5 per hour |
| Use a password-reset code | 10 per hour |
| Register (teacher, student or school admin) | 10 per hour, shared |
| Sign in with Google | 20 per hour |
| Everything else, signed out | 60 per minute |

### What went wrong

These limits were written correctly and were switched on. **They were
not actually working.** Every one of them was silently doing nothing.

The cause was a single missing setting. Our app runs behind Railway's
network, so requests reach us second-hand and we have to work out who
the original visitor was by reading a header they attach. That header
lists two addresses: the real visitor, and the Railway machine that
passed the request along. We were reading the wrong one — and Railway
spreads traffic across a pool of machines, so that second address
**changed from one request to the next**.

The practical effect: the system treated almost every single request as
a brand-new stranger. A counter that resets on every request never
reaches its limit. Someone could attempt to log in as many times as they
liked and never be stopped.

We measured this against the live beta service before fixing it: 14
rapid login attempts against a "10 per minute" cap were all allowed
through, and 8 password-reset requests against a "5 per hour" cap were
all allowed through.

**Fixed on 2026-09-03** by telling the app which of the two addresses is
the real visitor. Verified on beta immediately afterwards: the 11th
login attempt in a minute is now correctly refused, and it stays refused
even when the caller tries to disguise itself by faking the header.

### What this exposed while it was broken

We should assume that, for as long as this was live, the following were
possible for anyone on the internet:

- **Unlimited password guessing.** Partly contained by a separate
  protection: an account locks for 15 minutes after 5 wrong passwords
  in a row. That limit worked, and is per-account, so it held even
  though the rate limiting did not.
- **Unlimited guessing of the six-digit codes** we email for account
  activation and student invitations. Six digits is a million
  combinations — trivial to work through when nothing stops you
  retrying. This is the most serious of the three, because student
  invitation codes are checked against *every* pending invitation at
  once rather than one named account, so the odds improve as more
  invitations are outstanding.
- **Unlimited automated sign-ups and password-reset emails**, meaning
  anyone could have used the system to send large volumes of email, or
  fill the database with fake accounts.

**We have not established whether any of this was actually exploited.**
Saying "no impact" would be a guess. The honest position is that the
door was open and we have not yet checked whether anyone walked through
it. Reviewing access logs from the affected period for repeated failed
logins or bursts of code requests from a single source is the way to
answer that, and is recommended before making any statement to a school
about this.

### What is still true after the fix

- The fix is **verified on the beta environment. It has not yet reached
  production.** Until it does, everything described above still applies
  to the live service.
- Rate limiting counts **per internet address**, not per account. A
  whole school sharing one office connection shares one budget, and an
  attacker spread across many addresses gets a fresh budget for each.
  The per-account 15-minute login lockout is what covers that second
  case.
- The limits protect against bulk automated abuse. They are not a
  substitute for multi-factor authentication, which we do not offer
  (see §2).

---

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
- Login, code-request and sign-up endpoints are rate-limited against automated abuse — **once §3's fix reaches production. On beta today, not yet live.**

**Should be disclosed to schools now, or fixed before claiming otherwise:**
- No field-level encryption of student PII/grades — only Google OAuth tokens are encrypted at the field level.
- No self-service data export or account deletion for end users.
- Access tokens can't be revoked early if stolen (24-hour exposure window).
- **Rate limiting was not functioning at all until 2026-09-03 (§3), and the fix is still only on beta.** Do not describe login, verification codes or sign-up as brute-force protected on the live service until it ships. Whether the gap was exploited has not been investigated.

**Highest priority fix, in order:**
1. Ship the rate-limiting fix to production (§3) — currently beta only.
2. Review logs from the affected period to establish whether the gap was exploited.
3. Lengthen the six-digit student invitation code, which is checked against every pending invitation rather than one account (§3). Rate limiting reduces this risk but does not remove it.

**Confirmed manually (2026-09-01):**
- `SECURE_SSL_REDIRECT` is turned on in the live production environment.
- `.env` / `QA.env` / `live.env` have never been committed to git history.

**Confirmed by live measurement (2026-09-03, beta service):**
- Before the fix: 14 login attempts against a 10/minute cap were all
  allowed; 8 password-reset requests against a 5/hour cap were all
  allowed. The limits were doing nothing.
- After the fix: the 11th login attempt in a minute is refused, and stays
  refused when the caller fakes the address header to disguise itself.
- Not yet measured on production, because the fix has not shipped there.
