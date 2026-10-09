# Epic A S4: before and after values (design, before code)

Branch `task/epic-a-s4`, cut from phase2/epic-a `19b2072`. Plan: `08_epic_a_completion_plan.md` §5, plus the SM's S4 notes:
- no `token_epoch` tracking;
- AI grading's before/after goes in `GRADING_COMPLETED`;
- `GRADE_CHANGE` is only for human or bulk changes;
- the `pre_save` cost is measured in Gate 6.

A code survey of this tree found five places where the code differs from what plan 08 assumed. **Rulings needed: R1–R6** (§4).

## 1. Mechanism
- **`audit/history.py`: one registry.** Each registered model maps to its tracked fields and its action. Two paths use it:
  - **Instance saves:** a `pre_save` receiver snapshots the tracked fields, reading the row only when the instance has a pk and the save's `update_fields` includes a tracked field. A `post_save` receiver diffs the snapshot and emits **one event per changed record**, with `before`/`after` restricted to the fields that changed. A create emits `before=None`; a delete (`post_delete`) emits `after=None`.
  - **Bulk writes:** a queryset `.update()` skips signals, so those call sites use `record_bulk(queryset, **changes)`. Inside one transaction it:
    1. reads `before` for the tracked fields (`select_for_update`);
    2. applies the update;
    3. emits one event per row whose value actually changed (D3).
- **Values are scalars only:** a Decimal becomes a string, a datetime an ISO string, an FK its id string, a bool/int as is. That fits `sanitise`'s scalar rules; there's no text field to leak.
- **A per-action `before`/`after` allow-list** (`audit/metadata.py`, `BEFORE_AFTER_ALLOWLIST`). Today `before`/`after` pass only the generic `ALLOWED_KEYS` check. S4 narrows them per action, just as `metadata` is. The registry is built from this allow-list, so a field can't be tracked without being allowed for its action. A test asserts that, for every registered model, the tracked fields ⊆ the allow-list, and that no tracked field names a person or their work.
- **Suppression context** (`audit.history.suppressed(reason)`), for:
  - the AI grading save (its values go into `GRADING_COMPLETED`);
  - `record_bulk` itself (so a bulk path's own saves never double-emit).
- **The actor** is S3's `audit.context.current_request_actor()`: the signed-in user, or SYSTEM outside a request. **S4 therefore depends on S3** (R6).
- **S1's generic event** is not written for a request that stored a named event, so an update-grade gives one `GRADE_CHANGE` and no STATE_CHANGE.

## 2. What is tracked (IDs, statuses, numbers only)
| Model | Fields | Action (retention) |
|---|---|---|
| `students.StudentSubmission` | `score`, `score_percentage`, `max_points`, `graded_at`, `is_published`, `needs_review` (R1) | `GRADE_CHANGE`, new (STUDENT_RECORD) |
| `classrooms.StudentCourse` | `enrollment_status`, `course_id` (create and delete included) | `ROSTER_CHANGE`, exists (STUDENT_RECORD) |
| `users.CustomUser` | `user_type`, `is_active`, `is_staff`, `is_superuser`, `school_id`. **Not** `token_epoch` (SM note 1), `password`, email, names, lockout counters or `last_login` | `PERMISSION_CHANGE`, exists (GENERAL) |
| `billing.UserSubscription` | `plan_id`, `is_active`, `is_trial`, `stripe_status`, `billing_cycle_end`, `cancelled_at`, `auto_renew` | `SUBSCRIPTION_CHANGE`, new (GENERAL) |
| `billing.LicenseSubscription` | `plan_id`, `is_active`, `max_seats`, `stripe_status`, `billing_cycle_end`, `auto_renew` | `SUBSCRIPTION_CHANGE` |
| `billing.SchoolCreditAllocation` (a seat) | `is_active` (create included) (R4) | `SUBSCRIPTION_CHANGE` |

**Never tracked:**
- `answers`, `raw_input`, `feedback`, `ai_feedback`, `formatted_grade` and `review_reasons` (the grader's per-question text);
- `StudentCourse.ai_summary`;
- the user's email, names, bio, image, password and activation token.

The PII check constraint on `AuditEvent` remains the backstop.

## 3. Where the writes are, and what each becomes
**Grades (StudentSubmission):**
| Route or path | Writes via | S4 |
|---|---|---|
| update-grade (`students/views.py:1125`) | `save(update_fields=...)` | signal, 1 `GRADE_CHANGE` |
| publish (`:1360`) | queryset `.update(is_published=True)` | `record_bulk`, 1 `GRADE_CHANGE` |
| publish-all-grades (`assignments/views.py:1807`) | queryset `.update` | `record_bulk`, 1 per student (D3) |
| mark-reviewed (`students/views.py:1404`) | queryset `.update(needs_review=False, review_reasons=...)` | `record_bulk` (tracks `needs_review` only), 1 `GRADE_CHANGE` |
| AI grading save (`students/services.py:434`, all three grading paths) | `save(update_fields=GRADING_RESULT_FIELDS)` | suppressed; before/after go into `GRADING_COMPLETED` (R2) |
| grading claim / claim-failed (`students/services.py:167/175`) | `.update(grading_state=...)` | untracked field; guard allow-list |
| lazy `raw_input` backfill on GET (`:330`) | `.update(raw_input=...)` | untracked; allow-list |
| Django admin `delete_selected`, API destroy | `.delete()` / `instance.delete()` | `post_delete`, `after=None` (R5) |

**Enrolments:** `remove_student_from_course` (an instance delete) and the StudentCourse API go through the signal. `activate_pending_enrollments_on_login` (`classrooms/services/enrollment.py:378`) and `_google_auth` (`users/views.py:2114`) move PENDING to ENROLLED with `.update` **on every student sign-in** (R3).

**Users:**
- The admin `activate_users`/`deactivate_users` actions (`users/admin.py:97/104`) become `record_bulk`, one `PERMISSION_CHANGE` per user.
- Admin change-form edits go through the signal.
- `revoke_all_sessions`, the lockout counters, `stamp_last_login` and the H-3 remediation command touch only untracked fields: allow-list.

**Subscriptions:**
- `activate_subscription`'s `.update(is_active=False)` (`billing/services.py:193`) becomes `record_bulk`.
- The two `total_credits_consumed` updates are untracked: allow-list.
- Everything else is instance saves.

**The S4 guard** is an AST sweep in the style of H-38 (`classrooms/tests_teacher_access_sweep.py`: `ALLOWED` + stale-entry + self-tests). Every production `.update(`/`bulk_update(` whose keywords name a tracked field must go through `record_bulk` or be listed with a reason. A bulk write of a tracked field can't skip history silently.

## 4. Rulings needed
- **R1. No `reviewed`/`reviewed_at` field exists.** The review state is `needs_review` (bool), and the resolution sits inside `review_reasons` JSON (grader text; never tracked). **Recommend:** track `needs_review`; mark-reviewed's event shows `needs_review: true → false`, and its actor gives the "by". The "at" is the event's own time.
- **R2. `GRADING_COMPLETED` is emitted only by the async path** (`assignments/tasks.py:527`). The synchronous `POST .../grade` (`students/views.py:796`) emits nothing, and the legacy `grade_all_submissions` is not dispatched. With the AI save suppressed, a sync grading would leave no before/after. **Recommend:** the sync `grade` view emits `GRADING_COMPLETED` too, carrying the same before/after. The legacy task is left alone, with a note. Before/after go in `GRADING_COMPLETED`'s `before`/`after` columns, not its metadata, so one query shape serves both kinds of grade history. The allow-list gains GRADING_COMPLETED's score fields.
- **R3. The login-time PENDING → ENROLLED activation** runs on every student sign-in, for their pending rows only (each row flips once). **Recommend:** record it through `record_bulk` as `ROSTER_CHANGE`, one per activated enrolment, with the student as actor. It's a real status change on the student's record and happens once per row, so the volume is bounded.
- **R4. "Seats".** `LicenseSubscription.max_seats` is the purchased seat count; a seat in use is an active `SchoolCreditAllocation`. **Recommend:** track both, as `SUBSCRIPTION_CHANGE`: `max_seats` on the licence, and allocation `is_active` create/flip. An admin's add/remove-teacher already leaves a `CREDIT_TRANSACTION` under S3, but that records credits, not the seat.
- **R5. Deletes.** Plan 08 says StudentCourse includes create and delete. **Recommend** the same for StudentSubmission (a deleted graded submission is a grade change, `after=None`) and CustomUser (`PERMISSION_CHANGE` with `after=None`). The QA-harness deletes (`billing/stripe_live_qa.py`, `billing/live_qa/*`) are test tooling and go on the allow-list.
- **R6. S4 needs S3's actor rule** (`request_audit_state(request)`, `current_request_actor()`), which is not in phase2/epic-a yet. **Recommend:** I write S4 against a thin `history._actor()` that calls `current_request_actor()` once S3 is in; until then it falls back to SYSTEM, and no test pins the actor. When 0b has merged S3, 0b merges the epic-a tip into `task/epic-a-s4` (0b does the merges); I then switch `_actor()` over and add the actor tests before gating.

**DATA_EXPORT.** `download-pdf` renders the assignment's questions and rubric, not student work, and no other export route exists. **Recommend:** emit `DATA_EXPORT` on `download-pdf` with `file_size_bytes` (its allow-list already has it), plus a note that no student-data export exists yet. When one is added, its route must emit it (S2's guard sees new routes).

## 5. Tests (plan §5.5), then Gates
- Each route or action gives exactly the expected before/after pair: update-grade, publish, publish-all (30 rows → 30 events), mark-reviewed, enrol/drop, the login activation, a role/status change in the admin form, the admin actions, the API, a subscription change and a seat change.
- An untracked field change emits nothing, and an unchanged tracked field emits nothing.
- An AI grading gives exactly one `GRADING_COMPLETED` with before/after and **zero** `GRADE_CHANGE`, on both the async and the sync path.
- The allow-list covers every registered model. A PII sentinel in answers, feedback, `ai_summary` or a name never appears in any event.
- The guard's self-tests and its stale-entry test.
- Gate 5: an emit failure during a grade save leaves the grade saved (FR-A-11).
- Gate 3: a publish racing an update-grade gives two events with consistent before/after (`record_bulk` reads under the row lock).
- Gate 6: p95 for one grade save and for a roster import of 30, with and without capture (SM note 4).
