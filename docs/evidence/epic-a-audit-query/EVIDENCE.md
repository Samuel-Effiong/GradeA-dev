# Audit-event query API evidence (§8)

Backfilled evidence record. This piece landed as commit `ff70a16` on
`integration/epic-a` before the "verification evidence lives in the repo"
standard's per-piece `EVIDENCE.md` convention was applied retroactively to
already-landed Epic A work; the code itself was not changed to produce
this file. Original worktree (`GAP-epic-a-audit-query`, branch
`task/epic-a-audit-query`, off `integration/epic-a`@`626cf08`) and its
isolated test DB were torn down after landing, per the one-worktree-per-task
cleanup convention, so the raw regression log from that run no longer
exists; the counts below are from the session record made at the time, not
a re-run. Re-running the targeted suite against the current tree (below)
independently confirms the same shape.

## 1. What was built

Two read-only, paginated, filterable endpoints over `AuditEvent`, per
§8:

- `GET /api/v1/super-admin/audit/events` — unrestricted, `IsSuperAdmin`
  only. Filterable by actor_id, actor_role, action, school_id,
  department_id, time_from, time_to, outcome, reason_code
  (`audit/filters.py::SuperAdminAuditEventFilter`).
- `GET /api/v1/school-admin/audit/events` — same filters minus
  `school_id`, hard-scoped to `request.user.school_id` at the queryset
  level (`audit/views.py::SchoolAdminAuditEventListView.get_queryset()`).
  `school_id` is not a recognized field on this endpoint's FilterSet at
  all (`SchoolAdminAuditEventFilter`), so a client-supplied `school_id`
  param has zero effect rather than being validated and overridden —
  the literal FR-A-09 acceptance criterion.

Both use `StandardPageNumberPagination` (project default) and
`DjangoFilterBackend`. `audit/serializers.py::AuditEventSerializer`
exposes all `AuditEvent` fields, read-only. Registered in `audit/urls.py`,
included from `AutoGrader/urls.py`.

## 2. Test suite

`audit/tests_query_api.py`:

- `SuperAdminAuditEventPermissionTests` — non-super-admins get 403/401,
  a super admin gets 200 and sees events across schools.
- `SchoolAdminAuditEventPermissionTests` — non-school-admins denied, a
  school admin gets 200 scoped to their own school.
- `SchoolAdminCrossTenantAdversarialTests` — the FR-A-09 case, at two
  levels (mirroring the H18/H19 pattern):
  - Queryset-level (unit): calls
    `SchoolAdminAuditEventListView().get_queryset()` directly for a
    School Admin of school A, asserts it returns only school A's events
    even when constructed against a request carrying school B's events
    in the DB.
  - Request/response-level (integration): a School Admin of school A
    hits the live endpoint with `?school_id=<school B's id>` in the query
    string and asserts a 200 with an empty, well-formed page for school
    B's data — never an error, never school B's rows, and the supplied
    `school_id` is silently ignored rather than rejected.

Fixtures (`_TwoSchools`) use real `classrooms.models.School` objects for
the `CustomUser.school` FK, with `AuditEvent` rows created via plain
`.create()` (append-only blocks `.update()`/`.delete()`, not inserts).

Targeted run at the time: `audit.tests_query_api` — all tests OK.
Full regression at the time (`--settings=settings_worktree --keepdb
--parallel 4`): 4698 tests, OK, 28 skipped.

## 3. Re-run against the current tree (this backfill)

`python manage.py test --settings=settings_worktree audit.tests_query_api -v 2`

```text
Ran 16 tests in 1.420s

OK
```

(Run together with `audit.tests_admin_action` in one invocation; see
`docs/evidence/epic-a-admin-audit/EVIDENCE.md` §3 for the same run.)

## 4. Conclusion

Both endpoints match §8's filter surface, and the FR-A-09 school-isolation
requirement is proven at both the unit (queryset) and integration
(request/response) level, with the adversarial cross-tenant case as the
explicit target of both tests. No aggregation; filter combinations hit
the indexes already present on `AuditEvent`.

Post-commit sha256 (from `git show ff70a16:<path>`, not the working copy —
pre-commit hooks can rewrite a file after it's written):

```text
265c4572399c818ca6db9d93f435f0913fd6cab96259af68c5b550514abdee3f  audit/filters.py
002cf5717d6b662b9b52f11ccf198ac6729def3c5881847ebefd3f7aad4d3de2  audit/serializers.py
2b9546518e648e6e6ea7db1831a248ea5d9a775614f3d83786e54bb69dacf924  audit/tests_query_api.py
4b9c8aa246dd3b7d27aa880a04099758b5e67e9d642d76704e99ba79c7cf2103  audit/urls.py
fe602102a6aca138b8ca1f4d7e1667a5918a6b184477ce45f834c4e59cf36ba3  audit/views.py
4269924ac6a0d1370d8a6731e6a957199790073ac97326aca8291b574c26c9b5  AutoGrader/urls.py
```
