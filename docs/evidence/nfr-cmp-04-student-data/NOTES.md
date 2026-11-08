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

### Step 0.3: proposed step plan (for the SM's ruling)

**Flag:** `STUDENT_DATA_TOOLS_ENABLED`, default False. While it is off,
every route answers json_404, as the tags routes do.

**Step 1b: map fixes, after the rulings (red, then green, then one plant):**
- Classify `tags.Tag.teacher` and `tags.Tag.created_by_id`.
- Widen `USER_ID_COLUMN` so a name ending in `_by_id` is caught, which
  closes the `created_by_id` gap.
- Add email-keyed entries (BetaWhitelist, Waitlist) and the profile image,
  as ruled.
- Fix `StudentRiskAlertState.course_scoped` and `ChatSession`, as ruled.
- Plant: drop the new regex branch. It must be killed.

**Step 2: export.** A service, `students.data_tools.export_student(student,
*, scope)`. It builds the files from the map: one JSON file for each
entry with `export=True`, plus `profile.json` and a README (wording).
Each model gets an explicit list of exported fields.
- Tests:
  - Every `export=True` entry produces a file.
  - Every field of an exported model is either listed for export or
    listed as left out; this guard fails on a new field.
  - Security data is never in the zip: the bytes are searched for the
    token, the OTP code and the Google credential values.
  - Two students in the same course: the other student's rows and values
    never appear.
  - Callers: a student exporting themselves is refused (D2). A school admin
    is limited to their own school, and one with no school sees nothing.
    A super admin may export anyone. A teacher only as ruled.
  - Flag off: 404.
  - One audit event for each export.
- Plants (one per run): the student filter dropped on one table; a
  security table exported; the school check dropped; a mapped table
  skipped.

**Step 3: erasure.** A service, `erase_student(student, *, by)`. It is one
transaction with the student row locked. Refusals come first:
- the account is not a student;
- any NOT_A_STUDENT column points at the student (checked from the map,
  so a new entry is enforced automatically);
- the wallet is not empty;
- [billing records, if ruled].

Then it deletes every DELETE entry from the map, explicitly, with the
course scope for a teacher. A full erasure also deletes the empty wallet,
writes the erased-student record and deletes the user row. The profile
image file is removed after commit.
- Tests:
  - Two students share every table. Every DELETE table loses only the
    target's rows; the other student's rows are unchanged, checked by
    count and by id.
  - A teacher's erasure: rows in another teacher's course are untouched,
    and the account remains.
  - KEEP_RECORD rows are byte-identical before and after.
  - Each refusal, driven from the map; a refusal changes nothing.
  - The erased-student record is written with all its fields.
  - A failure injected midway rolls everything back.
  - OutstandingToken rows are deleted, not unlinked.
  - The erased account cannot sign in.
  - A second erasure answers "not found".
  - Final grades in the target's other courses are unchanged by a
    teacher's erasure.
- Plants (one per run): the student filter dropped on one delete (it would
  delete another student's rows); the course scope dropped; the
  NOT_A_STUDENT check skipped; the wallet check skipped; the
  erased-student record not written.

**Step 4: battery and gate.** All plants run again, each restored by sha256.
Then a near run under the machine lock: students, classrooms, users,
audit, billing, tags and dashboard, plus
`AutoGrader.tests_migration_rollback_defaults` for the new table.

### Step 0.4: wording that needs owner approval (none written yet)
- Refusal messages:
  - the account is not a student;
  - the account owns courses, licenses or other staff data;
  - the wallet still holds credits;
  - [the student has billing records, if ruled];
  - the student is not in your courses or your school. The proposal is
    to reuse the H-296 "not found" text, which needs no new wording.
- Success messages: export ready (the download itself has none); student
  data erased (full); student removed from your courses and their work
  erased (teacher).
- The frontend's second confirmation (D7) is frontend wording, listed so
  the owner sees it.
- Email, if any:
  - to the student after a full erasure (sent before the address is
    removed);
  - after a teacher's erasure, either the existing `student_course_removal`
    email or a new one.
  - Proposal: no new email.
- The README inside the zip, and the zip's file and folder names.
- Audit action labels: "Student data exported" and "Student data erased".
- A Django admin label for the erased-student record, if it is registered.

## Step 1b: map fixes (SM rulings 2026-11-08)

Rulings:
- **R1:** Tag.teacher and Tag.created_by_id are NOT_A_STUDENT. The guard
  regex widens to any name ending in `_by_id`. Before the widening, a
  search of every bare UUID, integer or char column found that the wider
  pattern catches only `tags.Tag.created_by_id`. No column that isn't a
  user would be caught, so no exception list is needed.
  (`BenchmarkQuestionOutcome.graded_by` has no `_id` and stays unmatched.)
- **R3:** StudentRiskAlertState goes with a full erasure only
  (course_scoped=False), with no recompute.
- **R4:** ChatSession is course_scoped.
- **R7:** BetaWhitelist and Waitlist are DELETE with no export, matched by
  email, case-insensitively. The test that a different email is untouched
  belongs to step 3, the erasure.
- **R8:** the profile image is exported, then deleted from storage
  on_commit. The tests that a storage failure doesn't undo the erasure and
  is logged at ERROR belong to step 3.
- **R2, R5:** still with the owner, so not part of this step.
- **R9:** later; see the open follow-ups.
- **R10:** in lane, with ORM calls only.
- **R11:** steps 2 and 3.

Map design: `Entry` gains
- `match`:
  - `USER`: the column is the student, as an FK or a bare id;
  - `EMAIL`: the column equals the student's email, ignoring case;
  - `OWN_ROW`: a column on the student's own user row.
- `stored_file`: the column names a file in storage.

The tests read both with `getattr`, so the red fails test by test, not on
the import.

Prediction for run 05 (red, `students.tests_student_data_map`), Ran 20:
- **Failures:**
  - the old relation test, naming `tags.Tag.teacher`;
  - `test_the_guard_sees_a_bare_created_by_id`;
  - `test_any_name_ending_in_by_id_...`, on the 3 positive names only;
  - the risk-alert test (R3);
  - the chat test (R4);
  - `test_a_course_scoped_table_...`, for StudentRiskAlertState only;
  - `test_every_other_entry_matches_by_user`, for all 45 entries.
- **Errors (KeyError):**
  - the tag-columns test, on its first subtest;
  - the whitelist/waitlist test, on both subtests;
  - the profile-image test.
- **Pass:**
  - the 8 other old tests;
  - the email-column and own-row tests, which pass with no entries to
    check (vacuously).

Open follow-up (R9, the SM files it): the erased-student record promises
7 years. A purge has to exist before the first record turns 7 years old.
No purge is built now.

Run 05 result (`05_red_step1b.log`): Ran 20, FAILED (failures=51, errors=5).
- **Failures:** 45 come from `test_every_other_entry_matches_by_user` (one per
  entry), and 6 from the other predicted tests, which each failed exactly
  as predicted.
- **Errors:** the whitelist/waitlist test ×2 and the profile-image test
  ×1, as predicted. The tag-columns test errored on **both** subtests,
  not only the first: a KeyError inside a subTest is reported per subtest,
  so the second subtest ran too.
- **Verdict: prediction incomplete** (the tag-columns error count was
  1 predicted, 2 actual). Every failure and error is for the predicted
  reason. The prediction above is left as it was written.

Code:
- `Match` (USER, EMAIL, OWN_ROW) and `stored_file` are added to `Entry`.
- The map gains 5 entries, for 50 in all:
  - Tag.teacher and Tag.created_by_id (NOT_A_STUDENT);
  - BetaWhitelist.email and Waitlist.email (DELETE, EMAIL, no export);
  - CustomUser.profile_image (DELETE, OWN_ROW, export, stored_file).
- StudentRiskAlertState: course_scoped False. ChatSession: course_scoped
  True.
- `USER_ID_COLUMN` gains `|^[a-z0-9_]+_by_id$`.

Prediction for run 06 (green, `students.tests_student_data_map`): Ran 20,
OK.

Run 06 result (`06_green_step1b.log`): Ran 20, OK. **As predicted.** The map
holds 50 entries.

**Correction (counted from the code, 2026-11-08).** At 677ef2ebd,
`STUDENT_DATA_MAP` held **43** entries, not 45. Step 1's line "45 entries"
was wrong. (Its own sum is also off: 38 + 5 + TokenProxy = 44.) I copied
45 without counting, so:
- The run 05 prediction said "for all 45 entries". The log shows 43
  (`grep -c` of `test_every_other_entry_matches_by_user` FAIL lines). That
  is a second miss in the prediction, which stays **prediction incomplete**
  and is not amended.
- The run 05 result line above misstates the split. Correctly: 51 failures
  = 43 from `test_every_other_entry_matches_by_user` + 8 subtest failures
  from the other predicted tests (relation 1, created_by_id 1, `_by_id`
  names 3, R3 1, R4 1, course-scoped 1).
- The run 06 line "50 entries" is wrong: the map now holds **48**
  (43 + 5).

Prediction for run 07 (plant: remove the `_by_id` branch from
`USER_ID_COLUMN`): **killed**. Ran 20, FAILED (failures=4), and no errors:
- `test_the_guard_sees_a_bare_created_by_id`: 1;
- `test_any_name_ending_in_by_id_...`: 3, the positive names.

The completeness tests still pass, because the map already names the
column. The file is restored from a saved copy, and its sha256 is checked.

Run 07 result (`07_plant_no_by_id.log`): Ran 20, FAILED (failures=4): the
3 positive names and the `created_by_id` test. **Killed, as predicted.** The
file was restored from a saved copy, and its sha256 matched.

Step 1b is done. Open for later steps:
- R2 and R5 are with the owner.
- The R7 test that a different email is untouched, and the R8 tests (a
  storage failure keeps the erasure and is logged at ERROR) are in step 3.
- R9: the purge follow-up.

## Step 2: export (SM go 2026-11-08, with the teacher row OFF)

### Design (expectations, before any test)

**Route.** One route: `GET students/<uuid:pk>/data-export`, named
`student-data-export`.
- While `STUDENT_DATA_TOOLS_ENABLED` is off (the default), `dispatch()`
  answers json_404 before authentication, as the tags routes do. The route
  is listed in `CONCEALED_ROUTES`.
- It is one route, not separate `super-admin/` and `school-admin/` ones,
  so the caller matrix lives in one function. `IsSuperAdmin`'s automatic
  ADMIN_ACTION event therefore doesn't apply, and the route writes its own
  event (below).

**Who may call.** This is decided in one place:
`students.data_export.EXPORT_CALLERS`.
- **SUPER_ADMIN** (the `is_real_super_admin` check): any student, in full.
- **SCHOOL_ADMIN:** a student whose own `school_id` equals the admin's, in
  full. If either school is null, the answer is "not found" (SM R6).
- **TEACHER:** governed by `TEACHER_EXPORT_ALLOWED = False`, the R5 switch,
  which stays off until the owner answers. With it switched on, a teacher
  gets only the course-scoped tables, limited to their own courses, plus
  the student's name and email. A student outside their courses is "not
  found".
- **Everyone else**, including the student themselves (D2): "not found".

Every refusal gives the same response: the house not-found answer.
- A target that is not a student, an unknown id, a caller out of scope,
  the student exporting themselves and a teacher while the switch is off
  all get it, so the response never shows whether a student exists.
- An anonymous caller with the flag on gets the house 401.
- A refusal writes no event.

**The zip** is built in memory, one per student, as `application/zip`.
- It holds one JSON file per map entry with `export=True`, a
  `profile.json`, a manifest (the counts per file, the time it was made and
  the scope) and the profile image file, if there is one.
- ChatMessage rows are nested under their ChatSession.
- Rows are in a fixed order (time, then id), and the keys are sorted.
- Values: a UUID becomes a string, a time is ISO 8601, a decimal becomes a
  string, and a foreign key becomes the target's id.
- **Every file and folder name, the download's file name and the README
  are PENDING placeholders.** They live in one `EXPORT_NAMES` dict with
  `EXPORT_NAMES_APPROVED = False`, the same pattern as the late-door texts.
  Tests refer to the dict, never to literal names.
- JSON keys are the model field names. They are listed for the owner.

**Field lists.** `EXPORT_FIELDS` and `EXPORT_OMITTED` say, for every
exported model, which fields go in and which are left out (each left-out
field with a reason).
- **The guard:** for every exported model, including ChatMessage, the
  fields listed for export and the fields left out together equal all of
  the model's concrete fields, with no overlap. A model gaining a field
  fails the test until someone decides about it.
- Every map entry with `export=True` has a field list.
- No entry with `export=False` (the security tables, BetaWhitelist and
  Waitlist) has one.

| Model | Exported | Left out (reason) |
|---|---|---|
| CustomUser (profile.json) | id, first_name, middle_name, last_name, email, user_type, school, bio, date_joined, last_login, email_verified_at, registration_method, is_active, profile_image, profile_image_url | password, activation_token, activation_expires, failed_login_attempts, locked_until, token_epoch, must_change_password (sign-in and security); is_superuser, is_staff (always false for a student, internal) |
| StudentCourse | id, course, created_at, enrollment_status, withdrawal_date, final_grade, ai_summary, ai_summary_generated_at | student (the student themselves); auto_added (internal) |
| StudentSubmission | id, assignment, submission_date, answers, raw_input, score, score_percentage, max_points, feedback, formatted_grade, graded_at, is_published, ai_score, ai_feedback, ai_graded_at, was_regraded, regraded_at | student; grading_state, grading_started_at, grading_confidence, extraction_confidence, grading_prompt_version, grading_config_version, grading_strictness, grading_model, grading_fallback_used, grading_release, answers_model, answers_prompt_version, answers_config_version, answers_release, answers_recheck_model, answers_recheck_result, extraction_flags, ai_grading_completed_at, scheduled_grading_at, grading_task_name, attempt_count (how grading ran, internal); needs_review, review_reasons, review_severity, review_tier (the teacher's review queue: **Q2**); pending_result, pending_graded_at (a regrade not yet approved: **Q1**) |
| StudentRiskAlertState | is_at_risk, average_score, last_checked_at, last_alerted_at | id, student, school; alert_pending (internal) |
| BackgroundProcessingTask | id, assignment, submission, task_type, status, file_name, reason_code, created_at, finished_at | requested_by; celery_task_id, trace_id, batch_session, meta, error, retry_count, item_index, cancel_requested_at, started_at, updated_at (internal processing) |
| ChatSession | id, course, assistant_type, created_at, updated_at | user |
| ChatMessage (nested) | role, content, timestamp | id, session (nesting stands for it) |
| UserActivity | timestamp | id, user |
| Settings | theme, notify_student_submission, notify_weekly_summary, notify_assignment_due_reminder, notify_grading_complete, notify_assignment_edited, notify_new_assignment_posted, notify_teacher_activity_alerts, notify_at_risk_student_alerts | id, user |

**Audit.** A new action, `STUDENT_DATA_EXPORT`.
- Its label is a PENDING placeholder, and it goes on the wording list.
- Metadata: student_id, scope (`all`, `school` or `courses`) and the count
  per file. No names.
- It needs no migration: the TAG_* actions were added the same way.
- The metadata allow-list and `audit/tests_enums.py` gain the action.
- One event per export, written on success only.

### Expected tests, all in `students/tests_data_export.py`
- **The field guard**, as described above.
- **Every file present:** each `export=True` entry produces its file, even
  when it is empty (`[]`).
- **No security data:** a student with a token, both OTP codes, Google
  credentials, an outstanding token, a password hash and an activation
  token. A search of the zip's raw bytes, and of every file in it, finds
  none of those values.
- **Only the target's rows:** two students share a course, an assignment,
  a chat course, settings and activity. The other student's ids, answers
  and chat text never appear.
- **The caller matrix:**
  - a super admin gets 200;
  - a school admin of the same school gets 200;
  - a school admin of another school gets "not found";
  - an admin with no school gets "not found";
  - a student with no school, asked for by a school admin, gets "not
    found";
  - the student themselves gets "not found" (D2);
  - another student gets "not found";
  - a teacher with the switch at its default gets "not found", even for
    their own course's student;
  - a target that is a teacher gets "not found", and so does an unknown id.
- **Teacher switched on** (patched): the teacher's own courses only. Rows
  from another teacher's course are absent, and so are UserActivity,
  Settings and the profile image. A student outside their courses is "not
  found".
- **The flag:** off gives 404 for every caller, anonymous included. On,
  an anonymous caller gets 401.
- **Audit:** one event after a success, none after a refusal. Its metadata
  is on the allow-list.
- **The zip:** it opens, every file is valid JSON, the names come from
  `EXPORT_NAMES`, and the profile image's bytes match what is stored.

### Plants (one per run; each restored by sha256)
- **E1:** the student filter dropped on the StudentSubmission export.
- **E2:** a security entry exported (Token set to `export=True` in the
  map).
- **E3:** the school check dropped for a school admin.
- **E4:** one `export=True` entry skipped when the zip is built.
- **E5:** `TEACHER_EXPORT_ALLOWED` defaulted to True.

**Logs:** 08 red, 09 green, 10–14 the plants, then a gate (students,
audit's route coverage and enums, users) as 15.

### Questions for the SM before the red run
- **Q1:** StudentSubmission's score and feedback when `is_published` is
  False. The proposal: export them as they are, since this is the
  student's data held by the school. The other option is to leave them out
  until the teacher publishes. `pending_result` (a regrade not yet
  approved) is left out either way.
- **Q2:** review flags (`needs_review`, `review_reasons`, the severity and
  the tier). The proposal: leave them out, as the teacher's workflow.
- **Q3:** the profile image can't be read from storage during an export.
  The proposal: still export, log at ERROR, and list the file as missing
  in the manifest, rather than failing the whole export.
- **Q4:** the school admin's full export includes the student's rows from
  courses at other schools (a student's school is one field; their courses
  can be anywhere). The proposal: yes, in full, as R6 scopes by the
  student's school.

Wording added to the list (all PENDING): every file and folder name in the
zip, the download's file name, the README, the manifest's keys, the JSON
keys (field names) and the `STUDENT_DATA_EXPORT` label.

### SM rulings on Q1–Q4 (2026-11-08) and the changed declaration

- **Q1, leave out until published** (the SM changed my proposal; the owner
  is told this is the default and can reverse it). The SM named score and
  feedback, for H-130's reason: a student must not learn that a grade
  exists before the teacher releases it. Applying that rule faithfully
  needs every field the student's own views hide before release. Score and
  feedback alone would let the grade through elsewhere in the zip. The
  student's views were read for this: `StudentSubmissionListSerializer`
  (students/serializers.py), `max_points_shown` (H-133),
  `answer_document_for_student` (students/services.py) and
  `released_course_grade` (classrooms/grade_service.py).
  - **StudentSubmission: present only when `is_published` is True; absent
    (`null`) before.** These are score, score_percentage, feedback,
    formatted_grade, graded_at, was_regraded and regraded_at.
    - Feedback goes through `student_safe_feedback`, formatted_grade
      through `student_safe_formatted_grade`, the student's own whitelist.
      So even published feedback carries no `second_opinion` and no
      teacher-only section.
  - **max_points:** before release it is the assignment's total; after
    release it is the row's own value (`max_points_shown`'s rule, H-133).
  - **raw_input:** `answer_document_for_student`, the ungraded rebuild
    before release and the stored document after.
  - **Now left out entirely:** ai_score, ai_feedback and ai_graded_at.
    These are the AI's draft grade before the teacher's review, which the
    student never sees, published or not. **This changes the design**
    (they were in the exported list).
  - **StudentCourse.final_grade:** the student-visible figure
    (`released_course_grade`), not the stored one, which counts unreleased
    work.
  - **StudentCourse.ai_summary and ai_summary_generated_at: left out,
    pending Q5.** This is the AI summary a teacher asks for. It is
    generated from the student's work, possibly including unreleased
    grades, and the student never sees it. Leaving it out is the
    conservative default, by the same reasoning as Q1. **This changes the
    design.**
  - **StudentRiskAlertState** is unchanged: its average and flag count
    published work only (dashboard/services.py).
  - **Tests:** an unpublished submission's 7 fields are null; its
    max_points is the assignment's total; its raw_input is the ungraded
    document, with no score in it. The zip bytes contain neither the stored
    score nor the feedback text. A published submission has them all, in
    their student-safe form. The final grade counts released work only.
- **Q2, YES:** the review flags are left out.
- **Q3, YES:** if the image can't be read, the export still runs, an
  ERROR is logged, and the manifest lists the file as missing. The
  manifest key is PENDING. Tested.
- **Q4, YES, in full.** The student's own school answers the student's
  data request, and every row in the export is the student's own data,
  including rows from courses at other schools. A school admin's export is
  therefore complete, not limited to that school's courses.
- **The one-route layout is approved**, with its own `STUDENT_DATA_EXPORT`
  event and the label PENDING.
- **Added to the matrix:** an account that is not `is_real_super_admin`
  is refused. That covers an account of type SUPER_ADMIN without
  `is_superuser`, and one with `is_superuser` but another type. A super
  admin acting as someone else, if the app has that, is refused too.

**Q5, for the SM:** should `ai_summary` be left out (the default above)
or exported?

Revised field table rows (they replace the earlier rows):

| Model | Exported | Conditional | Left out |
|---|---|---|---|
| StudentSubmission | id, assignment, submission_date, answers, raw_input (student form), max_points (H-133 form), is_published | score, score_percentage, feedback (student-safe), formatted_grade (student-safe), graded_at, was_regraded, regraded_at: null until published | student; ai_score, ai_feedback, ai_graded_at (the AI's draft); the grading-run internals as before; the review flags (Q2); pending_result, pending_graded_at |
| StudentCourse | id, course, created_at, enrollment_status, withdrawal_date, final_grade (released only) | — | student; auto_added; ai_summary, ai_summary_generated_at (Q5) |

The guard now counts three lists: exported, conditional and left out.
Together they must cover every field, with no overlap.

**Run 08 (red) prediction:** `students.tests_data_export` cannot import
`students.data_export` (the module isn't built yet), so the run ends in 1
import error. The route, flag and enum tests elsewhere fail on their own
assertions:
- `audit.tests_enums`: STUDENT_DATA_EXPORT is missing;
- `audit.tests_route_coverage`: no change yet, because the route doesn't
  exist.

The exact counts are written down after the test file is written, before
the run.

### SM rulings (2026-11-08, second message) and the changed declaration

- **Audit: reuse `AuditAction.DATA_EXPORT`** (its label "Data export" is
  already approved; no new label). The target is `Student` with the
  student's id. The metadata is `file_count`, `item_count` and `scope`
  (`all`, `school` or `courses`).
  - **How the allow-list works:** an action's entry in
    `METADATA_ALLOWLIST` is the only set of keys it accepts, and every
    such key must also be in `ALLOWED_KEYS` (`audit/tests_metadata.py`).
    So `scope` is added to `ALLOWED_KEYS`, and `DATA_EXPORT` gets
    `{file_count, item_count, scope}`. Its values are short strings with
    no `@`, so they pass `sanitise`'s shape and personal-data rules.
  - **No schema change:** `metadata` is a JSON column, and the action
    already exists.
  - My first audit test assumed the global list and the per-action list
    combine; they don't. Corrected before any run.
- **a4c6f1fd9** (H-130 parity) is accepted as written.
- **Q5:** ai_summary stays out. The owner is told, with the AI draft fields.
- **R5, the owner's decision of 2026-11-08: YES.**
  - `TEACHER_EXPORT_ALLOWED = True`, scope `courses`.
  - **What "the teacher's courses" means:** a course has exactly one link
    to a teacher, `Course.teacher`, its owner. The code has no co-teacher
    or assignment model; I searched every relation to the user model and
    to Course. So a teacher's courses are the ones they own.
  - **The teacher's export:** the course-scoped tables (StudentCourse,
    StudentSubmission through the assignment, ChatSession) in those
    courses only, plus a profile limited to `TEACHER_PROFILE_FIELDS` (id,
    first_name, middle_name, last_name, email). It has no activity,
    settings, risk alert, processing records or image.
  - **Every refusal is json_404**, the unknown-URL answer: the same bytes
    for the flag being off, an unknown student, a caller out of scope and
    a target that isn't a student.
  - **The row added to the matrix:** a teacher at the student's school with
    a course of their own, which the student isn't in, gets exactly the
    same response as a teacher from another school. Patching the switch
    off gives json_404 to every teacher.
- **Plant E5 changes** (the old E5, "defaulted to True", is now the
  default): **E5 is the teacher's course filter dropped**, so a teacher
  gets every course.
- **R2** is still with the owner, and step 3 is unchanged.

The test file is `students/tests_data_export.py`, with 42 tests:
- the field guard: 6;
- content: 11;
- the caller matrix: 14;
- teachers: 6;
- audit: 2;
- the flag: 3.

`audit/tests_route_coverage.py` lists `student-data-export` in
`CONCEALED_ROUTES`. `tags/tests_catalog.py` only checks that its own routes
are in that list, so it isn't affected.

**Prediction for run 08 (red):** `students.tests_data_export` and
`audit.tests_route_coverage`. Ran 23, FAILED (failures=1, errors=1).
- **The 1 error:** `students.tests_data_export` fails to import (it
  imports `students.data_export`, which doesn't exist). The loader reports
  that as one failed test, so none of the 42 tests run.
- **The 1 failure:** `test_every_registry_entry_names_a_real_route`, on
  the subtest `route='student-data-export'` (the route doesn't exist yet).
- The other 21 route-coverage tests pass. (22 tests are counted from the
  file, plus the one failed import, makes 23.)

Run 08 result (`08_red_export.log`): Ran 23, FAILED (failures=1, errors=1).
- The error: `ModuleNotFoundError: No module named 'students.data_export'`.
- The failure: the registry test, on `route='student-data-export'`.
- **As predicted.**

**A test corrected before green (recorded, not hidden).** `DATA_EXPORT`
already had an allow-list entry, `{file_count, file_size_bytes}`
(audit/metadata.py), and I hadn't read it when I wrote the audit tests.
The code widens that entry and does not narrow it, so the entry is now
`{file_count, file_size_bytes, item_count, scope}`, and the export records
`file_size_bytes` too. The two audit tests that asserted the exact key set
now include `file_size_bytes`. `scope` is added to `ALLOWED_KEYS`. No schema
change.

Code: `students/data_export.py` (the declarations, `EXPORT_CALLERS`,
`build_export`) and `students/views_data_export.py`.
- The view returns `json_404` for every refusal.
- Submissions are shown through the student's own helpers:
  `max_points_shown` (H-133), `answer_document_for_student` (H-130),
  `student_safe_feedback` and `student_safe_formatted_grade`.
- The final grade is `released_course_grade`.
- The route `students/<uuid:pk>/data-export` is added, along with
  `STUDENT_DATA_TOOLS_ENABLED` (default False) in settings.

**Prediction for run 09 (green):** these modules:
- `students.tests_data_export`
- `students.tests_student_data_map`
- `audit.tests_route_coverage`
- `audit.tests_metadata`
- `audit.tests_enums`

Ran 115 (42 + 20 + 22 + 28 + 3, counted from the files), OK.

Run 09 result (`09_green_export.log`): Ran 115, **FAILED (failures=3).
The prediction was wrong** (it said OK), and it is left as written. The
causes:
1. **A real code bug, caught by the SM's added matrix row:**
   `test_an_account_that_is_not_a_real_super_admin_is_refused`. The
   account with user_type SUPER_ADMIN but no `is_superuser` got 200.
   `_super_admin` read `caller.is_real_super_admin` without calling it.
   It is a method (users/models.py:192), so the bare reference is always
   truthy, and any SUPER_ADMIN-typed account passed. Fixed by calling it.
   The other subtest, a TEACHER with `is_superuser`, was correctly refused.
2. **My fixture was wrong:** `test_a_released_grade_is_in_the_export`. The
   student's whitelist (`student_safe_feedback`) copies
   `overall_performance_analysis` only when it is a dict, and my fixture
   stored a string, so the projection was `{}` (correctly). The fixture
   now stores `{"summary": "Good work"}`. The code is unchanged.
3. **My registry entry was wrong:**
   `test_every_registry_entry_names_a_real_route`. `CONCEALED_ROUTES`
   belongs to the **write**-route sweep (`write_routes()` keeps only
   routes with POST, PUT, PATCH or DELETE). The export route is GET only,
   so it can't be listed there. The entry is removed. My
   `test_the_route_is_concealed_in_the_route_sweep` is replaced by
   `test_the_route_is_read_only`: the route answers no write method, so the
   write sweep can't miss it. The dark behavior is still pinned by
   `test_off_by_default_everyone_gets_the_unknown_url_answer`. The erasure
   route (step 3, POST) is a write route and **will** be listed in
   `CONCEALED_ROUTES`. This changes R11 for the export route, which I'm
   reporting to the SM.

Log numbers shift by one: 10 green again, 11–15 the plants E1–E5, 16 the
gate.

**Prediction for run 10 (green, the same 5 modules):** Ran 115, OK. The
count is unchanged, because one test is replaced by another.

Run 10 result (`10_green_export.log`): Ran 115, OK. **As predicted.**
`makemigrations --check`: no changes.

**After lint (pre-commit refused the commit; no behavior change intended):**
- mypy: the view returns `json_404` for a missing student before computing
  the scope, so the student is never None. That was the same branch before.
- flake8 F811: the released-grade lookup is `partial(released_course_grade,
  submissions)` instead of a nested def.
- flake8 B907: `Content-Disposition` is built with Django's
  `content_disposition_header`.

Log numbers shift again: 11 green after lint, 12–16 the plants E1–E5, 17
the gate.

**Prediction for run 11 (the same 5 modules):** Ran 115, OK.

Run 11 result (`11_green_after_lint.log`): Ran 115, OK. **As predicted.**

mypy again: `content_disposition_header` is typed `str | None`. The header
now falls back with `or "attachment"`; it is never None for an attachment,
so behavior is the same. Logs: 12 green, 13–17 the plants E1–E5, 18 the
gate.

**Prediction for run 12 (the same 5 modules):** Ran 115, OK.

Run 12 result (`12_green_after_typing.log`): Ran 115, OK. **As predicted.**

### Plants (predictions, before any plant runs)

Each plant is run on `students.tests_data_export` and
`students.tests_student_data_map` (62 tests), one plant per run. The file
is restored from a saved copy, and its sha256 is checked. A prediction
names the tests that must fail (as FAIL or ERROR). "Only" means no other
test fails.
- **13, E1:** the student filter is dropped on the StudentSubmission
  export (every submission is exported). **Killed** by
  `test_only_the_students_own_rows` and
  `test_a_teacher_gets_their_own_courses_only`, and only those.
- **14, E2:** `authtoken.Token` is set to `export=True` in the map.
  **Killed** by:
  - `test_sign_in_data_is_deleted_and_never_exported` (the map pin);
  - `test_every_field_is_decided_exactly_once`, on the subtest
    `authtoken.Token`, which has no field list;
  - every test that builds a full export: the build looks up a name for
    Token that doesn't exist, so the request answers 500.
  - Not killed: the teacher tests, because Token isn't course-scoped.
- **15, E3:** `_school_admin`'s school check is dropped. **Killed** by
  `test_a_school_admin_of_another_school_is_refused`,
  `test_a_school_admin_with_no_school_is_refused` and
  `test_a_student_with_no_school_is_refused_to_a_school_admin`, and only
  those.
- **16, E4:** `users.UserActivity` is skipped when the zip is built.
  **Killed** by `test_every_exported_table_has_its_file_even_when_empty`
  and `test_only_the_students_own_rows`, and only those.
- **17, E5:** the teacher's course filter is dropped in `_rows` (a
  teacher gets every course of the course-scoped tables). **Killed** by
  `test_a_teacher_gets_their_own_courses_only` and
  `test_the_other_courses_teacher_sees_only_that_course`, and only those.
