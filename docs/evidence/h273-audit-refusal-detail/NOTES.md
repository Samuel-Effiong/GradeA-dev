# H-273 NOTES: Audit Refusal Detail

## §1 Survey

### §1a Emit sites that see a request

Every audit row written for an HTTP request flows through one of these paths:

| Path | File | Line | Action | When | Has exception? | Has status? |
|------|------|------|--------|------|----------------|-------------|
| **ADMIN_ACTION denied** | audit/admin_action.py | :73 `emit_denied` | ADMIN_ACTION | IsSuperAdmin.has_permission returns False | No (permission check, not exception) | No (emitted before view runs; status known: 403) |
| **ADMIN_ACTION response** | audit/admin_action.py | :88 `emit_for_response` | ADMIN_ACTION | Middleware after view completes | Via `response._audit_exc_class` | Yes (`response.status_code`) |
| **STATE_CHANGE generic** | audit/request_audit.py | :214 `emit_generic_state_change` | STATE_CHANGE | Middleware, authenticated state-changing request with no surviving named event | Via `response._audit_exc_class` | Yes (`response.status_code`) |
| **Anonymous refusal** | audit/request_audit.py | :185 `emit_anonymous_refusal` | AUTH_LOGIN / STATE_CHANGE | Middleware, anonymous 4xx/5xx with no surviving event | Via `response._audit_exc_class` | Yes (`response.status_code`) |
| **Named call sites** | Various (see §1b) | — | Various | Explicit call in view/service code | Sometimes (inside except block) | Sometimes |

### §1b Named emit call sites (non-test, non-evidence)

Counted 38 explicit `emit(` calls across:
- assignments/tasks.py (4), assignments/views.py (6)
- audit/admin_action.py (2), audit/request_audit.py (2), audit/emitter.py (1)
- audit/history.py (3), audit/tasks.py (2)
- billing/license_stripe_mutation.py (1), billing/models.py (1)
- classrooms/views.py (1)
- students/item_retry.py (1), students/services.py (1), students/views.py (4)
- users/auth_audit.py (2), users/serializers.py (3), users/views.py (5)

### §1c Where the exception is available

**Approach (B)**: stash on the response from `custom_exception_handler`.

1. `custom_exception_handler` (users/exceptions.py:34) receives `(exc, context)`.
2. Stash `response._audit_exc_class = type(exc).__name__` before returning.
3. For `ValidationError` with a dict detail: stash `response._audit_fields = sorted(exc.detail.keys())`.
4. For `CodedError` / permanent refusal: stash `response._audit_reason_code = exc.reason_code.value` (directive 4).
5. Middleware reads the stashed values when composing metadata.

**Exception class matching** (directive 3): use `isinstance` checks on the actual classes, not name strings, so subclasses map correctly. Include `django.http.Http404` and `django.core.exceptions.PermissionDenied` (DRF converts these, but `exc` in the handler is still the Django class).

### §1d Reason label derivation — three-tier fallback (directive 2)

For every 4xx/5xx row:
1. **(a)** If the row already has a `reason_code` → no label needed (keep it).
2. **(b)** If `_audit_exc_class` is stashed → map via `isinstance` to a label.
3. **(c)** Status-based fallback from a closed set:

| Status | Label |
|--------|-------|
| 400 | `BAD_REQUEST` |
| 401 | `NOT_AUTHENTICATED` |
| 403 | `PERMISSION_DENIED` |
| 404 | `NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | `CONFLICT` |
| 413 | `TOO_LARGE` |
| 415 | `UNSUPPORTED_MEDIA_TYPE` |
| other 4xx | `CLIENT_ERROR` |
| 5xx | `SERVER_ERROR` |

The class → label mapping (tier b):

| Exception class (isinstance) | Label |
|------------------------------|-------|
| `rest_framework.exceptions.NotFound`, `django.http.Http404` | `NOT_FOUND` |
| `rest_framework.exceptions.MethodNotAllowed` | `METHOD_NOT_ALLOWED` |
| `rest_framework.exceptions.PermissionDenied`, `django.core.exceptions.PermissionDenied` | `PERMISSION_DENIED` |
| `rest_framework.exceptions.NotAuthenticated` | `NOT_AUTHENTICATED` |
| `rest_framework.exceptions.AuthenticationFailed` | `AUTHENTICATION_FAILED` |
| `rest_framework.exceptions.ValidationError`, `django.core.exceptions.ValidationError` | `VALIDATION_FAILED` |
| `rest_framework.exceptions.ParseError` | `PARSE_ERROR` |
| `rest_framework.exceptions.UnsupportedMediaType` | `UNSUPPORTED_MEDIA_TYPE` |
| `rest_framework.exceptions.NotAcceptable` | `NOT_ACCEPTABLE` |
| Unknown class | falls through to tier (c) |

The raw class name is **never stored**. The label vocabulary is a closed constant set `REASON_LABELS`, and a test asserts every emitted label is in it.

### §1e Validation error field names

For `ValidationError`, extract sorted list of failing field NAMES only (never messages):
- `exc.detail` as dict `{field: [errors]}` → sorted keys, filtered to `^[A-Za-z_][A-Za-z0-9_]{0,63}$` (SM defect 2: dynamic keys like email addresses are PII), capped at 20
- `exc.detail` as list → `["non_field_errors"]`
- `exc.detail` as string → `["non_field_errors"]`

Stored as `metadata["fields"]`. If the sanitised list was truncated at 20, `metadata["fields_truncated"] = true`.

### §1f Ordering: status and exception into the row

1. View runs (may raise)
2. `custom_exception_handler` catches exception → produces Response, stashes `_audit_exc_class`, `_audit_fields`, `_audit_reason_code`
3. Middleware sees the Response
4. If ADMIN_ACTION tagged: `emit_for_response` fires (enriches metadata)
5. If no surviving event: `emit_generic_state_change` fires (enriches metadata)
6. If anonymous with no event: `emit_anonymous_refusal` fires (enriches metadata)

**No ordering problem.**

### §1f2 Coded refusals (directive 4)

**Verified**: a `CodedError` / permanent refusal is caught by `custom_exception_handler` (users/exceptions.py:45-63). The handler returns a Response with the coded body, but does NOT emit an audit event. If the view itself didn't emit one, the middleware writes a STATE_CHANGE or ADMIN_ACTION row.

**Today**: the middleware-written row carries NO `reason_code` for ADMIN_ACTION (admin_action.py:88 passes none) and NO `reason_code` for STATE_CHANGE (request_audit.py:214 passes none). So a coded refusal's reason_code (e.g. `INSUFFICIENT_CREDITS`, `OVERAGE_CAP_EXCEEDED`) is **lost**.

**Fix**: stash `response._audit_reason_code = exc.reason_code.value` in the exception handler. The middleware passes it as `reason_code` to `emit()`. The emitter already validates it against `ReasonCode.values`, and both ADMIN_ACTION and STATE_CHANGE accept any valid `reason_code` (the emitter's `reason_code` parameter is action-independent). So we write it as `reason_code` (the top-level column), not as `reason_label` in metadata.

When a stashed `_audit_reason_code` is present, `reason_label` is NOT set (tier (a) of the fallback: row already has a reason_code).

### §1f3 emit_denied: exactly one row (directive 5)

**Confirmed**: a denied request produces exactly one ADMIN_ACTION row.

Flow: `IsSuperAdmin.has_permission` returns False → `emit_denied` fires (one row) → `REQUEST_ATTR` is NOT set on the raw_request → middleware's `emit_for_response` does NOT fire (view is None) → `emit_generic_state_change` checks `should_record`: for authenticated denials, the event already survived so `a_surviving_event_names` returns True → no generic row. For anonymous denials: `should_record` requires `is_authenticated` → False → no generic; `emit_anonymous_refusal` sees the surviving denied event → no extra row.

**Result**: exactly one row in all cases (authenticated denial, anonymous denial).

The denied row gets: PERMISSION_DENIED as `reason_label`, 403 as `http_status`, method from request, route from resolver_match.

### §1g target_types in use

From explicit `target_type=` in emit calls:
- `"CustomUser"` (31), `"StudentSubmission"` (16), `"Assignment"` (14), `"AuditEvent"` (8)
- `"Course"` (2), `"LicenseStripeMutationIntent"` (2), `"CreditLedger"` (1), `"SchoolCreditAllocation"` (1)

From history.py REGISTRY (model name after dot):
- `"StudentSubmission"`, `"StudentCourse"`, `"CustomUser"`, `"UserSubscription"`, `"LicenseSubscription"`, `"SchoolCreditAllocation"`

From dynamic resolution (view class name via admin_action.resolve_target / request_audit._target):
- Every IsSuperAdmin-gated ViewSet and every state-changing route's view class.
- SchoolViewSet detail routes carry `target_id = school.pk` via resolve_target reading `kwargs["pk"]` (confirmed: SchoolViewSet is a ModelViewSet using pk-based routing at classrooms/views.py:293).

Missing from original mapping: `"School"` (the model name, which can appear from a future history entry or explicit emit). Added to mapping.

### §1h target_label mapping

| target_type | target_label |
|-------------|--------------|
| `CustomUser` | "User account" |
| `StudentSubmission` | "Student submission" |
| `Assignment` | "Assignment" |
| `AuditEvent` | "Audit event" |
| `Course` | "Course" |
| `School` | "School" |
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
| `AssignmentGenerationSessionViewSet` | "Assignment generation" |
| `View` | "View" |
| (fallback) | raw target_type value |

### §1i Route, not raw path (directive 1)

**Revised**: store `route` = `request.resolver_match.route` (the URL pattern, e.g. `api/v1/sessions$`), NOT `request.path`. The `route` key already exists in ALLOWED_KEYS and STATE_CHANGE's allowlist.

If `resolver_match` is None (404 on an unknown URL): `route = None`, `reason_label = NOT_FOUND`.

STATE_CHANGE already records `route` as `match.view_name` (the URL name, e.g. "session-list"). The new field reuses the same key name but in ADMIN_ACTION we record the URL pattern via `resolver_match.route`. For STATE_CHANGE, the existing `route` value (view_name) is kept; we add the URL pattern as well if useful, but since STATE_CHANGE already has view_name, and ADMIN_ACTION had nothing, the primary addition is to ADMIN_ACTION.

**Decision**: for ADMIN_ACTION, add `route` = `resolver_match.route` (URL pattern). For STATE_CHANGE, the existing `route` = view_name stays. Both already have `route` in the allowlist.

**Frontend/API doc note (SM review)**: `metadata.route` means the URL *pattern* (`api/v1/schools/(?P<pk>[^/.]+)$`) on ADMIN_ACTION rows and the URL *name* (`school-detail`) on STATE_CHANGE rows. Consumers must not assume one format; the action column disambiguates.

### §1j New metadata keys needed (revised)

Keys to add to `ALLOWED_KEYS`:
- `reason_label` (the derived short label from the closed vocabulary)
- `fields` (sorted list of failing field names on ValidationError)

Keys already in `ALLOWED_KEYS` that we reuse:
- `method` (HTTP verb)
- `http_status` (response status code)
- `route` (URL name or pattern)
- `source` (already in ADMIN_ACTION)

Per-action allowlist widening:
- `AuditAction.ADMIN_ACTION`: add `method`, `http_status`, `route`, `reason_label`, `fields`
- `AuditAction.STATE_CHANGE`: add `reason_label`, `fields` (already has `method`, `http_status`, `route`)
- `AuditAction.AUTH_LOGIN`: add `reason_label`, `fields` (already has `auth_method`, `http_status`)
- `AuditAction.ACCOUNT_REGISTER`: add `reason_label`, `fields` (already has `auth_method`, `http_status`)

### §1k Coded refusal reason_code flow (directive 4 resolution)

Coded refusals' `reason_code` is written as the top-level `reason_code` column (not as `reason_label` in metadata). The emitter accepts any valid `ReasonCode` on any action. Stashed as `response._audit_reason_code`. When present, the three-tier fallback stops at tier (a) — no `reason_label` is set.

### §1l Query API changes

**trace_id filter**: already indexed (`audit_trace_ix`). Exact UUID match.
**target_id filter**: needs new index `audit_target_time_ix`. Built concurrently (directive 6): `AddIndexConcurrently`, `atomic = False`, precedent billing/migrations/0051.

Both filters: exact UUID, added to `_BaseAuditEventFilter` (shared by both endpoints). School admin queryset scoping still applies.

**target_label**: SerializerMethodField, mapping dict in serializers.py, fallback to raw value.
**target_name**: SerializerMethodField, School only. Resolved with ONE query per page (id→name dict built in serializer context via `get_serializer_context`), never one per row (directive 7). School admin sees only their own school's rows (queryset guarantee); test added anyway.

### §1m SchoolViewSet target_id verification (directive 7)

Confirmed: `SchoolViewSet` (classrooms/views.py:293) is a `ModelViewSet` with `IsSuperAdmin` permissions. Detail routes use `pk` kwarg. `admin_action.resolve_target` reads `kwargs.get("pk")` → `target_id = school.pk`. List/create routes have no pk → `target_id = None`.

### §1n API schema (directive 9)

The audit endpoints use drf-spectacular annotations (audit/views.py:38-53, :56-66, :75-86). New fields for the frontend handoff:

**New query parameters** (both endpoints):
- `trace_id` (UUID, exact match) — filter by server trace id
- `target_id` (UUID, exact match) — filter by target object id

**New response fields** (AuditEventSerializer):
- `target_label` (string) — human-readable label for target_type, e.g. "School", "User account"
- `target_name` (string, nullable) — resolved name for School targets only; null for all others

The `metadata` object on 4xx/5xx rows now additionally contains:
- `method` (string) — HTTP method (on ADMIN_ACTION rows; STATE_CHANGE already had it)
- `http_status` (integer) — response status code (on ADMIN_ACTION rows; STATE_CHANGE already had it)
- `route` (string) — URL pattern (on ADMIN_ACTION rows; STATE_CHANGE already had view_name as `route`)
- `reason_label` (string) — short reason label from closed vocabulary (NOT_FOUND, VALIDATION_FAILED, etc.)
- `fields` (list of strings) — failing field names on ValidationError responses

The top-level `reason_code` column now carries coded refusal codes (e.g. INSUFFICIENT_CREDITS) on ADMIN_ACTION and STATE_CHANGE rows where the view raised a CodedError.

## §2 Implementation plan

### Step 1: Metadata enrichment + exception stashing (scope a + d)

1a. Add `reason_label`, `fields` to `ALLOWED_KEYS` in audit/metadata.py.
1b. Widen ADMIN_ACTION's METADATA_ALLOWLIST: add `method`, `http_status`, `route`, `reason_label`, `fields`.
1c. Widen STATE_CHANGE's METADATA_ALLOWLIST: add `reason_label`, `fields`.
1d. Widen AUTH_LOGIN and ACCOUNT_REGISTER: add `reason_label`, `fields`.
1e. In users/exceptions.py `custom_exception_handler`:
  - Stash `response._audit_exc_class = type(exc).__name__` and the exception object ref `response._audit_exc = exc`
  - For ValidationError with dict detail: stash `response._audit_fields = sorted(exc.detail.keys())`
  - For CodedError / permanent refusal: stash `response._audit_reason_code = exc.reason_code.value`
  - For unhandled 500: already has `response._raw_exc`
1f. Write `audit/request_detail.py` with:
  - `REASON_LABELS` closed constant set
  - `_exc_to_label(exc)` using isinstance matching
  - `_status_to_label(status_code)` for tier (c)
  - `enrich_request_metadata(request, response, metadata)` → adds method, http_status, route; derives reason_label; adds fields
  - `stashed_reason_code(response)` → returns the stashed reason_code or None
1g. Call from:
  - `admin_action.emit_for_response` — full enrichment + pass reason_code
  - `admin_action.emit_denied` — method, route, http_status=403, reason_label=PERMISSION_DENIED (no response needed)
  - `request_audit.emit_generic_state_change` — full enrichment + pass reason_code
  - `request_audit.emit_anonymous_refusal` — full enrichment (reason_code already handled by its own logic)

### Step 2: Query API + serializer (scope b + c)

2a. Add `trace_id` (UUIDFilter) and `target_id` (UUIDFilter) to `_BaseAuditEventFilter`.
2b. Migration: `AddIndexConcurrently` for `audit_target_time_ix` on `(target_id, -occurred_at)` where target_id IS NOT NULL. `atomic = False`. Precedent: billing/migrations/0051.
2c. Add `target_label` SerializerMethodField. Mapping dict in audit/serializers.py.
2d. Add `target_name` SerializerMethodField. School-only resolution with ONE query per page via `get_serializer_context` building `{school_id: name}` dict. `assertNumQueries` test.
2e. Update drf-spectacular `OpenApiParameter` annotations on both views for `trace_id` and `target_id`.

### Step 3: Tests

See §4 for mutant targets.

## §3 Near-run expectations

### Modules in near run (directive 10)
audit, users, classrooms, billing, students, assignments, AutoGrader (7 apps).

### Baseline (from DiscoverRunner id lists)
7-app total: 6690 at f5d2d577. Tip identical (zero diff).

**Expected**: Ran 6690 + new tests, ~20 skipped, OK.

Exact new test count declared after tests are written.

(Superseded: original 6-app baseline was 5832; see baseline_id_list.log for history.)

### Pre-existing tests with exact metadata assertions that WILL BREAK

Read before coding (directive 10):

1. **audit/tests_admin_action.py:107** — `assertEqual(event.metadata, {"source": "DummySuccessView"})`. ADMIN_ACTION success on a DummyView run through `_run()` (APIRequestFactory, not full middleware). **WILL BREAK** when `emit_for_response` enriches metadata with method, http_status, route. Must update to superset assertion or exact new dict.

2. **audit/tests_state_change.py:84-87** — `assertEqual(event.metadata, {"route": "session-list", "method": "POST", "http_status": 201})`. STATE_CHANGE success. **WILL BREAK** when enrichment adds reason_label (no — success doesn't get reason_label). Actually re-reading: enrichment only adds reason_label on 4xx/5xx. On success, method/http_status/route are already there. So this **may survive** if we only add keys on failure. But we DO add route to all ADMIN_ACTION rows. Let me re-check: this is STATE_CHANGE, not ADMIN_ACTION. STATE_CHANGE already has route/method/http_status. We only add reason_label/fields on failure. **SURVIVES** on success paths.

3. **audit/tests_route_coverage.py:313** — `assertEqual(event.metadata, {"auth_method": "self_registration"})`. ACCOUNT_REGISTER success via real middleware. Anonymous refusal path already adds `http_status`. Wait — this is a SUCCESS registration. The anonymous refusal path (emit_anonymous_refusal) only fires for 4xx/5xx. This SUCCESS goes through the named auth event in users/views.py, which calls emit directly with its own metadata. **SURVIVES** (named event, not middleware-enriched).

4. **audit/tests_admin_action.py:107** (re-analysis): `_run()` calls `middleware(request)` where middleware = `AdminActionAuditMiddleware(view_class.as_view())`. This IS the middleware. The view returns 200, so `emit_for_response` fires. Currently metadata is `{"source": "DummySuccessView"}`. After enrichment, it will have `method`, `http_status`, `route` added. **WILL BREAK**.

### Edited pre-existing tests

1. `audit/tests_admin_action.py` — update exact metadata assertion at :107 to include new keys.

### Break-run plan

After editing tests_admin_action.py:107, run that single test class:
```
python manage.py test audit.tests_admin_action.AutomaticCoverageTests.test_new_view_granted_produces_exactly_one_success_event
```
Expected: pass with new metadata dict.

## §4 Mutant expectations

### M-H273-1: Remove route from ADMIN_ACTION enrichment
Kill: test asserts metadata["route"] present on ADMIN_ACTION event.

### M-H273-2: Remove reason_label derivation (all tiers)
Kill: test asserts reason_label == "NOT_FOUND" for a 404 response.

### M-H273-3: Remove fields extraction for ValidationError
Kill: test asserts metadata["fields"] == ["field_a", "field_b"] on validation error.

### M-H273-4: Remove _audit_exc_class stash in custom_exception_handler
Kill: test asserts reason_label is populated for a DRF-handled 4xx.

### M-H273-5: Remove trace_id filter from query API
Kill: test filters by trace_id, gets exactly the matching row.

### M-H273-6: Remove target_id filter from query API
Kill: test filters by target_id, gets exactly the matching row.

### M-H273-7: Remove target_label from serializer
Kill: test asserts "target_label" in response data.

### M-H273-8: Change target_label fallback to empty string instead of raw value
Kill: test that an unknown target_type returns the raw value as label.

### M-H273-9: Remove target_name resolution for School
Kill: test that target_name is the school's name when target_type is "SchoolViewSet".

### M-H273-10: Remove method from ADMIN_ACTION metadata enrichment
Kill: test asserts metadata["method"] present on ADMIN_ACTION events.

### M-H273-11: Remove http_status from ADMIN_ACTION metadata enrichment
Kill: test asserts metadata["http_status"] present on ADMIN_ACTION events.

### M-H273-12: Store request.path instead of resolver_match.route
Kill: test uses a route with a UUID segment, asserts the literal UUID value never appears in metadata["route"] (the pattern has `<uuid:pk>` or `(?P<pk>...)`, not the actual id).

### M-H273-13: Use exc message text instead of exc class name for reason_label
Kill: test asserts reason_label is a fixed code from REASON_LABELS (e.g. "NOT_FOUND"), not a variable string.

### M-H273-14: Remove stashed coded refusal reason_code passthrough
Kill: test that a CodedError's reason_code appears on the STATE_CHANGE/ADMIN_ACTION row.

### M-H273-15: Remove status-based fallback (tier c)
Kill: test that a view returning 4xx directly (no exception handler path) still gets a reason_label from status.

### M-H273-16: Label coverage — remove one target_type from the mapping
Kill: label coverage test enumerates all producible target_types and asserts each has an explicit label (directive 8).

### M-H273-17: emit_denied missing PERMISSION_DENIED label or 403 status
Kill: test that a denied ADMIN_ACTION row has reason_label=PERMISSION_DENIED and http_status=403.

### M-H273-18: target_name query count — use per-row query instead of batch
Kill: assertNumQueries test (directive 7).

### M-H273-19: Restore exc.reason_code.value in coded branch (SM defect 1)
Kill: a view raising InsufficientCreditsError must not 500 and must write audit row with reason_code INSUFFICIENT_CREDITS.

### M-H273-20: Remove field-name sanitisation regex (SM defect 2)
Kill: a ValidationError keyed by an email address must not store the email anywhere in the audit row.

### M-H273-21: generate_request_id back to .hex
Kill: byte-identical test (X-Request-ID header = body reference = audit trace_id, all dashed 8-4-4-4-12).

### M-H273-22: item_results reference back to .hex
Kill: per-item reference = audit trace_id (both dashed).

### M-H273-23: drop dash-tolerant normalisation in trace_id filter
Kill: filter test with undashed input returns the expected row.

## §4b Dashed-reference addendum (SM revised addendum 2026-10-30)

Owner wants the DASHED UUID form (8-4-4-4-12) as the canonical reference format
everywhere. `str(uuid.uuid4())` produces this form.

### Code changes

**1. `AutoGrader/request_context.py:63`**
```python
# Before
return uuid.uuid4().hex
# After
return str(uuid.uuid4())
```
Comment on line 41 ("bare uuid4 hex (32 lowercase hex chars, no dashes)") must be
updated to reflect dashed form.

**2. `students/item_results.py:66`**
```python
# Before
"reference": task.trace_id.hex if task.trace_id else None,
# After
"reference": str(task.trace_id) if task.trace_id else None,
```
Comment on lines 63-65 ("server id as hex") must be updated.

**3. `audit/filters.py` — trace_id filter**
`trace_id` filter must accept dashed or undashed, any case, trimmed.
Garbage → 400. Implementation: custom `CharFilter` with a `method` that
normalises the input through `uuid.UUID(value.strip())` and filters by
the resulting UUID. django_filters.UUIDFilter alone rejects undashed input.

**4. `_VALID_INBOUND_ID_RE` (request_context.py:49)**
Already accepts dashes (`[A-Za-z0-9._-]{1,128}`). No change needed.

### Repo grep: .hex / undashed formatting of request_id, trace_id, reference

Sites using `.hex` on UUIDs that represent request_id/trace_id/reference:

| File | Line | Usage | Action |
|------|------|-------|--------|
| `AutoGrader/request_context.py` | 63 | `uuid.uuid4().hex` | **CHANGE** to `str(uuid.uuid4())` |
| `students/item_results.py` | 66 | `task.trace_id.hex` | **CHANGE** to `str(task.trace_id)` |
| `audit/tests_trace_server_owned.py` | 66,70,75,81,109 | `victim.trace_id.hex`, `event.trace_id.hex` | **BREAK-RUN EDIT**: change to `str(...)` |
| `audit/tests_emitter.py` | 279,291,320,359,367 | `.hex` in test setup | **BREAK-RUN EDIT**: change to `str(...)` |
| `students/tests_batch_item_results.py` | 277 | `item.trace_id.hex` | **BREAK-RUN EDIT**: change to `str(...)` |
| `AutoGrader/tests_request_context.py` | 18-19 | `assertEqual(len(value), 32)` + `^[0-9a-f]{32}$` | **BREAK-RUN EDIT**: change to dashed UUID pattern |
| `AutoGrader/tests_middleware.py` | 55 | `uuid.UUID(client_id).hex` | **BREAK-RUN EDIT**: change to `str(uuid.UUID(client_id))` |
| `classrooms/tests_h71_student_add_role.py` | 43 | `REFERENCE = re.compile(rb'"reference":"[0-9a-f]{32}"')` | **BREAK-RUN EDIT**: change to dashed UUID regex |
| `audit/tests_failed_auth_cap.py` | 363 | `.hex[:8]` for email generation | **NO CHANGE** (not a reference/trace_id) |
| `docs/evidence/epic-a-s7a/mutate.py` | 52 | `.hex` in evidence script | **NO CHANGE** (historical evidence) |

Sites using `.hex` for non-reference purposes (email generation, run IDs, queue names,
passwords, stripe IDs, tags): **NO CHANGE** — these use `.hex[:N]` for short unique
strings, not for trace/reference formatting.

### Regex/length checks assuming 32 chars

| File | Line | Pattern | Action |
|------|------|---------|--------|
| `AutoGrader/tests_request_context.py` | 18-19 | `len(value), 32` + `^[0-9a-f]{32}$` | **BREAK-RUN EDIT**: `len(value), 36` + `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` |
| `classrooms/tests_h71_student_add_role.py` | 43 | `[0-9a-f]{32}` | **BREAK-RUN EDIT**: `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}` |
| `_VALID_INBOUND_ID_RE` | 49 | `[A-Za-z0-9._-]{1,128}` | **NO CHANGE** — already accepts dashes |

### Celery signals propagation

`AutoGrader/celery_signals.py` propagates `request_id` as a string through Celery
headers. `_extract_request_id()` reads it as a string. `_stamp_request_id_on_publish()`
stamps it as a string. No `.hex` or length check. **NO CHANGE** needed — dashed form
passes through transparently.

### Sentry tagging

`AutoGrader/middleware.py:64`: `sentry_sdk.set_tag("request_id", request_id)` — tags
with the string as-is. `celery_signals.py:101`: same. **NO CHANGE** — Sentry accepts
any string tag. Dashed form works. Sentry queries using the old undashed form will stop
matching after the change (noted in frontend-facing NOTES below).

### Log filters

`RequestIDLogFilter` injects `record.request_id = get_request_id() or "-"`. No format
check. **NO CHANGE** — dashed form logged as-is.

### Frontend-facing NOTES

- References returned by the API are now in dashed UUID form (8-4-4-4-12).
- The trace_id search box should accept whatever the user pastes (dashed or undashed).
- Sentry search for `request_id` tag must use the new dashed form for new events.
  Old events retain their undashed tags.

## §5 Red-run plan

Revert by call-site:
1. Revert enrichment calls in admin_action.py → tests for method/route/http_status/reason_label on ADMIN_ACTION fail.
2. Revert enrichment calls in request_audit.py → tests for reason_label on STATE_CHANGE fail.
3. Revert _audit_exc_class stash in custom_exception_handler → tests for reason_label via exception mapping fail.
4. Revert trace_id/target_id filters in filters.py → query API filter tests fail.
5. Revert target_label/target_name in serializers.py → serializer output tests fail.

## §6 Break-run expectations

### Files edited

Production code:
- audit/metadata.py — adding keys to ALLOWED_KEYS and METADATA_ALLOWLIST (additive)
- audit/request_detail.py — NEW file (enrichment helper)
- audit/admin_action.py — enriching metadata before emit, adding reason_code passthrough
- audit/request_audit.py — enriching metadata before emit, adding reason_code passthrough
- audit/serializers.py — adding SerializerMethodFields
- audit/filters.py — adding UUIDFilters
- audit/models.py — adding target_id index
- audit/views.py — updating OpenApiParameter annotations
- users/exceptions.py — stashing exc class, fields, reason_code on response

### Pre-existing tests edited
1. `audit/tests_admin_action.py:107` — exact metadata dict updated to include new keys.

### Break-run
Run: `audit.tests_admin_action.AutomaticCoverageTests.test_new_view_granted_produces_exactly_one_success_event`
Expected: PASS with updated assertion.

## §7 Checkpoints

1. After survey + NOTES: commit and send sha to SM. ✓ (c2826baf)
2. After NOTES amendment (this): commit and send sha to SM.
3. After step 1 (metadata enrichment + stashing): commit and send sha to SM.
4. After step 2 (query API + serializer + migration): commit and send sha to SM.
5. After step 3 (tests + expectations + evidence): commit and send sha to SM.
