# 05 — Epics B–I Roadmap

**Status:** planning document for Feature Lead review. No product code.
**Companion to:** [01a_requirements_specification.md](01a_requirements_specification.md),
[03_architecture.md](03_architecture.md), [03a_data_model.md](03a_data_model.md),
[04_epic_a_implementation_plan.md](04_epic_a_implementation_plan.md).
**Also read:** the three founder PDFs/docs in `docs/backend/phase 2/` (Backend
Requirements, Founder Approval Checklist, QA Testing Requirements).
**Audit-action facts** come from `audit/enums.py` on `task/epic-a-land`
(c75efcc), which is not yet on beta. Re-check when Epic A merges.
**Date:** 2026-09-26. The delivery window ends 2026-12-31, about 14 weeks out.

---

## 0. Conventions and one trap

**IDs.** `BE-x-nn`, `FE-x-nn`, `QA-x-nn` are the source-document IDs. `FR-x-nn`
and `NFR-*` are 01a's distilled IDs, so use them when writing plans.

**Two different "A1–A8" lists exist and they do not match.** The Backend
Requirements §6 assumptions and the Founder Approval Checklist §5 both number
their items A1–A8, but they are different lists:

| Topic | Backend doc | Founder Checklist |
|---|---|---|
| Category weights total 100% | BE-A1 | CL-A3 |
| More than one tag/Unit per item | BE-A2 | CL-A6 |
| Audit retention | BE-A6 | CL-A8 |
| Who publishes to the shared library | (none; BE-F-08 says members add, admin may not) | CL-A5 |
| Tag scope, Lesson ownership, per-member restriction, feedback-edit visibility, estimate-is-advisory | BE-A3, A4, A5, A7, A8 | (no equivalent) |
| LMS student auth, grade conflicts, archived-session freeze, protected-class subgroups | (not in Part 1) | CL-A1, A2, A4, A7 |

01a §6 uses the Backend numbering (`D-A1`…). QA-C-05 and QA-F-05 also use the
Backend numbering ("assumption A3", "assumption A5"). To stop the two lists
being confused, this document writes **BE-A#** and **CL-A#** everywhere.
Neither is ever written bare.

**Sizes.** One engineer, calendar weeks, including tests and the evidence doc
but excluding independent verification:
S ≤ 1 wk · M 1.5–2.5 wk · L ≥ 3 wk. Week figures start from 03_architecture
Part V and are adjusted where §2 below found extra work.

---

## 1. Summary

| Epic | Title | Size | FRs | New tables (03a) | Hard prerequisites | Notes |
|---|---|---|---|---|---|---|
| B | Credits | **L** (3.5 wk; split B0 + B1, §5) | FR-B-01…11 | `AIJob`; `CreditRequest` cond. | A | Only epic that changes an existing hot path |
| C | Tags | **M** (2.5 wk; delivered in three slices, §2.1) | FR-C-01…09 | `Tag`, `AssignmentTag`, `LessonTag` | B0 | FKs to F and D arrive in those epics |
| D | Lessons | **L** (2.5–3 wk after cuts) | FR-D-01…10 | `Lesson`; `LessonVersion` cond. | B1, C1, F1 | Owns the retrieval interface |
| E | Assignment updates | **L** (3.5 wk; split E1–E4, §3.5) | FR-E-01…13 | `CategoryTemplate`, `FeedbackRevision`, repair `CourseCategory` | B0; E4 needs D | Changes live grade maths |
| F | Departments: School Admin | **L** (3 wk) | FR-F-01…13 | `Department`, `DepartmentMembership`, `SharedLibraryEntry` | A; F2 needs E copy | Fourth tenancy dimension; P0 gate |
| G | Departments: Licensed Teacher | **S** (1.5 wk) | FR-G-01…08 | none | F | Read surface over F |
| H | School Admin AI insights | **L** (4 wk) | FR-H-01…09 | `Insight`, `Intervention` | B, C, D, E, F | **Recommended for Part 2** |
| I | Grading strictness | **L** as a whole (I-1 ~2 wk + I-2 ~2 wk; see [07](07_epic_i1_implementation_plan.md)) | FR-I-01…09 | `SubmissionGrading` | BE-I-04 first; E1 | Largest schema change |

Recommended scope (01a D-09): **defer H**, and defer BE-D-09 DOCX, BE-D-07
version history and BE-C-05 merge to fast-follows. That leaves about 19–20
engineer-weeks (§6).

---

## 2. Cross-epic findings that change the order

These came from reading 03a table-by-table against the build order in
03_architecture Part I. They are new. Each needs a Feature Lead decision.

### 2.1 Tag ↔ Lesson ↔ Department form a foreign-key cycle

`Tag.department → Department` (F), `Tag.lesson → Lesson` (D), and
`Lesson.department → Department` (F) (03a §2.3, §2.6). But 03_architecture
runs C at Stage 3, before F and D. C therefore cannot ship the full `Tag`
table as specified.

**Proposal.** Land `Tag` in three additive slices.

| Slice | Ships in | Contents |
|---|---|---|
| C1 | Epic C | `Tag` with `owner_type = TEACHER` only, types `SUBJECT`/`UNIT`, `AssignmentTag`, normalisation, `Topic` backfill, tag audit events |
| C2 | Epic F | Add nullable `department` FK; widen the owner CHECK to allow `DEPARTMENT`; department-scoped autocomplete (FR-C-02, BE-A3) |
| C3 | Epic D | `Lesson` + `LessonTag`; add nullable `tag.lesson` FK and the `LESSON` type; `PROTECT` and delete-handling (FR-C-03); the `LESSON`-tag CHECK |

Adding nullable FKs is additive under `docs/MIGRATIONS.md`. Changing a CHECK
is not risky here, but the migration must be classified. FR-C-07 (analytics
dimensions) is delivered after E1 and C3, because it needs the grade service
and the Lesson dimension.

### 2.2 `FeedbackRevision` points at a table that Epic I creates

03a §2.13 makes `FeedbackRevision.grading` an FK to `SubmissionGrading`, and
§2.12 makes that table the largest change in Epic I. But 03_architecture puts
E (feedback pair) before I, and `10 Gates`/R-2 want the grade service landed
with no behaviour change first.

**Options for the Feature Lead.**

| Option | Effect |
|---|---|
| (a) Pull the `SubmissionGrading` *expand* step (create table, backfill, dual-write) forward with BE-I-04 in Stage 1, and leave readers and contract for Epic I | Matches 03a's migration step 1 and lets E3 and BE-I-04 share one schema. **Recommended.** Costs about 1 wk earlier and makes BE-I-04 slightly larger |
| (b) `FeedbackRevision` keys to `StudentSubmission` now and is re-pointed in I | Two migrations on a training-signal table; the strictness/prompt-version columns QA-ACC-13 needs would not exist yet |

On beta today there is no `prompt_version`, `config_version`,
`SubmissionGrading` or strictness field anywhere in `*.py` (grep, 2026-09-26),
so BE-I-04 has not landed. Whether it is in flight elsewhere needs confirming
(see §8, Q1).

### 2.3 FR-E-10 needs Epic D's retrieval interface, but E is built before D

BE-E-09 says to pass the Lesson "through the same retrieval interface as
BE-D-05". D comes after E. FR-E-10 and FR-E-11 (Lesson tag on assignments)
therefore split out of E (as E4) and run after D and C3.

### 2.4 Only a small part of Epic B is a prerequisite for C, E and F

03_architecture serialises B → C/E because "zero-credit assertions live in B".
That is true only of `zero_credit_scope()` (FR-B-10). Reservation, `AIJob`,
the queued state and dispersal are needed only by AI-generation paths
(D-03, E-10, H) and by nothing in C, F, G or E1–E3.

**Proposal.** Split B:

- **B0 (S, ~0.5 wk):** `zero_credit_scope()` in the metering layer;
  delete the unused `get_ai_model_function()` (FR-B-01, at
  `ai_processor/services.py:729` on beta) with a "no other call path" test.
  This unblocks C, E and F.
- **B1 (L, ~3 wk):** estimation band, verdict, reservation, `AIJob`,
  queued-pending-credits, dispersal, `CreditRequest` if needed.

The trade-off is that B1 and E1 both touch the grading job path
(`assignments/tasks.py`, `students/task_tracking.py`), so they need explicit
file-ownership and a rebase agreement. 03_architecture R-4 applies. If the
Feature Lead prefers zero contention, keep B strictly serial and add
~3 wk to the critical path.

### 2.5 Audit-action gaps

Epic A's enum was written ahead of these epics and is close, but incomplete.
Verified against `audit/enums.py` on `task/epic-a-land`. **Present and
dormant:** `ASSIGNMENT_COPY`, `LESSON_CREATE/UPDATE/DELETE`,
`TAG_CREATE/RENAME/DELETE`, `DEPARTMENT_CREATE/UPDATE/DELETE`,
`DEPARTMENT_MEMBER_ADD/REMOVE`, `LIBRARY_ADD/EDIT/COPY`, `DATA_EXPORT`,
`PERMISSION_CHANGE`, `CREDIT_TRANSACTION`.

**Not in the enum, needed by these epics:**

| Missing action | Needed by | Requirement |
|---|---|---|
| `LESSON_COPY` | D | FR-D-06, FR-A-01 lists lesson copy under "copy" |
| `TAG_MERGE` | C (fast-follow) | FR-C-05 |
| `LIBRARY_DELETE` | F | Removing an entry is a library action FR-A-01 covers ("add / edit / copy" only; delete needs a decision) |
| Category/weight change (with `before`/`after`) | E | FR-E-07 requires it audit-logged; today's `ASSIGNMENT_UPDATE` does not cover a Course-level weight |
| `FEEDBACK_EDIT` / `FEEDBACK_REVERT` | E | FR-E-09 |
| `AIJOB_QUEUED` / `AIJOB_EXPIRED` (or equivalent) | B | FR-B-05 says queued work needs a lifetime and notification; nothing records that transition |
| `INSIGHT_GENERATE`, `INTERVENTION_DISPOSE` | H | FR-H-06/08, deferred with H |
| `LIBRARY_APPROVE/REJECT` | F, conditional | Only if CL-A5 approval holds |

Adding an enum value is a coordinated change and touches the
`retention_class_for` mapping (04 §3.2), so each epic plan should list the
values it adds. **Feature Lead to decide whether each epic adds its own or
one small "audit vocabulary" task lands them all.**

**Data export and permission change have no owning epic.**

- `PERMISSION_CHANGE` fits F (FR-F-07 per-member flag changes).
- `DATA_EXPORT` has no producer: 04 §6 confirms no export endpoint exists.
  NFR-CMP-04 (deletion/export "for every student record type introduced in
  Part 1") is owned by nobody. The new student-linked tables
  (`SubmissionGrading`, `FeedbackRevision`, item rows) must participate. I
  recommend it is treated as a Security or Platform task, with each epic's
  plan stating which tables it adds to the deletion/export walk. Raised in
  §8, Q4.

### 2.6 The library-copy shape blocks F and touches the hottest table

03a §2.11 / 03_architecture §9.12a: `Assignment.course` is NOT NULL, and a
library entry has no Course. The chosen shape (nullable `course` + CHECK, or a
content-bearing entry) is undecided and goes to the Decision Request.
Making `course` nullable touches the most-queried table and every
`assignment.course.<x>` access must be swept. F2 cannot start until this is
decided; F1 does not need it. `Assignment.grade_level` also does not exist on
beta (grep of `assignments/models.py`), although BE-E-01 says a copy carries
it; E2 must add it (03a §8 item 5).

---

## 3. Per-epic detail

### 3.1 Epic B — Credits · **L**

**Scope.** Central metering already holds (`execute_graded_task` is the only
chokepoint). What is missing: a p50/p90 estimate and verdict (X-3),
reservation (today's path is check-then-execute), a durable queued state
(X-8), threshold warnings, dispersal, actual-cost exposure, and zero-credit
assertions.

**Requirements.** BE-B-01…09 → FR-B-01…11; NFR-CRD-01…05, NFR-REL-01/02,
NFR-TST-06. QA-B-01…07.

**Data model.** New `AIJob` (03a §2.2) with the state machine
`ESTIMATED → RESERVED → RUNNING → SETTLED | PARTIALLY_COMPLETE →
QUEUED_PENDING_CREDITS → …`; `BackgroundProcessingTask` gains
`job`, `reason_code`, `retry_count`, `item_index` (03a §4.5).
`CreditRequest` only if FR-B-11 needs a queue. Reservation is one conditional
UPDATE; never `select_for_update` across a network call.

**Depends on.** A (audit, reason codes for `INSUFFICIENT_CREDITS`).
**Audit actions.** `CREDIT_TRANSACTION` exists. Dispersal is a paired
debit/credit; the job-lifecycle gaps are in §2.5.
**Used by.** D (FR-D-03), E (FR-E-10), H (FR-H-07), I (regrade cost).

**Founder items (verbatim).**
- Checklist §5, unnumbered: *"What exactly happens when a teacher runs out of
  credits?"* — *"Queue or partially complete. Your public FAQ promises work is
  never blocked or lost, and the implementation must match that sentence."*
- BE-A8: *"Credit sufficiency estimation is advisory. A teacher may proceed
  past a 'you may not have enough credits' warning."* (impact: "a blocking
  estimate changes the job submission path").
- QA-ERR-02: *"Credits are not consumed for a failed grading call, or are
  refunded — confirm which with the founder and test it."*
- D-02 in 01a §6 (admin wallet: separate purchase pool vs. two pools) and D-04
  (credit request: queue vs. notification) are working defaults, not founder
  answers.

**Open risks.** Wall-clock behaviour of the reservation under real
concurrency must be re-confirmed on Postgres 16 (NFR-TST-11; local is 18.6).
The 03_architecture Stage 2 text has an unfinished sentence about where
uploaded files live during a queued state; it needs an answer before B1's
plan is written.

### 3.2 Epic C — Tags · **M**

**Scope.** Three tag types, reusable, normalised, multi-valued, copy-safe,
analytics dimensions, and migration of course-scoped `Topic`.

**Requirements.** BE-C-01…08 → FR-C-01…09; NFR-CMP-04, NFR-PRF-03.
QA-C-01…05.

**Data model.** `Tag`, `AssignmentTag`, `LessonTag` (03a §2.3–2.5), delivered
in the C1/C2/C3 slices of §2.1. `Topic` and `Assignment.topic` are left in
place for one release (expand), removed in a later contract step. **Row
counts of `Topic` / `Assignment.topic` are still unknown** (03_architecture
Part VIII #3) and size the backfill; that query must run against a
production-shaped database before C1's plan is written.

**Depends on.** B0 (tag operations are zero-credit, FR-B-10). C2 needs F;
C3 needs D.
**Audit actions.** `TAG_CREATE/RENAME/DELETE` exist. `TAG_MERGE` missing
(§2.5). Copy paths re-resolve tags by normalised name (FR-C-06); copies emit
the copying epic's action, not a tag action.

**Founder items.**
- BE-A2 / CL-A6: Checklist *"May an assignment carry more than one Unit?"* —
  *"Yes."* (working assumption; agrees with FR-C-04).
- BE-A3 (tag scope): *"Tags are teacher-scoped for Individual Teachers and
  Department-scoped (shared) for Licensed Teachers within a Department."*
  Impact "Medium — changes the tag data model and the reuse behaviour." The
  Frontend phrases it differently (suggestions draw from the teacher's history
  *and* the Department). 01a's default: both, teacher-private plus
  Department-shared.
- Deferral D-09 (01a): BE-C-05 merge to a fast-follow.

### 3.3 Epic D — Lessons · **L**

**Scope.** Session-independent `Lesson` entity, two creation paths, tagging,
copy, draft/published, last-edited attribution, search, and the retrieval
interface that E4 and H consume.

**Requirements.** BE-D-01…09 → FR-D-01…10; NFR-REL-08 (optimistic
concurrency), NFR-PRF-03 (400 lessons), NFR-CMP-03. QA-D-01…06.

**Data model.** `Lesson` (03a §2.6) with **no `session` and no `course`
column**; `LessonTag` and the `Tag.lesson` FK (C3). `LessonVersion` only if
BE-D-07 survives the cut. No embedding store; the retrieval interface is a
deterministic query over `content_text` + tags (NFR-MNT-02). **Must verify**
the `Assignment.raw_input` ProseMirror shape so `Lesson.content` matches
(03a §8 item 4).

**Depends on.** B1 (FR-D-03 generation metered through `AIJob`), C1 (tag
framework), F1 (`Lesson.department` FK and the department-ownership branch).
The user-owned half of D can start before F1 if `department` is added
additively, but the P0 authorisation matrix should not be split across
releases.

**Audit actions.** `LESSON_CREATE/UPDATE/DELETE` exist; `LESSON_COPY`
missing (§2.5). **Provenance:** AI-generated lessons carry the prompt
version via BE-I-04.

**Founder items.**
- BE-A4: *"A Lesson belongs to one owner (a teacher or a Department) and is
  not co-owned. Sharing is achieved by placing it in a Department, not by
  multiple ownership."*
- X-9 / D-09: DOCX export is a candidate external dependency; the Backend
  document says *"If you find an item here that does have an external
  dependency, raise it immediately, because it was placed in the wrong
  part."* Recommendation is PDF only, and ask whether the need is
  portability or editability.
- D-08 (01a): Department deletion offers delete-with-content or reassign.

### 3.4 Epic F — Departments: School Admin · **L**

**Scope.** `Department`, `DepartmentMembership` with per-member flags,
Department Lessons (via D), the Shared Assignment Library, audit, and the
BE-F-10 boundary.

**Requirements.** BE-F-01…12 → FR-F-01…13; NFR-SEC-01…06, NFR-PRF-06
(`cachegen:dpt`), NFR-TST-02/08. QA-F-01…07, QA-SEC-02…05, 07, 09.

**Data model.** `Department`, `DepartmentMembership`, `SharedLibraryEntry`
(03a §2.9–2.11); `Assignment` gains `department`, `last_edited_*`, and
(pending) nullable `course` (§2.6); `Tag.department` (C2). Approval columns
only if CL-A5 holds. `on_delete=PROTECT` from `Lesson.department` and
`SharedLibraryEntry.department` enforces BE-F-12 structurally.

**Slicing.** **F1** = Department, membership, flags, audit, `cachegen:dpt`,
the adversarial matrix (no library dependency). **F2** = Shared Library
(needs E2's deep-copy path and the §2.6 decision). Doing F1 early moves the
P0 gate forward.

**Depends on.** A (department audit fields exist), B0. **Cross-stream:**
NFR-PRF-06 requires `cachegen:dpt` *before* H-1 stage 3; the Platform
Hardening stream currently has `task/h1-stage3-wildcard-removal` open. That
sequencing must be agreed with them (§8, Q5).

**Audit actions.** `DEPARTMENT_CREATE/UPDATE/DELETE`,
`DEPARTMENT_MEMBER_ADD/REMOVE`, `PERMISSION_CHANGE` (per-member flag with
before/after), `LIBRARY_ADD/EDIT/COPY` all exist. `LIBRARY_DELETE` and any
approval actions are missing (§2.5).

**Founder items.**
- Checklist CL-A5: *"Who may publish to the school shared library?"* —
  *"Licensed teachers submit; School Admin approves."* **Conflicts with
  BE-F-08**, where members add directly and admins **may not** create.
  01a's default: BE-F-08 binds; approval columns are specified as
  conditional (D-03). This is the single most consequential unresolved
  founder item for F.
- BE-A5: *"A School Admin may prohibit specific Department members from
  editing Department Lessons or the Shared Assignment Library, at the level
  of a per-member permission flag rather than per-item ACLs."* (impact
  "Medium — per-item ACLs are materially more work").
- BE-F-08 / D-09: *"do not model it as a hard-coded role prohibition that
  would be expensive to relax."*
- 01a D-07: nullable `course` vs. content-bearing entry (needs a
  founder/Feature Lead call, §2.6).

### 3.5 Epic E — Assignment updates · **L**

**Scope.** Copy and bulk copy, categories and weights, a single grade
service, deterministic recalculation, excused/missing treatment, feedback
edit pair, and lesson-grounded generation.

**Requirements.** BE-E-01…10 → FR-E-01…13; NFR-REL-06/07/09, NFR-PRF-07.
QA-E-01…07.

**Slicing** (one task each, in this order):

| Slice | Contents | Depends on |
|---|---|---|
| **E1** | Shared grade service absorbing all four call sites, **no behaviour change, proven byte-identical** (R-2), then recalculation out of the `post_save` signal into a coalesced task (X-7) and the H-5 `full_clean()` fix | B0 |
| **E2** | `CourseCategory` repair (H-6 resolves as "build it"), `CategoryTemplate`, `Assignment.category/relative_weight/grade_level`, weights-sum-to-100, excused/missing status; deep copy + bulk copy with X-6 category matching | E1 |
| **E3** | `FeedbackRevision` (FR-E-09), zero-credit | §2.2 decision, E1 |
| **E4** | FR-E-10 lesson-grounded generation, FR-E-11 lesson tag | D retrieval interface, C3 |

FR-E-12 (weight preview) and FR-E-13 (category breakdown) ride with E2.

**Data model.** As above (03a §2.7, §2.8, §2.13, §4.1, §4.4). Row count of
`CourseCategory` needs to be confirmed as zero (03a §2.8).

**Depends on.** B0 (zero-credit scope). **Audit actions.** `ASSIGNMENT_COPY`
exists. Bulk-copy per-item results also go to the audit log (FR-E-02).
Weight-change and feedback-edit actions are missing (§2.5).

**Founder items.**
- BE-A1 / CL-A3: *"Must category weights total exactly 100%?"* — *"Yes,
  enforced at save."*
- BE-A7: *"Teacher edits to AI feedback replace the text a student sees,
  while the original AI text is retained internally and is not
  student-visible."* **The Checklist appears to contradict this:** its
  unnumbered row reads *"Should teacher-editable AI feedback be pulled
  forward into Phase 2?"* — *"Currently deferred. Recommended for
  reconsideration — see the note in the requirements documents."* BE-E-08 is
  a Part 1 "must", so the two documents disagree on whether E3 is in Part 1.
  Needs a founder answer before E3 is planned.
- X-6 (category carry-over across Courses) and X-7 are architecture
  proposals needing sign-off, not founder items.

### 3.6 Epic G — Departments: Licensed Teacher · **S**

**Scope.** Read surfaces over F: my Departments, roster (name and role only),
Department Lessons and Library access subject to flags, zero-credit
independent copies, empty-state responses, server-side denial for Individual
Teachers.

**Requirements.** BE-G-01…08 → FR-G-01…08. QA-G-01…05, QA-SEC-06.
**Data model.** None. Roster serializer is an allow-list (NFR-SEC-06) and a
test asserts the exact field set.
**Depends on.** F (both slices for G-04/05/06), D for lesson access.
**Audit actions.** `LIBRARY_COPY` in both directions (emitted by the shared
copy service from E). No new values.
**Founder items.** None specific. Inherits BE-A5 and CL-A5 from F.

### 3.7 Epic H — School Admin AI insights · **L** · recommended for Part 2

**Scope.** Aggregate analytics for admins via a tool interface, the model
narrates and never computes, minimum group size, provenance, disposition.

**Requirements.** BE-H-01…09 → FR-H-01…09; NFR-CMP-01/03, NFR-MDL-02.
QA-H-01…06, QA-CMP-02, QA-CMP-05.

**Data model.** `Insight`, `Intervention` (03a §3.2–3.3), conditional.

**Depends on.** B, C, D, E, F and BE-I-04 simultaneously. Cannot start
before about week 10 of the plan under any staffing.

**Why not Part 1.**
- 03_architecture X-2: a per-query floor of five cannot satisfy QA-CMP-02
  (differencing attack); the honest fix is a finite set of pre-defined
  aggregation cells or noise/query-history, and that trade-off is the
  founder's.
- The Backend document itself calls it the *"Highest-risk epic. This epic
  touches student records, algorithmic recommendation and aggregate
  reporting at the same time... Where you are unsure, choose the more
  conservative option and raise it."*
- Checklist §3.1: *"Decide whether to de-identify student data before it
  reaches a model provider."* and the QA-owned provider-fields document are
  unresolved and gate H.

**Founder items.** De-identification (Checklist §3.1, item "Decide the
de-identification approach for model calls": *"Architectural — raise with
the backend engineer during Phase 2 design, not after."*); minimum group
size and the X-2 trade-off; deferral itself (01a D-09).

**Part 1 obligation even if deferred.** FR-H-04 / D-05 (Deferred): the
tool interface must be reusable by a teacher-scoped caller, and FR-D-05's
retrieval interface must be general enough for a second corpus (Deferred
D-02). D and E4 carry those.

### 3.8 Epic I — Grading strictness · **L** as a whole

**Scope.** BE-I-04 stamping (first), then strictness scale, versioned config,
three-scope precedence, regrade with an authoritative flag, confidence
signal, published/unpublished distinction.

**Requirements.** BE-I-01…08 → FR-I-01…09; NFR-MDL-05, NFR-TST-01.
QA-ACC-06…13, QA-I-01…04.

**Data model.** `SubmissionGrading` (03a §2.12), the largest schema change:
three-deploy expand → migrate readers → contract. Columns added to
`Assignment`, `Course`, and per-teacher settings (`Settings` vs `CustomUser`
is still unverified, 03a §8 item 6). Configuration content lives in code;
only a version string is stored (X-1).

**Slicing.** **I-1** = BE-I-04 + `SubmissionGrading` expand step
(Stage 1, §2.2 option (a)). **I-2** = strictness, regrade, migrating the four
readers via E1's service, contract step (~2 wk, after E1).

**Depends on.** E1 (one place for readers to change). BE-I-07 is already
implemented and only needs a recurring test.

**Audit actions.** `GRADING_REQUESTED/COMPLETED/FAILED` exist; regrade
reuses them. Student deletion/export participation for `SubmissionGrading`
(NFR-CMP-04) — see §2.5.

**Founder items.**
- Checklist §5, unnumbered: *"What variance is acceptable when the same
  submission is graded twice?"* — *"Undecided — QA cannot set an accuracy
  gate without it."* (NFR-MDL-05, QA-ACC-06.) Blocks the I-2 release gate,
  not the build.
- X-1: BE-I-02 ("versioned, **stored** grading configuration") vs. BE-I-06
  ("released through the same gate as application code") — needs sign-off on
  the in-code interpretation.

---

## 4. Founder-approval and open-decision register

Everything above, gathered so it can go to the founder once. Quotes are
verbatim from the Checklist unless marked. "Blocks" is the earliest
work that cannot be finalised without it.

| # | Item | Source | Working default | Blocks |
|---|---|---|---|---|
| 1 | *"Who may publish to the school shared library?"* → *"Licensed teachers submit; School Admin approves."* vs. BE-F-08 | CL-A5 vs BE-F-08 | BE-F-08 binds; approval conditional | F2 schema |
| 2 | *"Should teacher-editable AI feedback be pulled forward into Phase 2?"* — *"Currently deferred."* vs. BE-E-08 (must) | CL unnumbered | BE-E-08 binds | E3 |
| 3 | *"What variance is acceptable when the same submission is graded twice?"* — *"Undecided"* | CL unnumbered | none | I-2 release gate |
| 4 | *"What exactly happens when a teacher runs out of credits?"* | CL unnumbered | queue durably, expire with notice | B1 design |
| 5 | Model failure: no charge vs. refund | QA-ERR-02 | refund scope (exists) | B1 tests |
| 6 | De-identification before model calls | CL §3.2 | not in Part 1; schema leaves room | H (and NFR-CMP-10) |
| 7 | Tag scope | BE-A3 | both, private + Department | C2 |
| 8 | Lesson single owner | BE-A4 | yes | D |
| 9 | Per-member flag, not per-item ACL | BE-A5 | yes | F1 |
| 10 | Weights total 100% | BE-A1 / CL-A3 | yes | E2 |
| 11 | Multiple tags/Units per item | BE-A2 / CL-A6 | yes | C1 |
| 12 | Retention 12 mo / 3 yr | BE-A6 / CL-A8 | **confirmed 2026-09-22** (04 §11 item 1) | (done) |
| 13 | Estimate advisory | BE-A8 | yes | B1 |
| 14 | Credit request: queue vs. notification | FE-B-04, D-04 | notification | B1 |
| 15 | Admin wallet: separate purchase pool | 03 Stage 2, D-02 | separate pool | B1 |
| 16 | Scope cut: H to Part 2; DOCX, version history, tag merge fast-follow | 03 Part VI, D-09 | as recommended | sequencing |
| 17 | Library entry shape: nullable `Assignment.course` vs. content-bearing | 03a §2.11, D-07 | nullable + CHECK | F2 |
| 18 | DOCX need: portability vs. editability | X-9 | PDF only | D fast-follow |

CL-A1, A2, A4, A7 (LMS student auth, grade conflicts, archived-session
freeze, protected-class subgroups) and Checklist §1–2 (Google Classroom,
Canvas/Schoology) belong to Part 2. CL-A7 and BE deferred D-04 are relevant
now only as a *negative* requirement: NFR-CMP-06, no protected-class
attribute stored anywhere in Part 1, tested.

---

## 5. Build order and parallelism

The unit of parallelism is a task worktree with one active engineer. This
stream has two engineers, so up to two slices run at once. Slices that touch
the same files must be sequenced or must agree file ownership first.

```
Wave 0   (Feature Lead)   Epic A lands · confirm BE-I-04 status (Q1)
Wave 1   Eng 1: B0 (S) ─► B1 (L)                        ─────────────────►
         Eng 2: I-1 = BE-I-04 + SubmissionGrading expand ─► E1 ─► E2
Wave 2   Eng 1: (B1 continues)  ─► C1
         Eng 2: E2 (cont.) ─► F1                        [P0 gate, cachegen:dpt]
Wave 3   Eng 1: C1 ─► C2 (with F1) ─► D
         Eng 2: F1 ─► F2 (needs E2 copy + Q6) ─► G
Wave 4   Eng 1: D (cont.) ─► C3, E4
         Eng 2: G ─► E3 ─► I-2
Deferred H → Part 2. BE-D-09 DOCX, BE-D-07, BE-C-05 merge → fast-follows.
```

### What can and cannot run in parallel

| Parallel | Why it is safe | Watch |
|---|---|---|
| B0 ‖ I-1 | Different subsystems | none |
| B1 ‖ E1 → E2 | B1 is the metering/job path; E1 is grade maths and signals | Both touch the grading task path (§2.4); agree file ownership |
| C1 ‖ E2 | C1 is tags; E2 is categories/copy | Copy carries tags (FR-C-06): E2's copy must call C1's re-resolve helper, so C1 lands first or E2 stubs it |
| F1 ‖ E2 | F1 has no library dependency | Both extend `classrooms/models.py`; sequence migrations |
| D ‖ G | 03_architecture Stage 5 | G-03 needs D |
| F2 ‖ D | Both after F1 | F2 needs E2's copy |

| Never parallel | Reason |
|---|---|
| A → everything | Audit contract |
| B0 → C/E/F | `zero_credit_scope()` |
| E1 → I-2 | One reader change point |
| F1 → G | G is a read surface over F |
| D + C1 → E4 | Retrieval interface and Lesson tag |
| Everything → H | Confluence |

### Rough schedule against the window

Two engineers, weeks from the start of Wave 1, using the sizes in §1 and
§3. **This is an estimate without gate-verification time.** The Verification
Engineer's independent runs per `10 Gates.md` are on a separate stream, but a
slice is not done until they pass, and R-1/R-7 still apply.

| Weeks | Eng 1 | Eng 2 |
|---|---|---|
| 0–0.5 | B0 | I-1 (BE-I-04 + expand; ~2 wk) |
| 0.5–3.5 | B1 | I-1 (to wk 2) → E1 (1 wk) → E2 (~1.5 wk, runs to wk ~4.5) |
| 3.5–6 | C1 (~1.5 wk) + C2 | F1 (~2 wk) |
| 6–9 | D (~2.5 wk) | F2 (~1 wk) → G (~1.5 wk) |
| 9–11 | C3 + E4 (~1 wk) | E3 (~1 wk) → I-2 (~2 wk) |

That is about **12–13 weeks** of the ~14 available, leaving about 1 week of
slack (I-1 is ~2 wk, not ~1: 07 §0; the rows after it shift by about a week; E3 is
gated on I-1b, the dual-write deploy), and assumes no rework and the Q-items below answered within two weeks.
A slip in B1 delays D (FR-D-03), C3 and E4, not the rest, which is the reason
for the B0/B1 split.

---

## 6. Sizing basis

| Epic | 03_architecture (wk) | This roadmap (wk) | Delta |
|---|---|---|---|
| B | 3.5 | 3.5 (B0 0.5 + B1 3) | 0 |
| C | 2.5 | 2.5 (C1 1.5 + C2/C3 ~1) | 0 |
| E | 3.5 | 3.5 (E1 1, E2 1.5, E3 ~1, E4 ~0.5 within D wave) | 0, re-cut |
| F | 3 | 3 (F1 2 + F2 1) | 0 |
| D | 3 (2.5 after cuts) | 2.5 | 0 |
| G | 1.5 | 1.5 | 0 |
| I | 2 (+1 BE-I-04) | 2 + 2 (I-1 re-sized in 07) | +1 |
| H | 4 | deferred | −4 |
| **Total** | | **~20–21 eng-weeks** | |

E is 3.5 wk in 03_architecture and still is, but I moved ~0.5 wk of E to
after D (E4) and pulled the `SubmissionGrading` expand step (~0.5–1 wk) into
I-1; that expand cost was previously inside "I remainder".

---

## 7. What each epic plan must contain

Every epic plan (in the style of 04) should list, in addition to 04's sections:

1. The audit actions it *adds* to `audit/enums.py` and their retention class.
2. The tables it adds to the student deletion/export walk (NFR-CMP-04).
3. Which `10 Gates.md` gates apply, and which do not, with the reason.
4. The migration class of every migration (`docs/MIGRATIONS.md`) and the
   three-deploy schedule for any contract step.
5. A feature-flag or environment gate so it ships dark (NFR-MNT-05).
6. Its rows in the role-by-resource matrix (NFR-SEC-01).

---

## 8. Questions for the Feature Lead

| # | Question | Needed by |
|---|---|---|
| Q1 | Has BE-I-04 (prompt/config/strictness/model on every graded submission) started, and does `SubmissionGrading` expand go with it (§2.2 option (a))? Not present on beta today | Wave 1 |
| Q2 | Accept the B0/B1 split (§2.4), or keep B strictly serial? | Wave 1 |
| Q3 | Accept the three-slice `Tag` delivery (§2.1)? | C1 plan |
| Q4 | Who owns the data-export endpoint and per-student deletion (NFR-CMP-04)? No epic owns it | before B/E plans |
| Q5 | `cachegen:dpt` must precede H-1 stage 3 (NFR-PRF-06). Can the Platform Hardening Lead confirm where `task/h1-stage3-wildcard-removal` stands? | before F1 |
| Q6 | Library entry shape (nullable `Assignment.course` vs. content-bearing entry) | before F2 |
| Q7 | One "audit vocabulary" task, or each epic adds its own values (§2.5)? | before B1 |
| Q8 | Confirm H, DOCX, version history and tag merge are deferred, or the scope changes | SM/founder |
| Q9 | Items 1–3 in §4 (library publish, feedback-edit deferral, variance) need a founder answer; who takes them? | E3, F2, I-2 |
