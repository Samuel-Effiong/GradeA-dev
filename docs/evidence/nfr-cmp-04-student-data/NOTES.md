# NFR-CMP-04: student data export and erasure

Owner rulings (2026-11-06):
- D1/D4: the activity log AND credit records keep the student's name and email after erasure.
- D2: no student self-service.
- D3: a teacher's erasure removes the student from that teacher's courses only; full erasure = school admin or super admin.
- D5: no AI-provider zero-retention change. D6: JSON zip export. D7: second confirmation is frontend only.
- An erased-student record (name, email, erased_at, erased_by) is kept 7 years, so kept ID-only rows still resolve to a name and email.
- F4: uploaded files will be kept 30 days in Cloudinary (B1b); they join this map when B1b adds them.

## Step 1: the map and its guard (this commit)
- `students/student_data_map.py`: 45 entries. These are the 38 FK/OneToOne links to the user model (from model introspection on staging 8aafa47dd) plus 5 bare user-id columns (AuditEvent actor_id/target_id, CreditLedger/CreditUsageLog user_id, AICallRecord billed_user_id), plus TokenProxy, which shares authtoken's table. Roles: DELETE (export yes/no), KEEP_RECORD, NOT_A_STUDENT, FINANCIAL_ACCOUNT.
- `students/tests_student_data_map.py`: 9 tests. Completeness covers every user relation and bare user-id column, plus no stale entries. The owner rulings are pinned by name.
- Red without the map: 01_red.log (import error). Green: 02_green.log, 9 OK.
- Plants (03_plants.log): P1 drop an entry, P2 activity log deleted, P3 security data exported. All killed; the file was restored and checked by sha256.

## Decisions taken by the SM (reportable)
- NOT_A_STUDENT columns make erasure refuse rather than cascade: an account that owns a course or a license is not only a student.
- The credit wallet is deleted only when empty; otherwise erasure refuses.
- BackgroundProcessingTask.requested_by is the student's own data (students can upload answers, H-141). Its export carries file names only.

## Handover to Lane 2 (2026-11-08)

The owner approved Lane 2 taking over NFR-CMP-04 (via the SM). Step 0 is a
read-only survey and a checkpoint.

### Step 0.1: staging merged, guard expectations (before the run)

`origin/staging` 703310b47 is merged at 3512a7fb5. Since 8aafa47dd the only
new models are C1's `tags.Tag` and `tags.AssignmentTag`. Their user links:
- `tags.Tag.teacher`: a ForeignKey to the user model, not in the map.
- `tags.Tag.created_by_id`: a bare UUID holding a user's id, not in the map.
  The guard's column pattern (`USER_ID_COLUMN`) does not match
  `created_by_id`, so the guard cannot see it. That is a gap in the guard.
- `tags.AssignmentTag`: no user link (it points at an assignment and a tag).

A wider search of bare columns found one other unmatched name,
`ai_processor.BenchmarkQuestionOutcome.graded_by`. It is a CharField naming
the benchmark grader, not a user, so it is out of scope.

Prediction for run 04 (`students.tests_student_data_map`): Ran 9, exactly
1 failure, `test_every_relation_to_a_user_is_in_the_map`, naming
`[('tags.Tag', 'teacher')]` and nothing else. The bare-column test passes,
because of the gap. Nothing is classified before the SM rules.

Run 04 result (`04_guard_after_merge.log`): Ran 9, 1 failure,
`test_every_relation_to_a_user_is_in_the_map`, naming exactly
`[('tags.Tag', 'teacher')]`. **As predicted.**

### Step 0.2: survey of today's code (read-only)

**Who may call.** Roles come from `CustomUser.user_type`. A super admin is
`is_real_super_admin` (user_type SUPER_ADMIN and is_superuser), which is
the `IsSuperAdmin` check. That check also writes the ADMIN_ACTION audit
event. `IsSchoolAdmin` checks the type only, so a school admin's scope is
their `school_id`. The pattern to copy is `SchoolAdminAuditEventListView`:
when an admin has no school, it shows nothing. A student's school is
`CustomUser.school`, which can be null. A teacher's courses are those
where `Course.teacher` is the teacher.

**Routes.** The houses are `super-admin/...` and `school-admin/...`
(audit/urls.py), and course routes go under `courses/<id>/...`. There is
no Django admin action today. One should not be added, because it would
skip the scope checks.

**The nearest code that exists:**
`classrooms.services.enrollment.remove_student_from_course` deletes one
StudentCourse and nothing else, by design (it once called
`student.delete()`). It then sends the `student_course_removal` email.
A teacher's erasure (D3) goes further: it also removes the student's
submissions in that teacher's courses.

**Delete side effects (called, not edited):**
- A StudentSubmission delete recalculates the final grade
  (`classrooms/signals.py`) and writes a GRADE_CHANGE history event (ids
  and numbers only, never answer text).
- A StudentCourse delete writes ROSTER_CHANGE and refreshes caches.
- On a full erasure, StudentCourse should be deleted before the
  submissions, so the grade recalculation finds no enrollment to rewrite.
  To check in step 3.

**How the user model deletes today (introspection):**
- `token_blacklist.OutstandingToken.user` is SET_NULL. A cascade would
  leave the rows, unlinked. Erasure must delete them explicitly, so it
  works from the map, not from the cascade.
- **KEEP_RECORD rows that are SET_NULL:** `BillingTransaction.user` and
  `.performed_by`, and `PaymentDispute.user`. Deleting the user would
  remove those records' only link to the student. The erased-student
  record would no longer resolve them, unlike CreditLedger and
  CreditUsageLog, which keep a bare `user_id` and `user_email`, and
  AuditEvent, which keeps `actor_id` and `actor_email`. **Needs a ruling.**
- Some NOT_A_STUDENT columns are PROTECT (license rows). The refusal check
  runs first, so these are never reached.
- `CreditWallet` is not under the append-only guard (only AuditEvent,
  CreditLedger and CreditUsageLog are), so an empty wallet can be deleted.

**Data the map does not cover:**
- `CustomUser.profile_image` is a file on the user row. It needs exporting
  and deleting from storage.
- `users.BetaWhitelist.email` and `users.Waitlist.email` are keyed by email
  address, not user id, so the guard cannot see them.
- `StudentRiskAlertState` is marked `course_scoped`, but it has no course
  column, only student and school. A teacher's erasure cannot scope it
  by course.
- `ChatSession` has a course column but is not `course_scoped`.
- No StudentSubmission has a file field. Uploaded files are not stored as
  rows yet; per F4 they join with B1b.

**Export (D6):** nothing in the app builds a zip today (`zipfile` is used
only by `scripts/credscan.py`). The proposal is to build one in memory per
student and send it as an `application/zip` download.

**The erased-student record:** a new table holding the original user id
(unique), first, middle and last name, email, erased_at, erased_by (id and
email) and `keep_until` (erased_at + 7 years). It is written only by a full
erasure; a teacher's erasure keeps the account. No purge job exists yet.
