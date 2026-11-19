# F1: spec conflicts and gaps

Rule used for conflicts: the Backend Requirements PDF (founder's document) binds over 01a, 03, 03a and 05, which were derived from it (01a D-03). "Plan" points at STEP0_PLAN.md.

## Conflicts

| # | Conflict | Binds | Handling |
|---|---|---|---|
| C-1 | **CL-A5 vs BE-F-08**: checklist says teachers submit to the shared library and the admin approves; BE-F-08 says members add directly and the admin may not create | BE-F-08 (01a D-03) | F2 only. F1 neither adds approval columns nor hard-codes "admin cannot" (its edit helper takes a surface argument). The founder item is still open in 05 §4 (#1) and blocks the F2 schema, not F1 |
| C-2 | **BE-F-12 vs FR-F-12/D-08**: BE-F-12 covers only a teacher removed from a department or leaving the License. 01a adds "department deletion requires delete-with-content or reassign" | BE for the core; 01a's addition kept | Plan §8. Owner Q4 confirms |
| C-3 | **Forbidden-exposure list**: §5.2 lists Courses, rosters, submissions, grades. BE-F-10 adds credit balance. 01a FR-F-10 lists five | Superset (five) | Plan §5, §7 |
| C-4 | **License id vs school id**: BE-A-02, 03 AuditEvent say License id; code stores `school_id` on `AuditEvent` | Code/tenancy = `School` | 03a open item 2 answered in the plan (finding 1). Doc fix only |
| C-5 | **03a `Tag.department` is CASCADE**, but 03a says deletion is resolved by BE-F-12 (PROTECT). Cascade would silently untag assignments on department delete | BE-F-12 intent | Recommend PROTECT plus the content registry; C2 to adopt. Needs a 03a amendment |
| C-6 | **FR-G-07 vs FR-G-08**: a teacher in no department gets 200 empty; an individual teacher "has no Department" and must be denied without disclosure | Both, by route | List: 200 empty for any teacher (discloses nothing). Detail/roster: 404. Plan §4.1 |
| C-8 | **FR-A-01 "exactly one event per action" vs one event per teacher on delete**: draft 1 logged N+1 events for a department delete | FR-A-01 | One DEPARTMENT_DELETE event with counts and capped `member_ids`; leaving the license logs one REMOVE per department left. Plan §8, §9 |
| C-9 | **BE-F-02/BE-G-08 vs `school_id`-only access** (the H-38 course rule): a lapsed teacher keeps own courses by `school_id` but loses sessions by `is_under_license()` | BE-F-02, BE-G-08: active allocation on an active license | Plan §5.2. The owner chose to keep departments running through a lapse (Q1), overriding BE-F-02/BE-G-08's strict reading; the replacement and removal cut-offs remain. The courses-vs-sessions inconsistency is pre-existing |
| C-7 | **A5 naming**: BE doc names it "A5" (assumptions table), 05 and 01a call it "BE-A5" | n/a | Naming only |

## Gaps (the spec doesn't decide)

| # | Gap | Plan's default | Needs |
|---|---|---|---|
| G-1 | Who is a "Licensed Teacher" precisely? | **Closed (owner Q1, draft 4):** `school_id` plus an active non-admin allocation on the school's newest license (active or not) plus an active school, checked on every read and write | Done |
| G-2 | What happens to departments when a license lapses/cancels (only removal clears `school`) | Nothing changes on a lapse (owner Q1); a teacher left off a replacement license is cut off | Done |
| G-3 | Re-adding a teacher who left | No automatic return | Owner Q5 |
| G-4 | Flag shape: one or two (BE-F-07 vs 03a) | ONE `can_edit` flag (owner Q2) | Done |
| G-5 | Display of a departed author's name (FR-F-06, FR-F-12) | Name stays | Deferred to D (no content in F1) |
| G-6 | Whether the admin appears on the roster | Yes, role "School admin" | Owner Q3 |
| G-7 | Super Admin's role on departments (FR-F-02 says only School Admin) | No API access; internal admin tools only | Owner Q6 |
| G-8 | Values of the roster `role` field (FR-G-02) | "School admin" / "Teacher" | Owner Q3, SM |
| G-9 | FR-G-02 says no field beyond name and role, but a client needs a row key | Literal: only `{name, role}`, stable order; the front end keys by position | SM / front end. If a key is wanted, an opaque membership id is the smallest addition and needs an explicit exception to the acceptance wording |
| G-10 | Status for teacher join/leave/create attempts (FR-G-01 says "denied") | No such routes; admin routes answer teachers 403 | SM |
| G-11 | Limits on departments per school and members per department | No department cap; 500-member safety bound with `DEPARTMENT_TOO_MANY_TEACHERS` | SM (no owner wording beyond W6) |
| G-12 | Notification to a teacher on placement change | Email on admin add and admin remove (owner Q8) | Done; wording W11, W12 |
| G-13 | Department name rules (uniqueness, case, length) | Normalized, unique per school, 100 chars | Owner Q9 |
| G-14 | Whether a department's `description` exists, and ordering of lists | Optional description; ordered by name | Low; SM |
| G-15 | What a department's audit event may record (names?) | Record name in before/after, fallback ids only; delete event carries capped `member_ids` | SM (plan D11) |
| G-16 | 05 Q5 ("`cachegen:dpt` before H-1 stage 3") | Moot: wildcard removal is done (H-1 step 4), guarded | SM to close in 05 |
| G-17 | `03_architecture.md` sections cited by NFR-PRF-06 and NFR-SEC-06 as "§3.4" and "§3.5" | I could not find sections with those numbers (the file is organized by Stage and Part); I used the NFR text itself | Doc fix |
| G-18 | Whether the existing `DEPARTMENT_*` and `PERMISSION_CHANGE` allow-lists were meant to stay empty/user-only | They are empty/user-only today, so any metadata is dropped | Plan §9 extends them |
| G-19 | Where code lives (03a says `classrooms`) | New `departments` app | SM (plan D1) |
| G-20 | Authorship as FK vs captured value (03 Stage 4 says decide before the schema) | Captured value (03a), so removal and account deletion cannot null or cascade it | Confirm |
| G-21 | Is membership shown to other members as a flag (restricted or not)? | No: only the person's own flags (in `mine`) and the admin see them | Owner Q2 context; SM |
| G-22 | Do the new `target_type` values need owner-approved labels? | Yes: `_TARGET_LABEL` is required by an existing test; W7, W8 | Owner |
| G-23 | Flag default | ON outside tests (owner rule 2026-11-18) | Done |
| G-24 | Which school admins | Any admin of the school | Owner Q7 |
