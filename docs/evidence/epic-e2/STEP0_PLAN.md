# E2 Step 0: grade categories and assignment copy (plan only)

SM brief 2026-11-12. Branch `task/epic-e2` from staging bcdd1c0c6. Sources: PHASE2_BREAKDOWN T7/T8, 05 §3.5, 03a §2.7/2.8/4.1/4.4, 01a FR-E-01..08/12/13, plan 09 (D1, D2, D4, §3.4), plan 11 §0.3. E3 (FeedbackRevision) is out. No product code.

## 1. Exists vs missing

| Area | On staging | Missing |
|---|---|---|
| Grade maths | `grade_service` (E1a) with its guard, oracle, 39 snapshots, budget and parity command; E1b skip | Categories, relative weight, excused, missing; D1 unification; recalculation when `total_points` changes (plan 09 §3.4 gap) |
| `CourseCategory` | `id` and `name` only, linked to nothing; the viewset is **unrouted** and broken (H-6); reachable only through Django admin | Course link, weight, position. Production row count unknown. |
| Templates, `Assignment.category`/`relative_weight`/`grade_level` | none | all (grade_level: Q8) |
| Excused / missing | "Missing" exists only as the H-237 at-risk rule. Non-submitters have **no submission row**. | An "excused" record; a stated treatment |
| Copy | `ASSIGNMENT_COPY` exists but nothing emits it; C1's `resolve_in_scope` waits for it; `zero_credit_scope` | single and bulk copy |

## 2. Migrations (all additive, one per app)

- **classrooms 0019:**
  - `CourseCategory` gains `course` (nullable, CASCADE), `weight_percent` (CHECK 0-100), `position`, `created_at`, and UNIQUE(course, name).
  - New `CategoryTemplate` per 03a §2.7.
  - `Course.missing_counts_as_zero`, default False (Q2).
- **assignments 0042:** `category` (SET_NULL) and `relative_weight` (default 1.00, CHECK > 0).
- **students 0042:** `ExcusedWork`(student, assignment, by, at; unique pair). It is a table, not a submission status, because a placeholder submission row would change every submission count. Missing work is derived (the H-237 rule, not graded, not excused) and never stored.
- **Later contract step:** `CourseCategory.course` becomes NOT NULL once Q7 is settled.

## 3. Maths (only in `grade_service`)

- **A course with no categories, no excuses and missing work not counted takes today's code path unchanged.** It is byte-identical under the E1a oracle, snapshots, budget and parity command.
- **Categories:**
  - Category grade = Σ(relative_weight × %) / Σ relative_weight.
  - Final grade = Σ(weight × category grade) / Σ weight over the categories with graded work (Q4).
  - Same clamp and half-up 2 dp as today.
- **Excused** work is left out of both the top and the bottom. **Missing** counts as 0 only per Q2. **Uncategorized** work per Q3.
- The staff grade, released grade (H-130), cross-course grade, breakdown (FR-E-13) and preview (FR-E-12) all share the same functions.
- **Recalculation (FR-E-07):** a change to categories, a weight, a relative weight, `total_points` or an excuse recomputes the course's final grades in one transaction under the course lock, off the hot path. It is audited with before and after values. No background task (D2).
- **D1 (Q1):** the plain-mean figures (P1-P8; 18 call lines today) move to the weighted formula, behind the flag.

## 4. API (dark: `GRADE_CATEGORIES_ENABLED`, `ASSIGNMENT_COPY_ENABLED`; 404 after sign-in when off)

- **Categories:**
  - `GET/PUT /courses/{id}/categories`: PUT replaces the whole set. It must total 100 or be empty, under `select_for_update` on the course.
  - `.../categories/preview` (writes nothing).
  - `.../grade-breakdown?student=`.
  - `.../grading-policy`: the treatments, API-discoverable (FR-E-04/08).
- **`/category-templates`:** teachers manage their own; a school admin manages the school's. Course create takes `template_id`.
- **Assignment PATCH** gains `category` and `relative_weight`. **Excuse:** `POST/DELETE /assignments/{id}/excused`.
- **Copy:** `POST /assignments/{id}/copy {course_id}`; `POST /assignments/bulk-copy` (up to 50, Q9), with a per-item result and reason code (FR-A-07).
- **H-6:** the broken viewset is deleted.

## 5. Copy design

- **Tenancy:** a teacher only, and the source course and the destination both pass `teacher_can_reach_course` (H-38). Anything else gives 404 per item.
- **Carried:** title, instructions, `raw_input` with its hash, questions (the answer key and rubric live inside them), points, count, type, `self_assessment`, `custom_ai_prompt`.
- **Destination fields:**
  - Category: matched by normalized name in the destination, else uncategorized (X-6).
  - Relative weight: carried.
  - Tags: via `resolve_in_scope`.
- **Never:** submissions, grades, feedback, excuses, generation history, PDF cache, fingerprints, `topic`.
- **The copy lands as DRAFT, with no due date and auto-grade off,** so no post_save effect fires: the new-assignment email, reminders, and the auto-grade task, which spends credits.
- **Same course:** UNIQUE(course, title, hash) would refuse the copy, so the title gets "(copy)" or "(copy 2)".
- **Credits and audit:** `zero_credit_scope("assignment.copy")`; ASSIGNMENT_COPY per item.
- **Images** stay shared by address. There is no foreign key and deleting an assignment deletes no file, so NFR-REL-07 holds.

## 6. Risks

1. **Numbers moving for untouched courses.** The old path is kept. The oracle and snapshots run with the flags off and on, the budget is held, and plant P1 covers it. D1 moves dashboard numbers on purpose, once, only on Q1 = yes.
2. **Existing `CourseCategory` rows.** They are global and admin-made. The SM should run a read-only `SELECT count(*) FROM classrooms_coursecategory` on production before the build; I queried no live database. The migration leaves those rows course-less; Q7 decides.
3. **Silent exclusion** (FR-E-08) of uncategorized work or empty categories. It is stated in grading-policy and the breakdown (Q3, Q4).
4. **Copy tenancy** (plant P3) and **copy side effects** (a test checks nothing is scheduled or sent).
5. **Student data:** `ExcusedWork` joins the NFR-CMP-04 walk and the student data map guard.
6. **Hypothesis** (D4) is a new dev-only dependency for property tests. CI dependency audit; SM call.

## 7. Tests (first, red then green)

- **Maths:**
  - property tests: excused, missing, sums, idempotence;
  - FR-E-05 example;
  - breakdown sums to the final grade;
  - preview writes nothing;
  - each recalculation trigger, with the audit before and after.
- **No-change:** E1a suites with the flags off and on.
- **API:** the 100% error names the total; roles (teacher own, admin school, student none); flag off.
- **Copy:**
  - deep and independent;
  - no student rows;
  - zero credit (B0 helper);
  - category match, tags;
  - DRAFT, nothing scheduled;
  - same-course suffix;
  - bulk partial success;
  - a foreign source or destination gives 404.
- **Concurrency:** two weight PUTs at once leave one valid set (NFR-REL-09).
- **Guards:** route coverage, audit labels and allowlist, history guard, student data map, grade guard.
- **Plants (3):**
  - P1, a no-category course goes through the category maths;
  - P2, excused counts as 0;
  - P3, copy into an unreachable course succeeds.
- **One near run on the final tree:** classrooms, students, dashboard, assignments, tags, billing, users and guards67, with the flags unset. Expected count from code.

## 8. Steps and estimate (about 10 commits)

| # | Step | Days |
|---|---|---|
| 1 | Tests first, red | 1.5 |
| 2 | Migrations and models | 0.5 |
| 3 | Maths and recalculation triggers | 2 |
| 4 | Categories, templates, preview, breakdown, policy; H-6 delete | 1.5 |
| 5 | Excuse API | 0.5 |
| 6 | Single and bulk copy | 1.5 |
| 7 | D1 unification (Q1), snapshots re-recorded with the flag on only | 1 |
| 8 | Guards, plants, near run, NOTES | 1 |
| | **Total** | **9.5** |

05 §7:
- **Audit:** A1-A4 (owner sheet), general retention.
- **Data walk:** `ExcusedWork` joins the NFR-CMP-04 walk.
- **Gates:** 1-7 and 9 apply (9 for the new copy and template surface); Gate 8 runs on staging.
- **Migrations:** additive, with a contract step only for Q7.
- **Flags:** two dark flags.
- **Roles:** teacher (own reachable courses), school admin (school templates), student (none).
