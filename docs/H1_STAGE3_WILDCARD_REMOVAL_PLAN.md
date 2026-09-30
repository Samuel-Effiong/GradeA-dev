# H-1 Stage 3 — removing the legacy wildcard cache clearing

**Status: PLAN.** Approved to proceed (owner, 2026-09-15). Nothing is
implemented yet.

**Owner decisions (2026-09-15)**

- **Scope.** Stage 3 is kept completely separate from the stampede
  decision. Its goal is to replace broad invalidation with targeted
  invalidation while preserving correctness.
- **Fan-out.** Precise per-viewer refresh: only users who can actually see
  a change are invalidated. There is no broad flush and no widening of keys
  to school-wide or site-wide scopes (that is the H-15 herd).
- **Old staleness.** Staleness that exists today under BOTH mechanisms is
  fixed in Stage 3 too, so the gate can prove "no stale data" for every
  write path.
- **Data exposure.** The course-detail exposure (students receive draft
  assignments and classmates' emails) is a separate security item, fixed in
  its own change. It is not part of Stage 3.
- **Gate.** Stage 3 has its own full gate. A passing test count is not
  enough on its own: invalidation behaviour must be measured and verified
  against real Redis and PostgreSQL.

---

## 0. Correction to the Stage 2 evidence (found by the Stage 3 audit)

**Stage 2 recorded "33/33 applicable families migrated, freshness proven
with the legacy mechanism disabled". For assignment-driven changes that
proof was invalid.**

- `_bump_assignment_scopes` (assignments/signals.py) bumps
  `usr(assignment.teacher_id)`. `Assignment.teacher` is marked "IN REVIEW
  FOR REMOVAL" and **no production write path ever sets it**. The two
  creation endpoints (`assignments/views.py` `create`, `create_async`), the
  AI save and the draft save all omit it.
- Every production assignment therefore has `teacher_id = NULL`.
  `bump_many` drops null ids, so an assignment change bumps no user
  generation at all.
- The Stage 2 tests created assignments with `teacher=self.teacher`
  explicitly, a field value production never produces, so they proved a
  refresh production does not get.
- Nobody sees stale data today only because the legacy wildcards
  (`assignments:*`, `*user*`, `*teacheradmin*` …) still clear everything.
- The same audit found that a comment in `dashboard/views.py` ("every
  SCOPE_COURSE bump is accompanied by the teacher's SCOPE_USER bump") is
  false for Assignment.

**Rule adopted for Stage 3 tests:** fixtures must create rows the way
production does, through the real endpoint or service, or with exactly the
fields production sets. A test must never set a field that no production
path sets.

---

## 1. Verified gaps: what only the wildcards refresh today

Evidence: the read-only audit of commit `29cc1c7`, with P0 items
re-verified line by line against the code. P0 and P1 would become stale
data visible to another user once the wildcards are removed.

| # | Pri | Gap | Stale for | Replacement (precise, per viewer) |
|---|---|---|---|---|
| G1 | P0 | Assignment create/edit/publish/unpublish/delete bumps no `usr` (§0) | the course teacher's assignment, course, submission and generation-session lists and `teacheradmins:` dashboards; enrolled students' assignment, course, enrolment and submission lists | `_bump_assignment_scopes`: `usr(course.teacher_id)` plus `usr` of every student enrolled in `assignment.course_id`, in one `bump_many` (one query for the student ids) |
| G2 | P0 | `studentadmins:` dashboard keys are unversioned; the summary's enrolment check runs only on a miss | the student; a withdrawn student keeps reading course analytics for the TTL | build the three keys with `versioned_key`, scope `usr(student)`. Every change they read already bumps the student's `usr` once G1 and G5 land |
| G3 | P0 | publish-all refreshes only `newly_published[0]` | every other student in the batch | bump `usr` of every student in `newly_published`, plus teacher `usr`, `crs`, `sch` and `global` once, in one `bump_many` |
| G4 | P0 | a school admin's cached `customusers:` retrieve outlives loss of access (user moves school, student unenrolled) and renames. Mixin retrieve returns before `get_object()` on a hit | school admins, a cross-school exposure window | wherever a user or enrolment change bumps `sch`, also bump `usr` of that school's active SCHOOL_ADMIN users (old and new school on a move) |
| G5 | P1 | students' copies of teacher-owned rows: Course, Topic and Session edits, course moved to another session, teacher rename, another student's roster or submission change | enrolled students' course, topic, session and enrolment lists | Course, Topic and Session receivers and teacher-rename fan-out bump `usr` of students enrolled in the affected course(s); roster and submission changes bump `usr` of the course's other enrolled students |
| G6 | P1 | school-owned Sessions have `teacher = NULL`, so nobody is refreshed | school admins (the actor too), the school's licensed teachers, superadmins | `clear_session_cache`: `usr` of the school's SCHOOL_ADMIN and TEACHER users plus `created_by`, and every superadmin's `usr` |
| G7 | P1 | School create/edit/archive reaches no superadmin's `schools:` list | superadmins (the actor too) | bump every superadmin's `usr` in `clear_school_cache` |
| G8 | P1 | CustomUser and Settings changes reach no superadmin (`customusers:`, `settingss:`); a student's Settings change never reaches their teacher | superadmins; teachers | bump superadmins' `usr` on CustomUser and Settings changes; give Settings the same `viewer_scopes_for_users` fan-out as CustomUser |
| G9 | P2 | AssignmentGenerationSession create/delete is wildcard-only; message create has no receiver | the owner's own generation-session list | receivers bump `usr(session.user_id)` on session and message save and delete |
| G10 | P2 | enrolments and course renames by other teachers leave `my_students` stale | other teachers of the same student | StudentCourse and Course changes bump `usr` of every teacher of the affected student's courses |
| G11 | P2 | reassigning a course's teacher leaves the old teacher stale | the previous teacher | capture the previous `teacher_id` in `pre_save`; bump it too |
| G12 | P3 | CourseCategory has no receiver (viewset is unrouted) | anyone, if routed | receiver bumping `global`; key scope `global` |
| — | none | BatchUploadSession receiver clears keys no cached payload reads | — | delete the receiver |

**Pre-existing staleness, included by owner decision:**

| # | Write path | Fix |
|---|---|---|
| P1 | grading-claim `QuerySet.update()` of `grading_state` (`students/services.py`), shown in the submission list | call `invalidate_submission_caches` after the claim, as the other three `.update()` paths do |
| P2 | Google sign-in PENDING→ENROLLED promotion `.update()` (`users/views.py`), which runs after the user save's bumps | invalidate the promoted enrolments' student, teacher, course and school scopes after the update |
| P3 | AssignmentGenerationMessage saves (no receiver) | covered by G9 |
| P4 | `repair_question_blooms_levels`, `strip_duplicate_option_letters`, `strip_html_from_assignment_titles`, `backfill_assignment_rigor`: `bulk_update`/`.update()` on assignments | bump the G1 scopes for each touched assignment's course, once per course, after the batch |
| P5 | CreditWallet nested in `me` / CustomUserSerializer, no receiver | wallet and credit-bucket receivers bump the owning user's `usr` |

---

## 2. What is removed

- Every `delete_cache_patterns(...)` call in `classrooms/`, `assignments/`,
  `students/` and `users/` signals, and `SUBMISSION_CACHE_PATTERNS`.
  `invalidate_submission_caches` keeps its name and signature, because
  three `.update()` call sites and their freshness tests depend on it.
- Both `delete_cache_patterns` definitions (`AutoGrader/cache_utils.py` and
  the local copy in `classrooms/signals.py`), `batched_cache_invalidation`
  (no production callers), and the "backend lacks delete_pattern" warning
  path. This also closes H-4 (duplicated implementations).
- **Kept, and not a legacy wildcard:** `assignments/pdf_cache.py`'s
  exact-prefix clear of one assignment's own PDFs. Its key is versioned by
  `updated_at` and no other family shares its prefix.

---

## 3. Verification gate (owner's list, made concrete)

All runs use real PostgreSQL and real Redis and follow the strict gate
procedure: fresh test DB, no `--keepdb`, full logs, fingerprint.

| Gate | What proves it |
|---|---|
| Regression / baseline | full suite on the parent commit before any change (recorded count and failures), and again at the end |
| Cache correctness / invalidation | a **viewer × mutation freshness matrix**. For every cached family and every write path above (G1–G12, P1–P5, plus the Stage 2 mutations), and for every role that can see the change (actor, owner, enrolled student, other student, teacher, other teacher, school admin, other school's admin, superadmin), cache a response, perform the mutation through the real endpoint or service, then assert the next read equals an uncached read. Every "must NOT move" case is asserted too. Fixtures follow the §0 rule |
| No wildcard left | a guard test that fails if non-test code calls `delete_pattern`/`delete_cache_patterns`, other than the documented PDF exact-prefix clear. Plus a Redis command spy asserting **zero SCAN** during every matrix mutation |
| Security / tenant isolation | cross-school and cross-teacher cases in the matrix. Access-revocation cases (withdrawal, school move, deactivation) prove the revoked viewer's next cached read changes. Other tenants' cached responses stay byte-identical through a burst of tenant-A mutations |
| Mutation | remove each new bump (G1–G12, P1–P5) one at a time; the matching matrix cases must fail; restore verified by checksum |
| Concurrency | barrier-synchronised concurrent mutations (e.g. publish-all while students read; concurrent enrolments) lose no bump and leave no stale read |
| Failure / retry | Redis unavailable during a bump never fails the database write, and data is fresh once Redis returns. Retried Celery tasks (grading claim, redelivery) invalidate correctly |
| Stress / scale | fan-out bump cost measured (queries, Redis round trips, latency) at realistic scale: a course with 30 and 300 students, a school with many admins, bulk publish-all. It must be O(1) queries per mutation and one pipelined round trip |
| Before/after measurement | Redis commands per mutation: SCAN count before vs after (expected: many → 0); bump counts; request latency on the mutation endpoints |
| Final regression | the strict committed-tree gate on the merged tip, then two concurrent full suites (H-9 rule) |

---

## 4. Order of work

1. Record the baseline: the full suite on the Stage 3 parent.
2. Build the freshness matrix harness and run it with the wildcards
   **disabled but not yet removed**. The gaps must show as failures, which
   proves the matrix detects them.
3. Implement G1–G12 and P1–P5 as targeted bumps. Rerun the matrix with the
   wildcards disabled: all green.
4. Remove the wildcards and helpers (§2). Rewrite the tests that assert
   wildcard behaviour; the patches that disable the wildcards become
   unnecessary and are replaced by the no-SCAN guard.
5. Run the rest of the gate: mutation, concurrency, failure, stress,
   measurement.
6. Commit; strict gate on the committed tree; owner approval; land.
