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
