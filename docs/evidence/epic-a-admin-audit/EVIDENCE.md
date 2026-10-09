# Automatic ADMIN_ACTION audit instrumentation evidence (§6)

Backfilled evidence record. This piece landed as commit `e1c1566` on
`integration/epic-a` before the "verification evidence lives in the repo"
standard's per-piece `EVIDENCE.md` convention was applied retroactively to
already-landed Epic A work; the code itself was not changed to produce
this file. Original worktree (`GAP-epic-a-admin-audit`, branch
`task/epic-a-admin-audit`, off `integration/epic-a`@`148d32c`) and its
isolated test DB were torn down after landing, per the one-worktree-per-task
cleanup convention, so the raw regression log from that run no longer
exists; the counts below are from the session record made at the time, not
a re-run. Re-running the targeted suite against the current tree (below)
independently confirms the same shape.

## 1. What was built

~18 `IsSuperAdmin`-gated endpoints across `billing/`, `dashboard/` and
`users/` share no common base class, so per-endpoint ADMIN_ACTION
instrumentation would silently be missed on a 19th endpoint. Hooked into
`IsSuperAdmin` itself instead — the one thing every one of them already
declares:

- `classrooms/permissions.py::IsSuperAdmin.has_permission()` — on denial,
  emits `ADMIN_ACTION`/`DENIED` immediately (a complete outcome already
  known at that point) via `audit/admin_action.py::emit_denied()`. On
  grant, tags the request with the view instance
  (`setattr(raw_request(request), REQUEST_ATTR, view)`) for the outcome
  that isn't knowable yet.
- `audit/middleware.py::AdminActionAuditMiddleware` — plain Django
  middleware, runs post-response, and if the raw request carries the tag,
  emits `ADMIN_ACTION`/`SUCCESS` or `/FAILURE` from the tag and the final
  status code (`audit/admin_action.py::emit_for_response()`).
- `audit/admin_action.py::resolve_target()` — calls
  `view.get_audit_target(request)` if the view defines it, else falls
  back to the view's class name plus a `pk`/`id` URL kwarg.
- Registered in `AutoGrader/settings.py`'s `MIDDLEWARE`.

### The DRF Request-wrapper gotcha this design works around

DRF's `Request` wrapper proxies attribute *reads* through to the
underlying `HttpRequest` (`__getattr__` delegation) but not attribute
*writes* — `setattr(request, ...)` inside `has_permission()` creates an
instance attribute on the wrapper only, invisible to
`AdminActionAuditMiddleware`, which (as plain Django middleware) only
ever sees the raw `HttpRequest`. `raw_request(request)` (`getattr(request,
"_request", request)`) unwraps to the object both sides actually share.

## 2. Test suite

`audit/tests_admin_action.py`:

- `DummySuccessView`/`DummyFailingView`/`DummyViewWithTarget` — plain
  `APIView` subclasses declaring only `permission_classes =
  [IsSuperAdmin]`, no endpoint-specific instrumentation, run directly
  through `AdminActionAuditMiddleware(view_class.as_view())` via
  `APIRequestFactory` + `force_authenticate` (no URL registration
  needed) — this is the "brand-new 19th endpoint, zero endpoint-specific
  code" proof.
- `AutomaticCoverageTests` — exactly-one-event assertions for: grant +
  200 success, grant + runtime failure (500), permission denial (403),
  unauthenticated denial (401 — DRF's `permission_denied()` raises
  `NotAuthenticated`, not `PermissionDenied`, whenever there's no
  `successful_authenticator`, regardless of what the permission class
  itself returned), and a `get_audit_target`-override case.
- `RealEndpointIntegrationTests` — hits the real, pre-existing
  `super-admin-audit-events` URL through the full `APIClient`/`MIDDLEWARE`
  stack, proving the wiring holds end-to-end and not just against the
  dummy views.

Targeted run at the time: `audit.tests_admin_action` — all tests OK.
Full regression at the time (`--settings=settings_worktree --keepdb
--parallel 4`): 4715 tests, OK, 28 skipped. Combined final regression
(both this piece and the §8 query-API piece landed together): 4732
tests, OK.

## 3. Re-run against the current tree (this backfill)

`python manage.py test --settings=settings_worktree audit.tests_query_api audit.tests_admin_action -v 2`

```text
Ran 16 tests in 1.420s

OK
```

## 4. Conclusion

ADMIN_ACTION coverage is genuinely automatic: it lives in the shared
`IsSuperAdmin` permission class and a middleware, not in any individual
view, so every current and future `IsSuperAdmin`-gated endpoint is
covered without endpoint-specific code — proven directly by the dummy
views in `AutomaticCoverageTests`, which declare nothing beyond
`permission_classes = [IsSuperAdmin]`.

Post-commit sha256 (from `git show e1c1566:<path>`, not the working copy —
pre-commit hooks can rewrite a file after it's written):

```text
f0ae383c922556a5cae21fd9ff200a07c8ddf443ebddc59eaeae6c92111a5386  audit/admin_action.py
da0c138ebc88564f5a9036d9f89d98a743d6cc0d058af7ea2fbbd2f968b6d815  audit/middleware.py
22db2151682a245d26f341efbdbaf0568dc4878a079332158b39be9aba744854  audit/tests_admin_action.py
8fff9139588f91c8be85d4804a9a1f5f5553976db1683ff52e211614c8c41778  classrooms/permissions.py
34837c0931e4fe2bcb055868d7428a9eb2729c84b55c923c2176278996091013  AutoGrader/settings.py
```
