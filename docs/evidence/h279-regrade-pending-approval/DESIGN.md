# H-279 design notes (STEP 1: design only, no code)

Ticket (owner, 2026-11-02): an AI re-grade of an already released grade must
be approved by the teacher before students see it.
Branch task/h279-regrade-pending-approval from origin/beta e11e5653.

## 1. Survey: AI grading paths that can reach a released submission

Every AI grading goes through one chokepoint:
`students/services.py: grade_engine` → `_run_grading_pipeline` →
`_populate_and_save_grade`, whose final write is
`submission.save(update_fields=GRADING_RESULT_FIELDS)` (services.py:915),
inside `cancellable_final_save` (an atomic block) and `history.suppressed()`.

| # | Path | Entry | Can it reach a released row? |
|---|------|-------|------------------------------|
| P1 | Synchronous grade | students/views.py:913 `grade` | **Yes**: no graded filter |
| P2 | grade-async | students/views.py:954 → `grade_engine_async` | **Yes**: no graded filter |
| P3 | Scheduled single grading | students/views.py `schedule_grade_async` → PeriodicTask → `grade_engine_async` | **Yes**: no graded filter |
| P4 | Batch item retry | students/item_retry.py:151 → `grade_engine_async` (no `only_if_ungraded`) | **Yes, by race**: the item was queued ungraded, but the paper may have been graded and released meanwhile |
| P5 | grade-all | assignments/views.py:1753 filters `graded_at__isnull=True` at dispatch; the run does not pass `only_if_ungraded` | **By race only** (graded and released between dispatch and run) |
| P6 | Legacy `grade_all_submissions` | assignments/tasks.py:132, ungraded filter, lazy iteration | By race only (no dispatcher left) |
| P7 | Scheduled batch / due-date auto-grade | `grade_batch_async`, `_dispatch_tracked_grading` with `only_if_ungraded=True` | **No**: the claim refuses graded rows (H-215a) |
| P8 | Celery redelivery / duplicate | grade_engine claim | Covered by whichever path above it belongs to |
| P9 | benchmark `--persist` | ai_processor/benchmark/runner.py | Local tool only |

Not AI grading, but they touch grade columns on a released row:
- `update_grade` (students/views.py:1157): the teacher's manual grade.
- `teacher_feedback` (students/views.py:1077): queues or writes `formatted_grade`.
- `formatted_grade_async` (assignments/tasks.py:~1190): the follow-up that writes `formatted_grade` if the result stamp still matches (H-145).
- `mark_reviewed`: review flags only.
- Re-upload and raw-text edit: already closed on graded rows (`_check_submission_open`, `ensure_submission_open`).

**Conclusion:** one write site (`_populate_and_save_grade`) covers P1–P9. The
held/not-held decision must be made **under the row lock at the final save**,
not when the run starts. A teacher can publish the old grade while a run is
in flight; that is today's leak through P1–P5 as well.

## 2. Survey: readers of the grade columns

Columns: score, score_percentage, max_points, feedback, formatted_grade,
raw_input, graded_at, grade label (LABEL_FIELDS), plus review fields.
These are production references (tests, docs, migrations and benchmark
excluded). They are submission-level attribute reads, quoted names and ORM
lookups, by file:

| File | Refs | What reads |
|------|------|------------|
| students/serializers.py | 73 | student and teacher submission serializers, `max_points_shown`, the H-127/H-133 released-only gates |
| dashboard/views.py | ~48 | teacher, student and admin dashboards (14 ORM aggregates: `Avg("…score_percentage")`, `__gte` mastery filters, values()) |
| dashboard/services.py | 30 | dashboard service aggregates (4 ORM) |
| students/views.py | 27 | grade, update-grade, publish, PDF/doc routes (mixed reads and writes) |
| students/services.py | ~31 | `answer_document_for_student`, `notify_student_of_graded_submission`, the pipeline |
| assignments/serializers.py | 21 | assignment views with the student's submission |
| assignments/tasks.py | 16 | formatted-grade task, reconcile numbers |
| assignments/views.py | 13 | publish-all, exports (1 ORM) |
| ai_processor/services.py | ~2 relevant | student AI summary (5947: score_percentage, score) |
| dashboard/rigor.py, question_difficulty.py, risk.py, tasks.py | 5+4+…+1 | rigor (2 ORM), per-question difficulty from `feedback`, report text |
| classrooms/final_grade.py, signals.py, serializers.py, recalculate_final_grades | 3+2+2+1 | final grade (3 ORM in signals, 1 in the command) |
| assignments/services.py | 2 | at-risk (graded_at and is_published) |
| students/feedback_projection.py | 3 | student-safe feedback |

**Totals:** about 300 references in about 25 production files, **at least 25
of them ORM-level** (aggregates, filters, values). An ORM-level read cannot
take a Python helper; it would need a parallel column expression.

### Blast radius per option

- **(A) pending fields.** Changed readers for students and analytics: **0**.
  New or changed code is limited to about 8 sites:
  - `_populate_and_save_grade`
  - `emit_grading_completed` (metadata)
  - single publish
  - publish-all
  - `update_grade`
  - `formatted_grade_async` (+ `_reconcile_formatted_grade_numbers`)
  - teacher detail and list serializers (expose the pending result)
  - one migration

  Any reader added later is safe by default.
- **(B) released snapshot.** Every reader above that a student or an analytic
  sees must switch: about 200 references in about 18 files, including the 25+
  ORM aggregates.
  - The final grade and the dashboards would need snapshot columns that are
    kept in step for every non-held write (first grading, publish, manual
    grade, backfill of every released row).
  - It needs a backfill, which the ticket rules out ("Migration: no
    backfill").
  - Any reader added later that forgets the snapshot leaks.

**Recommendation: (A).**

## 3. Design (A)

### 3.1 Storage (one migration, no backfill)
Two new nullable columns on StudentSubmission:
- `pending_result` (JSONField, null): the whole held AI result, keyed by the
  column names it would have written:
  - score, ai_score, max_points, score_percentage, feedback,
    grading_confidence, graded_at (ISO), formatted_grade, raw_input
  - review: needs_review, review_reasons, review_severity, review_tier
  - LABEL_FIELDS
  - ai_graded_at, ai_grading_completed_at
- `pending_graded_at` (DateTimeField, null, db_index): the held result's
  grading time. It is the stamp and the "has a pending re-grade" filter.

Why a JSON blob and not about 20 `pending_*` columns: there is one place that
writes it and one that promotes it, nothing aggregates over it, and the
migration is small. The values go through the model fields on promotion, so
Decimal and datetime types are restored there.
(If SM prefers typed columns, the design is otherwise the same.)

### 3.2 The re-grade save (`_populate_and_save_grade`)
The result is computed exactly as today. At the final save, inside the
existing `cancellable_final_save` atomic block:
`select_for_update()` the row and read `is_published`.
- **Not released** (first grading, or a re-grade of an unreleased grade): it
  writes `GRADING_RESULT_FIELDS` exactly as today. No change.
- **Released:**
  - It writes only `grading_state=DONE`, `pending_result` and
    `pending_graded_at`.
  - Every released column, including `formatted_grade` and `raw_input`, is
    left byte-for-byte as it was. The H-146 clear of `formatted_grade` does
    not apply here, because the released text still matches the released
    score.
  - Test: the snapshot of every GRADING_RESULT_FIELDS column except
    grading_state is equal before and after.

The decision is made under the lock, so a publish that lands mid-run sends
the result to pending.

### 3.3 Approval: reuse the existing publish endpoints (no new endpoint, no new wording)
"Publish" already means "release to the student".
- **Single** `POST …/publish`:
  - Today, on an already published row, it is a 200 no-op.
  - New: if `pending_result` is set, it promotes under `select_for_update`.
    Promotion copies the blob into the main columns (graded_at := the held
    graded_at) and clears both pending columns.
  - The promotion is an **instance save** (`update_fields`), not a queryset
    update, so post_save fires:
    - the history tracker writes the GRADE_CHANGE (§3.8);
    - the cache receiver runs (§3.9);
    - `update_student_course_final_grade` recalculates the final grade.
  - Then `notify_student_of_graded_submission(submission, is_update=True)`
    sends the existing "Updated grade" email.
- **publish-all:**
  - It does the same for every graded row of the assignment with a pending
    result, one locked promotion per row, and notifies each with
    `is_update=True`.
  - First-time publishing is unchanged.
  - `updated_count` counts promotions too. The message text is unchanged.

### 3.3a Student notification on approval (exactly once)
What `notify_student_of_graded_submission` does today (services.py:1183):
- It sends email only, through `send_email_task`.
- It skips @student.local addresses, unpublished rows, and students without
  `UserSettings.notify_grading_complete` (opt-in, default False).
- `is_update=True` gives the subject "Updated grade: …".

Today both publish paths notify only rows that flip is_published False→True.
A held re-grade sits on an already published row, so nothing would be sent.

New rule: **the email follows the promotion, not the flip.** One promotion
sends one email.
1. **One claim per approval.** `promote_pending(submission_id, actor)` runs
   in one atomic block:
   - It locks the row with `select_for_update` and re-reads `pending_result`.
   - If the result is NULL (already promoted, or never held), it returns
     None, and nothing is saved or sent.
   - Otherwise it copies the result into the main columns, clears both
     pending columns, does the instance save (GRADE_CHANGE, cache, final
     grade), and returns the promoted row.
2. **Send after commit.** The caller sends
   `notify_student_of_graded_submission(row, is_update=True)` through
   `transaction.on_commit`, and only when `promote_pending` returned a row.
   A rolled-back promotion therefore sends nothing. Two racing approvals
   serialize on the row lock, and the second sees NULL. So there is one
   email per approval, and the opt-in and @student.local rules apply
   unchanged.
3. **Single publish.**
   - Row not yet published: the flip, and the first-release email
     (`is_update=False`), exactly as today.
   - Row already published with a held result: `promote_pending`, and on
     success the "Updated grade" email.
   - Row already published with nothing held: a no-op, as today.
   A held result cannot exist on an unpublished row. The held save only
   happens when is_published is True, and nothing unpublishes. So a single
   request never takes both branches.
4. **publish-all over a mix of rows.**
   - New rows: the existing record_bulk flip and first-release emails,
     unchanged.
   - Held rows: `graded_submissions.filter(pending_result__isnull=False)`,
     each through `promote_pending` with its own lock, and an "Updated
     grade" email only for rows that `promote_pending` returned.
   - A row is in the flip set or the held set, never both: the flip set is
     `is_published=False` and the held set is `is_published=True`.
     Each row gets at most one email.
   - Two concurrent publish-alls: each row's claim is won once, so one email
     per row in total.
   - `updated_count` = flipped + promoted. The message text is unchanged.
5. **update_grade while held.**
   - It promotes and applies the edit in the same locked save.
   - Its existing `if submission.is_published: notify(…, is_update=True)` is
     the only email. The promotion inside it sends none of its own.
   So there is one email, not two.

No new wording: the existing "Updated grade" email, `is_update=True`.

Red tests (committed with the expectations before any code, after design
approval; email captured through a patched `send_email_task.delay`):

| Test | Expected |
|------|----------|
| N1 single publish of a held re-grade, opted-in student | 1 email, subject "Updated grade: …", content shows the new % |
| N2 double click (2 sequential publishes) | 1 email total; 1 GRADE_CHANGE |
| N3 2 threads racing single publish (TransactionTestCase) | 1 email; 1 GRADE_CHANGE |
| N4 publish-all over {new, held, released-nothing-held, ungraded} | new → 1 "Assignment graded"; held → 1 "Updated grade"; released-nothing-held → 0; ungraded → skipped; updated_count = 2 |
| N5 publish-all twice | second sends 0 |
| N6 publish-all ‖ single publish on the same held row (threads) | 1 email |
| N7 opt-out / @student.local student | 0 emails, promotion still happens |
| N8 update_grade on a held row | exactly 1 "Updated grade" email (not 2) |
| N9 the held re-grade itself | 0 emails until approval |
| N10 promotion rolled back (forced save error) | 0 emails |

### 3.4 Teacher hand-edit while pending (SM default)
`update_grade`: the hand edit is itself the approval.
1. Under the row lock, it promotes the pending result first.
2. It then applies the teacher's score on top of the promoted feedback,
   exactly as today: overridden review resolution, document rebuild,
   formatted-grade clear and re-queue, and the update notice when released.

The order matters: the teacher saw the new AI result and corrected it. Editing
the old base and discarding the pending result would lose the per-question
feedback the teacher was looking at.
`teacher_feedback` and `mark_reviewed` act on the released columns and do not
touch pending (see Q4).

### 3.5 A second re-grade while one is pending
It replaces `pending_result` and `pending_graded_at` wholesale. The claim
(C3) already stops two runs at once. Each run emits its own
GRADING_COMPLETED. Nothing is refunded for the replaced result.

### 3.6 Credits
Charged as today: one grading charge per run, held or not. No refund on
replace or approve. Q3 covers the follow-ups.

### 3.7 needs_review and the review queue
The held result's review fields stay inside `pending_result` and are promoted
with it. The released row's queue state is unchanged while the result is held,
and the review queue's semantics do not change. The teacher finds held
re-grades by `pending_graded_at` (teacher-only field and filter, §3.10). See
Q2 for the alternative.

### 3.8 Audit
- **The held re-grade:** GRADING_COMPLETED as today. Its before and after are
  empty (no released column changed). It gets one new metadata key,
  `"held_for_approval": true`, added to METADATA_ALLOWLIST for
  GRADING_COMPLETED.
- **Promotion:** one GRADE_CHANGE for score, score_percentage, max_points,
  graded_at and needs_review, through the existing history tracker. The actor
  is the approving teacher.
- **Hand edit while pending:** the promotion and the edit are one save, so
  there is one GRADE_CHANGE from released to edited.

### 3.9 Cache invalidation
- The held save, the promotion and the formatted-task write are instance
  saves, so the post_save receiver (`invalidate_submission_caches`) fires for
  each one.
- publish-all promotions are per-row instance saves.
- Tests check that a cached student detail, the student dashboard and the
  final grade show the released result until approval and the new one right
  after.

### 3.10 What the teacher sees
The teacher detail serializer (staff only) gets `pending_result` (the held
score, percentage, max_points, feedback, formatted_grade, review fields) and
`pending_graded_at`. The list serializer gets `pending_graded_at`, and the
list filter `?has_pending_result=true` is added.
- Student serializers (`StudentSubmissionDetailStudentVersionSerializer`,
  StudentUploadAnswer…) are fenced to never include these fields. This gets a
  static test in the H-127 style.
- The new names are API keys, not wording; SM approves the names (Q5).

### 3.11 Formatted-grade follow-up
- It is queued with the stamp of the **held** result.
- At write time under the lock:
  - if `pending_graded_at` still matches, it writes `pending_result.formatted_grade`;
  - else, if the main stamp matches (it was approved meanwhile), it writes
    main `formatted_grade` as today;
  - else it is superseded, as in H-145.
- `_reconcile_formatted_grade_numbers` is given the held numbers when it
  writes into pending.

### 3.12 Concurrency
Every writer below locks the row (`select_for_update`) for its final write
only. No lock is held across AI calls.

| Race | Outcome |
|------|---------|
| approve ‖ re-grade save | Serialized on the row. Save → approve: the newer result is promoted (see Q1). Approve → save: a new held result appears and needs its own approval. |
| first publish ‖ in-flight run | The save sees is_published under the lock, so the result is held (today it leaks) |
| two approvals (double click, single ‖ publish-all) | The second sees pending NULL: no-op, one GRADE_CHANGE, one email |
| update_grade ‖ re-grade save | Serialized. Either the edit promotes and edits and a later save holds a new result, or the save holds first and the edit promotes it |
| formatted task ‖ promotion | The stamp rules in §3.11 |

Planned threaded tests (TransactionTestCase, run_concurrently): approve ×N
racing a held save, and single-publish ‖ publish-all, with an exact count of
GRADE_CHANGE rows and emails.

### 3.13 Out of scope / unchanged
- Assignment status (ruling 1).
- Unreleased re-grades: they behave as today.
- P7 auto paths.
- Rejecting or discarding a held result: not in the rulings (Q6).

## 4. Questions for SM

- **Q1 (approve races a newer re-grade).** The teacher looked at held result
  R1 and clicks Publish, but R2 replaced it a moment earlier. Should approval
  promote whatever is held at lock time (R2), or must the request carry
  `pending_graded_at` and be refused if it is stale?
  - The second is safer, but a refusal needs a reason code and wording. No
    existing code fits; the closest is NOT_RETRYABLE.
  - I recommend the stamp plus a new code, if you approve the wording.
    Otherwise, promote R2 and return the promoted result in the response.
- **Q2 (review queue).** Should a held result that is flagged (grader
  disagreement or similar) appear in the review queue, or only under the new
  pending filter?
  - I recommend the pending filter only, so the queue does not change meaning.
  - The alternative is writing the review fields to the main row, which is
    teacher-only. But that means `mark_reviewed` would resolve flags of a
    grade that is not released yet.
- **Q3 (follow-up charges).** Today `student_summary_async` and the
  formatted-grade task are dispatched right after grading.
  - For a held result I propose: the formatted grade at grading time, as
    above; the AI student summary at approval instead. Otherwise the summary
    is regenerated (and charged) from the unchanged released numbers.
  - The number of charges is the same; only the timing moves. Is that OK?
- **Q4 (teacher_feedback).** Does editing or regenerating the formatted grade
  while a result is held count as approval? I propose no: it acts on the
  released text only.
- **Q5 (names).** Approve the names `pending_result`, `pending_graded_at`, the
  list filter `has_pending_result`, and the audit key `held_for_approval`.
- **Q6 (reject).** Should the teacher be able to discard a held result? It is
  not in the rulings. Today the only way out is approving it or hand-editing
  it.

## 5. Plan after approval (DOCTRINE)
1. NOTES, red tests and expectations committed first.
2. One mutant per guard (held save, lock-time decision, single promote,
   publish-all promote, hand-edit promotes first, replace, formatted-task
   stamp, student-serializer fence, audit key, cache/final grade), each in a
   disposable worktree with sha256 restore.
3. A near run only: no full suite without the owner's explicit go-ahead.
4. Unique test DB `test_AutoGrader_h279`, venv outside the worktree.
5. No Co-Authored-By trailer.
