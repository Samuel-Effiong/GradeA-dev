# F1 Step 0: Departments, membership, per-member edit flag, audit, `cachegen:dpt`, adversarial matrix (plan only)

**OWNER ANSWERS FOLDED IN (draft 4, 2026-11-19): Q1 changed (departments keep working through a license lapse; §5.2), Q2 changed (one edit switch; §3.3), Q8 changed (teachers get an email; §9b), estimate now 15 days; §15k lists every change. Earlier text in §15 that argues for the old Q1 or two flags is history, marked superseded.**

**FINAL (draft 3, consolidated after the Fixer's final check at `97153680c`, which agreed with all six remaining points and added R1-R3, now folded in). This is the one plan; §15 is kept as the history of how disputes were settled.** Lead planner draft 2 (draft 1 was `3ffdd2482`; draft 2 folds in both independent documents: the review at `9122caf2d` and the direct challenge at `08be30681`; §15 says what changed and why). SM brief 2026-11-19. Branch `task/epic-f1` from staging `88f33b474`. **Docs only: no product code, no migrations, nothing pushed.**

Sources read in full: Backend Requirements PDF (all of it: personas, §5 constraints, A1-A8, Epics A-I, Deferred, Deliverable), 01a (§1, Epics A, F, G, NFR-SEC/PRF/TST, decisions D-A*/D-0*), 03a (§2.9-2.11, §4, §6-8), 03 (§2.5, Stages 4-5, R-5), 05 (§3.4, §4, §5), 10 Gates. Code read: `classrooms/models.py` (School, H-38 helpers), `billing/license_service.py` (`remove_teacher_from_license`, `add_teachers_batch`, cancel and renewal paths), `billing/license_views.py`, `users/models.py` + `users/signals.py`, `audit/{enums,metadata,models,emitter,history,views}.py` and the route-coverage and history guards, `AutoGrader/cache_generation.py`, `tags/` (C1), `classrooms/permissions.py`, and E2 on `task/epic-e2` (STEP0_PLAN, migration names). Research is in §14.

Citations: **BE-x** = Backend Requirements PDF (binds); **FR/NFR** = 01a; **03a/03/05** = architecture docs. Where they differ, §13 says so and says which binds (rule from 01a D-03: the Backend Requirements PDF is the binding document).

---

## 0. Findings that change the plan (read these first)

1. **The tenancy root is `School`, not a license row.** `CustomUser.school` (nullable FK, `SET_NULL`) is what every queryset scopes by. `LicenseSubscription` has a `school` FK and a school can hold several license rows over time (a replaced license is set inactive). 03a open item 2 is answered: **`Department.school → classrooms.School`**. The spec's "License id" (BE-A-02, 03 AuditEvent) is already `school_id` in the code (`audit/models.py`), and `department_id` and its index (`audit_dept_time_ix`) already exist. Epic A did this part.
2. **`teacher.school` is the only thing that says "this teacher is on this license", and it is cleared in exactly one place on removal** (`remove_teacher_from_license`, H-38, only when `teacher.school_id == license.school_id`). It is **not** cleared when a license lapses, is cancelled (OFFLINE sets `is_active=False` and leaves `school` alone), or fails renewal. It can also change in Django admin (tracked as PERMISSION_CHANGE `school_id`) or by deleting a school (`SET_NULL`). So "leaves the License" (BE-F-12) is **at least five different events**, not one. A design that reaps memberships only inside `remove_teachers` is not enough (§6).
3. **Revival hazard.** If a stale membership row survives a removal, then **re-adding the teacher to the school later silently re-grants the old department access** (the new allocation makes the liveness test true again) with no admin action and no audit event. This is the most dangerous quiet failure in F1. It drives D4 and D8: liveness is checked at read time **and** rows are reaped.
3a. (Superseded by owner Q1, §5.2, §15k.) **(Draft 2) The code has two definitions of "on a license" and they differ on a lapse.** Courses use `school_id` (H-38: `teacher_course_access_q`, so a teacher on a lapsed license still reads and writes their own courses). Sessions, session write rights and credit wording use `is_under_license()` (an active allocation on an **active** license: `SessionViewSet.get_queryset`, `CanManageSession`, `billing/errors.py`), so a lapsed teacher is treated as individual-track there. BE-G-08 says Individual Teachers have **no** Department. Departments are a shared resource of the license, so they follow the second definition (§5.2).
4. **`add_teachers_batch` keeps an existing `school` and only sets it when empty** (`if not user.school`). A teacher already on another school is refused earlier (`user.school_id != school.id` branch). Cross-school membership is therefore possible only if our own add path is wrong; that is where P0 tests go.
5. **The audit enum already has `DEPARTMENT_CREATE/UPDATE/DELETE/MEMBER_ADD/MEMBER_REMOVE`, `LIBRARY_*` and `PERMISSION_CHANGE`, but every DEPARTMENT_* metadata allow-list is `frozenset()`** (`audit/metadata.py:360-364`) and `PERMISSION_CHANGE`'s before/after allow-list only knows user fields. Any metadata we send today is dropped and logged as an error. F1 must extend both allow-lists, and the S4 registry/route guards will tell us if we forget.
6. **The wildcard-invalidation question (05 Q5, R-5) is already moot.** `cache_generation.py` states the wildcards were removed in H-1 step 4 and `tests_no_wildcard_invalidation.py` guards it. `cachegen:dpt` is a new scope in an existing, tested pattern, not a sequencing risk. 05 still lists it as open; the SM should close it.
7. **FR-F-10 is unusually testable here** because F1 creates *no* path to courses, rosters, submissions, grades or credits. The risk is **widening an existing rule by accident** (for example a future "teachers who share a department" shortcut in `teacher_course_access_q`). So the strongest F1 test is a **membership-invariance differential** over every readable route (D0 in §7.2), not only tests on the new endpoints.
8. **Two requirements (FR-G-03/04, FR-F-07's enforcement) need content that does not exist until D and F2.** F1 can ship the flag, the helper that answers "may this member edit lessons/library", the registry that forces future content models into the matrix, and tests of the helper. It cannot test "restricted write against a lesson API". §1 draws the line.

---

## 1. F1 / F2 / D / G scope line (where requirements straddle)

| Requirement | In F1? | Notes |
|---|---|---|
| FR-F-01 Department entity, persists across Sessions | **Yes** | No `session` column; schema test |
| FR-F-02 admin-only create/rename/delete/membership; members from the license's teachers | **Yes** | BE-F-02 |
| FR-F-03 zero/one/many departments; placement admin-only | **Yes** | BE-F-03 |
| FR-F-07 per-member flag | **Split.** F1: column(s), admin endpoint to set, audit with before/after, `can_member_edit()` helper, helper tests. **D / F2: the denial on the real lesson and library APIs** | BE-F-07, A5 |
| FR-F-10 "nothing else" | **Yes** (new endpoints) + differential over existing routes | BE-F-10 |
| FR-F-11 audit | **Yes** | BE-F-11 |
| FR-F-12 removal/leave behavior | **Split.** F1: membership ends cleanly, access ends at once, delete service and the explicit delete-or-reassign choice with a content registry. **D/F2/C2: their content registers itself** | BE-F-12 |
| FR-F-04/05/06 Department Lessons and attribution | No (D) | |
| FR-F-08/09, FR-G-04/05/06 Shared Library, copy | No (F2, needs E2 copy) | |
| FR-F-13 search/filter | No (D/F2) | |
| FR-G-01 my departments; FR-G-02 roster (name and role); FR-G-07 empty list; FR-G-08 individual teacher denied | **Recommended yes, department-only part** | 05 sequences G after F. These four need nothing but F1's tables, and FR-F-10's *serializer-level* test is about the roster serializer, so building it here puts the riskiest serializer under F1's matrix. **Option for SM: defer to G.** See D9 |
| FR-G-03 | No (D/G) | |
| FR-A-01/02/09 department audit fields | **Mostly already done** (A). F1: allow-lists + guard coverage | |
| NFR-PRF-06 `cachegen:dpt` | **Yes** | |
| NFR-TST-02, NFR-TST-08 | **Yes** (matrix; multi-tenant seed fixture, first deliverable) | |
| FR-C-02 department-scoped tags | No (C2). F1 exports the one function C2 must call | §11 |

---

## 2. Requirement to design map

| Req | Source | Design |
|---|---|---|
| FR-F-01 | BE-F-01 | `Department(school, name, name_key, description, created_by_id, timestamps)`, no `session` field; test `test_no_session_column` plus "new Session changes nothing" |
| FR-F-02 | BE-F-02 | Admin viewset, role class `IsSchoolAdmin` plus a school check; members validated by `validate_member_candidate()` (§4.2) |
| FR-F-03 | BE-F-03 | `DepartmentMembership` UNIQUE(department, teacher); many rows per teacher; no teacher-callable write route |
| FR-F-07 | BE-F-07, A5 | Flag columns on the membership; PATCH member; `can_member_edit()` |
| FR-F-10 | BE-F-10, §5.2 | `departments/access.py` single rule + sweep guard + allow-list serializers + invariance differential (§5, §7) |
| FR-F-11 | BE-F-11 | Emit in the service layer, inside the write transaction (§9) |
| FR-F-12 | BE-F-12 | Membership ends by delete; content is owned by the Department so it stays; delete service with explicit choice (§6, §8) |
| FR-G-01/02/07/08 | BE-G-01/02/07/08 | Read views in F1 (option) (§4.3) |
| NFR-SEC-01..06, 09 | | §5, §7, and the task rule: no task reads "all" (F1 adds no task) |
| NFR-PRF-06 | | §10 |
| NFR-TST-02/08 | | §7 and §12 step 1 |
| NFR-ACC-02 | | 404 means "not present", 403 with a code means "not permitted"; a member sees their own flags (§4.3) |

---

## 3. Data model

### 3.1 Where the code lives (design choice D1)

| Option | Trade-off |
|---|---|
| **A. `classrooms` app (03a, 05 say so)** | Matches the docs. `classrooms/models.py` is also extended by E2 (migration `classrooms/0019_grade_categories` is on `task/epic-e2`); F1 would be `0020` and must wait for E2 to land or be rebased. `classrooms` is already a very large module |
| **B. New app `departments` (recommended)** | No migration-number race with E2 (`departments/0001` depends on `classrooms` 0018, `users` latest). All of the authorization code, the matrix and the guard live in one place the sweep guard can police. Costs: a new app entry in INSTALLED_APPS, URLs, the swagger-group test and the standing guard lists; the docs say `classrooms` so 03a gets a one-line note |
| C. Models in `classrooms`, code in `departments` | Splits the boundary; worst of both |

**Recommendation: B.** The roadmap's own warning ("both extend `classrooms/models.py`; sequence migrations", 05 §5) disappears. This is a technical choice for the SM, not the owner.

### 3.2 `Department`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `school` | FK `classrooms.School`, `CASCADE`, not null, indexed | Tenancy root (finding 1) |
| `name` | Char(100) | As typed, trimmed |
| `name_key` | Char(100) | Lower-cased, whitespace collapsed (same rule family as `tags.normalize`, BE-C-08 style) |
| `description` | Text, blank | Optional; spec silent (gap G-21) |
| `created_by_id` | UUID | Captured value, not FK (an admin's account may go; 03a) |
| `created_at`, `updated_at` | datetime | |

Constraints: `UNIQUE(school, name_key)`; `CHECK name_key <> ''`. Index `(school, name_key)` is the unique's. **No `session` field (FR-F-01).**

Name rule choices (D2): exact (`UNIQUE(school, name)`: "Math" and "math " both allowed, confusing), normalized key (**recommended**, matches C1 behavior, one extra column), or case-insensitive functional index (works, but the error path needs the same normalization in code anyway). **Recommend normalized key.**

### 3.3 `DepartmentMembership`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `department` | FK `CASCADE`, indexed | |
| `teacher` | FK `users.CustomUser`, `CASCADE` | Teacher deleted, row goes (H-298 delete path stays valid) |
| `can_edit` | Bool, default True | One per-member edit switch covering department lessons and the shared library (owner Q2; BE-F-07, A5 "a per-member permission flag"). BE-F-07 default: may edit |
| `added_by_id` | UUID | Captured |
| `created_at`, `updated_at` | datetime | |

Constraints: `UNIQUE(department, teacher)`. Index `(teacher)` for "my departments".

**One flag or two (D3, settled by the owner's Q2: ONE):** the owner chose a single `can_edit` switch, matching the singular "per-member permission flag" in A5 and BE-F-07. The alternatives (two booleans per 03a, or permission-code rows) are not built. `can_member_edit(user, dept, surface)` keeps its `surface` argument ("lessons" or "library") so a later split is a data change in one function rather than a new call-site contract; both surfaces read `can_edit` today. It must not hard-code the admin role (F2 carry-over, §3.5).

### 3.4 Migrations (additive, no data move)

- `departments/0001_initial`: both tables, constraints, indexes. Depends on `classrooms.0018` (latest on staging) and the latest `users` migration. **No dependency on E2**, so it can be built in parallel and merged in either order.
- If SM chooses option A for D1: number is `classrooms/0020` after E2's `0019`.
- `audit` needs **no schema migration** (the columns exist). `metadata.py` allow-list changes are code only.
- No backfill: departments are new. Rollback: drop the two tables; nothing else references them in F1.

### 3.5 What F1 deliberately does not add

**F2 carry-over list (do not forget):** (1) add `Department.admin_can_add_to_library` (BE-F-08: data, not code) in F2's own migration; (2) `can_member_edit(user, dept, surface)` must **never hard-code the admin role**: F1 writes it with the surface argument and a test that it contains no role literal for the library surface; (3) the founder item on library authoring (CL-A5 vs BE-F-08) is still open in 05 §4; (4) register each new content model in the F1 registry (§8) and the student data map; (5) a roster cache, if ever added, needs a `dpt` bump on colleague name change (§10).

`Lesson.department`, `SharedLibraryEntry`, `Assignment.department`, `Tag.department` (D, F2, C2). Their `on_delete=PROTECT` and the content registry (§8) are how those later epics plug in.

---

## 4. API

Two URL prefixes, one per role, following the house convention (`school-admin/...`, `super-admin/...` in `audit/urls.py`, `dashboard/urls.py`): **`/school-admin/departments/...`** for admins and **`/departments/...`** for teachers. A view never chooses a serializer or queryset by role. Both behind `DEPARTMENTS_ENABLED` (§11): flag off, every route answers as an unknown URL (the C1 `DarkUntilEnabled` pattern, `json_404`). Role denial before object lookup; object lookup always through the scoped queryset.

### 4.1 Status rule (D6)

| Situation | Answer | Why |
|---|---|---|
| Object not visible to the caller (other school, other department, nonexistent, malformed id) | **404, one identical body** | NFR-SEC-05, QA-SEC-09, the house convention; BE-G-08 "do not disclose existence" |
| Role not allowed on the route at all (teacher on an admin route, student anywhere) | **403** | No object is disclosed; matches `IsSchoolAdmin` behavior everywhere else |
| Visible but forbidden (restricted member's write, F2/D) | **403 with a reason code** | Member already knows it exists; NFR-ACC-02 wants "not permitted" distinct from "not present" |
| Teacher in no department, or an individual teacher, on the *list* | **200 empty list** | BE-G-07; also leaks nothing about which kind of teacher they are |
| Individual teacher on a department detail/roster | **404** | BE-G-08 |

Options for the teacher-on-admin-route case: 404 (hides that the feature exists for admins) or 403 (**recommended**; FR-G-01's "any join/leave/create call is denied", honest, no new information because the route list is in the public schema).

### 4.2 School Admin (FR-F-02; role `SCHOOL_ADMIN`, scope `user.school_id`)

| Method and path | Purpose | Reply |
|---|---|---|
| `GET /school-admin/departments/` | School's departments | `[{id, name, description, member_count, created_at}]` |
| `POST /school-admin/departments/` | Create `{name, description?}` | 201 department |
| `GET /school-admin/departments/{id}/` | Detail incl. members with flags | admin member row: `{membership_id, teacher_id, name, email, can_edit, added_at}`. No credits, no courses |
| `PATCH /school-admin/departments/{id}/` | Rename/describe | 200 |
| `DELETE /school-admin/departments/{id}/?content=...&target=<dept id>` | See §8 | 200 summary or 409 with the choice |
| `POST /school-admin/departments/{id}/members/` | `{teacher_ids: [...]}` (max 50) | 200 `{successful, failed, added, errors}` per-item coded, same shape as `add_teachers` |
| `PATCH /school-admin/departments/{id}/members/{membership_id}/` | `{can_edit}` | 200 membership |
| `DELETE /school-admin/departments/{id}/members/{membership_id}/` | Remove one member | 204 |

**Membership write shape (D7):** bulk-add with per-item results (**recommended**, partial success exactly like `add_teachers`, and a school onboarding a faculty adds 20 people at once) versus single `POST` per teacher (simpler, 20 round trips and 20 audit/cache bumps from the client) versus `PUT` the whole roster (the E2 categories style; two admins editing at once clobber each other, and a replace silently removes people, which FR-F-12 forbids doing quietly). **Recommend bulk-add plus single delete.**

`validate_member_candidate(department, teacher_id)` (one function, one place):
The teacher id is **plain input**, resolved by the service inside the admin's school, never through a `PrimaryKeyRelatedField` queryset (DRF reports that reverse-relation querysets can be ignored; research §14). `teacher_ids`, and `can_edit` on PATCH, are the only writable inputs: `school`, `department`, `teacher`, `added_by` in a body are ignored (mass assignment, M20).

1. id parses and exists, else coded `TEACHER_NOT_ON_LICENCE` (existing reason code; same answer for unknown, malformed, student, other school, so the route is no oracle, mirroring `_remove_one_teacher`).
2. `user_type == TEACHER`, `is_active`.
3. `teacher.school_id == department.school_id`.
4. An **active, non-admin `SchoolCreditAllocation` on the school's current license** (active or not; §5.2; "Licensed Teachers on that License", BE-F-02). A lapse does not block an add (owner Q1).
5. Not already a member, else `DEPARTMENT_ALREADY_MEMBER`.
6. Member cap (gap G-13).

The teacher row is locked (`select_for_update`) and 2-3 are re-checked under the lock, which closes the add-versus-remove race (§6.3).

Roles: `SCHOOL_ADMIN` only, **any** school admin of the department's school (matches every other admin path; the license's single `admin_user` is not the test). During a license lapse admins and teachers keep working exactly as before (owner Q1). Super Admin: **no API access to these routes** (FR-F-02 says only School Admin; gap G-7, owner Q6). Super Admins use Django admin, which is audited as ADMIN_ACTION already. The route-coverage sweep fires every write route as a superadmin with an empty body and demands exactly one event: our routes answer 403 and emit one `DENIED` event (via the existing `emit_denied` in `classrooms/permissions.py`), which satisfies it.

An admin with no school (`school_id is None`) gets an empty list and 404 on detail (the AUDIT-NULL-SCHOOL lesson: `filter(school_id=None)` means IS NULL and must never be allowed to match).

### 4.3 Licensed Teacher read surface (FR-G-01/02/07/08; option in §1)

| Method and path | Reply |
|---|---|
| `GET /departments/` | `[{id, name, can_edit}]`, empty list when none (BE-G-07). The member's **own** flag is included so the front end can render disabled-with-reason (NFR-ACC-02, FE-G-06); a restriction is not secret from the person it applies to |
| `GET /departments/{id}/roster/` | `[{name, role}]` and nothing else (FR-G-02: "contains no field beyond name and role"). Ordered by name, then membership id (stable). `role` values: "Teacher" for each live member, "School admin" for each active admin of the department's school (owner Q3; admins are not members, so they are added to the roster by the roster view from the school's admins, not from membership rows). Order: admins first, then teachers, each by name |

Teachers use a distinct prefix from admins so that a serializer or queryset is never chosen by role inside one view (a classic leak: one serializer, `if is_admin` branches). Two small views, two serializers, each with an explicit field tuple. There is **no join, leave or create route for teachers** (FR-G-01); the admin routes answer them 403.

**The roster is the one sanctioned exception to "a teacher sees only their own data" (BE §5.2 vs BE-G-02).** It shows names and roles of colleagues in departments the viewer belongs to, nothing more, and it must not be read as precedent for any other colleague-visible field.

### 4.4 Reason codes (shared contract with the front end, BE-A-06: adding one is a coordinated change)

New: `DEPARTMENT_NAME_TAKEN`, `DEPARTMENT_NAME_INVALID`, `DEPARTMENT_ALREADY_MEMBER`, `DEPARTMENT_CONTENT_CHOICE_REQUIRED`, `DEPARTMENT_REASSIGN_TARGET_INVALID`, `DEPARTMENT_BATCH_TOO_LARGE`, `DEPARTMENT_TOO_MANY_TEACHERS` (the old per-school limit code is gone: no cap on departments). Reused: `TEACHER_NOT_ON_LICENCE`. Reserved for D/F2 (not built in F1): `DEPARTMENT_EDIT_RESTRICTED`. Each needs a message and remediation hint (`AutoGrader/reason_codes.py`), which is wording for the owner (OWNER_SHEET W-series).

---

## 5. Authorization model and how FR-F-10 is enforced

### 5.1 The one rule (D5)

All "what may this user reach" logic lives in **`departments/access.py`** and nowhere else:

```
is_licensed_teacher_of(user, school_id) -> bool        # the liveness predicate, object form
visible_departments(user)         -> QuerySet[Department]
member_department_ids(user)       -> QuerySet of ids   # the function C2/D/F2 must call
live_memberships(user)            -> QuerySet[DepartmentMembership]
can_member_edit(user, dept, surface, *, lock=False) -> bool  # surface: "lessons" | "library"; lock=True reads the membership FOR SHARE for use inside a content write (T20)
```

Rule of liveness, defined once (**draft 4, owner Q1**): a membership counts only while ALL of: `teacher.user_type == TEACHER`, `teacher.is_active`, `teacher.school_id == department.school_id`, `department.school.is_active`, and the teacher holds an **active, non-admin `SchoolCreditAllocation` on the school's current license**, where **current license = the most recently created `LicenseSubscription` of that school (order `created_at`, then `id`), whether or not it is active**. Admin: `user_type == SCHOOL_ADMIN`, `school_id == department's school` (non-null), school active. A lapse, cancel or failed renewal changes none of these. Everything else sees nothing. The same predicate backs the Q form and the object form, tested against each other as H-38 does.

| Option | Trade-off |
|---|---|
| **A. Central module + a sweep guard (recommended)** | Same method that worked for H-38: `classrooms/tests_teacher_access_sweep.py` fails if production code scopes on `course.teacher` directly. F1 adds `departments/tests_access_sweep.py`: fails if any module outside `departments/access.py` imports `DepartmentMembership`, filters on `department__...`, or reaches a model with a `department` FK without going through the helper, with an explicit reasoned allow-list. The rule cannot be re-decided per view |
| B. Per-view `get_queryset` | Fast, and exactly how a P0 leaks: one forgotten filter |
| C. DRF `has_object_permission` only | Does nothing for list endpoints and for related-object leaks. Research §14 agrees: scope the queryset first, object permission is the backstop |
| D. django-guardian style per-object grants | Per-item ACLs are what A5 refuses ("materially more work"), and they would cache membership truth in rows that can go stale (finding 3) |

### 5.2 Liveness: read-time predicate AND reaped on change (D4; rewritten for the owner's Q1)

Memberships are **both** filtered by the liveness predicate at read time **and** deleted when the teacher stops being on the school (§6). Either alone is unsafe: predicate-only revives access when the teacher returns (finding 3); reaper-only leaves a window if a reap is missed.

**Owner decision (Q1, 2026-11-19): a license problem stops AI, not school organization.** Departments keep working through a lapse, cancel or failed renewal. Access ends only when (a) the teacher is actually removed from the school, or (b) the school gets a replacement license and the teacher is left off it. Admins can also add teachers during a lapse.

**The predicate, in one paragraph.** A teacher is live in a school when they are an active teacher whose `school_id` is that school, the school is active, and they hold an active non-admin allocation on the school's *current* license, meaning the newest license row created for that school, **regardless of whether it is active**. A lapse deactivates the license row but leaves the teacher's `school_id` and allocation alone, and the lapsed license is still the newest, so nothing changes. Removing a teacher clears `school_id` and deactivates the allocation (and the reap deletes the memberships). Replacing a license (`license_service.py:1123`) creates a *newer* license and enrols only the carried-forward teachers, so a teacher left off keeps `school_id` and an active allocation on the now-*older* license, which is not current, and the predicate cuts them off. One indexed query: allocation `license_subscription_id = (newest license of the school)`, `is_active`, `is_admin_allocation = false`, `user_id`. The revival protections are unchanged: removal still reaps (§6.2), and a teacher returning later has no memberships to revive.

Options considered:

- **A. Newest license row by creation, active or not (chosen).** No schema change; matches the replacement path exactly; one subquery.
- B. Any active allocation on any license of the school. Simple, but a teacher left off a replacement would stay live forever (their old allocation never turns off). Rejected.
- C. A `current_license` pointer on the school, set at creation and replacement. Cleaner reading, but it is a billing-schema change and a second source of truth that replacement code must keep in sync. Not worth it for F1.

**Edge cases (each a test, T10):** a school with no license row: no teacher is live (admins unaffected); two licenses created in the same instant: ordered by `id`, deterministic; a teacher carried forward gets a new allocation on the new license and stays live; a teacher whose license lapsed and who is then removed loses access at removal; a teacher on a lapsed license can be added to a department by an admin.

**What changes elsewhere:** nothing for courses (they keep `school_id`), sessions or billing. Departments now follow the same "keeps working through a lapse" feel as courses, but cut off on replacement and removal, which the courses rule does not do on replacement. That is a small inconsistency with courses that I recommend noting (risk 6a); F1 does not change course access. **Q10 (offline contracts that end) becomes moot for access**: an offline license that is never deactivated and one that is deactivated behave the same here. Note kept.

### 5.3 Serializer level (NFR-SEC-06)

- Every serializer in `departments/` is a plain `Serializer` or a `ModelSerializer` with an explicit `fields` tuple. **No `__all__`, no `exclude`, no nested `CustomUser`/`Course`/wallet serializer.** A guard (AST over `departments/serializers.py`) fails on any of those.
- A test per serializer asserts the **exact field set** (roster: `{name, role}`; my-departments: `{id, name, can_edit}`; admin member row as in §4.2), using a payload built from a teacher who has courses, students, submissions, grades and a credit wallet, then asserting none of their values appear anywhere in the response text (a "canary" test: unique sentinel strings in the teacher's course title, student name, wallet balance).
- Teacher-name display uses `first_name`/`last_name` only; never `email` for members (admin sees email; members do not).
- **The `credit_wallet` leak, closed exactly (cross-review b).** `CustomUserSerializer` (`users/serializers.py:71`) nests `CreditWalletSerializer` as `credit_wallet` and `SettingsSerializer` as `settings`. Four independent closures: (1) `departments/serializers.py` imports **nothing** from `users.serializers`, `billing.serializers`, `classrooms.serializers` or any other app's serializers, enforced by an AST guard (so a later nesting fails the build, not review); (2) every serializer has an explicit `fields` tuple, checked by the same guard; (3) the roster and `mine` querysets use `.values(...)`/`.only(...)` of exactly the displayed columns, so the wallet is not even loaded; (4) exact key-set equality on every payload plus the canary test. The canary uses **implausible numbers and strings in both raw and display forms** (a wallet balance such as `73190.4417` is also checked as `73,190.44` and `73190`; a course title and a student name carry a unique sentinel), because a plain "balance 10" can collide with a page size or an id. (5) **A repo-wide guard, not only `departments/`**: no serializer anywhere that is registered on a `/departments/` or `/school-admin/departments/` route may nest a serializer from another app (checked from the URL resolver down to each view's `serializer_class`). Plant P2 nests the user serializer into the roster.

### 5.4 Queryset level

`Department.objects` stays default. Views use `visible_departments(user)`; the helper's output is the *only* source of ids for related lookups (member add candidates, delete reassign target). The reassign target and the add candidate are looked up **inside** the admin's own school, so a smuggled foreign id fails as "not found", never as "belongs to someone else".

### 5.5 What F1 must never touch

`teacher_course_access_q`, `teacher_can_reach_course`, `reachable_courses` (classrooms/models.py:252-297), course/student/submission/grade/wallet views. A test pins that these functions' results are byte-identical for a user with and without memberships (D0 in §7.2), and `tests_teacher_access_sweep.py` keeps passing unchanged.

---

## 6. Membership changes, teacher removal, leaving the license (FR-F-12, H-38)

### 6.1 Events that end membership

| # | Event | Today | F1 action |
|---|---|---|---|
| 1 | Admin removes member | n/a | delete row; audit; bump |
| 2 | `remove_teacher_from_license` (clears `school`, deactivates the allocation) | school cleared | **delete that teacher's memberships in that school, in the same transaction**, one DEPARTMENT_MEMBER_REMOVE each (`source=left_license`), ordered as in §6.2 |
| 3 | License lapses, is cancelled, or fails to renew (the license row becomes inactive) | `school` kept, allocation kept | **no change: departments keep working** (owner Q1). Nothing is deleted or audited. Admins can still add teachers |
| 3b | School replaces its license (`license_service.py:1123`) and a teacher is not carried forward | `school` kept, allocation kept on the old license | **the predicate cuts them off** (the allocation is no longer on the school's current license); rows kept. If they are later removed from the school, the reap deletes the rows |
| 4 | `school_id` or `user_type` changed by any instance save (users API, Django admin, scripts) | tracked as PERMISSION_CHANGE | reap inside `CustomUser.save()` itself, atomic and **fail closed** (§6.2); bulk writes through `record_bulk` |
| 5 | School hard-deleted | users `SET_NULL` (a raw collector UPDATE: no save, no signal), departments `CASCADE` | memberships go with the departments. **T24 pins `Department.school` as CASCADE** so nobody changes it to PROTECT/SET_NULL unnoticed; the audit rows live on (no FKs) |
| 6 | Teacher account deleted | `CASCADE` | row goes; content stays (owned by department) |
| 7 | Teacher becomes admin/student | `user_type` changes | reaper as 4, plus liveness predicate |
| 8 | Account deactivated (`is_active=False`) | n/a | predicate only; rows kept, so reactivation restores placement (a reversible admin act should not wipe placements) |
| 9 | School soft-deleted (`SchoolViewSet.destroy` sets `is_active=False`) | data kept | predicate fails for everyone including admins; data kept |

### 6.2 Where the reaper lives, lock order, and failure mode (D8, settled in §15)

**Decision (changed again in draft 2 after the direct challenge): one idempotent function, `end_memberships(teacher, *, source, actor)`, in `departments/service.py`, called from two places, and it fails closed.**

1. **Explicitly in `remove_teacher_from_license`**, in this order: (i) lock the allocation row (as today); (ii) **lock the teacher user row** (`select_for_update`; the existing function does not, my draft 1 claim to the contrary was wrong: the teacher row is only implicitly locked by the UPDATE at `license_service.py:2009`); (iii) `end_memberships(source=left_license)`; (iv) clear `school` and save; (v) then the existing wallet and bucket work, untouched.
2. **Inside `CustomUser.save()`** (an override), for every *other* instance save that writes `school` or `user_type`, in **one `transaction.atomic()`**. Rules (R1, R2 from the Fixer's final check):
   - **Act only when the save writes the field:** `update_fields` is None or contains `school`, `school_id` or `user_type`. A `save(update_fields=["password"])` after an in-memory change never reaps.
   - **Look cheaply, decide by state (R1).** The comparison with the values captured at load time (a `from_db` hook) only decides *whether to look*. `end_memberships(teacher, new_school_id, new_user_type)` then deletes exactly the memberships that would be invalid after the save: those whose `department.school_id != new_school_id`, or all of them if the new type is not TEACHER. A stale snapshot (for example `refresh_from_db()` then `save()`, which does not reset the captured originals) can at worst cost one extra indexed query; it can never delete a valid new membership.
   - **Skip the user-row lock when there is nothing to reap (R2b):** lock and reap only when the **stored** `school_id` is non-null and changes, or `user_type` leaves TEACHER. Enrolment (`_get_or_invite_teacher`, `license_service.py:1312-1314`, school from None) therefore takes no new lock, which keeps the new lock off paths H-222 has not audited.
   - Otherwise: lock the teacher row, `end_memberships(source=account_change)`, `super().save()`.
   It covers the users API (`CustomUserSerializer.school` is a writable `PrimaryKeyRelatedField`) and Django admin. After path 1 the override finds nothing to do (one indexed query, no event), so there is **no double event**.

**Why an override and not a signal receiver (draft 1) (the challenge's point 2, fail closed).** I checked Django 5.2's `Model.save_base`: for a model without multi-table parents (`CustomUser` extends the abstract `AbstractUser`) a save is **not** wrapped in a transaction, and `post_save` runs after the UPDATE has already committed in autocommit. A receiver that raises therefore cannot undo the school change; "fail closed" is impossible from a signal outside an outer `atomic`. An override that wraps lock, reap and save in one `atomic` makes a failed reap abort the school or role change, which is the point: a dormant membership plus a later re-add is the revival hazard. (The challenge proposed a signal plus a skip flag; the goal is the same, the mechanism that can actually fail closed is the override.)

**Failure semantics:** the reap in both paths is **not** swallowed; if it raises, the change does not happen. This reverses draft 1 (which copied the cache-bump rule "never fail the user's write"). Cache bumps still fail open (staleness is TTL-bounded); a reap is a security boundary and fails closed. Because staleness cannot be bounded, no weekly sweep is needed to cover "removed Monday, re-added Tuesday": the fail-closed reap covers it. The safety-net command is therefore **dropped from the plan** (it stays a possible tool, not a step).

| Option | Trade-off |
|---|---|
| A. Explicit call only (review) | Correct order, but misses the users API and Django admin writers of `school` |
| B. Signal receiver (draft 1, and the challenge's proposal with a skip flag) | Cannot fail closed outside an outer `atomic` (verified); hides a required step in a signal |
| **C. Explicit call plus an atomic `save()` override, one idempotent function (chosen)** | Fails closed everywhere instance saves go; one clear source label (`left_license` first); cost: an override on a hot model whose fast path is a tuple comparison |
| D. Weekly sweep command | A window the revival hazard exploits; not needed once the reap fails closed |

**Lock order (checked against `billing/locks.py`, `remove_teacher_from_license` and the H-222 comment at `license_service.py:2031-2034`, "nothing locks a user row after a wallet"). One order for every F1 path:**

> **allocation row, then teacher user row, then department row, then membership rows (always taken in primary-key order); wallet and buckets after, unchanged.**

- `remove_teacher_from_license`: allocation (existing), **teacher (added)**, reap (membership rows), then save, then wallet/buckets. The reap sits before `teacher.save` and `lock_wallet_first`, so nothing in F1 is acquired after a wallet.
- `add member`: allocation `FOR SHARE`, teacher `FOR UPDATE`, department `FOR SHARE`, then insert the membership. (Draft 1's add path locked only the teacher and re-read school; the allocation lock is the missing piece.) Because remove holds the teacher row through the whole transaction, a concurrent add waits, then re-checks `school` and refuses (this is the challenge's fix for its point 3a, adopted).
- `delete department`: department `FOR UPDATE`, then membership rows by primary key; **no allocation or user locks**.
- Membership rows are always locked with `select_for_update().order_by("pk")` before deleting, in the reap and in delete, so two transactions deleting overlapping sets (a delete-department and a reap of one teacher in the same department) lock them in the same order and cannot deadlock.
- The concurrency test is a **three-way** case (20+ repeats, real processes, Gate 3): remove-teacher, add-member and delete-department at once, plus each pair; assert no deadlock error, no live membership for a teacher off the school, and no orphan.

### 6.3 Concurrency (Gate 3)

- Add vs remove race: admin adds teacher X while another admin removes X from the license. Both take the allocation row first (add `FOR SHARE`, remove `FOR UPDATE`) and then the teacher row, so they serialize: either the add finishes and the removal's reap deletes the new row, or the removal finishes and the add's re-check fails with `TEACHER_NOT_ON_LICENCE`. A leftover row, if any bug allowed one, is ignored by the liveness predicate. Test: real processes, **at least 20 repeats**, assert final state has no live membership for a teacher who is off the school.
- Two admins adding the same teacher: UNIQUE + `IntegrityError` mapped to `DEPARTMENT_ALREADY_MEMBER` (not a 500).
- Rename vs rename: last write wins, `updated_at` correct; unique-name collision is a coded 400.
- Delete vs add member: delete locks the department row (`select_for_update`); add takes the same lock; the loser gets 404 or coded refusal, never an orphan row.

### 6.4 Content for a removed teacher (BE-F-12)

Content authored by a removed teacher stays because content is **owned by the Department** (03a: `Lesson.department PROTECT`; authorship is a captured `created_by_id`/`last_edited_by_id`, not an FK, so removal or account deletion cannot null or cascade it). F1 has no content yet, so F1's obligation is (a) the membership delete never touches content, proven by a structural test that every FK to `Department` from another app is `PROTECT`, enforced by the registry in §8, and (b) the audit/attribution decision of how a former teacher's name shows (deferred to D, which adds content; not asked now).

**Re-adding a former member (owner Q5):** no automatic return to old departments. The admin adds them again deliberately. (Option: keep dormant rows and revive on re-add; rejected because of finding 3 and because "the teacher has no control over placement" means placement should always be a fresh admin act, with its own audit event.)

---

## 7. Adversarial matrix (test list)

### 7.1 Fixture (NFR-TST-08, the first deliverable)

`departments/tests_matrix_fixture.py`: two schools S1, S2. S1: admin A1, teachers T1 (D1 member, editable), T2 (D1 member, **restricted**), T3 (D2 only), T4 (no department), T5 (D1 and D2), T6 (**removed** ex-member, still has a user but off the school), T7 (on S1 with lapsed license). S2: admin A2, teacher T8 (member of D3). Individual teacher I1. Student ST. Super admin SU. Anonymous. Admin with no school A0. Each teacher has courses, enrolled students, submissions, grades, a credit wallet with a **unique canary balance**, a course title and a student name carrying canary text. Three departments D1, D2 (S1), D3 (S2), D1 and D2 overlapping on T5. Two concurrent Sessions, one Course that outlives a Session (to prove departments do not reset).

### 7.2 One test plan (draft 2): T-rows (new F1 surfaces), M-rows (existing paths), D0 (the differential), and guards

Merged from the lead's rows and the independent review's M1-M20. Nothing is dropped; §15d maps each review row to the test that covers it. Columns: actor, route, expected. "404*" = the one identical not-found body. "A" is a teacher with data (courses, students, submissions, grades, wallet with a canary balance); "B" shares a department with A.

**T-rows: the new F1 surfaces**

| # | Test |
|---|---|
| T1 | **Create/rename/delete department:** admin A1 ok; teachers, I1, ST, SU: 403; anon 401; A2 on D1: 404*; A0 (no school): 404* |
| T2 | **Members add/remove/flag:** A1 ok on own departments only; adding T8 (S2), a student, I1, an admin, or a deactivated account: coded `TEACHER_NOT_ON_LICENCE`, identical body for each; A2 adding T1 to D3: coded; T1/T2 on every member route: 403 |
| T3 | **List (teacher):** T4, I1: 200 `[]`; T1: exactly D1; T5: D1 and D2 separated (FR-F-03); T3 never sees D1; A1: S1's departments; A2: only D3 |
| T4 | **Detail/roster by guessed id:** T3 on D1, I1 on D1, T8 on D1, random uuid, malformed id: all the same 404*, byte-equal |
| T5 | **Roster field set:** exactly `{name, role}`; no email, id, balance, course, flag |
| T6 | **Own flags:** T2's list shows restricted flags; T1's roster shows no one's flags |
| T7 | **Canary:** no response from any F1 route contains any user's canary strings |
| T8 | **Removed teacher:** after `remove_teachers` access ends in the same transaction; list `[]`, roster 404*; one REMOVE per membership with `source=left_license`; no double event (the second reap finds nothing to end) |
| T9 | **Revival:** removed then re-added to the license: no department access until an admin places them again |
| T10 | **License states (owner Q1):** after lapse, cancel, failed renewal, an OFFLINE deactivation or an ended offline contract: the teacher's list and roster are **unchanged**, and an admin can still add a teacher. **Replacement:** a carried-forward teacher keeps access; a left-out teacher gets list `[]` and detail 404*, rows kept; a school with no license row shows teachers nothing. Same newest-license tie-break test |
| T11 | **School change via Django admin, users API (`school` PATCH) or `record_bulk`:** memberships gone, one event each |
| T12 | **Cross-school membership by any path:** API, bulk add, direct model save (the model's `save()` validates, so Django admin cannot create one either), teacher moved between schools |
| T13 | **FR-G-01 denials:** teacher POST/PUT/PATCH/DELETE on every admin route: 403, nothing created or changed, **including a PATCH of their own membership flags (no self-unrestrict)** |
| T14 | **Audit:** exactly one event per write with actor, role, school, department, target; a denial leaves one DENIED event; A2's audit view shows none of S1's department events; filtering by D1's id as A2 returns an empty well-formed 200 (FR-A-09) |
| T15 | **Flag off:** every route 404 for every actor including A1 and SU |
| T16 | **FR-F-01:** no `session` field; a new Session or rollover leaves departments, memberships and flags byte-identical |
| T17 | **School inactive** (soft-deleted): predicate fails for everyone; data kept |
| T18 | **Multiple admins:** any SCHOOL_ADMIN of the school may manage; one of another school may not |
| T19 | **Mass assignment (M20):** `school`, `department`, `teacher`, `added_by` in any body cannot move a row across schools or change who added it |
| T21 | **Moving schools (reviewer 2.7):** teacher removed from school A and added to school B: no A memberships remain, B's admin can place them, A's departments show nothing |
| T24 | **School hard-delete:** `Department.school` stays CASCADE (a test pins it); memberships go with the departments; audit rows survive |
| T25 | **Reap by state (R1):** load a teacher at S1, move them to S2 elsewhere (reaped), place them in an S2 department, then `refresh_from_db()` and `save()` on the first instance: the S2 membership survives |
| T26 | **Skips (R2):** `save(update_fields=["password"])` after an in-memory school change reaps nothing and takes no user lock; enrolling a teacher whose stored school is null reaps nothing and takes no user lock; a real school or role change still reaps |
| T27 | **Membership emails (owner Q8, §9b):** an admin add sends one email to that teacher, an admin remove sends one; a failed or rolled-back change sends none; a broker outage or a failing send never fails or undoes the change and is logged; adding 50 teachers queues exactly 50 emails, after commit; no email on a leave-the-school reap, a school or role change, a department delete, or a flag change; the address never appears in a log line; a deactivated teacher gets none |
| T22 | **Size bounds:** a request with more than 50 `teacher_ids`: 400 `DEPARTMENT_BATCH_TOO_LARGE` (message W13), nothing added; an add that would take a department past 500 members: `DEPARTMENT_TOO_MANY_TEACHERS`, nothing added (safety bounds, not product rules; there is no cap on departments per school) |
| T23 | **Messages and labels:** every new reason code carries a message and a remediation hint (what the admin does next), the house 404 and house 403 texts are reused unchanged, and each new `target_type` has an entry in `_TARGET_LABEL` (the existing `audit/tests_h273_query_filters.py` check fails otherwise) |
| T20 | **Flag race (reviewer 2.5):** the helper `can_member_edit(..., lock=True)` reads the membership `FOR SHARE`, a flag PATCH takes `FOR UPDATE`: a two-process test shows an edit that commits after a flag change is refused (the helper's contract, so D and F2 implement the denial once) |

**M-rows: every existing path, B asks for A's objects (checked at queryset and serializer level, absolute expectations; B and A share a department)**

| # | Resource and path | Expected |
|---|---|---|
| M1 | Courses: `CourseViewSet` (`classrooms/views.py:1302`), scoped by `teacher_course_access_q` | A's course 404*; list excludes it |
| M2 | Course helpers: `reachable_courses` call sites (12 non-test call sites; assignments, dashboard) | each with A's course id: 404* |
| M3 | Rosters: `StudentCourseViewSet` (`classrooms/views.py:2311`) | A's enrollment 404* |
| M4 | Roster accounts: `CustomUserViewSet` (`users/views.py:367`), filters | A's student and A: 404*; `?enrollments__course=<A's>` empty |
| M5 | Submissions: `StudentSubmissionViewSet` (`students/views.py:284`) | A's submission 404* |
| M6 | Submission tasks: `TaskViewSet` (`users/views.py:2820`, `teacher_may_reach`, `students/task_access.py:53`), sessions | A's task/session/results 404* |
| M7 | Assignments: `AssignmentViewSet` (`assignments/views.py:316`), generation sessions | A's assignment 404* |
| M8 | Grades: dashboard teacher routes; E2 grading-policy and breakdown routes once E2 lands | A's course breakdown 404* |
| M9 | Grades export: student data export, teacher scope | B exporting A's student 404* |
| M10 | Credit balance: `CreditWalletViewSet` (`billing/views.py:426`), buckets, ledger, usage | B's list never contains A's wallet; A's wallet id 404* |
| M11 | Credit balance (license): `SchoolCreditAllocationViewSet` (`billing/license_views.py:1202`); `LicenseSubscriptionViewSet` for teachers | B reads A's allocation 404*; license list for teachers empty |
| M12 | Nested serializers: any serializer nesting `CustomUserSerializer` (`users/serializers.py:71` carries `credit_wallet`) | exact key-set equality on every F1 payload, not a negative `assertNotIn` (§5.3) |
| M13 | Audit: `SchoolAdminAuditEventListView` (admins only; no teacher view) | teacher 403/404; foreign `department_id` filter by an admin: empty 200 (T14) |
| M14 | Search and filters: tags, user filters, list filters | a `department` / `department_id` parameter on existing lists is ignored or rejected, never widening |
| M15 | Caches: `UserCacheMixin` keys per requesting user and `usr` generation | department payload keys include viewer id; a removed member's cached payload is not served (T8, §10) |
| M16 | Celery/notifications: fan-out is to the course teacher | **guard (changed by owner Q8):** the only mail F1 sends is the two membership emails in `departments/notifications.py` (§9b); an AST guard fails on any other mail or task call in `departments/`, and any later fan-out to "department members" must add a row here |
| M17 | Django admin | Department and Membership registered superuser-only with `raw_id` for teacher, no inline exposing a wallet, `save_model` goes through the model `save()` validation; an admin save leaves an ADMIN_ACTION event |
| M18 | New surfaces for teachers | = T3, T4 |
| M19 | New writes by teachers | = T13 |
| M20 | Mass assignment | = T19 |

**D0: the membership-invariance differential (generic, catches change)**

Enumerate every readable (GET) route the resolver serves, as `audit/tests_route_coverage.py` does for writes. For B, record status and body of every route **before** B is added to D1 (which contains A), then **after**, and assert equal (modulo timestamps and the new routes). Repeat for a restricted member and for a student; evaluate `teacher_course_access_q(B)` and `reachable_courses(B)` before and after. **Why both M-rows and D0:** the M-rows are *absolute* (they would catch a path that was already leaky before F1 and give a named failure per resource); D0 is *relative* but *complete* (it catches a widened rule on a route nobody wrote a row for, including F2/D routes later, by route name). Neither replaces the other.

**Hardening against a vacuous pass (the challenge's first test hole, adopted).** The differential would pass for the wrong reason in five ways, each closed on purpose: (1) `UserCacheMixin` caches per user, so "after" could be the cached "before": the cache is flushed (and `usr` bumped) between the two snapshots, and each snapshot is also taken twice (cold and warm) and they must agree; (2) it must probe **other people's ids**, not only B's own: every route that takes an id is called with the ids of A's and every colleague's objects (course, student, submission, grade, wallet, allocation), harvested from the fixture; (3) a **positive control** in the same run: A reads those same ids with 200, so a route that answers 404 to everyone (a broken fixture) fails the run; (4) the route list is pinned (a route removed from the enumeration fails; a new readable route without a fixture fails by name); (5) the snapshot normalizer removes only timestamps and the department routes, and a test mutates one field of one response and proves the comparison notices. Cost: about 2.5 days of the red-tests step.

### 7.3 Structural guards

- `departments/tests_access_sweep.py` (§5.1): no module outside `departments/access.py` imports `DepartmentMembership`, filters on `department__...` or reaches a department-FK model; **plus an AST check (from the review, adopted) that every action of every `departments` viewset calls the access helpers** before touching a queryset, so a new route added without the check fails by name.
- `tests_serializer_allowlist.py` (§5.3): explicit `fields`, no cross-app serializer imports, exact key sets.
- **Registry guard:** `DEPARTMENT_SCOPED_RESOURCES` and a test failing if any model has an FK to `Department` without registering `(visible_for, reassign, delete, count)` and `on_delete=PROTECT`. **It also scans bare columns** (the challenge's second test hole): any model field named `department_id` or `*_department_id`, or a UUID/char field whose name contains `department`, that is not a ForeignKey fails unless it is on a short allow-list (`AuditEvent.department_id`, a captured value by design). A test-only dummy model with a bare `department_id` UUID proves the guard fires.
- **Student data map guard** (`students/tests_student_data_map.py`, lines 68-100): it **fails the build** for every relation to a user and every bare `*_id` user column not listed in `students/student_data_map.py`. F1 adds `DepartmentMembership.teacher`, `DepartmentMembership.added_by_id` and `Department.created_by_id`, which must be added as staff (`_staff`-role) entries with a reason (draft 1 wrongly said to "assert" the map; it is a required edit, and it is in step 2). No export file.
- Route coverage (`audit/tests_route_coverage.py`): each new write route joins `write_routes` (one event each; 403s leave one DENIED event). Count it so it is not a surprise STOP.
- History guard, swagger groups ("Departments" and "School Admin Departments" tags), reason-code catalogue test, the 67-label standing guard list per the near-run rule.

### 7.4 Planted bugs (3, for risky logic only, per the lean rules; each written down with the test that must fail)

| Plant | Break | Must fail |
|---|---|---|
| P1 (tenancy) | `visible_departments` drops the school/membership filter | T3, T4, M18, D0 |
| P2 (liveness) | `is_licensed_teacher_of` ignores the newest-license allocation check | T10 (left-out teacher on a replaced license) and the Fixer's replacement-license case |
| P3 (reap) | `end_memberships` becomes a no-op inside `remove_teacher_from_license` | T8, T9, and the Fixer's removed-teacher M-row |

The nested-serializer break (earlier P2) is caught by the serializer guards (§5.3) and is no longer a plant. T8, T9 and T10 assert row state and access through the routes so these plants turn them red.

Gate 2 breaks one protection at a time, so a missing reap is **not** folded into P3: with the predicate intact a missing reap is invisible to T8, but **T9 (revival) fails**, and T9 is written red first like every other test. (A cross-school add accepted is covered by T12; mass assignment by T19.)

---

## 8. Department delete (FR-F-12, D-08)

`DELETE /departments/{id}/?content={refuse|delete|reassign}&target=<department id>`.

1. Locks the department row (and the target if reassigning), same school only.
2. Asks the **registry** (§7.3) for each registered content type's count. In F1 the registry is empty, so a delete of an empty or member-only department just works; the choice flow, the 409 and the reassign path are built and tested **against a test-only registered dummy content model** so that D, F2, C2 inherit working behavior instead of writing it.
3. If content exists and `content` is absent: **409 `DEPARTMENT_CONTENT_CHOICE_REQUIRED`** with counts per type. Nothing changes.
4. `reassign`: content moves to `target` (must be another department of the same school, not itself): one transaction, one audit event per content type moved plus the delete. `delete`: deletes content explicitly (admin confirmed in the UI; the API requires the explicit parameter), counts audited. Everything is in one transaction: all or nothing.
5. Members: memberships are removed with the department. **One** DEPARTMENT_DELETE event carries `member_count`, `member_ids` (capped at 50, with `member_ids_truncated` true beyond that), `item_count` and the choice (D10 below, changed in draft 2).

| Option | Trade-off |
|---|---|
| **A. Hard delete behind PROTECT and the explicit choice (03a, recommended)** | Audit rows carry captured values, so the log survives. Simple; nothing hidden. Irreversible, hence the explicit choice and name-confirmation in the UI |
| B. Soft delete (`archived_at`) | Undo possible; but then every query needs the archived filter (another quiet leak surface), `UNIQUE(school, name_key)` gets conditional, and "delete" never frees the name. It is also the pattern most likely to leave archived content visible to ex-members |
| C. Cascade | Content vanishes silently; forbidden by BE-F-12 |

Audit of a delete (D10, **changed in draft 2 after the cross-review**): (A) **one DEPARTMENT_DELETE event with `member_count`, capped `member_ids`, `item_count`, `delete_choice` and the target (chosen; the challenge's compromise)**; (B) N+1 events (draft 1). I now prefer A: FR-A-01's acceptance is "each listed action produces **exactly one** well-formed event", and the action here is the department delete; the route-coverage sweep also expects one event per write route. Who was in the department on that day is still reconstructable from the stream of earlier MEMBER_ADD/MEMBER_REMOVE events (adds minus removes), which is complete because every membership change is logged. A teacher *leaving the license* is different: each membership removed there is its own membership change, so it keeps **one MEMBER_REMOVE per department left**.

---

## 9. Audit events (FR-F-11, FR-A-01/02, BE-A-01)

Use the **existing** actions; no new enum values needed (owner-approved labels already exist in `audit/enums.py`).

| Action | When | `target_type` | Metadata allow-list to add |
|---|---|---|---|
| `DEPARTMENT_CREATE` | create | `Department` | `item_count`? none needed; `department_id` is the event's own field |
| `DEPARTMENT_UPDATE` | rename/describe | `Department` | `changed_fields`; before/after `name` (see D11) |
| `DEPARTMENT_DELETE` | delete (one event, includes counts) | `Department` | `member_count`, `member_ids` (list, capped at 50), `member_ids_truncated`, `item_count`, `delete_choice`, `target_department_id` |
| `DEPARTMENT_MEMBER_ADD` | add (one per teacher) | `DepartmentMembership` | `teacher_id`, `source` |
| `DEPARTMENT_MEMBER_REMOVE` | admin remove, left license (one per department left), role/school change | `DepartmentMembership` | `teacher_id`, `source` (`admin`, `left_license`, `account_change`) |
| `PERMISSION_CHANGE` | flag change | `DepartmentMembership` | before/after `can_edit` (extend the before/after allow-list, today user fields only; 05 §3.4 names this action) |

All carry `school_id` and `department_id` (the emitter already accepts both). Emit **inside the write transaction** (rule from S4: a rolled-back change leaves no event). Denials emit `DENIED` with a reason code. Sanitiser: ids and flags only except D11.

**D11 department name in events:** (A) ids only, as tags do (a tag's name is text a teacher wrote, so tag events never record it); (B) **record `name` in before/after (recommended)**: department names are organizational labels an admin chooses, the audit view is already school-scoped, and after a delete the event is the only record of what the department was called. Risk: a free-text name could hold something sensitive; mitigated by the existing generic sanitiser. If the sanitiser refuses, fall back to A. SM call; no owner wording involved.

Retention class: `GENERAL` (12 months); none touches student records.

`target_type` strings (`Department`, `DepartmentMembership`) are stored values, **but the audit API shows a label for each** through `_TARGET_LABEL` in `audit/serializers.py`, and `audit/tests_h273_query_filters.py:218-251` fails the build if a target type has no entry. So the two labels are **required, not conditional** (changed in draft 2 after the challenge): "Department" and "Department member" (OWNER_SHEET W7, W8). They are admin-facing wording and need owner approval.

Because `PERMISSION_CHANGE` is shared with user-account privilege changes, a flag change reads "Permission changed" in the log with `target_type=DepartmentMembership` and `metadata.source=department`. Option: a new `DEPARTMENT_MEMBER_UPDATE` action with its own label (clearer in the school admin's view, one more owner-approved label, W-series). **Recommend PERMISSION_CHANGE as the roadmap says**, the label question goes on the sheet as optional.

---

## 9b. Membership emails (owner Q8: teachers DO get an email)

**How the app sends transactional email today (read from the code, not guessed):** a function in a `services/notifications.py` module builds the subject and a merge payload, then hands it to `safe_delay(send_email_task, ...)` (`AutoGrader/dispatch.py`, `AutoGrader/tasks.py`). `send_email_task` is a Celery task (3 retries, 30 seconds apart) that sends through MailerLite with a template id (the shared notice template) and falls back to plain mail. `safe_delay` swallows and logs a broker outage and lets any other error through. Existing examples: `classrooms/services/notifications.py` (added to a course, invitations).

**F1 design.** One module, `departments/notifications.py`, with `send_added_to_department_email(teacher, department)` and `send_removed_from_department_email(...)`. Same path, same shared template, same merge keys, so no new template is needed. Rules:

1. **After commit, never inside the change.** Queued with `transaction.on_commit`, so a rolled-back add or remove sends nothing. The enqueue is wrapped so that **any** error is logged (teacher id and department id only, never the address, which the repo's logging hook forbids) and swallowed: an email must never block or undo the change (the owner's rule).
2. **When:** an admin adds a teacher; an admin removes a teacher. **Not sent** (my recommendation, flagged to the SM): when memberships end because the teacher left the school or changed role (they have no school to be told about), when a department is deleted, or when only the edit switch changes. If the SM wants a delete email, it is one more function and test.
3. **50 teachers at once:** yes, 50 emails, one per teacher (each is personal: it names the department). They are queued after the add commits, one task each, so the admin's request returns right away and the queue sends them. A failed one retries on its own (3 times) and never affects the others. The request cap (50 teacher ids per add) also caps the worst case at 50 emails per request. I do **not** recommend staggering or digesting for F1: MailerLite handles bursts of this size and the existing student-invite flows do the same; if production shows a rate limit, add a countdown stagger in this one module. Option B (one digest email per admin action per teacher) is the same as A here because each teacher belongs to one department per action.
4. **Template and name:** the shared notice template already greets by the recipient's name, so the text below has **no placeholders except school and department names**.
5. **Tests:** T27.

**Exact wording (new, for owner approval; W11 and W12):**

W11, subject: `You've been added to the {department} department`
W11, body: `A school admin at {school} has added you to the {department} department on Grade A+. You can see the department and the people in it the next time you open the app. Your courses, students, grades and credits are not shared with anyone in the department. Questions? Ask your school admin.`

W12, subject: `You've been removed from the {department} department`
W12, body: `A school admin at {school} has removed you from the {department} department on Grade A+. You no longer see this department. Your courses, students, grades and credits are not affected. If you think this was a mistake, ask your school admin.`

---

## 10. Cache: `cachegen:dpt` (NFR-PRF-06)

- Add `SCOPE_DEPARTMENT = "dpt"` to `cache_generation.VALID_SCOPES` (entity-id scope; the name contains none of "user", "school", "course", "settings", "admin", so no legacy pattern can ever match it; the namespace-safety test covers it).
- **Bump set on every membership or department change:** `("dpt", department_id)` and `("usr", member_user_id)` for each affected member (the existing user scope). The `usr` bump is required, not optional: a "my departments" key built only from `dpt` counters of the departments the user currently belongs to would not change when a *new* department is added to them (the new one is not in the key), so it would serve a stale list. Bump through `bump_many` (pipelined, with the post-commit re-bump the module already does).
- **The permission decision is never answered from the cache (cross-review, adopted).** A license replacement that leaves a teacher off, or a removal, changes no department row and no `dpt`/`usr` counter in some paths, so a cached "my departments" payload would keep being served to a teacher whose liveness predicate has just started failing. Every member read therefore runs the liveness predicate first (one indexed EXISTS) and only then reads the cache; the cache holds **payload only**.
- Cached: `GET /departments/` (my departments), key `departments:list:user=<id>` versioned on `[("usr", id)] + [("dpt", d) for d in the user's departments]` (`batched=True`, the pattern built for scope lists that grow with data). The key always carries the viewer id.
- **Not cached: the roster.** One indexed query over at most the member cap. This also avoids a coupling the review found: a colleague's *name change* alters a roster, and would otherwise need a `dpt` bump for every department of the renamed user (the reviewer's 2.9). If the roster is cached later, that bump must be added to the `CustomUser` save path. The cheaper design wins for now.
- Admin routes are not cached.

| Option | Trade-off |
|---|---|
| **A. Scope + hooks + a real cached consumer + extended counter-destruction test (recommended)** | A counter nobody reads cannot be mutation-tested end to end; the consumer proves "exactly the affected members' views invalidate" (NFR-PRF-06) |
| B. Scope and hooks only; cache nothing until D/F2 | Least code and zero staleness risk now; the invalidation is untested against a real read until later, and the scope would be added "for a future reader" |
| C. No cache for department views, TTL-free | Fine on cost (two small indexed queries), but then `dpt` has no purpose and NFR-PRF-06's test cannot exist |

Tests: a license replacement leaving a teacher off, with a warm cache, is refused (predicate before cache); add the scope to `tests_cache_generation` namespace-safety, extend the **counter-destruction test** (delete the counter in Redis: reads must not revive stale entries, the existing guarantee), a membership add/remove/flag/delete each invalidates exactly the affected viewers (and a bystander's key survives), Redis down degrades to a coherent stale-or-fresh read, never an error (Gate 5), and the "no wildcard" guard still passes.

---

## 11. Feature flag, E2 and C1 interplay

**Flag (D12, changed in draft 2 per the owner's 2026-11-18 rule that Phase 2 flags default ON):** `DEPARTMENTS_ENABLED`, `env.bool(..., default=not _RUNNING_TESTS)` in `settings.py` (`_RUNNING_TESTS` exists at `settings.py:1668`), alongside `TAGS_ENABLED`. Tests that need it off set it explicitly. Default ON is safe here because every route is permission-checked and nothing is visible until the front end ships screens. Flag off: all `/departments/` routes answer as unknown URLs after sign-in. The **reap and cache bumps run regardless of the flag** (cheap no-ops when no rows exist; they must still close access if the flag is ever switched off after use). Options: one flag for F and G (**recommended**; one switch-on checklist), or separate `DEPARTMENTS_ADMIN_ENABLED` / `DEPARTMENTS_MEMBER_ENABLED` (lets the admin surface go first; but a half-on state tests twice as many combinations). F2/D add their own flags and require this one as well. A test pins the default (on outside tests); the local `.env` needs no unsetting for it because the default is on.

**E2:** no shared files in the recommended option (new app). Migration numbering is independent. F1 must not touch E2's copy path or `teacher_can_reach_course`. For F2 later: library to course copy needs *two* checks, source "department member can see this entry" (F1's helper) and destination `teacher_can_reach_course` (E2's). F1's helper is written so F2 can call it from E2's copy service.

**C1/C2 (tags):** C1's `TagViewSet.get_queryset` is `owner_type=TEACHER, teacher=self` and `tags/models.py` pins `tags_tag_teacher_owner_ck` (CHECK `owner_type=TEACHER`). F1 changes nothing there. C2 (FR-C-02) must use `member_department_ids(user)` and register `Tag.department` in the content registry (03a says `Tag.department` is `CASCADE`, which conflicts with the delete rule: deleting a department would silently untag every assignment that used a department tag; see gap G-5; recommend `PROTECT` plus registry).

**Audit/A:** nothing to wait for; `department_id` and index are on `AuditEvent`.

---

## 12. Build slicing (tests first, lean rules: no declaration commits, STOP only for bugs/failing tests/design, 3 plants, merge staging once, under 10 commits)

| # | Step | Days |
|---|---|---|
| 1 | **Red:** fixture (§7.1), T/M rows (incl. the newest-license predicate cases, T25-T27), serializer field-set and canary tests, the hardened invariance differential D0 (about 2.5 days), sweep and registry guards, flag, audit, reason-code and label tests | 4.25 |
| 2 | Models (`save()` validates school), `0001` (one `can_edit` flag), `access.py` (the one rule with the newest-license subquery), flag (default on), URL/schema groups, student data map entries, `_TARGET_LABEL` entries, audit allow-lists, Django admin registration | 1.25 |
| 3 | Admin API: departments CRUD, member add/remove, flag PATCH, reason codes with hints, audit emission | 2 |
| 4 | Member read API (`mine`, `roster`), allow-list serializers | 1 |
| 5 | `end_memberships()`: explicit call, atomic `CustomUser.save()` override (state-based, update_fields and null-school skips), bulk path; fail-closed, lock-order, revival and concurrency tests (Gate 3) | 2 |
| 6 | Delete service, content registry, dummy-content tests | 1.25 |
| 7 | `cachegen:dpt`, hooks, the one cached consumer, predicate-before-cache, counter-destruction extension | 1 |
| 8 | **Membership emails (new, owner Q8):** `departments/notifications.py`, after-commit enqueue, failure isolation, logging, AST guard, T27 | 1 |
| 9 | Plants (3), standing guards, near run, NOTES | 1.25 |
| | **Total** | **15** |

**Estimate 15 working days (range 14-16)**, about 3 weeks. Change from draft 3 (14 days): emails +1; the newest-license predicate and its replacement tests +0.25 in the red step; the single flag saves 0.25 in the models step. Without the member read surface (step 4 deferred to G) it is 14.

Gates (10 Gates.md): 1 baseline, 2 mutation (the three plants plus guard breaks), 3 concurrency (§6.3), 4 adversarial (§7), 5 failure (Redis down, a failed reap rolls the whole removal back, mid-delete rollback), 7 real Postgres/Redis, 9 isolation (the entire matrix is a Gate 9 piece); 6 stress only for a large school (500 teachers x 20 departments: list/roster query counts bounded, a measured budget in the evidence); 8 on staging at switch-on.

---

## 13. Differences between the Backend Requirements and the architecture docs (which binds)

| Topic | Backend PDF (binds) | Architecture docs | Effect on F1 |
|---|---|---|---|
| Deleting a department | BE-F-12 covers only removal from a department and leaving the License | 01a FR-F-12 and D-08 add "department deletion requires explicit delete-with-content or reassign" | BE binds for the core; the delete choice is a 01a addition we keep (consistent with "content must not silently disappear"). Owner Q4 confirms |
| Forbidden exposure list | §5.2 names Courses, rosters, submissions, grades. BE-F-10 adds credit balance | 01a FR-F-10 lists five | Superset binds: five |
| Flag shape | BE-F-07 "a specific member's ability to add or edit Department Lessons or Shared Library entries"; A5 "a per-member permission flag" | 03a: two booleans | Owner Q2 chose ONE `can_edit` (matches A5's singular flag); 03a's two columns are not built |
| License identity | BE-A-02 "License ID" | 03a/03 `license_id`; code `school_id` | Code and tenancy: `School`. Documentation fix only |
| Name of the assumption | BE doc calls it **A5** (in its assumptions table) | 05 calls it "BE-A5" | Naming only |
| Library authoring | BE-F-08 members add, admin may not, do not hard-code | CL-A5 (checklist) "teachers submit, admin approves" | BE-F-08 binds (01a D-03). F2 only |
| Who "Licensed Teacher" is | Role table: managed teacher account under a School License | 03a: `teacher.school_id == department.school_id and user_type == TEACHER` | Not precise enough; D4/Q1 |

---

## 14. Research (what I relied on, and fit)

| Topic | Source | Finding | Fit for this codebase |
|---|---|---|---|
| Cross-tenant leaks in DRF | [DRF permissions docs (mirror)](https://django-rest-framework-old-docs.readthedocs.io/en/latest/api-guide/permissions/), [DEV: IDOR in Django REST APIs](https://dev.to/securitystefan/how-to-prevent-idor-vulnerabilities-in-django-rest-apis-5763), [BOLA essentials](https://alivedise.github.io/backend-engineering-essentials/security-fundamentals/broken-object-level-authorization-bola) | Scope `get_queryset()` first so out-of-scope objects are 404; `has_object_permission` is a backstop, not a substitute; writable FK fields in a payload can reference another tenant's object; test with other tenants' tokens | **Fits and matches the existing rule** (404 for out of scope, NFR-SEC-05). The "smuggled FK" point is why add-candidate and reassign-target are looked up inside the admin's school (§5.4). Weak sources are blogs; the DRF behavior itself I verified from the codebase's own pattern (H-38 helpers, `IsSchoolAdmin`) |
| Per-member flags vs per-object ACLs | [Vinta: Django permission apps compared](https://www.vintasoftware.com/blog/controlling-access-a-django-permission-apps-comparison), [django-guardian](https://django-guardian.readthedocs.io/), [DZone: permissions at scale](https://dzone.com/articles/efficiently-computing-permissions-at-scaleour-engi) | Per-object grants (guardian) suit instance-level grants users create and revoke; tenant-wide roles are simpler as fixed role/flag plus queryset scoping; one team computed permissions in SQL rather than a library | **Guardian does not fit**: A5 chose a flag precisely to avoid per-item ACLs, and grant rows are a second source of truth that can go stale. Computing the rule in the queryset (as GitGuardian did) is what H-38 already does here |
| Cache invalidation on membership change | [Versioning group cache (Google patent)](https://patents.google.com/patent/US12197337), [Zanzibar summary](https://blog.bytebytego.com/p/how-google-manages-trillions-of-authorizations), [Okta cache-consistency thread](https://devforum.okta.com/t/best-practices-for-ensuring-consistency-in-token-caches/7012) | Embed a version/generation in the key so one change makes old keys unreachable (no mass delete); keys must carry tenant identity; Zanzibar-style consistency tokens are for a distributed ACL service; event-driven invalidation is the common alternative | **Fits** (this project's `cache_generation.py` is exactly generation versioning). Zookies do not: we have one database and a TTL-bounded cache, not a distributed ACL store. The "new membership is not in the old key" subtlety in §10 is my own derivation, not from these sources |
| Content when a member leaves | [U-M IT: Drive files of someone who left](https://teamdynamix.umich.edu/TDClient/30/Portal/KB/Article/9249/Transfer-Ownership-of-Google-Drive-Files-Owned-by-Someone-Who-Left-U-M), [Drive offboarding](https://buralog.jp/?p=9828) | Files owned by a person must be transferred when they leave; **files in Shared Drives belong to the drive, not the person**, and do not need transfer; ownership does not cascade through folders | **Strongly supports 03a's choice**: content owned by the Department (like a Shared Drive), authorship a captured value, membership just a door. It also argues against per-user ownership of department content. (A GitHub comparison did not come back from search; not claimed) |
| API1 BOLA / API3 BOPLA, related-field scoping, row-level scoping libraries, membership-table pattern | from the independent review: OWASP API Security Top 10 2023 ([Wiz](https://wiz.io/academy/owasp-api-security)); [DRF relations docs](https://sources.debian.org/src/djangorestframework/3.16.1-3/docs/api-guide/relations.md) and [encode/django-rest-framework#6938](https://github.com/encode/django-rest-framework/issues/6938); [django-scopes](https://pypi.org/project/django-scopes/2.1.0/); [django-organizations](https://pypi.org/project/django-organizations/2.3.0/) | Every id-taking route re-checks the object (BOLA); explicit allow-list serializers and no mass assignment (BOPLA); a `PrimaryKeyRelatedField` validates against the queryset it is given, and a reverse-relation queryset has been reported ignored; automatic tenant scoping and Postgres RLS stop "forgot one filter" bugs; a membership row holding per-member attributes is the established pattern | I did not fetch these myself (taken from the review, whose sources are secondary): BOLA/BOPLA map to the M-rows and §5.3, and the related-field point is why teacher ids are plain input (§4.2). django-scopes/RLS **do not fit now** (tenancy here is per role and per relation, H-38, not one tenant column; a retrofit is its own epic), and the AST sweep plus the differential give most of the protection at F1's size. django-organizations is not adopted: our admin is a school role, not a member flag |
| Soft vs hard delete | none found that applies | | Judgment from this codebase: audit rows are value-capturing and survive any delete; soft delete adds a filter every query must remember, the opposite of what a P0-averse design wants |

Search quality was modest (mostly secondary sources); I used them for direction and verified each against the code.

---

## 15. Cross-review of the independent review

Reviewer: `task/epic-f1-review` at `9122caf2d`, `INDEPENDENT_REVIEW.md`. Verdicts are AGREE, DISAGREE or PARTLY. Every code claim I relied on in a verdict I re-checked in the tree (listed where it matters). The five points the SM asked about come first.

### 15a. Liveness (your point a): the review is right, draft 1 was wrong. **SUPERSEDED by the owner's Q1 answer (§5.2, §15k): departments now keep working through a lapse; the replacement and removal cut-offs remain.**

**What a lapsed school's teacher sees today, from the code (not guessed):**

| Surface | Rule | A teacher on a lapsed or cancelled license (`school` set, allocation active, license inactive) |
|---|---|---|
| Their existing courses, read and write | `CourseViewSet.get_queryset` -> `teacher_course_access_q` -> `user.school_id == session.school_id` (`classrooms/models.py:252`, `classrooms/views.py:1405`) | **Still reachable.** H-38 deliberately keys on `school_id` |
| The school's session list | `SessionViewSet.get_queryset` -> `user.is_under_license()` (`classrooms/views.py:2081`) | **Gone**: falls back to INDIVIDUAL sessions only |
| Creating a course in a school session | `CourseSerializer.validate_session` -> `not user.is_under_license()` refuses (`classrooms/serializers.py:214`) | **Refused** |
| Session write rights | `CanManageSession` -> `is_under_license()` (`classrooms/permissions.py:139,161,180`) | Treated as individual-track |
| Credit-refusal wording | `billing/errors.py` keyed on `is_under_license()` | Individual wording |

So the code already treats such a teacher as **individual-track for everything that is a shared or license-defined resource**, and as an owner only for objects they already hold. A Department is the first kind. BE-G-08 ("Individual Teachers have no Department and no Shared Library... enforce server-side") and BE-F-02 ("members are selected from the Licensed Teachers on that License") point the same way. **Verdict: AGREE.** The predicate is `school_id` equality **plus** an active non-admin allocation on an active license of that school (and an active school), checked at request time on every department read and write. Admins are deliberately not blocked by a license lapse, so they can renew and manage; the review proposed the same (its 2.6).

**For FR-F-10 and BE-F-12:** FR-F-10 is not violated by either predicate (neither exposes another teacher's data), but fail-closed is the safer reading of a P0 boundary on a brand-new surface. BE-F-12 ("a teacher who leaves the License loses access immediately; content stays") is satisfied by the reap on removal, and the predicate makes a *lapse* behave the same way without a delete. **Is it an owner call?** The spec decides it (BE-G-08, BE-F-02), so it is a confirmation, not a choice: OWNER_SHEET Q1 is reworded as "access pauses on a lapse and returns on renewal", with the old alternative kept as option 2 so the owner can overrule. The inconsistency between the courses rule and the sessions rule is a pre-existing product quirk, recorded as risk 6a, not F1's to fix.

### 15b. The nested `CustomUserSerializer` (your point b): closed exactly, with more closures than draft 1

Confirmed in the tree: `users/serializers.py:71` has `credit_wallet = CreditWalletSerializer(read_only=True)` and the serializer also nests `settings`. Draft 1 already had an explicit `fields` tuple and an AST guard against "nested user/course/wallet serializers", but the guard text was loose. **Verdict: AGREE and strengthened (§5.3):** (1) no import from any other app's serializers in `departments/serializers.py`, AST-enforced; (2) explicit `fields` on every serializer; (3) `.values()`/`.only()` of exactly the displayed columns so the wallet is never loaded; (4) exact key-set equality plus the canary test. The review's plant (nest the user serializer into the roster) replaces my "add an extra field" plant as P2 because it is the realistic mistake.

### 15c. Where the reap lives, and lock order (your point c): both, one function

**Decision (§6.2): one idempotent `end_memberships()` called explicitly inside `remove_teacher_from_license` and by an atomic `CustomUser.save()` override** (not a signal receiver; see §6.2). Why not explicit-only as the review proposes: I found two other writers of `teacher.school`: the users API (`CustomUserSerializer.school` is a writable `PrimaryKeyRelatedField`; the source comment calls detaching a school "a real operation") and Django admin. An explicit call in the license service cannot see them, and a later re-attachment would revive stale rows (finding 3). Why not receiver-only (my draft-1 lean): a `post_save` receiver runs after the UPDATE has committed in autocommit, so it cannot fail closed (verified, 15i point 2), and the required reap would hide in a signal in the middle of a money path. **Double-reaping:** the function deletes what exists and emits one event per deleted row, so a second call finds nothing and writes nothing (test T8 asserts exactly one event per membership).
**Lock order, checked against `billing/locks.py`:** the existing order is licence, wallets by id, buckets; `remove_teacher_from_license` takes the allocation row, then writes the teacher row, then `lock_wallet_first`. The F1 order is **allocation, teacher, department, membership, then wallet and buckets (unchanged)**. The reap goes right after the allocation is deactivated and **before `teacher.save` and the wallet lock**, never between wallet and bucket locks, as the review also required. **A flaw this exposed in draft 1:** the add path locked only the teacher row, so add and remove could interleave after the allocation check; add now takes the allocation `FOR SHARE` first. **Verdict: PARTLY** (agree on the in-transaction explicit call and the ordering; disagree that nothing else is needed).

### 15d. M1-M20 vs the invariance differential (your point d): merged, no gaps

All twenty rows are adopted. They are **absolute** tests (named failure per resource, and they would catch a path that was already leaky); my differential (D0) is **relative** but **complete** (catches a widened rule on a route nobody wrote a row for, and covers F2/D routes later). §7.2 is the single plan. Mapping: M1-M11 and M12-M15 as written (route references re-checked against the tree: `CourseViewSet` classrooms/views.py:1302, `StudentCourseViewSet` :2311, `CustomUserViewSet` users/views.py:367, `StudentSubmissionViewSet` students/views.py:284, `TaskViewSet` users/views.py:2820, `AssignmentViewSet` assignments/views.py:316, `CreditWalletViewSet` billing/views.py:426, `SchoolCreditAllocationViewSet` billing/license_views.py:1202); M16 (notifications) becomes an AST guard that `departments/` has no task or mail; M17 (Django admin) becomes registration rules and a test; M18, M19, M20 = T3/T4, T13, T19. My rows that the review lacks: T8-T11, T17, T20, T21 (lifecycle, lapse, revival, lock race, school moves), the roster canary, D0 itself, the registry guard and the student data map guard. The review's rows that I lacked: M16, M17, M14 (department filter parameters on existing lists), the self-unrestrict case (T13), the AST route sweep (adopted into §7.3). **Verdict: AGREE.**

### 15e. Delete choice at F1 vs C-2 (your point e)

C-2 was a **source attribution** (the choice is in 01a FR-F-12/D-08, not in the Backend PDF), not a proposal to leave it out, and draft 1 already put it in F1 (§8). So there is no scope disagreement. **Verdict: AGREE** that F1's DELETE takes the content choice now. Two refinements: (1) the choice is **conditional**: `content` is required only when registered content exists (409 `DEPARTMENT_CONTENT_CHOICE_REQUIRED`), so a bare DELETE of an empty department keeps working and later epics change behavior (a correct 409), not the API shape; (2) the review would have F1 accept only `delete`; I keep `reassign` too, implemented generically through the registry and tested against a test-only dummy content model, because the choice flow is the thing that must be right once and F2/D should inherit it, not write it. The sharper disagreement is audit: see 15f row 2.4.

### 15f. Everything else, finding by finding

| Review item | Verdict | Reason and effect |
|---|---|---|
| H1 anchor to `School` | AGREE | Same as my finding 1 |
| H2 membership alone must not grant access | **AGREE** | 15a |
| H3 roster serializer biggest leak risk | AGREE | 15b; the roster is `{name, role}`, no id |
| H4 cleanup inside license paths | PARTLY | 15c |
| H5 own app `departments` | AGREE | Same as D1 |
| H6 delete takes the choice | AGREE | 15e |
| M1-M20, route sweep, key-set tests | AGREE | 15d |
| Plants P1-P3 | PARTLY | Adopted the nested-serializer plant and the trust-the-row plant; kept the queryset plant; **did not fold the missing reap into one plant** (Gate 2 breaks one thing at a time; T9 catches a missing reap) |
| 2.1 removal | AGREE | In-transaction, one event per membership, §6.2 |
| 2.2 teacher in two departments | AGREE | Already in the design (flags per membership, no union endpoint) |
| 2.3 admin of another school / null school | AGREE | `none()` for null school, 404* for a foreign id (T1, §4.2) |
| 2.4 delete audits **one** DELETE event | **AGREE (changed my mind)** | FR-A-01 acceptance is exactly one event per listed action and the route-coverage sweep expects one per write route; membership history is reconstructable from earlier ADD/REMOVE events. §8/§9 changed. Leaving the license stays one REMOVE per department left |
| 2.5 flag racing an edit (`FOR SHARE` / `FOR UPDATE`) | AGREE | Adopted as the `can_member_edit(lock=True)` contract and T20, so D and F2 implement the denial once |
| 2.6 lapse: access suspended, admin keeps access | AGREE | 15a |
| 2.7 moving schools | AGREE | T21 |
| 2.8 session rollover | AGREE | T16 |
| 2.9 cache: `dpt` plus `usr`, bump on a colleague's name change | PARTLY | Agree on the bumps and that the permission is never cached (adopted: predicate before cache). Avoided the name-change coupling by **not caching the roster** (§10). If the roster is cached later, the bump is required |
| 2.10 school inactive | AGREE | Predicate includes school active; T17 |
| 2.11 any school admin | AGREE | §4.2, T18 |
| 2.12 `created_by_id`/`added_by_id` in the student data map | **AGREE, and my draft was wrong** | The guard (`students/tests_student_data_map.py:68-100`) fails the build for every user relation and bare `*_id` user column not in the map; draft 1 said "assert it holds no student data", which misses the required edit. In step 2 |
| Data model rows: membership table, flag on the membership, two flags, hard delete, `department_id` on every F1 event, normalized names | AGREE | Same as mine |
| Data model: `Department.admin_can_add_to_library` in F1 | PARTLY | Agree it must be **data**, not a hard-coded role rule (BE-F-08). Disagree that F1 adds the column: it is F2's schema, F2's owner question (CL-A5) is unresolved, and an unused column in F1 would be speculative; adding it in F2's migration is equally "a data change, not a code change" |
| Gap 1 License vs School | AGREE | C-4 |
| Gap 2 "Licensed Teacher" undefined | AGREE | Now defined (§5.2), G-1 closed |
| Gap 3 roster role; restricted status visibility | AGREE | G-8, G-21; roster stays `{name, role}` |
| Gap 4 one or two flags | AGREE | D3, Q2 |
| Gap 5 license lapse unspecified | AGREE | Decided from BE-G-08; Q1 confirms |
| Gap 6 which admins; Super Admin read-only | PARTLY | Any admin of the school: agree. Super Admin: the review recommends read-only plus audit; I recommend **no API access** (Django admin for support). A read-only super-admin route is another surface with its own serializer and matrix rows, and BE says nothing about it. Owner Q7 offers the choice |
| Gap 7 five-item exposure list | AGREE | C-3 |
| Gap 8 delete choice from D-08 | AGREE | C-2, 15e |
| Gap 9 "exactly one event" vs cascades | AGREE | Resolved as in 2.4 |
| Gap 10 PERMISSION_CHANGE allow-list and "new target labels need owner approval" | AGREE (changed in draft 2) | Allow-list: already in §9. Labels: I called them conditional; the challenge pointed at `_TARGET_LABEL` and the test that fails without an entry, so they are required (W7, W8) |
| Gap 11 roster is the sanctioned exception | AGREE | Stated in §4.3 |
| Risk: migrations, license-service lock order, predicate cost, null-school guard, student data map, route coverage, H-1 stage 3 | AGREE | All covered (§3.4, §6.2, §5.2/§10, §4.2, §7.3, §0 finding 6) |
| URL layout `/school-admin/departments` (admin) and `/departments` (teacher) | **AGREE, adopted** | Matches the existing prefixes in `audit/urls.py` and `dashboard/urls.py`; replaces my `mine/` paths (§4) |
| `is_licensed_teacher_of(user, school_id)` as the named predicate | AGREE | Adopted as the helper name (§5.1) |
| Research (OWASP BOLA/BOPLA, DRF related fields, django-scopes/RLS, django-organizations, authorization cache invalidation) | AGREE | In §14. RLS/django-scopes do not fit now, and the review reached the same conclusion; I did not independently fetch those pages |

### 15g. What I still disagreed with: all six AGREED by the Fixer (`97153680c`); resolved, kept as history

1. **Reap mechanism.** The review wants explicit-only; the challenge wants a receiver with a skip flag. I keep an explicit call plus an atomic `CustomUser.save()` override (15c, 15i point 2): the users API and Django admin also write `school`, and only an override can fail closed.
2. (Superseded by owner Q1) **Liveness uses the allocation, not "the school has an active license".** An active non-admin allocation on an active license of the school, because a license replacement leaves a teacher with `school_id` and an allocation on an *inactive* license (15i point 1).
3. **Super Admin read-only API** (review gap 6): I would give none; Django admin is the support path.
4. **`Department.admin_can_add_to_library` column in F1** (review data model): belongs to F2's schema.
5. **F1 delete accepts `reassign` as well as `delete`** (review 15e): built once through the registry, tested on a dummy model.
6. **One plant per break** (review): a missing reap is not folded into the liveness plant; T9 catches it.

Everything else in both documents is adopted (15f, 15i).

### 15h. What the review missed that draft 2 keeps

The revival hazard as a named risk, the add-side allocation lock and the full lock order, the `usr` bump needed for a *new* membership to refresh "my departments", the invariance differential and registry guard, the writable `school` field in the users API as a second writer, the reason-code and wording lists, the flag and E2/C1 interplay, the Drive/Shared-Drive evidence for department-owned content, and the estimate.

### 15i. The challenge (`CHALLENGE_OF_LEAD_PLAN.md`, 08be30681), point by point

| # | Challenge | Verdict | Verified facts and effect |
|---|---|---|---|
| 1 | Liveness should be `school_id` plus an active school license; Q1 option 2 is inaccurate | **PARTLY** | **Agree** `school_id` alone was wrong and that Q1 option 2 misdescribed the code (a lapse, cancel or renewal failure leaves `school` and the allocation untouched; an OFFLINE license is never auto-deactivated, `billing/tasks.py:184`; a Stripe cancel keeps `is_active` to period end). Q1 is reworded neutrally. **Disagree** with "the school has an active license": license replacement (`license_service.py:1123`) sets the old license inactive and enrols only the teachers passed, so a left-out teacher keeps `school_id` and an allocation on an inactive license. The allocation-based test (the `is_under_license()` definition) is exact; a school-level test would let that teacher in. §5.2 |
| 2 | The reap should fail closed | **AGREE** | Checked in Django 5.2: `Model.save_base` does not open a transaction for a model without multi-table parents, so `post_save` runs after the commit and a receiver cannot undo it. Fixed by an atomic `save()` override (§6.2); a reap error aborts the change. Safety-net command dropped |
| 3 | Lock order: `remove_teacher_from_license` locks the allocation, not the teacher row | **AGREE** | Checked (`license_service.py` ~1961-2060): it locks the allocation, deactivates it, clears `school`, then takes `lock_wallet_first`. Draft 1 claimed a teacher-row lock that does not exist. The order is now allocation, teacher, department, membership, then wallets and buckets as `billing/locks.py` has it (§6.2) |
| 4 | Delete audit: one event with capped `member_ids` | **AGREE** | One DEPARTMENT_DELETE, `member_count` plus `member_ids` capped at 50 and a truncation flag (§8, §9) |
| 5 | Estimate 13-14 days | **AGREE** | 14 days, range 13-15 (§12) |
| 6 | Cut avoidable wording; add target labels and remediation hints | **AGREE** | The sheet drops 10 texts (the old W3, W5, W6, W13-W19), reusing house messages; target labels W7/W8 are required; each new code gets a remediation hint (T23). The one cap that stays is a 500-member bound (T22) |
| 7 | Flag default ON per the owner's 2026-11-18 rule | **AGREE** | `default=not _RUNNING_TESTS` (§11, D12); the old "defaults off" test is replaced by one that pins the default |
| H1 | Differential passes vacuously under `UserCacheMixin` and with only the user's own ids | **AGREE** | Hardened in five ways (§7.2 D0) |
| H2 | Registry guard misses bare `department_id` UUID fields | **AGREE** | The guard scans non-FK `department_id` fields with a test-only dummy (§7.3) |

### 15k. The owner's answers (2026-11-19) and what changed

Q1 changed: no pause on a lapse; the predicate is now an allocation on the school's newest license, active or not (§5.2); T10, plant P3, §6.1 rows 3 and 3b, the add rule, §10 and Q10 updated. Q2 changed: one `can_edit` flag (§3.3, §4, §5.3, §9, §13). Q3 yes (admin shown as "School admin"). Q4 yes (admin must choose). Q5 no auto-return. Q6 internal tools only. Q7 any admin of that school. Q8 changed: emails (§9b, M16, T27, step 8). Q9 no duplicates. Q10 same as today (now moot for access). Estimate 15 days.

### 15j. The Fixer's final check (`CHALLENGE_DRAFT2.md`, 97153680c)

All six remaining points: AGREED. Three tightenings, all ADOPTED: **R1** reap by state, not by diff (§6.2 path 2, T25); **R2** act only when the save writes the field, and skip the user-row lock when the stored school is null (§6.2, T26); **R3** wording: a lapse suspends access only when the license row is inactive; an OFFLINE license is never deactivated by the renewal sweep (`billing/tasks.py:179-185`, verified), so an ended offline contract keeps access until an admin deactivates it, same as sessions (§6.1 row 3, T10, owner Q10 with a recommendation). Carry-over list for F2 added in §3.5.

---

## 16. Risks

1. **Revival of a stale membership (finding 3).** Mitigation: read-time liveness, fail-closed `end_memberships()` (explicit call plus atomic save override), T9, plant P3.
2. **A future widening of an existing rule** (e.g. "teachers in my department" in a course query). Mitigation: invariance differential, sweep guard, registry guard.
3. **The `CustomUser.save()` override on a hot path.** It exits immediately when neither `school` nor `user_type` changed; when one did, a reap failure **does** abort the save (fail closed, by design); tested, including Django admin and the users API.
4. **Quiet failure of cache keys shared between viewers.** Key always includes the viewer id; test with two viewers.
5. **Staleness of the "my departments" key after add** (finding in §10). Mitigation: `usr` bump; test.
6. **Audit metadata silently dropped** (finding 5). Mitigation: audit-allow-list tests that assert the stored metadata, not only that an event exists.
6a. **Two existing definitions of "on a license"** (courses by `school_id`, sessions and course creation by `is_under_license()`). After the owner's Q1, departments follow neither exactly: they keep working through a lapse (like courses) but are cut off for a teacher left off a replacement license (like sessions). F1 neither causes nor fixes the older inconsistency; the SM may want it noted as a hardening item.
6b. **A new outbound email path** (owner Q8): volume per admin action up to 50; failure is isolated and tested (T27); the address is never logged.
7. **Doc drift:** 03a says `classrooms`, `Tag.department CASCADE`; this plan says `departments`, `PROTECT`. SM to confirm and amend 03a once.
8. **Scope creep into D/F2.** The line in §1; the registry is how they join.
9. **Roster `role` and key ambiguity** (gaps G-8, G-9) could force a serializer change late; settle before step 4.
10. **No production data queried.** No live database was touched; F1 has no backfill so none is needed.
