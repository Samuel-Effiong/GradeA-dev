# Security and tenant isolation

> Part of the [backend reference](README.md). This is a cross-cutting summary; each rule's home is linked. Related: [users-and-auth.md](users-and-auth.md), [billing-core.md](billing-core.md), [project-config.md](project-config.md).

## In plain terms

Four kinds of people use this app — students, teachers, school administrators, and the company's own staff — and each must see only their own corner of it. There is no separate "tenant id" column doing that work; instead **every list endpoint narrows its own query** based on who is asking, and a request for something outside that narrowing simply returns "not found". This document collects those rules in one place, alongside the other security boundaries: what counts as authentication, what the app does with text written by users and by the AI, and the handful of places where a mistake would cross a line that is meant to be absolute.

---

## Roles

| `user_type` | Belongs to a school? | Sees |
|---|---|---|
| `STUDENT` | **never** (`school` is NULL, except when invited by a licence-track teacher) | their own submissions and published assignments in their courses |
| `TEACHER` | only on the licence track | their own courses, assignments, submissions, and their students |
| `SCHOOL_ADMIN` | **required** | their school's teachers, students, and analytics |
| `SUPER_ADMIN` | **must not** | everything — but see the gaps below |

`SUPER_ADMIN` **requires both** `user_type == SUPER_ADMIN` **and** `is_superuser` ([classrooms/permissions.py:74-80](../../classrooms/permissions.py#L74-L80)). Checking one alone *"would let the two disagree."*

### Platform staff are not tenant members

Enforced in **two** places, deliberately:

| Where | Rule |
|---|---|
| `CustomUserSerializer.validate` ([users/serializers.py:156-221](../../users/serializers.py#L156-L221)) | a superadmin cannot be given a `school` or a tenant `user_type`; an account being promoted to `SUPER_ADMIN` must clear its school in the same request |
| `LicenseSubscriptionService.validate_admin_user` ([billing/license_service.py:274-322](../../billing/license_service.py#L274-L322)) | a superadmin cannot be named a licence's `admin_user` |

The consequence of the first hole, recorded in the code: a superadmin could become `user_type=SCHOOL_ADMIN` with a school attached, appear as that school's admin on every school screen **while still holding `is_superuser`**, and simultaneously lose their own access because `IsSuperAdmin` checks `user_type` — *"the account ends up able to administer neither the platform nor, legitimately, the school."*

The consequence of the second: a superadmin could name themselves a school's licence admin, **divert that school's admin credit allocation to their own wallet**, and displace the school's real admin.

Pre-existing bad rows are found by `audit_school_admins` ([billing-licenses.md](billing-licenses.md#repairing-existing-rows)) — **read-only**, because *"picking the right admin for a school is a business decision, and for a license it also moves a credit allocation."*

---

## Authentication

| Mechanism | Detail |
|---|---|
| Default classes | `JWTAuthentication`, then `SessionAuthentication` |
| Access token | **1 day**, HS256 |
| Refresh token | 2 days, rotated, blacklisted after rotation |
| Default permission | `IsAuthenticated` |
| Webhooks | **signature verification IS the auth** — no user, CSRF-exempt |
| Health endpoints | `AllowAny`, no auth classes, throttle-exempt |

**There is no revocation path for an access token before it expires** — only refresh tokens are blacklisted. A stolen access token is valid for up to 24 hours.

Password reset and password change both blacklist **every** outstanding refresh token ([users/views.py:880-882](../../users/views.py#L880-L882), [990-992](../../users/views.py#L990-L992)), so those flows do log out every other device — but only at the refresh boundary.

> **UNVERIFIED:** no rationale is recorded for the 1-day access lifetime. Check whether a frontend constraint (e.g. no silent-refresh implementation) drove it.

### The auth surface

Nine unauthenticated POST endpoints, each with its own throttle bucket ([project-config.md](project-config.md#throttling)):

| Endpoint | Bucket | Threat closed |
|---|---|---|
| `auth/login` | 10/min | credential stuffing |
| `auth/verify` | 5/hour | **6-digit activation code brute force** |
| `auth/otp` | 5/hour | **load-bearing for the reset lockout** |
| `auth/reset-password` | 10/hour | |
| `auth/register`, `/student`, `/school-admin` | 10/hour | signup spam |
| `auth/google-auth` | 20/hour | |
| `course/renew-student-token` | shares `register` | *"both a token-guessing oracle and a free outbound-mail trigger"* |

`OTPRequestThrottle` is not spam control — it is a **correctness dependency**: `PasswordResetOTP.generate_code()` resets the failed-attempt counter, so *"an attacker who can request unlimited fresh codes can clear the lockout and keep guessing"* ([users/throttling.py:36-47](../../users/throttling.py#L36-L47)).

Throttling is **anonymous-only by design** — `UserRateThrottle` is not enabled because dashboard and grading views are chatty ([settings.py:1015-1022](../../AutoGrader/settings.py#L1015-L1022)). The one exception is `custom_ai_prompt` (10/min per user), which is the *only* volume control on the unmetered superadmin AI path.

### Password reset hardening

| Control | Value |
|---|---|
| Code | 6 numeric digits, `get_random_string` |
| Validity | 15 minutes |
| `MAX_ATTEMPTS` | 5 |
| `LOCKOUT_DURATION` | 30 minutes |
| Comparison | `constant_time_compare` |
| Failure messages | **identical** for every non-lockout failure |

The OTP is looked up **by user, not by `(user, code)`** ([users/views.py:847-851](../../users/views.py#L847-L851)) — the old lookup *"made every failure indistinguishable from 'no OTP exists', which is why the attempts budget could not be enforced."*

**A residual enumeration leak:** `auth/otp` returns a neutral 202 for an unknown email, but a **known** email in the wrong state raises a specific `ParseError` ("Email already verified", "Email not verified") — which distinguishes it from an unknown one.

---

## Tenant isolation

There is no tenant middleware and no row-level security. **Isolation is `get_queryset()`, endpoint by endpoint.**

| Viewset | `TEACHER` | `STUDENT` | `SCHOOL_ADMIN` | `SUPER_ADMIN` |
|---|---|---|---|---|
| `CustomUserViewSet` | self + own students | self | self + school + school's students | all |
| `AssignmentViewSet` | own courses | enrolled + **`PUBLISHED` only** | **none** | **none** |
| `StudentSubmissionViewSet` | own courses' | own, excl. draft/unpublished assignments | **none** | **none** |
| `CourseViewSet` | own | enrolled | **none** | **none** |
| `SessionViewSet` | own individual, or school's if licensed | enrolled | school's | all |
| `StudentCourseViewSet` | own courses' | own | **none** | **none** |
| `SchoolViewSet` | — | — | — | **only** (`IsSuperAdmin`) |
| dashboard viewsets | own only | own only | own school only | all |

Four things worth noticing:

1. **`SUPER_ADMIN` sees nothing through most content viewsets.** Platform-wide visibility comes from the dashboard endpoints, not from the CRUD ones.
2. **Students are scoped through enrolment, not through a school** — they carry `school=NULL` unless a licence-track teacher invited them.
3. `AssignmentViewSet`'s student clause filters on `PUBLISHED` but **not** on enrolment status, so a `WITHDRAWN` student still sees published assignments.
4. `CustomUserViewSet.get_queryset` **must never raise** — `UserCacheMixin.get_cache_key` calls it for the model name *before* permissions run, so an exception surfaces as a 500 instead of a 401/403 ([users/views.py:299-302](../../users/views.py#L299-L302)).

### Seeing is not editing

The querysets deliberately let a teacher **read** their students. `partial_update` re-checks and raises `PermissionDenied` unless the target is the caller or a super admin ([users/views.py:329-346](../../users/views.py#L329-L346)) — *"without this check that read access would also grant write access over those accounts."*

### Custom actions bypass DRF's object hook

A DRF `@action` that never calls `self.get_object()` gets **no** `has_object_permission` check. The pattern used throughout `classrooms/views.py`:

```python
course = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])
```

*"a bare `Course.objects.get(pk=...)` here would let a teacher enrol a student into another teacher's course by guessing a course id"* ([classrooms/views.py:1331-1335](../../classrooms/views.py#L1331-L1335)).

A second ordering subtlety: the ownership lookup happens **before** the `transaction.atomic()` block, so its `Http404` propagates as a clean 404 rather than being swallowed by a blanket `except Exception` and downgraded to a 500.

### Privileged fields

`CustomUserSerializer.PRIVILEGED_FIELDS = ("user_type", "school")` are forced read-only unless the serializer was built with an authenticated super admin in context ([users/serializers.py:71-91](../../users/serializers.py#L71-L91)).

> **DRF ignores `Meta.read_only_fields` for fields declared on the class**, and `school` is declared there — so listing it in `Meta` alone would leave it **writable**. Both directions are set explicitly in `__init__`.

`AuthViewSet.register` builds the serializer with **no context at all**, so a client-sent `user_type` is silently dropped and the model default `TEACHER` applies.

### Known gaps

| Gap | Severity | Where |
|---|---|---|
| `TopicViewSet` has `permission_class` (**singular**) — DRF ignores it, so `IsTeacherOrReadOnly` is **not in effect**; only the default `IsAuthenticated` and the queryset scoping apply | medium | [classrooms/views.py:3031](../../classrooms/views.py#L3031) |
| `/tasks/status/<id>` falls back to `AsyncResult` with **no ownership check** when no tracked row exists | low — ids are UUIDs, data is a status string | [users/views.py:1757-1767](../../users/views.py#L1757-L1767) |
| `mark_reviewed`'s `@action` declares no `permission_classes`; protection comes from `get_permissions()`'s default branch | none in practice, fragile by construction | [students/views.py:1229](../../students/views.py#L1229) |
| Django-level `handler400/403/404/500` all render `"message": "Not Found"` | cosmetic, but misleading in logs | [AutoGrader/handlers.py:9](../../AutoGrader/handlers.py#L9) |

---

## Transport and browser security

Gated by `ENVIRONMENT` ([project-config.md](project-config.md#environment-gating)):

| Control | `prod` | Reasoning |
|---|---|---|
| `ALLOWED_HOSTS` | fixed allowlist + Railway domains | was `"*"` everywhere — *"permits Host-header injection (cache poisoning, poisoned password-reset links…)"* |
| `CORS_ALLOWED_ORIGINS` | explicit allowlist | `CORS_ALLOW_ALL_ORIGINS = True` previously overrode it in prod, *"letting any website call the API with a stolen/phished bearer token"* |
| `CORS_ALLOW_CREDENTIALS` | **`False`** | no cookie/session riding |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `True` | off locally, or a `Secure` cookie *"looks like a broken login"* on `http://localhost` |
| HSTS | 30 days, subdomains, **not preloaded** | *"submission to the browser preload list is effectively irreversible"* |
| `SECURE_PROXY_SSL_HEADER` | `X-Forwarded-Proto` | required behind the TLS-terminating proxy |
| `SECURE_SSL_REDIRECT` | **opt-in**, default `False` | *"enabling it before the proxy is confirmed to set `X-Forwarded-Proto` takes the whole site down with a redirect loop"* |

**Localhost is allowed cross-origin in every environment** ([settings.py:250-259](../../AutoGrader/settings.py#L250-L259)) — a deliberate exception, safe only because `CORS_ALLOW_CREDENTIALS` is `False`: *"it only lets localhost JS read responses to bearer-token requests it already had to have the token to make."*

**An unrecognised `ENVIRONMENT` fails closed for security settings** (`DEBUG=False`, prod `ALLOWED_HOSTS`/CORS) but resolves to **production Redis and Postgres** ([async-and-infrastructure.md](async-and-infrastructure.md#configuration)). Worth treating as a hazard.

---

## Untrusted input

The app treats three categories of text as hostile.

### 1. Text sent to the model

Three delimited wrappers, each with an explicit "this is DATA, not instructions" note ([ai-processor.md](ai-processor.md#prompt-injection-defence)):

| Input | Wrapper |
|---|---|
| Web pages the model asked for | `<untrusted_external_content source="…">` |
| Student answers | `<untrusted_student_answers>` |
| Dashboard context **and** the user's question | `<untrusted_context_data>`, `<untrusted_user_question>` |

The layering is stated precisely: *"Scores are clamped server-side afterwards, so injection can no longer push a score past the rubric cap — but within-cap inflation and poisoned feedback text still need the same treatment."* **The wrapper is defence in depth; the arithmetic clamp is the guarantee.**

### 2. HTML the model produced

`sanitize_ai_html` ([assignments/services.py:364-387](../../assignments/services.py#L364-L387)) — *"The AI is only instructed (not enforced) to emit safe markup, so **this is the actual security boundary**."*

`strip_raw_text_elements` runs **first**, because *"bleach removes the `<script>`/`<style>` tags but keeps their source, which would otherwise surface as visible prose."* Then `bleach.clean(tags=allowlist, attributes=…, protocols=[], strip=True)`.

The attribute allowlist is three entries: `colspan`/`rowspan` on `td`/`th`, and `class` on `span` **only when the value is exactly `math-block`**.

`sanitize_ai_image_url` permits only absolute `http`/`https` URLs with a netloc.

**This became a real boundary when the PDF renderer changed.** Under WeasyPrint a stray `<script>` was inert — it has no JS engine. **Chromium executes JavaScript while printing** ([assignments/pdf_document.py:53-62](../../assignments/pdf_document.py#L53-L62)), so every interpolated value in `pdf_document.py` is escaped or sanitised, including `instructions`, which the shared formatter does *not* sanitise on this path.

### 3. URLs the model chose

`fetch_url_content` is the SSRF surface, fully documented in [integrations.md](integrations.md#fetch_url_content--arbitrary-web-pages): scheme allowlist, blocked metadata hostnames, IP classification against private/loopback/link-local/multicast/reserved, **manual redirect following with re-validation on every hop**, 10s timeout, 2 MB cap, and `MAX_TOOL_CALL_ROUNDS = 3`.

### Other input handling

| Input | Guard |
|---|---|
| Uploaded files | `validate_upload_size` — `DATA_UPLOAD_MAX_MEMORY_SIZE` only caps in-memory buffering; a larger part spills to disk and is **still accepted** |
| PDFs | `MAX_PAGE_COUNT = 300`, one-page-at-a-time rasterisation |
| Inbound `X-Request-ID` | `^[A-Za-z0-9._-]{1,128}$` — the value is echoed and logged verbatim, so *"it must never be allowed to contain characters that could break log parsing"* |
| Ordering params | `filter_queryset` **re-validates against `ordering_fields`** when it bypasses `OrderingFilter` — *"an unvetted field name here would be an ORM injection point"* ([students/views.py:225-234](../../students/views.py#L225-L234)) |
| KaTeX asset paths | `target.relative_to(KATEX_DIR)` — bounded *"on principle, not on the strength of an argument about who can reach it"* |
| Assignment titles | HTML stripped on **every** save path |

---

## Data at rest and in transit

| Data | Protection |
|---|---|
| Google access/refresh tokens | `EncryptedCharField` via `FIELD_ENCRYPTION_KEY` |
| Passwords | Django's hasher; Google accounts get `set_unusable_password()` |
| Everything else | plaintext in Postgres |
| Sentry | `send_default_pii=False` — *"These carry student work, grades, and billing identifiers"* |
| Traces | sampled at 5% |
| Errors shown to users | `describe_user_error` / `describe_background_task_error` — only known user-authored exceptions pass through verbatim |
| Tracebacks | `DEBUG` only ([users/renderers.py:150-154](../../users/renderers.py#L150-L154)) |
| Background-task `error` column | a user-facing sentence, never a traceback; the real exception is logged server-side |

Placeholder student emails (`@student.local`) are returned as `email: null` by the API and excluded from every outbound mail queryset.

---

## The absolute lines

Four invariants the codebase treats as inviolable, each guarded in more than one place.

### 1. The email-track fork

Personal email → individual account. Business email → school account. **No merge path exists.** Enforced at five points; the full rule, its history, and the audit command are in [users-and-auth.md](users-and-auth.md#the-personal-vs-business-email-fork).

The two helpers **fail closed**: a malformed or disposable address is neither personal nor business and is refused on **both** tracks. `is_personal_email` is a *positive* test, so an unrecognised domain is refused on the individual track rather than waved through.

The rule fires on creation, on an email change, **and on a `user_type` change** — that last trigger was added after PATCHing a `jane@gmail.com` teacher to `SCHOOL_ADMIN` was found to sail through: *"'only a super admin can do it' is not the same as 'it is allowed'."*

### 2. Nobody is billed on both tracks

Access resolves **licence-first**, so an account holding both is *"charged every month by Stripe for credits they can never spend. **The first sign of it is a refund request.**"*

Guarded in both directions:

| Direction | Guard |
|---|---|
| individual → licence | `_get_or_invite_teacher` / `_enroll_teacher_internal` raise `IndividualSubscriptionConflictError` |
| licence → individual | `_assert_not_on_the_license_track` — **added later**; the original guard was one-directional |

### 3. No duplicate billed work

Two claims, both a single conditional UPDATE, both with a staleness window derived from a hard kill point: the grading claim and the Stripe event claim ([async-and-infrastructure.md](async-and-infrastructure.md#idempotency)).

The Stripe claim adds a **fencing token** so a slow original cannot overwrite a thief's `SUCCEEDED` — *"otherwise a slow-but-failing original could flip a freshly SUCCEEDED row back to FAILED and invite a replay of non-idempotent Stripe side effects."*

### 4. Two identically-named students cannot share a course

Because the name is how a scanned paper is matched to a person, and *"an ambiguous match silently attributes one student's grade to another."* Checked in four places, including `StudentCourse.clean()` via an unconditional `full_clean()` on save ([classrooms.md](classrooms.md#the-name-uniqueness-rule)).

> The matcher itself uses `icontains`, which is **looser** than the rule guards against — `"Ann Smith"` matches `"Joanne Smithson"`, and `.first()` picks one silently. See [students-and-submissions.md](students-and-submissions.md#student-matching-for-proxy-uploads).

---

## Credit and feature gating

`AIProcessor.execute_graded_task` is the **single chokepoint** — every AI call goes through it, and access resolution and billing-target resolution are *"deliberately a single pass (not two separate if/elif chains) so the access check and the billing-target resolution can never drift out of sync."*

| Caller | Billed | Gate |
|---|---|---|
| `STUDENT` | the assignment's teacher | `can_ai_be_used_for_assignment` |
| `TEACHER` / `SCHOOL_ADMIN` | themselves | `can_user_access_ai` |
| `SUPER_ADMIN` | **nobody — unmetered, unrestricted** | none |
| anything else | — | `ValueError` |

The superadmin path is why the `custom_ai_prompt` throttle exists: *"a wallet-balance check already caps eventual cost for non-superadmin roles, but nothing previously capped call frequency, and the superadmin path is unmetered entirely."*

`HasCreditBalance` ([users/permissions.py:8-91](../../users/permissions.py#L8-L91)) is the view-level pre-check. For a **student** it checks *the teacher's* wallet, resolved from URL kwargs in the order `assignment_id` → `course_id`/`id` → `submission_id`/`pk` — **so an endpoint whose kwargs don't match those names will deny a student.**

`ADMIN_ALLOWED_AI_FEATURES` is a trap worth repeating: *"Every AI-processor call site whose caller passes a `SCHOOL_ADMIN` user must have its `feature=` string listed here, **or it is unconditionally blocked for every school admin regardless of plan/credits**."*

---

## Audit trails

| Trail | Immutable? | Covers |
|---|---|---|
| `CreditLedger` | yes, append-only | every credit movement, signed, with metadata |
| `CreditUsageLog` | `is_refunded` flips | every consumption, refundable by `task_id` |
| `StripeEvent` | **rows are never deleted** | every webhook delivery, with payload and attempt count |
| `LicenseBillingRecord` | append-only | every offline billing action, with `performed_by` |
| `BackgroundProcessingTask` | terminal states are one-way | every long-running job, with `requested_by` |
| `review_reasons` | appended | every review resolution, with `by` and `at` |
| `UserActivity` | append-only | one row per authenticated request |
| `Assignment.ai_raw_payload` | overwritten per extraction | the untouched model response |
| `X-Request-ID` | logs + Sentry | one id across a request and every task it spawns |

`StudentSubmission.ai_score` / `ai_feedback` are **never** overwritten by a manual override — which is what makes teacher-alignment measurement possible ([ai-quality-harness.md](ai-quality-harness.md#grading_eval--the-accuracy-scoreboard)).

---

## Audit commands

Both are **read-only by design**, and both say why.

| Command | Finds |
|---|---|
| `audit_email_track_separation [--strict]` | school admins on non-business emails; licence seats on non-business emails; individual teachers on non-personal emails; **accounts billed on both tracks** |
| `audit_school_admins [--strict]` | licences administered by a superadmin; superuser accounts holding `SCHOOL_ADMIN` |

> *"Closing the doors does NOT repair rows already written through them. This command finds those rows so a human can decide what each one should be. It NEVER writes. The repairs are business decisions… not things to do behind anyone's back."*

`--strict` exits 1, for CI or cron. **Neither is scheduled** — running them is currently a manual act.

---

## Threats not addressed

Stated plainly so nobody assumes otherwise:

| Not addressed | Note |
|---|---|
| Access-token revocation before expiry | 24-hour window |
| Rate limiting for authenticated users | one bucket, for AI chat only |
| Row-level security in Postgres | isolation is application-level only |
| Audit log of *reads* | only writes are recorded |
| MFA | none |
| Account lockout on repeated login failure | throttling only, by IP |
| IP allowlisting for admin | none |
| Secrets rotation | manual |
| DNS rebinding on `fetch_url_content` | validated once, resolved again on connect |
| Timeouts on Stripe, Google, Cloudinary, or LLM calls | bounded only by gunicorn/Celery |

`.secrets.baseline` and `.pre-commit-config.yaml` exist at the repo root, so secret-scanning runs pre-commit — see [operations.md](operations.md).

---

## Configuration

| Var | Default | Security effect |
|---|---|---|
| `ENVIRONMENT` | **required** | gates hosts, CORS, cookies, HSTS, storage, keys |
| `SECRET_KEY` | required | JWT signing, Fernet key derivation |
| `FIELD_ENCRYPTION_KEY` | `""` | **empty means Google tokens are not encrypted** |
| `SECURE_SSL_REDIRECT` | `False` | opt-in only |
| `SENTRY_DSN` | `""` | `send_default_pii=False` regardless |
| `ALLOWED_BUSINESS_EMAIL_DOMAINS` | `[]` | non-empty makes business classification a **strict allowlist** |
| `DISALLOWED_EMAIL_DOMAINS` | `[]` | extra consumer domains |
| `DISPOSABLE_EMAIL_DOMAINS` | `[]` | refused on **both** tracks |
| `EXEMPT_EMAIL_DOMAINS` | `[]` | **bypasses both rules** — must stay empty in production |
| `ENABLE_BILLING_TIME_TRAVEL` | `False` | QA endpoint; 404 when off |
| `ENABLE_STRIPE_LIVE_QA` | `False` | QA suite + console; 404 when off |

`EXEMPT_EMAIL_DOMAINS` deserves its own line. It defaults to empty because it opens **both** gates, and `yopmail.com` *"used to sit here permanently, which meant anyone could mint a school admin with a public throwaway address"* ([settings.py:1230-1236](../../AutoGrader/settings.py#L1230-L1236)).

Both QA flags additionally require a `sk_test_` Stripe key, re-checked **before every Stripe call** — *"Re-asserting per call costs one string comparison and removes the entire class of 'it was test mode when we started' bugs."*
