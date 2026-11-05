# H-294: backend message audit against the QA Message Catalogue (15 Oct 2026)

Read-only. No code or wording changed. Base: beta a3ca097d8.
Owner question (2026-11-05): "the frontend does not generate messages, it
only shows what the backend sends" - so which catalogue rows are really ours?

## How
- (a) `extract_msgs.py` (AST) collected 476 message strings the backend can
  send (exception texts, `detail`/`message`/`error` values, ReasonSpec
  texts, `*_MESSAGE` constants) -> `backend-messages.json`, jargon-flagged.
  Plain `ValueError` texts are excluded: `describe_user_error` replaces them
  with a fallback before any user sees them.
- (b) A throwaway probe (Django test client, test DB, not committed) sent
  the failing/succeeding requests behind the catalogue rows and recorded
  status + top-level `message` -> `probe-results.json`.

## Key structural fact
Every JSON response has a top-level `message` (users/renderers.py). Errors
with no text get "An error occurred"; successes with no text get
"Request Successful". So an "App fallback" toast means either the frontend
didn't read `message`, or our `message` was too generic/raw to show.

## Findings - bugs
B1. POST /course with no session (omitted, null or "") -> 500
    (classrooms/serializers.py:199 `value.owner_type` on None). This is the
    catalogue's "500 seen when the session id is empty" (section 3).
    H-274 (d) tested *session* creation and closed it as not reproducible;
    the 500 is in *course* creation.
B2. Five billing refusals send {"status": "inactive", "message": ...} with
    a 404; the renderer flattens that to
    "1. Status: inactive\n 2. Message: No active subscription found to cancel".
    billing/views.py:897, 940, 1120, 1504, 1543 (subscription me/cancel/
    resume, credit wallet, subscription lookup).
B3. Publish-all with nothing graded answers 200 (success) with
    "No graded submissions found to publish." - a refusal sent as success.

## Findings - generic or raw server text (why the portal writes its own)
G1. Successes say "Request Successful": create session, create course,
    edit course, direct add, assignment edit. Session delete is a 204 with
    no body. -> catalogue "Create session succeeds (server message / none)",
    "Create course succeeds", "Direct Add succeeds", etc.
G2. 404s say "No Course matches the given query." /
    "No StudentSubmission matches the given query." (Django default naming
    internal model names).
G3. Field validation is DRF's default, field-labelled and numbered with
    newlines: "Name: This field may not be blank.", "1. First name: Ensure
    this value has at least 2 characters (it has 1).\n 2. ...",
    "Raw input: This field is required." (internal field name).
G4. Duplicate student on Direct Add by the same name answers
    "A student with the exact name 'Kim Doe' is already enrolled in this
    course." (name check runs before the email check).

## Findings - jargon in texts teachers/students see (wording needs owner OK)
- OTP: "An OTP has been sent if an account with that email exists."
  (users/views.py, reset), "An OTP code has been sent to your email"
  (users/views.py:1601, change-password code), "Invalid email, OTP code, or
  new password." (users/views.py:1456 and 4 more).
- token: "Email and Token are required." (users/views.py:1005), "Invalid or
  expired activation token." (2062), "Invalid token or user not found."
  (classrooms/services/enrollment.py:612), "Token has been revoked. Please
  log in again." (users/tokens.py:20), "Invalid or expired token" (1810).
- Google: "Google did not return an ID token" (2250), "Invalid Google token
  signature" (2472).
- log in vs sign in: "Too many failed login attempts. Please try again
  later." (reason_codes.py:368, users/serializers.py:490).
- overage/blocks/cycle: H-268 credit-limit text (owner-approved; catalogue
  BUG-008 still asks to drop "blocks"/"cycle"); "Redirecting to secure
  checkout to complete your overage purchase." (billing/views.py:1380);
  "We couldn't confirm the overage price..." (billing/overage_pricing.py:99);
  "This plan does not support overage credit purchases."
  (billing/stripe_service.py:2431).
- Stripe/route names: "This subscription has no associated Stripe
  subscription. (it may have been granted manually)..."
  (billing/stripe_service.py:1488); "...Please subscribe again via
  select-plan." (stripe_service.py:1080).
- IDs: "Course ID is required.", "Topic ID is required.", "Invalid Course
  ID format. Must be with a valid UUID" (assignments/views.py:804/872/878)
  - catalogue "Missing ID (developer checks)".
- Spelling (owner rule: American): "licence" in teacher/admin-facing texts
  reason_codes.py:521, 532; billing/license_service.py:421, 1979.
- Typo: "submissons" (assignments/views.py:1764) - fixed by H-292 (local).

## Not concluded (probe used the wrong method/fields - re-probe if wanted)
Schedule grading in the past, password change wrong current password,
notification settings save, select plan.

Admin/QA-tool texts (qa_time_travel, license admin ops, super-admin) are in
`backend-messages.json` but not listed here: not teacher-facing.
