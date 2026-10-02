# H-38 teacher-removal: Verification, part A (d5's delta)

Branch `task/teacher-removal` @ 314dc4f (code tree 3a254eb), rebased onto beta 4b902fc.

## Verification split (SM ruling, option (a))
The Verification Engineer (formerly grade-automator-plus-1a) wrote the H-38 core in its pre-reorg Platform-Hardening Lead role, so it does NOT verify that. The split:
- **Core, verified by the Security Engineer:** da8ee73 and ac4b4c2 (reproduce-first, by the earlier fix-teacher-removal session); 70ed8af (part 1, was ad93df2); ec30d22 (part 2, was 56099ce, as authored); 2809a57 (checkpoint); f7f712b (d5's mutation results on the core). Also the helpers in classrooms/models.py, which d5's delta does not touch (`git diff --quiet 2809a57 314dc4f -- classrooms/models.py`).
- **d5's delta, verified here:** 702ca6a..314dc4f, plus the rebase conflict resolutions inside ec30d22.
- Disclosed by the core's author: the draft-save `select_for_update` + helper regression and the loose non-2xx assertions both came from the core. The fixes are in this delta and verified below.

## Checks
- **Rebase resolutions:** `git range-diff fba1294..26abd15 4b902fc..f7f712b`. Only part 2 changed. The changes are beta's import context (billing.refusals, SessionOwnerType) and the StudentViewSet hunk dropped because beta deleted that viewset (H-22). I confirmed StudentViewSet is absent on both 4b902fc and the tip, and that dashboard/views.py's import block is exactly beta's plus the two helpers. Correct.
- **(c) select_for_update combined with the helper, whole tree:** 25 non-test files lock. Only 3 of them also use a helper (assignments/students/users views). Every lock in those three is by pk or plain filters, except the draft save, which is fixed. No other locked path combines with the helper.
- **Draft-save fix (c29238a):** an unlocked access check with the full helper filter, then a lock by pk. The locked `select_related("session__course","session__user")` is valid: I checked that AssignmentGenerationSession.user/.course and Message.session are all non-null FKs, so these are INNER JOINs.
- **The 5 rebase sites (85763e5):** each replaces exactly `<prefix>teacher=user` / `teacher_id != user.id` with a helper. I confirmed that `teacher_course_access_q` returns `own & reachable` and that `teacher_can_reach_course` returns False unless `teacher_id == user.id`. So each rewrite is a strict subset, and tenancy cannot widen. All the chains are single-valued FKs, with no multi-valued join effect, and none of them locks.
- **scale_my_students.py allowlist:** it holds. The file isn't imported anywhere, doesn't match Django's `test*.py` discovery, and isn't routed. Line 187 seeds fixture grades and line 206 is a reported roster count; neither is an access decision.
- **(a) Positive controls:** the one locked path has `ActiveTeacherDraftSaveTests`, which asserts 201 exactly. The custom-ai-prompt and shared-student paths have exact-200/202 controls too.
- **(b) Loose assertions:** the `assertNotIn((200,201,202,204))` style is gone. Every `assertLess(<500)` sits beside a strict `assertEqual`, or is in a helper whose callers assert content.
- **Tests:** in my own detached checkout (own test DB), `test_h38_part2_removed_teacher_routes` + `test_h38_teacher_removal` + `tests_teacher_access_sweep` pass 80/80.

## My mutation battery: 10/10 KILLED by DYNAMIC tests (the static sweep test excluded from every run; each restore sha-verified against 314dc4f)
| Mutant | Killed by |
|---|---|
| M1 draft save: lock re-folded with the helper | removed-teacher draft test + ActiveTeacherDraftSave positive control |
| M2 draft save: access-check filter dropped | removed-teacher draft test |
| M3 validate_course reverted | create, create-async and PATCH-into-school-course tests |
| M4 MyStudentsFilter reverted | course- and session-filter no-rows tests |
| M5 visible_enrollments reverted | user-detail course and session oracle tests |
| M6 my_students enrollments prefetch reverted | prefetch-caches + row-names tests |
| M7b my_students submissions prefetch reverted (prefetch occurrence only) | prefetch-caches test |
| M8 the 4 funded tests unfunded | all 4 fail (3 strict 404s + the custom-ai positive control), so funding is load-bearing and the tests are strict |
| M9 TeacherAIContextService back to `Course.objects.filter(teacher=...)` | test_custom_ai_prompt_on_the_school_course (context spy) |
(M7 as first written matched 2 sites, one of which is the core's `submissions_qs`, so it was re-run as the prefetch-only M7b.)
M9 is good news beyond this delta: e4984d6's tightened spy test now catches the D4_ai_context site DYNAMICALLY. `mutation_results_final.md` still lists D4 as sweep-only; please update it.

## Notes (non-blocking)
1. Three removed-teacher tests still accept `(402, 403, 404)` and are unfunded: `test_upload_assignment`, `test_generate_assignment_from_prompt` and `test_submission_upload_to_the_school_assignment`. They exclude 5xx, so they meet rule (b). But a 402 is billing refusing BEFORE the H-38 guard, so they exercise no H-38 code, and the submission-upload probe targets a different action (your own mutation doc, S1). Recommend funding them and asserting 404, as the other funded tests do, and retargeting the S1 probe, in H-38-F1. These guards (A2a, A2c, S1) are core sites, so the Security Engineer should weigh their coverage.
2. The draft-save lock has no `of=("self",)`, so it also locks the joined session, course and user rows. That's unchanged from beta, and your commit message says only the message row needs locking. Optional tightening.
3. The unlocked access check followed by a lock by pk leaves a tiny window where a teacher removed mid-request proceeds. Negligible, noted for completeness.

## Verdict (d5's delta): VERIFIED-WITH-NOTES
The branch is landable only when the Security Engineer's core verdict is also VERIFIED.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.

## Part A re-check (24c737f, 3161413, 68611b1, 3c6dec6): VERIFIED

**Disclosure first.** The roster_import allowlist entry that hid the name-match leak ("name match limited to students already with this course's teacher; the caller's access to `course` was scoped before this runs") was mine, written in the core in my pre-reorg role. The justification was wrong: this no-email path has no cross-school gate of its own. d5's fix and tests below are d5's work, so they're in my scope. The Security Engineer's core verdict (VERIFICATION_CORE.md) found the leak in the first place.

- **Roster fix:** `_find_existing_student_by_name` now applies `teacher_course_access_q(course.teacher, prefix="enrollments__course__")` in the SAME `filter()` call as the name match, so over the multi-valued `enrollments` relation the access rule and the match apply to the same enrollment row. The name fields are on CustomUser itself. The allowlist entry is removed, so the sweep guard now covers this line.
- **Tests:** in my own detached checkout of 3c6dec6, d5's regression set (H-38 modules + sweep + tenancy_and_roster + cross_school_enrollment + concurrency_and_resilience) gives **173 OK**.
- **My mutants** (static sweep excluded; each restore sha-verified):
  | Mutant | Killed by |
  |---|---|
  | roster match back to owner-only `enrollments__course__teacher=course.teacher` | both RemovedTeacherRosterNameMatchTests probes (school-B course, individual course) |
  | A2a upload guard back to owner-only (`request.user.courses.all()`) | test_upload_assignment only |
  | A2c generate guard back to owner-only | test_generate_assignment_from_prompt only |
  | S1 `_assignment_taught_by` back to `course__teacher=teacher` | test_batch_upload_answers_to_the_school_assignment only |
  So my part-A note 1 is closed: all three probes now reach their H-38 guard dynamically, each kills its own site, and S1 is retargeted to the action that actually calls `_assignment_taught_by`.
- **Exposure SQL (68611b1/3c6dec6):** the whole file sits inside `BEGIN TRANSACTION READ ONLY … ROLLBACK`. The `users_customuser` join and `u.email` are gone, and no name/email/password/token identifier appears outside comments. The active-licence filter excludes ordinary lapses (the under-count is documented). I executed the file statement by statement on the migrated test schema (a throwaway TransactionTestCase, never committed): 5 statements, all 3 queries run, and output columns are ids, a timestamp and counts only. A write attempted inside the same wrapper is refused ("read-only transaction").
- **mypy (3161413):** the whole-repo hook on 3c6dec6 flags only `docs/evidence/authz-oauth-takeover/replay_scripts/exploit_authz_oauth.py` (2 errors, from beta), which the batch's `exclude: ^docs/` removes. Every H-38 file is clean.
- Minor notes 2 and 3 from the first pass stand as observations, not defects: the draft-save lock also locks the joined rows (unchanged from beta), and the unlocked-check-then-lock window is negligible.

Verdict (part A): VERIFIED. Together with the Security Engineer's core verdict (VERIFIED-WITH-NOTES, b7ccfaf), H-38 is verified; only the full suite on the tip remains (queued with 0b).
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
