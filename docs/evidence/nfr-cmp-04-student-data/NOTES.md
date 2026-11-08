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
