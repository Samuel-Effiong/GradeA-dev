# H-1 Stage 3 — production write paths and cached reads (matrix reference)

**Status: REFERENCE.** This is a read-only catalogue of commit `29cc1c7`'s descendant `6a8e714`, compiled 2026-09-15 to build the viewer × mutation freshness matrix. Line numbers drift, so re-check a line before relying on it. Every URL is under `/api/v1/`, and routers have no trailing slash.

The matrix follows one rule: **drive each mutation through the endpoint or service listed here**, and build fixtures only with fields production sets (plan §0).

## Findings that shape the matrix

1. `Assignment.teacher` is never set in production, so an assignment save bumps no user generation. The teacher's cached `/assignments` list (user scope only) and students' lists refresh only through the `assignments:*` wildcard. Every existing fixture sets `teacher=`, which hides this (plan G1).
2. `publish_all_grades` invalidates only for `newly_published[0]` (plan G3).
3. AssignmentGenerationSession saves only run the wildcard, and messages have no receiver at all (plan G9).
4. The three `student-admin` dashboard keys are unversioned (plan G2).
5. `SchoolViewSet` overrides `list` and `retrieve`, so the mixin cache is not used there. Check the bodies before adding school rows.

## Assignments (`assignments/views.py`)

A teacher's queryset is `course__teacher=user`. A student's queryset is courses they are ENROLLED in or have COMPLETED, restricted to PUBLISHED assignments.

| Action | Route | Body | Writes | Patch for offline |
|---|---|---|---|---|
| create | POST `assignments` | `course`, `raw_input` (+`title`, `topic`, `status`, `due_date`) | `Assignment.objects.create` (no teacher), then serializer save | `assignments.services.ai_processor.extract_assignment_with_retry` |
| partial_update | PATCH `assignments/<pk>` | `title`/`status`/`topic`/`due_date`… | `instance.save()`; credits needed only when `raw_input` is sent | none without `raw_input` |
| update_async | PATCH `assignments/<pk>/update-async` | same | save; **credits always required** | Celery only with `raw_input` |
| destroy | DELETE `assignments/<pk>` | — | delete; post_delete removes PeriodicTasks | — |
| associate_topic | PATCH `assignments/<pk>/associate-topic?topic_id=` | — | save | — |
| publish_all_grades | POST `assignments/<pk>/publish-all-grades` | — | `QuerySet.update(is_published=True)`, then one invalidation (**first student only**) | email in try |
| grade_all | POST `assignments/<pk>/grade-all` | — | BatchUploadSession + tasks | `assignments.views.launch_processing_task` |
| schedule_grade_all_submission | POST `assignments/<pk>/schedule_grade_all_submission` | `schedule_time` | PeriodicTask, `save(update_fields)` | — |
| generate | POST `assignments/generate/<course_id>` | `prompt` | GenerationSession + USER/ASSISTANT messages | `assignments.views.ai_processor.generate_assignment_from_prompt_with_retry` |
| save_generated_draft | POST `assignments/generated-drafts/<message_id>/save` | `status` etc. | AssignmentSerializer save (no teacher), message save | none |

- **Publish and unpublish** have no dedicated endpoint. They are PATCH `{"status": "PUBLISHED" | "UNPUBLISHED" | "DRAFT"}`.
- **The AI-free route to a PUBLISHED assignment:** call `generate` with the one AI function patched to return `title`, `instructions`, `questions` and `self_assessment`, then call `generated-drafts/<message_id>/save` with `{"status": "PUBLISHED"}`. Each question needs `question_number`, `question_text`, `question_type` (OBJECTIVE, ESSAY or SHORT-ANSWER), `points`, `options` and `rubric`.
- **Generation sessions** support GET and DELETE only, scoped to `user=request.user`.

## Submissions (`students/views.py`, `students/services.py`)

`get_permissions` overrides the per-action permission classes.

| Action | Route | Writes | Patch |
|---|---|---|---|
| upload | POST `submissions/<assignment_id>/upload` (multipart `answer`) | submission save. Needs student auth, and the **teacher's** wallet must have credits | `students.services.ai_processor.extract_answer_with_retry` (+ optionally `students.views.AssignmentProcessingService.prepare_ai_content`) |
| raw-text edit | PATCH `submissions/<pk>` `raw_input` | `save(update_fields)` | same |
| grade | POST `submissions/<pk>/grade` | claim `.update()`, then `save(update_fields=GRADING_RESULT_FIELDS)`; post_save recomputes `StudentCourse.final_grade` | `students.services.ai_processor.extract_grade_with_retry` → `{"grading_summary": {...}}` |
| publish_grade | POST `submissions/<pk>/publish` | `.update(is_published=True)`, then `invalidate_submission_caches` | email in try |
| mark_reviewed | POST `submissions/<pk>/mark-reviewed` | `.update(needs_review=False…)`, then invalidation | — |
| update-grade | PATCH `submissions/<pk>/update-grade` `score` | `save(update_fields)`, launches Celery | `students.views.launch_processing_task` |
| destroy | DELETE `submissions/<pk>` | delete | — |

- **Grading claim** (`_claim_submission_for_grading`): a successful `.update(RUNNING)` invalidates nothing (plan P1). A failure runs `.update(FAILED)` and then invalidates.
- **`admin_grading_notified_at`:** set with `.update()` on the assignment; no signal fires.
- **`GET submissions/<pk>`** may `.update()` `raw_input`.

## Courses and enrolment (`classrooms/views.py`)

| Action | Route | Writes |
|---|---|---|
| create | POST `course` `{name, session, description?, topic_names?}` | teacher set via `CurrentUserDefault`, plus topics. An INDIVIDUAL session must be the unlicensed teacher's own; a SCHOOL session needs a licensed teacher in the same school |
| update | PATCH `course/<pk>` (name, description, session move, is_active, topic_names) | `topic_names` deletes and recreates topics |
| destroy | DELETE `course/<pk>` | cascade |
| add by email | POST `course/<pk>/students` `{email}` | active existing student → ENROLLED; new or inactive account → PENDING + invited user with `school=course.teacher.school` |
| direct add | POST `course/<pk>/direct-add-student` `{first_name, last_name}` | active ENROLLED student, `auto_added` |
| bulk add | POST `course/<pk>/bulk-add-students` `{raw_data}` | with email: PENDING; without email: direct add |
| remove | DELETE `course/<pk>/student/<student_id>` | `enrollment.delete()` |
| withdraw/status | PATCH `student-course/<enrollment_pk>` `{enrollment_status, withdrawal_date}` | default partial_update |
| topics | POST `course/<pk>/topics` `["Algebra"]` | TopicSerializer save |

- **Topics:** POST `topics` `{name, course}` (the teacher must own the course), plus PATCH and DELETE `topics/<pk>`.
- **PENDING → ENROLLED:** POST `auth/register/student` promotes with `save(update_fields)`, but Google sign-in uses `.update()` (plan P2). To run it offline, patch `requests.post` and `users.views.id_token.verify_oauth2_token`.

## Sessions, schools, licences

**Sessions:** POST `sessions` `{name}`. The session's ownership depends on who creates it:
- **School admin:** SCHOOL session, `school=user.school`, `teacher=None`, `created_by=user`.
- **Unlicensed teacher:** INDIVIDUAL session, `teacher=user`, `created_by=user`.
- **Licensed teacher:** 403.
- **Superadmin:** must send `school`.

**Schools** (the superadmin must have `is_superuser` **and** `user_type=SUPER_ADMIN`):
- POST `schools` `{name}`; PATCH `schools/<pk>`.
- DELETE archives the school (`is_active=False`).
- POST `schools/create_with_admin` `{school_name, admin_email (business), admin_first_name, admin_last_name}` creates an inactive admin with an activation token.
- POST `auth/register/school-admin` `{email, token, password}` activates that admin.

**Licences:**
- POST `license-subscriptions` `{school, plan, billing_method: "OFFLINE", teacher_emails}`.
- POST `license-subscriptions/<pk>/add_teachers` `{teacher_emails}` sets `school` on an existing teacher (`save(update_fields=["school"])`) or invites a new one. It then creates the allocation, wallet and a MONTHLY bucket.
- `is_under_license()` is derived from the subscription, not stored.
- No migration seeds `SubscriptionPlan`, so a LICENSE plan row is the one unavoidable ORM fixture.

## Users, settings, wallet

- **PATCH `users/<pk>`:** a user may edit only their own row unless superadmin. Writable fields are email, first/middle/last name, bio, profile_image and password. `school` and `user_type` are writable only by a superadmin. **No API path sets `is_active`.**
- **Admin actions** `activate_users` and `deactivate_users` run `.update(is_active)` and then `invalidate_user_caches`. Call them as `CustomUserAdmin(CustomUser, admin.site).activate_users(request, qs)`.
- **Settings:** PATCH `users/settings/<pk>` (`theme`, `notify_*`). GET `users/settings/my_settings` may `get_or_create`.
- **Wallet:**
  - `users/me` nests `credit_wallet.total_remaining_credits`.
  - `consume_credits` runs inside the AI layer, so patching `ai_processor` skips consumption.
  - A superadmin grant, POST `admin/credits/grant` `{user_id, blocks}`, calls `top_up_credits` and needs a plan.
  - Licence enrolment creates a bucket.
  - None of these has a receiver (plan P5).
- **Management commands** (all accept `dry_run`, `batch_size`, `school`), all `bulk_update`s with no invalidation (plan P4): `repair_question_blooms_levels`, `strip_duplicate_option_letters`, `strip_html_from_assignment_titles`, `backfill_assignment_rigor`.

## Cached reads

- **Mixin list and retrieve** (user scope): `assignments`, `assignment-generation-sessions`, `submissions` (list), `course`, `sessions`, `student-course`, `topics`, `users` (list is superadmin only), `users/settings`.
- **Not cached:** `student-course/my-students`.
- **Bespoke reads:** `course/my-courses` (student; user + global scope), `submissions/<pk>` (user scope), `users/me` (user), `users/settings/my_settings` (user).

**Dashboards** (GET, `dashboard/views.py`):
- **`super-admin/dashboard/…`:** adoption, usage, ai_performance, scaling_signals and students are global scope. schools is anysch, teachers is anyusr, concurrency is uncached.
- **`school-admin/dashboard/…`:** summary, at-risk-trend and students are user + school scope. teachers and teachers/<id> are school scope (+teacher for the detail view), as are assignment-activity-over-time and course-overview-chart. course-performance and unit-performance have no cache key found.
- **`teacher-admin/dashboard/…`:** overview/<session_id>, courses/<course_id>, assignments/<assignment_id> and students/<course_id> are user scope. The overview looks up the session with `teacher=teacher`, so SCHOOL sessions return 404.
- **`student-admin/dashboard/…`:** summary/<course_id>, assignments and overview use **unversioned** keys.

## Gotchas

- **Credits:** `HasCreditBalance` returns **400**, not 403, when the balance is zero. New wallets are empty.
- **Throttles:** limits live in Redis for the whole process (register 10/h, login 10/min, custom-ai-prompt 10/min).
- **Celery:**
  - there is no eager mode;
  - `launch_processing_task` publishes to the prefixed test broker and returns 503 if the broker is down;
  - `safe_delay` swallows broker errors;
  - `on_commit` callbacks run in `TestCase` only inside `captureOnCommitCallbacks(execute=True)`.
- **Storage:** `profile_image` uses Cloudinary in prod, dev and local, so avoid images.
- **Email:**
  - individual teachers need a personal email;
  - school admins and licensed teachers need a business or exempt domain.
- **Assignment validation:**
  - due dates must be in the future;
  - `(course, title, raw_input_hash)` is unique;
  - the assignment-create serializer does not check course ownership (security note, outside Stage 3's scope).
- **Fixture fields production always sets:**
  - production always sets `Session.created_by`;
  - invited, direct-add and bulk-add students get `school=course.teacher.school`.
- **Writes that bypass post_save:**
  - no invalidation at all: the grading claim, `admin_grading_notified_at`, Google enrolment `.update()`, and the management commands;
  - partial invalidation: publish-all.
