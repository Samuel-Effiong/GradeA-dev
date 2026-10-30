# H-273 NOTES: Audit Refusal Detail

## §1 Survey

### §1a Emit sites that see a request

Every audit row written for an HTTP request flows through one of these paths:

| Path | File | Line | Action | When | Has exception? | Has status? |
|------|------|------|--------|------|----------------|-------------|
| **ADMIN_ACTION denied** | audit/admin_action.py | :73 `emit_denied` | ADMIN_ACTION | IsSuperAdmin.has_permission returns False | No (permission check, not exception) | No (emitted before view runs) |
| **ADMIN_ACTION response** | audit/admin_action.py | :88 `emit_for_response` | ADMIN_ACTION | Middleware after view completes | No | Yes (`response.status_code`) |
| **STATE_CHANGE generic** | audit/request_audit.py | :214 `emit_generic_state_change` | STATE_CHANGE | Middleware, authenticated state-changing request with no surviving named event | No | Yes (`response.status_code`) |
| **Anonymous refusal** | audit/request_audit.py | :185 `emit_anonymous_refusal` | AUTH_LOGIN / STATE_CHANGE | Middleware, anonymous 4xx/5xx with no surviving event | No | Yes (`response.status_code`) |
| **Named call sites** | Various (see §1b) | — | Various | Explicit call in view/service code | Sometimes (inside except block) | Sometimes |

### §1b Named emit call sites (non-test, non-evidence)

Counted 38 explicit `emit(` calls across:
- assignments/tasks.py (4 calls: grading_requested, grading_completed, grading_failed, assignment_copy)
- assignments/views.py (6 calls: assignment CRUD, data export)
- audit/admin_action.py (2 calls: denied, response)
- audit/request_audit.py (2 calls: generic state_change, anonymous refusal)
- audit/emitter.py (1 call: failed_auth_summary re-emit)
- audit/history.py (3 calls: create, update, delete via post_save/post_delete)
- audit/tasks.py (2 calls: retention sweep)
- billing/license_stripe_mutation.py (1 call: subscription_change)
- billing/models.py (1 call: credit_transaction)
- classrooms/views.py (1 call: course-related)
- students/item_retry.py (1 call: grading_requested)
- students/services.py (1 call: submission_upload)
- students/views.py (4 calls: grading_requested, submission_upload, roster)
- users/auth_audit.py (2 calls: auth_login, auth_logout)
- users/serializers.py (3 calls: permission_change)
- users/views.py (5 calls: account_register, permission changes)

### §1c Where the exception is available

The H-273 requirement is to derive a reason label from the exception type, not its message text. The exception path is:

1. **DRF `custom_exception_handler`** (users/exceptions.py:34): receives `(exc, context)` — the raw exception. This runs BEFORE the response is returned to middleware. It logs the exception type but does NOT emit audit events. It produces the HTTP response.

2. **`AuditMiddleware.__call__`** (audit/middleware.py:42): runs AFTER the response exists. At this point `response` has `.status_code` but the original exception is **not on the response** (DRF's exception_handler returns a Response, not the exception). The exception IS available as `response._raw_exc` only for unhandled 500s (users/exceptions.py:113). For DRF-handled exceptions (4xx), the exception is gone.

**Key insight**: to get the exception type into the audit row, we have two options:
- (A) Emit/enrich from inside `custom_exception_handler`, where the exception is live
- (B) Stash the exception class name on the request/response for the middleware to read

Option (B) is cleaner: `custom_exception_handler` already sees every DRF-handled exception. We stash `type(exc).__name__` (never the message) on the response, and the middleware reads it when composing the audit metadata. For unhandled 500s, it's already on `response._raw_exc`.

**Chosen approach**: Option (B). In `custom_exception_handler`, set `response._audit_exc_class = type(exc).__name__` before returning. The middleware reads it.

### §1d Exception → reason_code mapping

These are the DRF exception types we'll encounter and their reason labels:

| Exception class | Status | Reason label |
|-----------------|--------|-------------|
| `NotFound` / `Http404` | 404 | `NOT_FOUND` |
| `MethodNotAllowed` | 405 | `METHOD_NOT_ALLOWED` |
| `PermissionDenied` | 403 | `PERMISSION_DENIED` |
| `NotAuthenticated` | 401 | `NOT_AUTHENTICATED` |
| `ValidationError` | 400 | `VALIDATION_FAILED` |
| `ParseError` | 400 | `PARSE_ERROR` |
| `UnsupportedMediaType` | 415 | `UNSUPPORTED_MEDIA_TYPE` |
| `NotAcceptable` | 406 | `NOT_ACCEPTABLE` |
| `Throttled` | 429 | (excluded — 429 is never recorded) |
| `AuthenticationFailed` | 401 | `AUTHENTICATION_FAILED` |
| `CodedError` / permanent refusal | varies | (keeps its own reason_code) |
| Unhandled (5xx) | 500 | `SERVER_ERROR` |

Rules:
- A coded refusal (`CodedError`, permanent refusal) keeps its own `reason_code` — we do NOT override it.
- A row that already has a `reason_code` set by the call site keeps it unchanged.
- The reason_code is only SET when no reason_code is already present.

### §1e Validation error field names

For `ValidationError`, extract the sorted list of failing field NAMES only (never messages). DRF's `ValidationError` can have:
- `exc.detail` as a dict `{field_name: [errors]}` → extract sorted keys
- `exc.detail` as a list → non-field errors, `fields = ["non_field_errors"]`
- `exc.detail` as a string → `fields = ["non_field_errors"]`

We store `metadata.fields` as the sorted list of field names.

### §1f Ordering: status and exception into the row

The sequence is:
1. View runs (may raise)
2. `custom_exception_handler` catches exception → produces Response, stashes `_audit_exc_class`
3. Middleware sees the Response
4. If ADMIN_ACTION tagged: `emit_for_response` fires (has `response.status_code` and now `_audit_exc_class`)
5. If no surviving event: `emit_generic_state_change` fires (same)
6. If anonymous with no event: `emit_anonymous_refusal` fires (same)

**No ordering problem.** The exception is available (stashed) by the time the middleware runs.

### §1g target_types in use

From explicit `target_type=` in emit calls:
- `"CustomUser"` (31 uses — auth, permissions, admin)
- `"StudentSubmission"` (16 — grading, grade changes)
- `"Assignment"` (14 — CRUD, copy, grading)
- `"AuditEvent"` (8 — retention sweep, in tests)
- `"Course"` (2 — course operations)
- `"LicenseStripeMutationIntent"` (2 — subscription mutations)
- `"CreditLedger"` (1 — credit transactions)
- `"SchoolCreditAllocation"` (1 — subscription change)

From history.py REGISTRY (derived from model name):
- `"StudentSubmission"` (GRADE_CHANGE)
- `"StudentCourse"` (ROSTER_CHANGE)
- `"CustomUser"` (PERMISSION_CHANGE)
- `"UserSubscription"` (SUBSCRIPTION_CHANGE)
- `"LicenseSubscription"` (SUBSCRIPTION_CHANGE)
- `"SchoolCreditAllocation"` (SUBSCRIPTION_CHANGE)

From dynamic resolution in request_audit._target / admin_action.resolve_target (view class name):
- `"SchoolViewSet"`, `"CustomUserViewSet"`, `"CourseViewSet"`, `"AssignmentViewSet"`, etc.
- Every IsSuperAdmin-gated ViewSet: `AdminCreditManagementViewSet`, `BillingTimeTravelView`, `BillingTransactionViewSet`, `CreditBucketViewSet`, `CreditLedgerViewSet`, `CreditUsageLogViewSet`, `CreditWalletViewSet`, `LicenseOverageOfflineRequestViewSet`, `LicenseSubscriptionViewSet`, `PaymentMethodViewSet`, `SchoolCreditAllocationViewSet`, `SettingsViewSet`, `SubscriptionManagementViewSet`, `SubscriptionPlanViewSet`, `WaitlistViewSet`, `BetaProfileViewSet`, `BetaWhitelistViewSet`, `BetaAnalyticViewSet`, `BetaAnaylicChartViewSet`, `SuperAdminDashboardView`

### §1h target_label mapping

For scope (c), a read-time display field. Covering the production target_types:

| target_type | target_label |
|-------------|--------------|
| `CustomUser` | "User account" |
| `StudentSubmission` | "Student submission" |
| `Assignment` | "Assignment" |
| `AuditEvent` | "Audit event" |
| `Course` | "Course" |
| `LicenseStripeMutationIntent` | "License change intent" |
| `CreditLedger` | "Credit ledger" |
| `SchoolCreditAllocation` | "Credit allocation" |
| `StudentCourse` | "Student enrollment" |
| `UserSubscription` | "User subscription" |
| `LicenseSubscription` | "License subscription" |
| `SchoolViewSet` | "School" |
| `CustomUserViewSet` | "User account" |
| `CourseViewSet` | "Course" |
| `AssignmentViewSet` | "Assignment" |
| `AdminCreditManagementViewSet` | "Credit management" |
| `BillingTimeTravelView` | "Billing time travel" |
| `BillingTransactionViewSet` | "Billing transaction" |
| `CreditBucketViewSet` | "Credit bucket" |
| `CreditLedgerViewSet` | "Credit ledger" |
| `CreditUsageLogViewSet` | "Credit usage log" |
| `CreditWalletViewSet` | "Credit wallet" |
| `LicenseOverageOfflineRequestViewSet` | "Overage request" |
| `LicenseSubscriptionViewSet` | "License subscription" |
| `PaymentMethodViewSet` | "Payment method" |
| `SchoolCreditAllocationViewSet` | "Credit allocation" |
| `SettingsViewSet` | "Settings" |
| `SubscriptionManagementViewSet` | "Subscription management" |
| `SubscriptionPlanViewSet` | "Subscription plan" |
| `WaitlistViewSet` | "Waitlist" |
| `BetaProfileViewSet` | "Beta profile" |
| `BetaWhitelistViewSet` | "Beta whitelist" |
| `BetaAnalyticViewSet` | "Beta analytics" |
| `BetaAnaylicChartViewSet` | "Beta analytics chart" |
| `SuperAdminDashboardView` | "Dashboard" |
| `SessionViewSet` | "Session" |
| `TopicViewSet` | "Topic" |
| `StudentCourseViewSet` | "Student enrollment" |
| `StudentSubmissionViewSet` | "Student submission" |
| `TaskViewSet` | "Task" |
| `CourseCategoryViewSet` | "Course category" |
| `StudentAdminDashboardView` | "Student dashboard" |
| `TeacherAdminDashboardView` | "Teacher dashboard" |
| `SchoolAdminDashboardView` | "School dashboard" |
| `AuthViewSet` | "Authentication" |
| (fallback) | raw target_type value |

### §1i Scope (d): successful rows

Adding method and path to successful rows is safe and useful. `request.path` contains no query string (that's `request.META['QUERY_STRING']`), so it carries only the URL pattern with the pk substituted — which is already visible as the route name + target_id. Adding it provides a human-readable "what endpoint" without needing to reverse-map URL names.

I see **no objection** to adding method and path to successful rows. The path never contains PII in this project (user identifiers are UUIDs in the path, names/emails are never in URLs). I'll add both to all request-scoped audit rows (success and failure alike) rather than splitting the behaviour.

### §1j New metadata keys needed

These keys need to be added to `ALLOWED_KEYS` in audit/metadata.py:
- `http_method` (the HTTP verb: GET, POST, PUT, PATCH, DELETE)
- `path` (request.path, the URL path without query string)
- `reason_label` (the derived short label: NOT_FOUND, VALIDATION_FAILED, etc.)
- `fields` (sorted list of failing field names on ValidationError)

And the per-action `METADATA_ALLOWLIST` entries that carry request context need widening:
- `AuditAction.ADMIN_ACTION`: add `http_method`, `path`, `status_code`, `reason_label`, `fields`
- `AuditAction.STATE_CHANGE`: add `http_method`, `path`, `reason_label`, `fields` (already has `http_status`, `route`, `method`)

Wait — `STATE_CHANGE` already records `method` and `http_status`. And `ADMIN_ACTION` records `source`. I should reuse `method` and `http_status` rather than inventing `http_method` and `status_code`:
- `method` already exists in ALLOWED_KEYS and STATE_CHANGE's allowlist
- `http_status` already exists in ALLOWED_KEYS
- `path` is new → add to ALLOWED_KEYS
- `reason_label` is new → add to ALLOWED_KEYS
- `fields` is new (for validation errors) → add to ALLOWED_KEYS

Updated plan:
- ADMIN_ACTION gets: `method`, `http_status`, `path`, `reason_label`, `fields`
- STATE_CHANGE gets: `path`, `reason_label`, `fields` (already has `method`, `http_status`)
- AUTH_LOGIN gets: `path`, `reason_label`, `fields` (already has `auth_method`, `http_status`)
- ACCOUNT_REGISTER gets: `path`, `reason_label`, `fields` (already has `auth_method`, `http_status`)

### §1k New reason_codes

These are NOT new ReasonCode enum values — they go in `metadata.reason_label`, a metadata field, not the top-level `reason_code` column. This avoids:
1. Widening the closed ReasonCode vocabulary for labels that are informational, not action-driving
2. Conflicting with existing reason_codes on the same row

The existing `reason_code` column stays unchanged. The `reason_label` is metadata-only.

### §1l Query API changes

Scope (b): add `trace_id` and `target_id` filters to both endpoints.

`trace_id`: AuditEvent already has `audit_trace_ix` index on `trace_id` — filter is index-backed.
`target_id`: NO existing index. Need to add one: `Index(fields=["target_id", "-occurred_at"], name="audit_target_time_ix", condition=Q(target_id__isnull=False))`.

Both filters are exact UUID match. Add to `_BaseAuditEventFilter` (shared by both endpoints — school admin already scopes to their school at the queryset level, so adding target_id is safe).

Scope (c): `target_label` as a read-time SerializerMethodField on `AuditEventSerializer`. The mapping dict lives in the serializer module. `target_name` for School only: resolve at read time from School.name via a prefetch or subquery, never stored.

## §2 Implementation plan

### Step 1: Metadata enrichment (scope a + d)

1a. Add `path`, `reason_label`, `fields` to `ALLOWED_KEYS` in audit/metadata.py.
1b. Add `method`, `http_status`, `path`, `reason_label`, `fields` to ADMIN_ACTION's METADATA_ALLOWLIST.
1c. Add `path`, `reason_label`, `fields` to STATE_CHANGE, AUTH_LOGIN, ACCOUNT_REGISTER METADATA_ALLOWLIST entries.
1d. In users/exceptions.py `custom_exception_handler`: stash `response._audit_exc_class = type(exc).__name__` and, for ValidationError, `response._audit_fields = sorted(exc.detail.keys())` (when detail is a dict).
1e. Write a helper `audit/request_detail.py` with:
  - `enrich_request_metadata(request, response, metadata)` → adds method, path, http_status; adds reason_label from `response._audit_exc_class` if present and no reason_code already set; adds fields from `response._audit_fields` if present.
1f. Call `enrich_request_metadata` from:
  - `admin_action.emit_for_response` (line 88)
  - `admin_action.emit_denied` (line 73) — method and path only, no response
  - `request_audit.emit_generic_state_change` (line 214)
  - `request_audit.emit_anonymous_refusal` (line 185)
1g. New ReasonCode enum values: none (reason_label is metadata).

### Step 2: Query API (scope b + c)

2a. Add `trace_id` (UUIDFilter) and `target_id` (UUIDFilter) to `_BaseAuditEventFilter` in audit/filters.py.
2b. Add `target_id` index via migration: `Index(fields=["target_id", "-occurred_at"], name="audit_target_time_ix", condition=Q(target_id__isnull=False))`.
2c. Add `target_label` SerializerMethodField to AuditEventSerializer. Mapping dict in serializer.
2d. Add `target_name` SerializerMethodField — for School target_type only, resolved from the School model at read time, never stored.

### Step 3: Tests

3a. Test `enrich_request_metadata` with each exception type → correct reason_label + fields.
3b. Test ADMIN_ACTION emit includes method, path, http_status, reason_label on 4xx.
3c. Test STATE_CHANGE emit includes path, reason_label on 4xx.
3d. Test anonymous refusal includes path, reason_label.
3e. Test query API: trace_id filter returns exact match. target_id filter returns exact match.
3f. Test target_label: each known target_type maps correctly; unknown falls back to raw.
3g. Test target_name: School resolves to name; non-School is null.
3h. Test existing metadata tests still pass (scrubbing, volume).
3i. Break-run expectations for any edited pre-existing test.
3j. Mutant expectations (see §4).

## §3 Near-run expectations

### Modules touched
audit, users (exceptions.py only), classrooms (no code change but tests may be affected by audit changes).

### Near run: audit + users + classrooms
From DiscoverRunner: Total 1914, Skipped 2, Ran 1912.

**Expected**: Ran 1912 + new tests, 2 skipped, OK.

The exact count of new tests will be declared after step 3 is written.

## §4 Mutant expectations

### M-H273-1: Remove path from enrich_request_metadata
Kill: test that asserts metadata["path"] is present on a 4xx ADMIN_ACTION event.

### M-H273-2: Remove reason_label derivation
Kill: test that asserts reason_label == "NOT_FOUND" for a 404 response.

### M-H273-3: Remove fields extraction for ValidationError
Kill: test that asserts metadata["fields"] == ["field_a", "field_b"] on a validation error.

### M-H273-4: Remove _audit_exc_class stash in custom_exception_handler
Kill: test that asserts reason_label is populated for a DRF-handled 4xx.

### M-H273-5: Remove trace_id filter from query API
Kill: test that filters by trace_id and gets exactly the matching row.

### M-H273-6: Remove target_id filter from query API
Kill: test that filters by target_id and gets exactly the matching row.

### M-H273-7: Remove target_label from serializer
Kill: test that asserts "target_label" in response data.

### M-H273-8: Change target_label fallback to empty string instead of raw value
Kill: test that an unknown target_type returns the raw value as label.

### M-H273-9: Remove target_name resolution for School
Kill: test that target_name is the school's name when target_type is a School-like type.

### M-H273-10: Remove method from ADMIN_ACTION metadata
Kill: test that asserts metadata["method"] is present on ADMIN_ACTION events.

### M-H273-11: Remove http_status from ADMIN_ACTION metadata
Kill: test that asserts metadata["http_status"] is present on ADMIN_ACTION events.

### M-H273-12: Include query string in path (violating the "never query string" rule)
Kill: test that path does NOT contain "?" even when request has a query string.

### M-H273-13: Use exc message text instead of exc class name for reason_label
Kill: test that reason_label is a fixed code (NOT_FOUND, etc.), not a variable string.

### M-H273-14: Remove condition that preserves existing reason_code
Kill: test that a CodedError's reason_code is unchanged (reason_label doesn't override it).

## §5 Red-run plan

Revert by call-site:
1. Revert `enrich_request_metadata` calls in admin_action.py → tests for method/path/reason_label on ADMIN_ACTION fail.
2. Revert `enrich_request_metadata` calls in request_audit.py → tests for path/reason_label on STATE_CHANGE fail.
3. Revert `_audit_exc_class` stash in custom_exception_handler → tests for reason_label fail (it's None).
4. Revert trace_id/target_id filters → query API filter tests fail.
5. Revert target_label/target_name → serializer output tests fail.

## §6 Break-run expectations

Files edited:
- audit/metadata.py (ALLOWED_KEYS, METADATA_ALLOWLIST) — adding keys only, not editing existing
- audit/admin_action.py — enriching metadata dict before passing to emit
- audit/request_audit.py — enriching metadata dict before passing to emit
- audit/serializers.py — adding fields
- audit/filters.py — adding filters
- audit/models.py — adding index
- users/exceptions.py — stashing exc class on response

Pre-existing tests edited: **none expected** at this point — new keys are additive, existing metadata is not removed, existing tests should not assert the absence of new keys. If a test asserts exact metadata dict equality (not subset), it would break. I'll verify by reading the relevant test files before coding.

Existing tests to verify:
- audit/tests_metadata.py: checks allowed keys and shape rules. Adding keys to ALLOWED_KEYS is safe; the test that refuses "person-naming" keys (`test_pii_keys_refused`) runs against a fixed list and won't match `path`, `reason_label`, or `fields`.
- audit/tests_admin_action.py: checks that ADMIN_ACTION events are emitted with correct metadata. If it asserts exact `metadata=={"source": "..."}`, adding new keys would break it.
- audit/tests_state_change.py: checks STATE_CHANGE events.

**Must read**: audit/tests_admin_action.py, audit/tests_state_change.py, audit/tests_query_api.py before coding to confirm no exact-match assertions.

## §7 Checkpoints

1. After survey + NOTES: commit and send sha to SM.
2. After step 1 (metadata enrichment): commit and send sha to SM.
3. After step 2 (query API): commit and send sha to SM.
4. After step 3 (tests + evidence): commit and send sha to SM.
