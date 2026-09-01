# Data Privacy & Security Compliance Write-Up — Grade Automator Plus

*Prepared from a review of the actual codebase as of 2026-09-01. This document describes what is genuinely implemented today, not a target state. Where something is a gap or a weak spot, it's called out plainly rather than glossed over — an honest write-up is more useful for compliance purposes than an optimistic one.*

For a deeper engineering-level reference, see [docs/backend/security-and-tenancy.md](backend/security-and-tenancy.md).

---

## 1. Who this covers and why it matters

Grade Automator Plus handles data covered by **FERPA** (the U.S. student-records privacy law) because it stores student names, submissions, and grades on behalf of schools and teachers. It also processes payments through Stripe, which brings light **PCI DSS** obligations (though card numbers themselves never touch our servers — see §7). Because student work is sent to third-party AI providers to be graded, that data flow is the single most important thing to understand and disclose to schools.

---

## 2. Who can see what (access control)

The app has four roles: **student, teacher, school admin, and super admin** (our own staff). There's no single "tenant ID" switch — instead, every screen and API endpoint is written to only fetch the data that role is allowed to see. A teacher's query for students only ever returns their own students; a school admin's queries are scoped to their own school; students only ever see their own submissions and assignments that have actually been published.

Login uses signed tokens (JWT) that expire after 1 day, with a separate refresh token good for 2 days that's invalidated and re-issued every time it's used. Changing your password immediately logs out every other device. Sensitive actions — the six-digit email verification code, password reset requests, login attempts — are all rate-limited so they can't be brute-forced from outside.

**Honest limitation:** if someone steals a valid access token, there's no way to revoke it early — it's simply good for up to 24 hours. There's also no multi-factor authentication and no account lockout after repeated failed logins (only IP-based rate limiting). Neither is unusual for a product at this stage, but both are worth knowing if a school's IT department asks.

---

## 3. What third-party AI providers actually see

This is the part that matters most for a school-facing compliance conversation.

Grading requests go through **OpenRouter**, a routing layer that forwards the request to whichever underlying AI vendor is configured — currently a mix that includes xAI (Grok), DeepSeek, OpenAI, and Google models, depending on load and fallback rules. In practice, that means student data can end up processed by more than one AI company, not a single named vendor.

**What gets sent:** the actual text (or scanned images) of a student's submission always goes to the AI — that's unavoidable, it's what's being graded. The **student's name** is a separate matter: it's only sent during the *extraction* step (matching a scanned page to a roster entry), never during the actual scoring/grading call, which only ever sees the rubric and the answer content — no name.

**Where the name-exposure risk actually was, and what's now been done about it:**

1. **Vendor-level: blocked which AI companies can even receive the request.** The application now sets OpenRouter's `provider.data_collection: "deny"` on every single AI call (`ai_processor/services.py`), which restricts routing to providers that don't collect/retain the data at all — this was verified against OpenRouter's own documentation and confirmed working in the test suite (27/27 passing). This is enforced in code, at the request level, not just a dashboard setting someone could forget to keep on.
2. **Account-level: confirmed as a second layer.** The OpenRouter account's own Data Training settings (`openrouter.ai/settings/privacy`) were checked directly and are already set correctly — "Allow paid endpoints that train on request data," "Allow free endpoints that train," "Allow free endpoints that publish prompts," and "Allow 1% data discount in workspaces" are all **off**. This is the setting that matters most for FERPA: it prevents any vendor (DeepSeek included, which trains on API data by default per their own privacy policy) from using student submissions to train their models at all. Verified directly against DeepSeek's published Privacy Policy: they train on API data unless a customer opts out, and doing so requires emailing them directly — OpenRouter's setting sidesteps that by simply never routing to them when this is off.
3. **Not yet enabled: Zero Data Retention (a stronger, separate setting).** OpenRouter also offers per-vendor "Zero Data Retention" toggles (Anthropic, OpenAI, Google, SpaceXAI, plus a "non-frontier" catch-all) that go further than blocking training — they stop the vendor from *storing the data at all*, even briefly for logs or debugging, by routing only through enterprise infrastructure (e.g. Azure OpenAI instead of OpenAI's own API) that contractually guarantees zero retention. These are currently all **off**. This is not a compliance violation on its own — FERPA's bar is "don't repurpose the data," not "never let a vendor's servers touch it," and Data Training being off already clears that bar — but turning ZDR on would meaningfully shrink how long copies of student data sit on any vendor's infrastructure, and is recommended as a hardening step.
4. **Not available on the current plan: regional routing.** OpenRouter also offers a Business-tier feature to guarantee inference only ever runs inside the EU or US (avoiding, among other things, any routing through infrastructure in mainland China, which is where DeepSeek is required to store data per their privacy policy). This is gated behind upgrading to a paid Business plan and isn't active today — it's the remaining gap after the training and account-level fixes above, and is a plan-upgrade decision rather than an engineering one.

Separately, our own error-monitoring tool (Sentry) is explicitly configured to **never** capture student work, grades, or billing details in error reports — that channel was already clean and unaffected by any of the above.

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

**Should be disclosed to schools now, or fixed before claiming otherwise:**
- No field-level encryption of student PII/grades — only Google OAuth tokens are encrypted at the field level.
- No self-service data export or account deletion for end users.
- Access tokens can't be revoked early if stolen (24-hour exposure window).
- No guarantee yet that AI inference runs only in the EU/US (a paid OpenRouter plan upgrade, not yet purchased).

**Confirmed manually (2026-09-01):**
- `SECURE_SSL_REDIRECT` is turned on in the live production environment.
- `.env` / `QA.env` / `live.env` have never been committed to git history.

**Remediated (2026-09-01):**
- AI providers are now blocked from training on student data, both in code (`provider.data_collection: "deny"` on every AI call) and confirmed at the OpenRouter account level (all Data Training toggles off).

**Recommended next hardening step:**
- Turn on OpenRouter's Zero Data Retention toggles (currently off) to stop vendors from even briefly storing request data, not just training on it.
