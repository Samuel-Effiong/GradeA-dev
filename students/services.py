import logging
from datetime import timedelta
from html import escape

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from ai_processor.services import ai_processor
from assignments.models import Assignment, AssignmentStatus
from assignments.services import AssignmentProcessingService
from AutoGrader.tasks import send_email_task
from classrooms.tasks import student_summary_async
from users.models import CustomUser, UserTypes
from users.services import get_opted_in_school_admins

from .exceptions import CannotAssociateStudentError, SubmissionGradingInProgressError
from .models import BackgroundTaskType, GradingState, StudentSubmission
from .task_tracking import (
    cancellable_final_save,
    create_processing_task,
    ensure_task_not_cancelled,
    launch_processing_task,
)

# from .serializers import StudentSubmissionSerializer


logger = logging.getLogger(__name__)


def student_submission_to_html(submission) -> str:
    """
    Converts student submission JSON into a globally standard HTML format
    suitable for rich-text editors (ProseMirror, TinyMCE, Quill, CKEditor, etc).
    """

    def safe(val):
        return escape(str(val)) if val else ""

    student_name = submission.student.get_full_name()

    meta_html = f"""
    <section>

        {submission.assignment.title}
        <p><strong>Due Date:</strong> {submission.assignment.due_date}</p>


        <h3>Student Information</h3>
        <p><strong>Name:</strong> {safe(student_name)}</p><br />


        <h3>Submission Metadata</h3>
        <p><strong>Submitted At:</strong> {safe(submission.submission_date.strftime("%Y-%m-%d"))}</p>
        <p><strong>Graded At:</strong>
        {safe(submission.graded_at.strftime("%Y-%m-%d")) if submission.graded_at else "Not graded yet"}</p>
        <p><strong>Score:</strong>
        {safe(submission.score) if submission.score is not None else "Not graded yet"}</p>
    </section>
    <hr/><br/>
    """

    questions_html = "<section><h3>Student Responses</h3>"

    if submission.answers:
        for ans in submission.answers:
            status = "Answered" if ans.get("answer_html") else "Skipped"

            questions_html += f"""
            <article style="margin-bottom: 24px;">
                <h4>Question {ans.get('question_number')}</h4>
                {ans.get('question_text')}

                <div>
                    <strong>Student Answer:</strong>
                    <div style="margin:8px 0; padding:10px; border-left:4px solid #ccc;">
                        {ans.get('answer_html') or "<em>No answer submitted.</em>"}
                    </div>
                </div>

                <p><strong>Status:</strong> {status}</p>
            </article>
            """

    questions_html += "</section>"

    # feedback_html = ""
    # if submission.get("feedback"):
    #     feedback_html = f"""
    #     <hr/>
    #     <section>
    #         <h3>Grading Feedback</h3>
    #         <div style="padding:12px; border:1px solid #ddd;">
    #             {submission.get("feedback")}
    #         </div>
    #     </section>
    #     """

    return f"""
    <article class="student-submission">
        {meta_html}
        {questions_html}
    </article>
    """


# Celery's hard kill point for one grading run - grade_engine_async sets
# this as its time_limit (see assignments.tasks), and the Redis broker
# visibility_timeout in settings is sized above it. Referenced by name in
# AutoGrader/settings.py's CELERY_BROKER_TRANSPORT_OPTIONS comment.
GRADING_TASK_TIME_LIMIT_SECONDS = 25 * 60

# How long a RUNNING grading claim may sit before another worker is allowed
# to steal it. Generous on purpose: a legitimate run is several sequential AI
# calls with retries, so a tight window would let a slow-but-alive run be
# stolen and double-billed - the exact problem the claim exists to prevent.
# Derived from (not merely near) the task's hard kill point: a worker that
# somehow ran past the kill point is dead by the time this window elapses,
# so a stale claim really is abandoned rather than merely slow.
GRADING_CLAIM_STALE_AFTER = timedelta(seconds=GRADING_TASK_TIME_LIMIT_SECONDS + 5 * 60)


def _claim_submission_for_grading(submission_id):
    """
    Atomically claim a submission for grading (C3). Returns True if the
    claim was acquired.

    A single conditional UPDATE, so two concurrent claimants (a Celery
    redelivery racing the still-running original, or a double-clicked
    grade button) serialize on the row lock and exactly one wins: the
    loser's UPDATE re-evaluates the WHERE clause against the winner's
    committed RUNNING state and matches zero rows.

    Claimable states: anything that is not a *fresh* RUNNING claim - IDLE,
    DONE (legitimate re-grade), FAILED, and a RUNNING claim older than
    GRADING_CLAIM_STALE_AFTER (left behind by a crashed/killed worker).
    """
    now = timezone.now()
    stale_cutoff = now - GRADING_CLAIM_STALE_AFTER
    claimed = (
        StudentSubmission.objects.filter(pk=submission_id)
        .exclude(
            grading_state=GradingState.RUNNING,
            grading_started_at__gt=stale_cutoff,
        )
        .update(grading_state=GradingState.RUNNING, grading_started_at=now)
    )
    return bool(claimed)


def _mark_grading_claim_failed(submission_id):
    """Release a held claim after a failed run so the submission is
    immediately re-gradable (FAILED is a claimable state)."""
    StudentSubmission.objects.filter(pk=submission_id).update(
        grading_state=GradingState.FAILED
    )


def _coerce_confidence(value):
    """Clamp a model-reported 0-100 confidence to a safe int; the DB field
    is non-nullable, and the model can emit null or junk here."""
    try:
        confidence = int(float(value))
    except (TypeError, ValueError):
        return 0
    return min(100, max(0, confidence))


def grade_engine(user, submission, processing_task_id=None):
    if not _claim_submission_for_grading(submission.id):
        raise SubmissionGradingInProgressError(
            f"Submission {submission.id} is already being graded."
        )

    # Keep the in-memory instance in sync with the claim we just wrote, so
    # the pipeline's final full save can't clobber the claim fields with
    # stale pre-claim values.
    submission.refresh_from_db(fields=["grading_state", "grading_started_at"])

    try:
        return _run_grading_pipeline(user, submission, processing_task_id)
    except BaseException:
        _mark_grading_claim_failed(submission.id)
        raise


def _run_grading_pipeline(user, submission, processing_task_id):
    from assignments.tasks import formatted_grade_async

    ensure_task_not_cancelled(processing_task_id)
    answer_json = submission.get_answer()
    submission.ai_graded_at = timezone.now()

    grading = ai_processor.extract_grade_with_retry(
        user,
        submission.assignment.questions,
        answer_json,
        assignment_model=submission.assignment,
        processing_task_id=processing_task_id,
    )

    ensure_task_not_cancelled(processing_task_id)
    submission.ai_grading_completed_at = timezone.now()

    # The pipeline recomputes and clamps all arithmetic before returning
    # (AIProcessor._finalize_grading_result), so grading_summary is
    # guaranteed present on any AI-produced result - this guard exists so a
    # malformed result from any other source fails loudly here instead of
    # persisting an unusable grade or raising an opaque KeyError.
    grading_summary = (
        grading.get("grading_summary") if isinstance(grading, dict) else None
    )
    if not isinstance(grading_summary, dict):
        raise ValueError(
            "Grading result has no grading_summary - refusing to persist it."
        )

    try:
        grading_score = round(float(grading_summary["total_score"]), 2)
        max_points = int(float(grading_summary["max_total_points"]))
        percentage = round(float(grading_summary["percentage"]), 2)
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Grading summary is malformed: {e}") from e

    submission.score = grading_score
    submission.ai_score = grading_score
    submission.max_points = max_points
    submission.score_percentage = percentage

    submission.feedback = grading
    submission.grading_confidence = _coerce_confidence(
        grading.get("grading_confidence")
    )
    submission.graded_at = timezone.now()
    submission.grading_state = GradingState.DONE

    # update the raw_input
    ensure_task_not_cancelled(processing_task_id)
    answer_html = student_submission_to_html(submission)
    submission.raw_input = AssignmentProcessingService.html_to_prosemirror_json(
        answer_html
    )

    with cancellable_final_save(processing_task_id):
        submission.save()

    # H4: follow-up tasks (formatted grade + AI summary refresh) dispatch
    # only after the grade's save has actually COMMITTED - via on_commit,
    # not merely placed after the save - so formatted_grade_async can never
    # finish first and have its formatted_grade write clobbered by this
    # function's own full-row save. In autocommit mode (the normal case)
    # the callback runs immediately; if a future caller wraps grade_engine
    # in an outer transaction, dispatch waits for that commit.
    user_prompt = f"""
    Student Name: {submission.student.get_full_name()}
    Course: {submission.assignment.course}


    Grading Result:

    {grading}

    Return a formatted response
    """

    def _dispatch_followups():
        try:
            formatted_processing_task = create_processing_task(
                requested_by=user,
                task_type=BackgroundTaskType.FORMATTED_GRADE,
                assignment=submission.assignment,
                submission=submission,
                meta={"step": "Queued for formatted grade generation"},
            )
            launch_processing_task(
                formatted_grade_async,
                formatted_processing_task,
                str(submission.id),
                user_prompt,
            )
            # Invalidate ai_summary
            student_summary_async.delay(
                str(submission.student.id),
                str(user.id),
                str(submission.assignment.course.id),
            )
        except Exception:
            # The grade itself is already committed - a follow-up dispatch
            # failure must not fail (or un-claim) the graded run.
            logger.exception(
                "Failed to dispatch post-grading follow-up tasks",
                extra={"submission_id": str(submission.id)},
            )

    transaction.on_commit(_dispatch_followups)

    try:
        _maybe_notify_admins_grading_complete(submission.assignment)
    except Exception:
        logger.exception(
            "Failed to check/send admin grading-complete notification",
            extra={"assignment_id": str(submission.assignment_id)},
        )

    return submission


def _maybe_notify_admins_grading_complete(assignment):
    """
    Fires the school-admin "grading complete" notification exactly once per
    assignment, the moment every submission on a PUBLISHED assignment has
    been graded (graded_at set).

    Deliberately fires only once ever per assignment (guarded by the
    persisted admin_grading_notified_at timestamp): a late submitter graded
    after the assignment was already marked complete does not re-trigger a
    second email. This avoids needing a second hook on submission creation
    for what would be a rare edge case.
    """
    if assignment.status != AssignmentStatus.PUBLISHED:
        return

    submissions = StudentSubmission.objects.filter(assignment=assignment)
    if not submissions.exists():
        return
    if submissions.filter(graded_at__isnull=True).exists():
        return

    # Atomic claim: if two submissions finish grading concurrently, only one
    # of them will see rowcount == 1 here and proceed to notify.
    claimed = Assignment.objects.filter(
        pk=assignment.pk, admin_grading_notified_at__isnull=True
    ).update(admin_grading_notified_at=timezone.now())
    if not claimed:
        return

    notify_school_admins_of_grading_complete(assignment)


def notify_school_admins_of_grading_complete(assignment):
    course = assignment.course
    teacher = course.teacher if course else None
    school = getattr(teacher, "school", None)

    if not school:
        return

    admins = get_opted_in_school_admins(school, flag="notify_grading_complete")
    if not admins.exists():
        return

    graded_count = StudentSubmission.objects.filter(assignment=assignment).count()

    context = {
        "assignment": assignment,
        "course": course,
        "teacher": teacher,
        "graded_count": graded_count,
    }
    html_message = render_to_string(
        "email/school_admin_grading_complete.html", context=context
    )
    message = (
        f'All {graded_count} submission(s) for "{assignment.title or "Untitled Assignment"}" '
        f"in {course.name} have been graded."
    )

    for admin in admins:
        try:
            send_email_task.delay(
                subject=(
                    f"Grading complete: "
                    f"{assignment.title or 'Untitled Assignment'} ({course.name})"
                ),
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin.email],
                html_message=html_message,
            )
        except Exception:
            logger.exception(
                "Failed to queue grading-complete admin email",
                extra={"admin_id": str(admin.id), "assignment_id": str(assignment.id)},
            )


def notify_teacher_of_student_submission(submission):
    teacher = submission.assignment.course.teacher

    if not teacher or not teacher.email:
        return

    try:
        teacher_settings = teacher.settings
    except ObjectDoesNotExist:
        return

    if not teacher_settings.notify_student_submission:
        return

    context = {
        "teacher": teacher,
        "student": submission.student,
        "assignment": submission.assignment,
        "course": submission.assignment.course,
        "submission": submission,
    }

    message = (
        f"{submission.student.get_full_name()} submitted "
        f"{submission.assignment.title or 'an assignment'} "
        f"for {submission.assignment.course.name}."
    )

    # content = f"""
    # {submission.student.get_full_name()} has submitted work for {submission.assignment.title or 'an assignment'}
    # for {submission.assignment.course.name}.
    #
    # <b>Submission Detail</b>
    #
    # <ul>
    #     <li><strong>Student:</strong> {submission.student.get_full_name()}</li>
    #     <li><strong>Course:</strong> {submission.assignment.course.name}</li>
    #     <li><strong>Assignment:</strong> {submission.assignment.title}</li>
    #     <li><strong>Submitted At:</strong> {submission.submission_date}</li>
    # </ul>
    #
    # You can review the submission from your Grade A+ Dashboard.
    # """

    # merge_data = {
    #     "name": f"{teacher.first_name}",
    #     "content": content,
    #     "support_email": settings.SUPPORT_EMAIL,
    #     "current_year": timezone.now().year,
    # }

    # html_content = render_to_string("email/token_activation.html", context=context)

    # return send_email_task.delay(
    #     subject="Verify your email and get started with faster, smarter grading",
    #     message="",
    #     from_email=settings.DEFAULT_FROM_EMAIL,
    #     recipient_list=[user.email],
    #     html_message=None,
    #     template_id="ynrw7gy0ye2l2k8e",
    #     merge_data=merge_data,
    # )

    try:
        html_content = render_to_string(
            "email/student_submission_notification.html", context=context
        )

        send_email_task.delay(
            subject=(
                f"New student submission: "
                f"{submission.assignment.title or submission.assignment.course.name}"
            ),
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[teacher.email],
            html_message=html_content,
        )
    except Exception:
        logger.exception(
            "Failed to queue student submission notification",
            extra={
                "submission_id": str(submission.id),
                "assignment_id": str(submission.assignment_id),
                "teacher_id": str(teacher.id),
            },
        )


def notify_student_of_graded_submission(submission, *, is_update=False):
    student = submission.student

    if (
        not student
        or not student.email
        or student.email.lower().endswith("@student.local")
        or not submission.is_published
    ):
        return

    try:
        student_settings = student.settings
    except ObjectDoesNotExist:
        return

    if not student_settings.notify_grading_complete:
        return

    assignment = submission.assignment
    course = assignment.course
    grade_details = (
        get_grade_details(submission.score_percentage)
        if submission.score_percentage is not None
        else None
    )
    score_display = (
        f"{submission.score_percentage}%"
        if submission.score_percentage is not None
        else "Grade available"
    )

    context = {
        "student": student,
        "assignment": assignment,
        "course": course,
        "submission": submission,
        "grade_details": grade_details,
        "score_display": score_display,
        "is_update": is_update,
    }
    message = (
        f"Your grade for {assignment.title or 'an assignment'} in "
        f"{course.name} is now available: {score_display}."
    )

    try:
        html_content = render_to_string(
            "email/assignment_graded_notification.html", context=context
        )

        send_email_task.delay(
            subject=(
                f"{'Updated grade' if is_update else 'Assignment graded'}: "
                f"{assignment.title or course.name}"
            ),
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[student.email],
            html_message=html_content,
        )
    except Exception:
        logger.exception(
            "Failed to queue graded assignment notification",
            extra={
                "submission_id": str(submission.id),
                "assignment_id": str(assignment.id),
                "student_id": str(student.id),
            },
        )


def upload_answers_engine(
    assignment,
    content,
    request_user,
    is_proxy_upload=False,
    processing_task_id=None,
):
    assignment_context = f"""
    This is the Assignment Context to use in properly extracting the student submissions
    {assignment.questions}
    """

    ensure_task_not_cancelled(processing_task_id)
    student_submission = ai_processor.extract_answer_with_retry(
        request_user,
        content,
        assignment_context,
        assignment_model=assignment,
        max_retries=3,
        processing_task_id=processing_task_id,
    )

    if student_submission is not None:
        target_student = request_user

        if is_proxy_upload:
            identified_name = student_submission.get("student_name")
            if not identified_name:
                raise CannotAssociateStudentError(
                    "Student name cannot be found in the submission"
                )

            name_parts = identified_name.split(" ", 1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else ""

            target_student = CustomUser.objects.filter(
                enrollments__course=assignment.course,
                enrollments__enrollment_status="ENROLLED",
                first_name__icontains=first_name,
                last_name__icontains=last_name,
            ).first()

            if not target_student:
                raise CannotAssociateStudentError(
                    "Student not among the enrolled students in the course"
                )

        # ----------------------------------------------------------------
        # Atomic submission limit enforcement + get-or-create + increment.
        #
        # Uses select_for_update() to prevent a TOCTOU race condition where
        # concurrent uploads from the same student could both pass the
        # attempt_count >= 3 guard simultaneously, each increment the
        # counter, and together bypass the 3-submission limit.
        #
        # attempt_count tracks *total submissions ever made*, starting at 1
        # on the very first upload and increasing on every subsequent one.
        # ----------------------------------------------------------------
        is_student_self_upload = (
            request_user.user_type == UserTypes.STUDENT and not is_proxy_upload
        )

        with transaction.atomic():
            # Lock the existing row (if any) for the duration of this block.
            existing_submission = (
                StudentSubmission.objects.select_for_update()
                .filter(assignment=assignment, student=target_student)
                .first()
            )

            if is_student_self_upload and existing_submission:
                current_count = existing_submission.attempt_count or 0
                if current_count >= 3:
                    raise ValueError(
                        "You have reached the maximum of 3 submissions for this assignment"
                    )

            if existing_submission:
                # Re-submission — update answers and increment counter.
                created = False
                submission = existing_submission
                ensure_task_not_cancelled(processing_task_id)
                submission.answers = student_submission.get(
                    "answers", submission.answers
                )

                if is_student_self_upload:
                    submission.attempt_count = (submission.attempt_count or 0) + 1
            else:
                # First submission — create the row and set counter to 1.
                # submission_date is set explicitly here (rather than left
                # to auto_now_add) because student_submission_to_html()
                # below renders this instance before it's ever saved, and
                # auto_now_add only populates the field on save.
                created = True
                submission = StudentSubmission(
                    assignment=assignment,
                    student=target_student,
                    answers=student_submission.get("answers"),
                    attempt_count=1 if is_student_self_upload else 0,
                    submission_date=timezone.now(),
                )

        # ----------------------------------------------------------------
        # Build raw_input outside the lock (it's CPU-only, no DB writes
        # needed during construction), then persist everything in one save.
        # ----------------------------------------------------------------
        ensure_task_not_cancelled(processing_task_id)
        answer_html = student_submission_to_html(submission)
        submission.raw_input = AssignmentProcessingService.html_to_prosemirror_json(
            answer_html
        )
        # Persist the extractor's confidence - the dashboard threshold-flags
        # low-confidence extractions, which stayed 0 forever while this
        # field was silently dropped on the upload path.
        submission.extraction_confidence = _coerce_confidence(
            student_submission.get("extraction_confidence")
        )
        with cancellable_final_save(processing_task_id):
            submission.save()

        if created and request_user.user_type == UserTypes.STUDENT:
            notify_teacher_of_student_submission(submission)

    return submission


def get_grade_details(percentage):
    """
    Returns (letter_grade, gpa, remark) for a given percentage score.

    Grading scale:
      A+  97-100  4.0  Excellent
      A   93-96   4.0  Excellent
      A-  90-92   3.7  Very Good
      B+  87-89   3.3  Good
      B   83-86   3.0  Good
      B-  80-82   2.7  Satisfactory
      C+  77-79   2.3  Satisfactory
      C   73-76   2.0  Pass
      C-  70-72   1.7  Pass
      D+  67-69   1.3  Poor
      D   63-66   1.0  Poor
      D-  60-62   0.7  Marginal Pass
      F   0-59    0.0  Fail
    """
    pct = float(percentage)
    if pct >= 97:
        return {"letter_grade": "A+", "gpa": 4.0, "remark": "Excellent"}
    elif pct >= 93:
        return {"letter_grade": "A", "gpa": 4.0, "remark": "Excellent"}
    elif pct >= 90:
        return {"letter_grade": "A-", "gpa": 3.7, "remark": "Very Good"}
    elif pct >= 87:
        return {"letter_grade": "B+", "gpa": 3.3, "remark": "Good"}
    elif pct >= 83:
        return {"letter_grade": "B", "gpa": 3.0, "remark": "Good"}
    elif pct >= 80:
        return {"letter_grade": "B-", "gpa": 2.7, "remark": "Satisfactory"}
    elif pct >= 77:
        return {"letter_grade": "C+", "gpa": 2.3, "remark": "Satisfactory"}
    elif pct >= 73:
        return {"letter_grade": "C", "gpa": 2.0, "remark": "Pass"}
    elif pct >= 70:
        return {"letter_grade": "C-", "gpa": 1.7, "remark": "Pass"}
    elif pct >= 67:
        return {"letter_grade": "D+", "gpa": 1.3, "remark": "Poor"}
    elif pct >= 63:
        return {"letter_grade": "D", "gpa": 1.0, "remark": "Poor"}
    elif pct >= 60:
        return {"letter_grade": "D-", "gpa": 0.7, "remark": "Marginal Pass"}
    else:
        return {"letter_grade": "F", "gpa": 0.0, "remark": "Fail"}
