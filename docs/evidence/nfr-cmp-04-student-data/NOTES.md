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

### Plant results (each restored from a saved copy, sha256 matched)

| Run | Plant | Result | Verdict |
|---|---|---|---|
| 13 | E1: student filter dropped (submissions) | killed, 4 failures: `test_only_the_students_own_rows` (the other student's answer and submission id, plus the id set) and `test_a_teacher_gets_their_own_courses_only` | **as predicted** |
| 14 | E2: Token exported | killed, 18 failures: the map pin, the field guard (`authtoken.Token`), and all 15 tests that build a full export, each answering 500 from `KeyError: 'authtoken.Token'`. The teacher tests pass. | **as predicted** |
| 15 | E3: school check dropped | killed, 4 failures: the 3 predicted, plus `test_a_refusal_writes_no_event`. Its refused caller is the other school's admin, who now succeeds and writes an event. | **prediction incomplete**: I missed that this test depends on the school check |
| 16 | E4: UserActivity skipped | killed: 1 failure (`..._has_its_file...`, on the subtest `users.UserActivity`) and 1 error (`test_only_the_students_own_rows`, a KeyError reading the missing file) | **as predicted** |
| 17 | E5: teacher's course filter dropped | killed, 2 failures: the 2 predicted | **as predicted** |

5 of 5 killed. 4 were as predicted and 1 was incomplete. Nothing was amended.

### SM review of step 2 (accepted at df4b5c138, with two fixes)

Approved:
- the widened DATA_EXPORT allow-list and the `scope` key;
- R11 for the GET route as built: the read-only assertion, plus the dark
  bytes pinned for every caller;
- the step 3 POST erasure route goes in `CONCEALED_ROUTES` (confirmed).

The SM also searched the tree and found no other bare `is_real_super_admin`
reference.

**Fix (1), HEAD.** The view allowed `head`, and Django serves HEAD
through `get()`, so a HEAD request built the whole zip and wrote a
DATA_EXPORT event for an export nobody downloaded. `head` is dropped (get
and options remain), so a HEAD request answers 405 (DRF's method not
allowed). Test: `test_head_is_refused_and_writes_no_event`.

**Fix (2), an anonymous caller with the flag on.** `IsAuthenticated`
answered 401, which told an anonymous caller the route exists. The
preferred fix is chosen: an anonymous caller gets the unknown-URL answer,
in the exact bytes.
- **Where:** not in `dispatch()`. The JWT is read inside DRF's `initial()`
  (authentication), which runs after `dispatch()` begins, so `dispatch()`
  can't yet tell an anonymous caller from a signed-in one.
- The view overrides `handle_exception`: `NotAuthenticated` and
  `AuthenticationFailed` (a missing login and a bad or expired token)
  answer `json_404`. Every other exception goes to DRF as before.
- **Tests:** `test_anonymous_is_refused_with_401` is **replaced** by
  `test_anonymous_gets_the_unknown_url_answer`. That changes an existing
  assertion, as the SM ruled. `test_a_bad_token_gets_the_unknown_url_answer`
  is added.
- The module docstring stays true: every refusal gets the unknown-URL
  answer.

**Prediction for run 18 (red, `students.tests_data_export`):** Ran 44
(42 - 1 replaced + 3 new), FAILED (failures=3):
- HEAD: 200 != 405;
- anonymous: 401 != 404;
- bad token: 401 != 404.

Log numbers: 18 red, 19 green, 20 the gate. The fixes get no plant of
their own; the SM's list didn't ask for one.

Run 18 result (`18_red_head_anonymous.log`): Ran 44, FAILED (failures=3):
HEAD 200 != 405, anonymous 401 != 404, bad token 401 != 404. **As
predicted.**

**Prediction for run 19 (green):** the 5 step 2 modules, Ran 117
(44 + 20 + 22 + 28 + 3), OK.

Run 19 result (`19_green_head_anonymous.log`): Ran 117, OK. **As predicted.**

**Prediction for run 20 (the step 2 gate, under
`flock ~/.machine-fullsuite.lock`): `students users audit`.**
- **Count:** Ran 2445, from `DiscoverRunner.build_suite`'s
  `countTestCases()`, nothing run. OK.
- **Skips:** only opt-in or environment ones, counted after the run, not
  predicted. These are:
  - `users/tests_activity_middleware_load.py` (load tests, Redis);
  - `users/tests_google_auth.py` (network);
  - `students/tests_async_edit_path.py`'s `skipUnless` class;
  - the audit benchmarks (`AUDIT_BENCH`);
  - `audit/tests_metadata.py:151`, a conditional guard.
- No migration was added, so `AutoGrader.tests_migration_rollback_defaults`
  isn't needed (makemigrations --check: no changes).

Run 20 result (`20_gate_step2.log`): Ran 2445, **FAILED (failures=4,
skipped=7). The prediction (OK) was wrong**, and it is left as written.
The 7 skips are all opt-in: AUDIT_BENCH ×2, CI_REQUIRE_NETWORK ×3 (1 plus
2 live Google tests), CI_REQUIRE_REDIS ×1 and RUN_REAL_AI ×1. Every
failure is a source-scanning guard I didn't run before the gate:
1. `students.tests_h279_regrade_pending_approval` ...
   `test_the_names_are_read_nowhere_a_student_route_reads`.
   `students/data_export.py` names `pending_result` and `pending_graded_at`,
   but only as keys of `EXPORT_OMITTED`, to leave them out. It is not on
   the guard's allowed list.
2. `students.tests_reading_label_static` ...
   `test_only_the_listed_files_name_a_reading_column`. The same cause, for
   the six `answers_*` reading columns, which are in `EXPORT_OMITTED`.
3. `audit.tests_h273_query_filters` ...
   `test_every_target_type_the_code_writes_has_a_label`. The SM ruled the
   target type `"Student"`, which has no entry in `audit/serializers.py`
   `_TARGET_LABEL`. A label there is user-facing wording.
4. `users.tests_roads_that_sign_in_or_activate` ...
   `test_every_site_is_on_the_named_list_with_its_count`:
   `('tags/_c1_fixtures.py', 'PASSWORD_KEYWORD'): found 1, pinned 0`.
   **This predates this branch.** The same test fails on the C1 branch at
   703310b47, which is staging now, re-run in the C1 worktree. It was
   introduced by C1 (my lane), and my C1 near runs didn't include `users`.

The fixes are proposed to the SM, and none is applied before the ruling.

The log is too large for the repo's large-file hook, so the gate log lives
outside the repo, as C1's near-run logs do:
`~/Documents/Projects/GAP-a0-runs/nfr-cmp-04/20_gate_step2.log`.

## Step 3: erasure (EXPECTATIONS ONLY: no code and no red until the SM rules)

### Route and flag
- **Route:** `POST students/<uuid:pk>/erase`, named `student-data-erase`.
  It sits behind the same `STUDENT_DATA_TOOLS_ENABLED` and the same
  `dispatch()` and `handle_exception` as the export: flag off, anonymous, a
  bad token and every refusal by scope all give the unknown-URL bytes.
- It is a write route, so it **is** listed in `CONCEALED_ROUTES` (SM
  confirmed). Only POST and OPTIONS are allowed.
- No request body: the caller's role decides the scope. There is no
  server-side confirmation count; D7 makes the second confirmation
  frontend-only.

### Who may erase, and how much: `ERASE_CALLERS`, one place
- **Super admin** (`is_real_super_admin()`): a full erasure of any student.
- **School admin:** a full erasure of a student whose own `school_id`
  equals the admin's, never null (R6, D3).
- **Teacher:** a course erasure (D3) of a student in a course the teacher
  owns. The student is removed from that teacher's courses only, and the
  account stays.
- **Everyone else**, including the student themselves and another
  student: json_404, as are an unknown id and a target that isn't a
  student.

### What a full erasure does, in one transaction with the student row locked
1. **Refusals, checked first, in this order.** Any refusal changes nothing
   and writes no erasure event. Each has its own reason code, and its text
   is PENDING:
   - **NOT_A_STUDENT:** any `NOT_A_STUDENT` column in the map points at
     the student. One `exists()` per entry, driven by the map, so a new
     entry is enforced automatically.
   - **Wallet not empty:** `CreditWallet.total_remaining_credits() > 0`,
     or either `dispute_deficit_credits` or `refund_deficit_credits` is
     above 0. A deficit means credits are owed, so the wallet isn't empty.
   - **Billing records (owner R2, 2026-11-08: REFUSE):** any
     `BillingTransaction.user`, `BillingTransaction.performed_by` or
     `PaymentDispute.user` row points at the student. Those columns are
     SET_NULL, so deleting the user would cut the records' only link.
2. **Deletes, each from the map and explicit (never left to a cascade):**
   - StudentCourse first, then StudentSubmission. With the enrollment
     already gone, the grade recalculation fired by a submission delete
     finds nothing to rewrite.
   - Then every other DELETE entry matched to the user, including
     `OutstandingToken`, which is SET_NULL and would otherwise be left
     unlinked.
   - The EMAIL entries: BetaWhitelist and Waitlist, `email__iexact` the
     student's email (R7).
   - The empty CreditWallet, along with its empty buckets by cascade.
     This is ORM calls only; billing/* is not edited (R10).
3. **The erased-student record:** a new model,
   `students.ErasedStudentRecord`, written before the user row goes.
   - Fields: `original_user_id` (a UUID, unique), first, middle and last
     name, email, `erased_at`, `erased_by_id` (a bare UUID), and
     `keep_until` = `erased_at` + 7 years.
   - The migration is additive, a new table with its constraints in
     CreateModel (H-217).
   - The record is **not** registered in the Django admin, so no label or
     wording is needed (SM: read-only or not at all).
   - Its own guard entries: `original_user_id` is KEEP_RECORD, and
     `erased_by_id` is NOT_A_STUDENT. The widened `_by_id` pattern catches
     `erased_by_id`.
4. **Then the user row is deleted.** Anything left that references the
   user must be a KEEP_RECORD bare id (AuditEvent, CreditLedger,
   CreditUsageLog, AICallRecord), which the deletion leaves alone.
5. **The profile image (R8):** deleted from storage on commit. A storage
   failure doesn't undo the erasure (the row is already gone) and is
   logged at ERROR.
6. **One erasure audit event** (the action needs your ruling: **E-Q1**).
   Target `CustomUser` with the student's id, if G3 is approved.
   Metadata: `item_count` and `scope` (`full` or `courses`), no names.

### What a course erasure (teacher, D3) does
- It runs the course-scoped entries only, filtered to the teacher's
  courses: StudentCourse, then StudentSubmission (through the assignment),
  then ChatSession.
- BackgroundProcessingTask rows whose submission is deleted go with it,
  by the existing CASCADE. Rows with no submission stay. **E-Q2:** should
  BackgroundProcessingTask be course-scoped too, through its assignment?
- The account, the wallet, the risk alert, activity, settings, security
  data and the image all stay. There's no erased-student record.
- The only refusal is that the target must be a student in one of the
  teacher's courses (json_404 otherwise). NOT_A_STUDENT, the wallet and
  billing refusals don't apply, because the account stays.

### Tests (all in `students/tests_data_erase.py`)
- **Only the target's rows:** two students share every table (course,
  assignment, chat, settings, activity, tokens, OTPs, the whitelist and the
  waitlist). For every DELETE entry, the target's rows go, and the other
  student's rows are unchanged, by count and by id.
- **Course scope:** rows in another teacher's course are untouched, and
  the account stays. Final grades in the student's other courses are
  unchanged.
- **KEEP_RECORD rows** (AuditEvent, CreditLedger, CreditUsageLog,
  AICallRecord) are identical before and after: the same rows, with every
  column equal.
- **Refusals,** each changing nothing (row counts in every mapped table
  unchanged, no erasure event):
  - one test per NOT_A_STUDENT entry, driven from the map;
  - wallet credits;
  - a dispute deficit;
  - a refund deficit;
  - **R2, one test per table:** BillingTransaction.user,
    BillingTransaction.performed_by and PaymentDispute.user;
  - **R2:** a student with **only** a `performed_by` row is refused.
- **The erased-student record** is written with every field, and
  `keep_until` = `erased_at` + 7 years.
- **R7:** a whitelist or waitlist row whose email differs only in case is
  deleted. A different email is untouched.
- **R8:**
  - the image is deleted from storage after commit;
  - a storage failure leaves the erasure done and logs ERROR;
  - a rolled-back erasure deletes no image.
- **Grade history:** no event written during the erasure (GRADE_CHANGE,
  ROSTER_CHANGE, the erasure event) carries the student's name or email
  in any field. Ids only.
- **Atomic:** a failure injected at the user-row delete rolls everything
  back, including the record and the deletes.
- **OutstandingToken rows** are deleted, not unlinked.
- **The erased account** can't sign in, and its old JWT gets the
  unknown-URL bytes.
- **A second erasure** gets json_404.
- **The caller matrix:**
  - super admin;
  - school admin of the same school, of another school, with no school;
  - a student with no school (school admin refused, super admin allowed);
  - the student themselves, another student;
  - a teacher of the course, a teacher of another course at the same
    school (the same bytes as a stranger);
  - a non-student target, an unknown id;
  - anonymous, a bad token;
  - HEAD and GET (405, nothing deleted);
  - flag off.
- **The route:** it is in `CONCEALED_ROUTES`, and the route sweep passes.

### Plants (one per run)
- X1: the student filter dropped on one DELETE entry (it deletes the other
  student's rows).
- X2: the course scope dropped for a teacher.
- X3: the NOT_A_STUDENT check skipped.
- X4: the wallet check skipped.
- X5: the R2 billing check skipped.
- X6: the erased-student record not written.
- X7: `iexact` changed to `exact` (R7).

### Wording (all PENDING, for the owner's single list)
- The refusal texts: not only a student (NOT_A_STUDENT), the wallet holds
  or owes credits, and billing records (R2).
- The success messages: the full erasure, and the course erasure.
- The erasure audit action's label, if E-Q1 is a new action.

### Questions for the SM
- **E-Q1, the audit action for an erasure.** Nothing existing fits
  exactly. The options:
  - a new `STUDENT_DATA_ERASE` with a PENDING label;
  - `ADMIN_ACTION` for full erasure, which doesn't fit a teacher's;
  - `ROSTER_CHANGE` for a course erasure, which is already written for
    each StudentCourse delete.

  I propose a new `STUDENT_DATA_ERASE` with the label PENDING, one action
  for both scopes, and `scope` in the metadata.
- **E-Q2:** BackgroundProcessingTask is course-scoped through its
  assignment, so a course erasure removes processing rows that have no
  submission. I propose yes.
- **E-Q3:** after a course erasure, should the student's risk alert be
  left alone (R3 said no recompute)? I propose yes, left alone.

### SM rulings on gate 20 (2026-11-08), applied

- **G1, G2 (approved as proposed):** `students/data_export.py` is added to
  the H-279 guard's `allowed` set and to the reading-label guard's
  `MAY_NAME`. The reason given: it names those columns only to leave them
  out of the export (`EXPORT_OMITTED`), pinned by
  `test_the_q1_q2_declarations`. The explicit `EXPORT_OMITTED` list stays,
  because default-deny would lose the "every new field is decided" guard.
- **G3 (approved):** the export event's `target_type` is `"CustomUser"`,
  with the approved label "User account". No new wording. The audit test
  is updated to match.
- **G4 (the SM's ruling: a rename, not a pin):** done on
  `task/c1-fixture-rename` at 79a8fcd48, off staging 703310b47. Its gate
  39 ran 284, OK. It is waiting for the SM's review and push. This branch
  takes it through the next staging merge.
- **Process (SM):** every near run or gate now includes `users` and the
  students and audit source-scanning guards.
- **The order:** the SM's G4-first message arrived after the step 3
  expectations were committed (efe737a18). They are documentation only,
  and the SM has been told.

Gate 21 runs after the staging merge that brings G4, with its own
prediction.

### Gate 21 (after the staging merge that brings G4)

`origin/staging` 79a8fcd48 (G4, the fixture rename) is merged at
604848e74. `students/tests_data_export.py` now imports
`tags._c1_test_fixtures`.

**Prediction for run 21**, under `flock ~/.machine-fullsuite.lock`:
`students users audit tags`. This includes `users` and every students and
audit source-scanning guard, per the SM's process note.
- **Count:** Ran 2578 (students 1017, users 982, audit 446, tags 133, from
  `build_suite`, nothing run), OK.
- **Skips: 7**, the same opt-in ones as gate 20: AUDIT_BENCH ×2,
  CI_REQUIRE_NETWORK ×3, CI_REQUIRE_REDIS ×1, RUN_REAL_AI ×1.
- makemigrations --check: no changes.
- The log goes to `~/Documents/Projects/GAP-a0-runs/nfr-cmp-04/21_gate_step2.log`.

Run 21 result (`GAP-a0-runs/nfr-cmp-04/21_gate_step2.log`): Ran 2578, OK
(skipped=7), with the same 7 opt-in skips. **As predicted.** Step 2 is
done.

## Step 3: SM rulings on E-Q1–E-Q3 (2026-11-08); the red is a GO after gate 21

- **E-Q1:** a new `AuditAction.STUDENT_DATA_ERASE`, one action for both
  scopes. Its label is PENDING, on the owner's wording list. Metadata:
  `scope` ("full" or "courses") and `item_count`. No names or emails.
- **E-Q2:** BackgroundProcessingTask is course-scoped through its
  assignment. This changes the map: `course_scoped=True`, and
  `COURSE_PATH` gets `assignment__course_id`. The export follows: a
  teacher's export now includes those processing rows for their courses.
  **This is a declaration change, pinned by a map test.**
- **E-Q3:** after a course erasure, the risk alert is left alone, with no
  recompute (R3). Pinned by a test.

### Step 3 declarations, completed before the red

**Found while designing the fixtures: the wallet's history is tied to kept
records.**
- `CreditUsageLog.wallet`, `CreditUsageLog.bucket` and `CreditLedger.bucket`
  are `DO_NOTHING` foreign keys. Deleting the user cascades to the wallet
  and on to its buckets. If any kept ledger or usage row points at those,
  the database's foreign keys fail at commit, and a kept record would lose
  its target.
- A student's uploads bill the **teacher's** wallet (`upload_door`), so a
  student's own wallet is normally an empty row with no buckets. That case
  is safe to delete.
- **Declared:** "wallet not empty" also covers **a wallet that has any
  bucket** (credit history), not only one with credits or a deficit. It is
  the same refusal and the same code, so no new wording. **For the SM to
  confirm at the checkpoint.**
- Test: a wallet holding one spent bucket (0 remaining) that a CreditLedger
  row references is refused, and nothing changes.

**Refusal codes** (new `ReasonCode` entries in `REASON_CODES`). Each is
ErrorClass VALIDATION, HTTP 409, not retryable. The message and the enum
label are both the placeholder "PENDING OWNER APPROVAL", with no new
wording:
- `STUDENT_ERASE_NOT_ONLY_A_STUDENT`
- `STUDENT_ERASE_WALLET_NOT_EMPTY`
- `STUDENT_ERASE_BILLING_RECORDS` (R2)

**Success:** 200 with the house envelope, "Request Successful", which is
existing wording. The data is `{scope, item_count}`. The success texts on
the wording list belong to the frontend, and the backend adds none.

**How NOT_A_STUDENT is tested.** I'd declared "one test per entry, driven
from the map", but the map has 23 such entries, across models with very
different required fields. So the test has two parts:
- a structural test: the refusal check's entry list equals
  `entries_with_role(NOT_A_STUDENT)` exactly, so a new map entry is
  enforced without a test change;
- real-row tests for the entries a student account can plausibly hold:
  `Course.teacher`, `Tag.teacher`, the bare `Tag.created_by_id`, and
  `BatchUploadSession.teacher`.

That is a narrowing of "one test per entry", for the SM to rule on.

**The map gains** (with the model): `students.ErasedStudentRecord`
`original_user_id` as KEEP_RECORD, and `erased_by_id` as NOT_A_STUDENT
(an admin's or teacher's id). The guard requires both once the model
exists. **E-Q2:** BackgroundProcessingTask becomes `course_scoped=True`.

**Prediction for run 22 (red):**
- The modules: `students.tests_data_erase`, `students.tests_student_data_map`
  and `audit.tests_route_coverage`.
- `tests_data_erase` can't import `students.data_erase`, which doesn't
  exist, so the loader reports 1 error.
- 1 failure: the new map pin, `test_processing_records_go_with_a_teachers_erasure`.
- 1 failure: the route-registry test, on the subtest
  `route='student-data-erase'`.
- Ran 44 (21 + 22 + 1), FAILED (failures=2, errors=1).

Run 22 result (`22_red_erase.log`): Ran 44, FAILED (failures=2, errors=1):
- the error: `ModuleNotFoundError: students.data_erase`;
- the failures: the registry test on `student-data-erase`, and the E-Q2
  map pin.

**As predicted.** `students/tests_data_erase.py` holds 43 tests (full 13,
refusals 13, course 6, matrix 7, flag and catalogue 4), not run yet
because of the import error.
