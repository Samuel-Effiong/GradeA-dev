import logging
from datetime import timedelta
from html import escape

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Value
from django.db.models.functions import Concat
from django.template.loader import render_to_string
from django.utils import timezone

from ai_processor.services import ai_processor
from assignments.models import Assignment, AssignmentStatus
from assignments.services import AssignmentProcessingService
from AutoGrader.celery import app as celery_app
from AutoGrader.tasks import send_email_task
from billing.refunds import billing_refund_scope
from classrooms.tasks import student_summary_async
from users.models import CustomUser, UserTypes
from users.services import get_opted_in_school_admins

from .exceptions import (
    AssignmentNotOpenError,
    CannotAssociateStudentError,
    SubmissionAlreadyGradedError,
    SubmissionBeingGradedError,
    SubmissionGradingInProgressError,
    SubmissionLimitReachedError,
    SubmissionProcessingInProgressError,
)
from .models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    GradingState,
    StudentSubmission,
)
from .signals import invalidate_submission_caches
from .task_tracking import (
    cancellable_final_save,
    create_processing_task,
    ensure_task_not_cancelled,
    launch_processing_task,
)

logger = logging.getLogger(__name__)

# How many times a student may submit their own answers to one assignment.
MAX_STUDENT_SUBMISSION_ATTEMPTS = 3


def student_submission_to_html(submission) -> str:
    """
    Converts student submission JSON into a globally standard HTML format
    suitable for rich-text editors (ProseMirror, TinyMCE, Quill, CKEditor, etc).

    Two different escapes are used on purpose. `safe()` escapes plain values
    that must never be markup. `rich()` runs the allowlist over the fields that
    legitimately *are* markup - question_text and answer_html come from the AI
    extractor reading a student-uploaded PDF or image, which makes them
    attacker-influenced, and they land in raw_input and are later rendered in a
    teacher's browser. The assignment renderer has always sanitised the
    equivalent fields; this one did not.
    """

    def safe(val):
        return escape(str(val)) if val else ""

    def rich(val):
        return AssignmentProcessingService.sanitize_ai_html(val) if val else ""

    student_name = submission.student.get_full_name()

    meta_html = f"""
    <section>

        {rich(submission.assignment.title)}
        <p><strong>Due Date:</strong> {safe(submission.assignment.due_date)}</p>


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
                <h4>Question {safe(ans.get('question_number'))}</h4>
                {rich(ans.get('question_text'))}

                <div>
                    <strong>Student Answer:</strong>
                    <div style="margin:8px 0; padding:10px; border-left:4px solid #ccc;">
                        {rich(ans.get('answer_html')) or "<em>No answer submitted.</em>"}
                    </div>
                </div>

                <p><strong>Status:</strong> {status}</p>
            </article>
            """

    questions_html += "</section>"

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
    # .update() bypasses post_save, so the cache-invalidation receiver
    # (students.signals.clear_student_submission_cache) never fires -
    # without this a failed submission keeps serving its cached
    # pre-failure detail (grading_state RUNNING) for up to CACHE_TTL, and
    # nobody sees that the run needs retrying. Uses the receiver's own
    # helper so the generation bumps and wildcard families can't drift
    # from what a normal save() would have cleared.
    submission = (
        StudentSubmission.objects.select_related("assignment__course__teacher")
        .filter(pk=submission_id)
        .first()
    )
    if submission is not None:
        invalidate_submission_caches(submission)


# Every column the grading pipeline is allowed to write. The final save is
# restricted to these (not a full-row save) because a run takes minutes,
# and the in-memory instance was loaded before it started: a full save
# would write back the stale copy of every OTHER column - a re-upload's
# `answers`/`attempt_count`, a publish's `is_published`, a formatter's
# `formatted_grade` - silently reverting whatever landed in between.
GRADING_RESULT_FIELDS = (
    "ai_graded_at",
    "ai_grading_completed_at",
    "score",
    "ai_score",
    "max_points",
    "score_percentage",
    "feedback",
    "grading_confidence",
    "graded_at",
    "grading_state",
    "needs_review",
    "review_reasons",
    "review_severity",
    "review_tier",
    "raw_input",
)


# Review-queue ordering. review_severity used to store the raw
# gap_fraction, which silently mis-ordered the queue: _severity classifies
# a disagreement "critical" when the two graders are >= 2 rubric levels
# apart EVEN IF the point gap is small (see ai_processor/second_opinion.py).
# So 20-vs-18 on a (20,19,18,0) ladder is critical at fraction 0.10, while
# 10-vs-6 on a (10,6,3,0) ladder is merely moderate at fraction 0.40 —
# and ordering by raw fraction buried the critical one below the moderate.
#
# The fix is a tier-weighted key: tier picks the band, gap_fraction only
# orders WITHIN a band. Bands are a third of the 0-1 range each, so a
# critical always outranks any moderate, which always outranks any
# borderline, and the value still fits the existing FloatField (no
# migration, no column type change).
_TIER_BASE = {
    "critical": 2 / 3,
    "moderate": 1 / 3,
    "borderline": 0.0,
}
# Worst-first, for denormalising the per-submission review_tier.
_TIER_RANK = {"critical": 3, "moderate": 2, "borderline": 1}


def _review_sort_key(tier, gap_fraction):
    """Tier-weighted 0-1 sort key for the review queue (see _TIER_BASE)."""
    # An unmeasurable gap (unknown points) is never treated as mild — it
    # sorts mid-band rather than at the bottom of it.
    fraction = 0.5 if gap_fraction is None else gap_fraction
    try:
        fraction = min(1.0, max(0.0, float(fraction)))
    except (TypeError, ValueError):
        fraction = 0.5
    # An unrecognised/missing tier is treated as moderate, matching
    # _severity's own "never downgrade what we can't measure" rule.
    base = _TIER_BASE.get(tier, _TIER_BASE["moderate"])
    return round(base + fraction / 3, 6)


def _worst_tier(tiers):
    """The most severe tier across a submission's disagreements."""
    ranked = [(_TIER_RANK.get(tier, 0), tier) for tier in tiers if tier]
    if not ranked:
        return None
    return max(ranked)[1]


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


def _populate_and_save_grade(submission, grading, processing_task_id):
    """
    Write an AI grading result onto the submission and persist it.

    Split out of _run_grading_pipeline so the whole grade-then-persist
    sequence sits inside one billing_refund_scope: everything in here can
    raise (a malformed summary, a ProseMirror conversion failure, the save
    itself), and every one of those failures must refund the run rather
    than charge for a grade that never landed.
    """
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

    # Review queue: when the blind second grader disagreed with grader A
    # on any question, flag the submission for the teacher — with both
    # sides' scores in review_reasons so the queue is self-describing.
    # Explicitly RESET on every grading run: a re-grade whose graders now
    # agree must clear a stale flag from an earlier run. (Second-opinion
    # failures/skips deliberately do NOT flag — see
    # AIProcessor._maybe_run_second_opinion.)
    #
    # There are now two INDEPENDENT sources of review, and they are
    # accumulated rather than chained: a submission can perfectly well
    # have both a missing answer and a grader disagreement, and an
    # if/elif would have silently reported only one of them.
    reasons: list = []
    sort_keys: list = []
    tiers: list = []

    # Source 1: we graded a question without having the student's answer.
    # ALWAYS critical, and always sorted to the very top of the queue.
    # Every other review reason is a judgement call about a grade we are
    # confident is at least *about* the right work; this one says we may
    # not have the student's work at all, which is a data-integrity
    # failure, not a marking disagreement. Scoring it 0 may still be
    # correct — but that is a conclusion for a human to reach, not one
    # the system is entitled to reach silently.
    for missing in grading.get("answers_not_found") or []:
        tiers.append("critical")
        sort_keys.append(_review_sort_key("critical", 1.0))
        reasons.append(
            {
                "type": "answer_not_found",
                "question_number": missing.get("question_number"),
                "answer_status": missing.get("answer_status"),
                "score_awarded": missing.get("score_awarded"),
                "max_points": missing.get("max_points"),
            }
        )

    # Source 2: the blind second grader disagreed with grader A.
    second_opinion = grading.get("second_opinion") or {}
    disagreements = second_opinion.get("disagreements") or []
    if disagreements:
        for d in disagreements:
            severity = d.get("severity") or {}
            # An unmeasurable gap (unknown points) is never treated as
            # mild — it sorts mid-queue rather than last.
            gap_fraction = severity.get("gap_fraction")
            tier = severity.get("tier")
            tiers.append(tier)
            sort_keys.append(_review_sort_key(tier, gap_fraction))
            reasons.append(
                {
                    "type": "grader_disagreement",
                    "question_number": d.get("question_number"),
                    "a_score": (d.get("a") or {}).get("score_awarded"),
                    "b_score": (d.get("b") or {}).get("score_awarded"),
                    "tier": tier,
                    "gap_fraction": gap_fraction,
                }
            )
    elif second_opinion.get("needs_review"):
        # The second opinion couldn't run for a reason the teacher needs to
        # know about — currently only "out of credits" (see
        # AIProcessor._maybe_run_second_opinion). Grader A's grade stands,
        # but it was never cross-checked, so it goes in the queue as
        # unverified rather than passing as silently confirmed. Treated as
        # moderate: unknowable, and _severity's own rule is to never
        # downgrade what we can't measure.
        tiers.append("moderate")
        sort_keys.append(_review_sort_key("moderate", None))
        reasons.append(
            {
                "type": second_opinion.get(
                    "review_reason", "second_opinion_unavailable"
                ),
                "detail": second_opinion.get("skipped"),
            }
        )

    if reasons:
        submission.needs_review = True
        submission.review_reasons = reasons
        submission.review_severity = max(sort_keys)
        submission.review_tier = _worst_tier(tiers)
    else:
        submission.needs_review = False
        submission.review_reasons = None
        submission.review_severity = None
        submission.review_tier = None

    # update the raw_input
    ensure_task_not_cancelled(processing_task_id)
    answer_html = student_submission_to_html(submission)
    submission.raw_input = AssignmentProcessingService.html_to_prosemirror_text(
        answer_html
    )

    with cancellable_final_save(processing_task_id):
        submission.save(update_fields=GRADING_RESULT_FIELDS)


# The formatted-grade follow-up lives in assignments.tasks, which imports
# this module. Dispatching by registered name (a Celery signature) instead
# of importing the function removes the students.services <-> assignments.
# tasks import cycle: nothing here needs the task object, only its name.
FORMATTED_GRADE_TASK_NAME = "assignments.tasks.formatted_grade_async"


def _formatted_grade_task():
    return celery_app.signature(FORMATTED_GRADE_TASK_NAME)


def _run_grading_pipeline(user, submission, processing_task_id):
    ensure_task_not_cancelled(processing_task_id)
    answer_json = submission.get_answer()
    submission.ai_graded_at = timezone.now()

    # The refund scope must cover PERSISTENCE, not just the AI call.
    # ai_processor's own inner billing_refund_scope closes the moment the
    # AI result exists, but everything after it here — the grading_summary
    # shape guard, _coerce_confidence, the HTML/ProseMirror conversion,
    # and the final save() — can still raise. Without this outer scope
    # those failures charged the teacher in full for a grade that was
    # never saved, and because FAILED is a re-claimable state, each retry
    # charged again. billing_refund_scope re-parents: the inner scope
    # hands its committed task_ids up to this one on success (see
    # billing/refunds.py), so a later failure here reclaims them too.
    with billing_refund_scope(
        reason="grading run failed before the grade was persisted"
    ):
        grading = ai_processor.extract_grade_with_retry(
            user,
            submission.assignment.questions,
            answer_json,
            assignment_model=submission.assignment,
            processing_task_id=processing_task_id,
        )

        _populate_and_save_grade(submission, grading, processing_task_id)

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
                _formatted_grade_task(),
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
    # Deliberate literal double-quotes around the title, not Python repr:
    # this is student/teacher-facing email text, so !r (single-quoted,
    # backslash-escaped repr() output) would read wrong.
    message = (
        f'All {graded_count} submission(s) for "{assignment.title or "Untitled Assignment"}" '  # noqa: B907
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


def notify_students_of_assignment_edit(assignment):
    """
    Notify every student who has already submitted work on `assignment` that
    the teacher has edited it (via the raw_input/AI re-extraction path in
    AssignmentProcessingService.update_assignment_from_extraction).

    This edit fully replaces `assignment.questions`, and grading links a
    submission's answers/feedback to a question only by question_number
    (see ai_processor.services._question_number_key), which is reassigned
    on every re-extraction - so an edit can in principle change what a
    previously submitted answer is now graded against. This notification
    doesn't attempt to detect whether any specific student's answers were
    actually affected (that would need matching old vs. new questions,
    deliberately deferred - see FUTURE_ROADMAP.md); it's a blanket,
    conservative "this assignment changed after you submitted" notice to
    every submitter.

    Modeled directly on notify_student_of_graded_submission: same opt-in
    guard, same synthetic-account exclusion, same Celery dispatch.
    """
    # student__settings too: the opt-in check below reads it per student,
    # which was one extra query per submitter on an assignment-wide loop.
    submissions = StudentSubmission.objects.filter(
        assignment=assignment
    ).select_related("student", "student__settings")

    for submission in submissions:
        student = submission.student

        if (
            not student
            or not student.email
            or student.email.lower().endswith("@student.local")
        ):
            continue

        try:
            student_settings = student.settings
        except ObjectDoesNotExist:
            continue

        if not student_settings.notify_assignment_edited:
            continue

        course = assignment.course
        context = {
            "student": student,
            "assignment": assignment,
            "course": course,
            "submission": submission,
        }
        message = (
            f"Your teacher updated {assignment.title or 'an assignment'} in "
            f"{course.name} after you submitted your work."
        )

        try:
            html_content = render_to_string(
                "email/assignment_edited_notification.html", context=context
            )

            send_email_task.delay(
                subject=f"Assignment updated: {assignment.title or course.name}",
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[student.email],
                html_message=html_content,
            )
        except Exception:
            logger.exception(
                "Failed to queue assignment-edited notification",
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

    # Refuse BEFORE the billed extraction call when the student is already
    # locked out (graded, or out of attempts). Not the authoritative check
    # - that is under the row lock further down - but a submission that
    # can never be accepted must not cost the student's teacher a credit.
    if request_user.user_type == UserTypes.STUDENT and not is_proxy_upload:
        ensure_student_may_submit(assignment, request_user)

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
        # StudentSubmission.answers is NOT nullable, and both write paths
        # below read this key. Before this guard, an extraction that came
        # back without it (or with a non-list under it) reached the DB as
        # SQL NULL and died there with a bare IntegrityError - AFTER the
        # teacher had been billed for the call, and with no indication of
        # what was actually wrong. Validated here, at the boundary, so the
        # failure is legible and the enclosing refund scope can reclaim
        # the charge.
        extracted_answers = student_submission.get("answers")
        if not isinstance(extracted_answers, list):
            raise ValueError(
                "Answer extraction returned no usable `answers` list "
                f"(got {type(extracted_answers).__name__}); refusing to "
                "persist an unusable submission."
            )

        target_student = request_user

        if is_proxy_upload:
            target_student = _match_enrolled_student(
                assignment.course, student_submission.get("student_name")
            )
        else:
            # Post-extraction re-check. The authoritative check is under
            # the row lock below; this one exists because the extraction
            # above took real time, and a grade can have landed meanwhile.
            ensure_student_may_submit(assignment, request_user)

        # ----------------------------------------------------------------
        # Atomic submission limit enforcement + get-or-create + increment.
        #
        # select_for_update() on the student's row prevents the TOCTOU race
        # where two concurrent uploads from the same student both pass the
        # attempt_count guard, each increment the counter, and together
        # bypass the submission limit. For that to hold, the lock has to
        # stay held until the increment is COMMITTED - so the save is
        # inside this block. (It used to be outside: the block released
        # the lock with the new count only in memory, the second upload
        # then read the old count from the DB, and both passed the guard.)
        # What sits under the lock besides the save is CPU-only HTML
        # rendering, milliseconds, and never a network call.
        #
        # attempt_count tracks *total submissions ever made*, starting at 1
        # on the very first upload and increasing on every subsequent one.
        # ----------------------------------------------------------------
        is_student_self_upload = (
            request_user.user_type == UserTypes.STUDENT and not is_proxy_upload
        )

        with transaction.atomic():
            existing_submission = (
                StudentSubmission.objects.select_for_update()
                .filter(assignment=assignment, student=target_student)
                .first()
            )

            if existing_submission:
                # Authoritative: the row is locked, so this decision cannot
                # race a concurrent upload, a grading claim, or a grade
                # landing on the row. Applies to proxy uploads too.
                _check_submission_open(
                    existing_submission, student_upload=is_student_self_upload
                )

            if existing_submission:
                # Re-submission - update answers and increment counter.
                created = False
                submission = existing_submission
                ensure_task_not_cancelled(processing_task_id)
                submission.answers = student_submission.get(
                    "answers", submission.answers
                )

                if is_student_self_upload:
                    submission.attempt_count = (submission.attempt_count or 0) + 1
            else:
                # First submission - create the row and set counter to 1.
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

            ensure_task_not_cancelled(processing_task_id)
            answer_html = student_submission_to_html(submission)
            submission.raw_input = AssignmentProcessingService.html_to_prosemirror_text(
                answer_html
            )
            # Persist the extractor's confidence - the dashboard
            # threshold-flags low-confidence extractions, which stayed 0
            # forever while this field was silently dropped on the upload
            # path.
            submission.extraction_confidence = _coerce_confidence(
                student_submission.get("extraction_confidence")
            )
            with cancellable_final_save(processing_task_id):
                if created:
                    submission.save()
                else:
                    # Only the columns this path owns. A full-row save here
                    # would write back the stale copy of every other column
                    # (score, feedback, is_published, grading_state...)
                    # from an instance loaded before the AI extraction ran.
                    submission.save(
                        update_fields=[
                            "answers",
                            "attempt_count",
                            "raw_input",
                            "extraction_confidence",
                        ]
                    )

        if created and request_user.user_type == UserTypes.STUDENT:
            notify_teacher_of_student_submission(submission)

    return submission


def _grading_claim_is_live(submission, now=None):
    """A RUNNING claim younger than the staleness window: a worker is (or
    must be assumed to be) grading this row right now. An older RUNNING
    claim was left by a dead worker and does not count - see
    _claim_submission_for_grading, which uses the same cutoff."""
    now = now or timezone.now()
    return (
        submission.grading_state == GradingState.RUNNING
        and submission.grading_started_at is not None
        and submission.grading_started_at > now - GRADING_CLAIM_STALE_AFTER
    )


def _check_submission_open(existing_submission, *, student_upload):
    """
    The server-side rules that close a submission row to uploads, checked
    in this order. The first two apply to EVERY upload path - the
    student's own and a teacher's proxy upload alike (owner, 2026-09-14:
    a graded row is immutable through the ordinary upload paths; a
    correction after grading needs an explicit replace/re-grade workflow).
    The third is the student's own attempt allowance.

    1. Graded: `graded_at` set - the only path that sets it is the grade
       persisting in _populate_and_save_grade, and it is what publish keys
       on too. Closed for good.
    2. Being graded (H-13, decided 2026-09-14): a live grading claim. The
       upload is refused rather than accepted, so a row can never carry
       answers newer than the grade that closes it.
    3. The attempt limit (MAX_STUDENT_SUBMISSION_ATTEMPTS), students only.
    """
    if existing_submission.graded_at is not None:
        raise SubmissionAlreadyGradedError(
            "This assignment has already been graded, so it can no longer "
            "be submitted again."
        )
    if _grading_claim_is_live(existing_submission):
        raise SubmissionBeingGradedError(
            "This submission is being graded right now, so it cannot be "
            "replaced. Please try again once grading has finished."
        )
    if (
        student_upload
        and (existing_submission.attempt_count or 0) >= MAX_STUDENT_SUBMISSION_ATTEMPTS
    ):
        raise SubmissionLimitReachedError(
            "You have reached the maximum of "
            f"{MAX_STUDENT_SUBMISSION_ATTEMPTS} submissions for this assignment"
        )


def remaining_student_attempts(submission):
    """How many more times the student may submit: 0 once graded (product
    rule), otherwise what the attempt limit leaves. None (no submission
    yet) means the full allowance."""
    if submission is None:
        return MAX_STUDENT_SUBMISSION_ATTEMPTS
    if submission.graded_at is not None:
        return 0
    return max(0, MAX_STUDENT_SUBMISSION_ATTEMPTS - (submission.attempt_count or 0))


def ensure_student_may_submit(assignment, student):
    """
    Cheap, lock-free pre-check of _check_submission_open for the
    request path and for the moment before a billed extraction call:
    raises SubmissionAlreadyGradedError / SubmissionLimitReachedError when
    the student is already locked out of `assignment`. Scoped to exactly
    (student, assignment): another student's grade, or this student's
    grade on another assignment, has no effect.
    """
    existing = (
        StudentSubmission.objects.filter(assignment=assignment, student=student)
        .only("graded_at", "attempt_count", "grading_state", "grading_started_at")
        .first()
    )
    if existing is not None:
        _check_submission_open(existing, student_upload=True)


def ensure_submission_open(submission):
    """Refuse-if-closed for an existing row (the raw-text edit path): graded
    or being graded. The attempt allowance is not consumed by an edit."""
    _check_submission_open(submission, student_upload=False)


ACTIVE_TASK_STATUSES = (BackgroundTaskStatus.PENDING, BackgroundTaskStatus.STARTED)

# The prompt the raw-text edit path sends alongside the edited ProseMirror
# text. Kept here (not in the view) so the synchronous and asynchronous
# routes cannot drift.
RAW_TEXT_EXTRACTION_PROMPT = """
Analyze the content of an educational assignment that is sent to you in PROSEMIRROR FORMAT and return a JSON

IMPORTANT: Return only valid JSON matching the required structure.
Do not include any explanatory text before or after the JSON
"""


def ensure_no_active_extraction(*, submission=None, assignment=None, student=None):
    """
    Refuse a second answer-extraction while one is still running for the
    same target. A client that timed out and retried must not queue a
    second billed run: the first task is still going to land. Callers hold
    a row lock (the submission, or the student's user row for a first
    upload) so two simultaneous requests cannot both pass this check.
    """
    active = BackgroundProcessingTask.objects.filter(
        task_type__in=(
            BackgroundTaskType.ANSWER_EXTRACTION,
            BackgroundTaskType.BATCH_ANSWER_UPLOAD,
        ),
        status__in=ACTIVE_TASK_STATUSES,
    )
    if submission is not None:
        active = active.filter(submission=submission)
    else:
        active = active.filter(assignment=assignment, requested_by=student)
    if active.exists():
        raise SubmissionProcessingInProgressError(
            "This submission is still being processed from an earlier "
            "request. Please wait for it to finish before sending it again."
        )


def update_submission_from_raw_text(
    user, submission, raw_input, processing_task_id=None
):
    """
    Re-extract a submission's answers from edited raw (ProseMirror) text and
    persist them. The single implementation behind both the synchronous
    PATCH route and extract_answer_background_task.

    Order matters: the closure rules are checked BEFORE the billed
    extraction (a graded or in-grading row can never accept the edit, so
    it must not cost a credit), then again under the row lock before the
    write, because a grade or a claim can land during the extraction. The
    extraction and the write share one refund scope, so a failure after the
    charge - a malformed result, a refusal under the lock, the save itself -
    reclaims the credit rather than charging for an edit that never landed.
    """
    assignment = submission.assignment
    if assignment.status != AssignmentStatus.PUBLISHED:
        raise AssignmentNotOpenError(
            "This assignment is not currently open for submissions."
        )
    if not raw_input or not str(raw_input).strip():
        raise ValueError("There is no text to extract answers from.")

    ensure_submission_open(submission)
    ensure_task_not_cancelled(processing_task_id)

    assignment_context = f"""
    This is the Assignment Context to use in properly extracting the student submissions
    {assignment.questions}
    """
    content = [
        {"type": "text", "text": RAW_TEXT_EXTRACTION_PROMPT},
        {"type": "text", "text": raw_input},
    ]

    with billing_refund_scope(
        reason="submission edit failed before the new answers were persisted"
    ):
        extracted = ai_processor.extract_answer_with_retry(
            user,
            content,
            assignment_context,
            assignment_model=assignment,
            max_retries=3,
            processing_task_id=processing_task_id,
        )
        answers = extracted.get("answers") if isinstance(extracted, dict) else None
        if not isinstance(answers, list):
            raise ValueError(
                "Answer extraction returned no usable `answers` list "
                f"(got {type(answers).__name__}); refusing to persist it."
            )

        with transaction.atomic():
            locked = StudentSubmission.objects.select_for_update().get(pk=submission.pk)
            _check_submission_open(locked, student_upload=False)
            ensure_task_not_cancelled(processing_task_id)
            locked.answers = answers
            locked.raw_input = AssignmentProcessingService.html_to_prosemirror_text(
                student_submission_to_html(locked)
            )
            locked.extraction_confidence = _coerce_confidence(
                extracted.get("extraction_confidence")
            )
            with cancellable_final_save(processing_task_id):
                # Only what this path owns - never a full-row save from an
                # instance that predates the extraction.
                locked.save(
                    update_fields=["answers", "raw_input", "extraction_confidence"]
                )
    return locked


def _match_enrolled_student(course, identified_name):
    """
    Resolve the student name the extractor read off a teacher-uploaded
    submission to exactly one ENROLLED student on the course.

    Exact (case-insensitive) first+last match wins. Only if there is no
    exact match do we fall back to substring matching, and in either case
    a match is accepted ONLY when it is unique: the previous `.first()`
    silently attributed the upload to an arbitrary student whenever the
    name was ambiguous - "Sam" matched Samuel and Samantha, a single-token
    name matched every student whose first name contained it - so one
    student's work and grade landed on another student's record with no
    error anywhere. Refusing is the only safe answer; the teacher can
    upload for that student directly.
    """
    name = " ".join((identified_name or "").split())
    if not name:
        raise CannotAssociateStudentError(
            "Student name cannot be found in the submission"
        )

    enrolled = CustomUser.objects.filter(
        enrollments__course=course,
        enrollments__enrollment_status="ENROLLED",
    ).distinct()

    # Exact: the whole name against "first last", so multi-word first names
    # ("Mary Ann Smith") match without guessing where the split is.
    exact = enrolled.annotate(
        full_name=Concat("first_name", Value(" "), "last_name")
    ).filter(full_name__iexact=name)
    # Two rows are enough to know it's ambiguous; never load a whole roster.
    matches = list(exact[:2])
    if not matches:
        first_name, _, last_name = name.partition(" ")
        fuzzy = enrolled.filter(
            first_name__icontains=first_name, last_name__icontains=last_name
        )
        matches = list(fuzzy[:2])

    if not matches:
        raise CannotAssociateStudentError(
            "Student not among the enrolled students in the course"
        )
    if len(matches) > 1:
        # Teacher-facing text: literal double quotes, not !r (see the same
        # choice in notify_school_admins_of_grading_complete).
        raise CannotAssociateStudentError(
            f'The name "{name}" matches more than one enrolled student in this '  # noqa: B907
            "course, so the submission could not be attributed safely. Please "
            "upload it for the right student directly."
        )
    return matches[0]


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
      D   65-66   1.0  Poor
      F   0-64    0.0  Fail
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
    elif pct >= 65:
        return {"letter_grade": "D", "gpa": 1.0, "remark": "Poor"}
    else:
        return {"letter_grade": "F", "gpa": 0.0, "remark": "Fail"}


# Reverse of the quality-point column above: a cumulative/overall GPA
# (averaged across courses in quality-point space, see
# dashboard.views.StudentAdminDashboardView.overview) mapped back to a
# letter grade and remark. A+ and A share 4.0 quality points in the table
# above - that distinction is percentage-only (97-100 vs 93-96) and isn't
# recoverable from a GPA value alone, so a GPA of 4.0 here reports as "A".
# Ordered highest to lowest; GPA_SCALE[i][0] is the inclusive lower bound.
GPA_SCALE = (
    (4.0, "A", "Excellent"),
    (3.7, "A-", "Very Good"),
    (3.3, "B+", "Good"),
    (3.0, "B", "Good"),
    (2.7, "B-", "Satisfactory"),
    (2.3, "C+", "Satisfactory"),
    (2.0, "C", "Pass"),
    (1.7, "C-", "Pass"),
    (1.3, "D+", "Poor"),
    (1.0, "D", "Poor"),
    (0.0, "F", "Fail"),
)


def get_letter_grade_from_gpa(gpa):
    """
    Returns {letter_grade, remark} for a cumulative GPA value, using the
    same quality-point scale get_grade_details() assigns per percentage
    bracket (see GPA_SCALE above). Use this - not get_grade_details() - to
    label an *overall* GPA that was itself computed by averaging several
    courses' quality points, so the letter grade shown always agrees with
    the GPA number next to it.
    """
    value = float(gpa)
    for threshold, letter, remark in GPA_SCALE:
        if value >= threshold:
            return {"letter_grade": letter, "remark": remark}
    return {"letter_grade": "F", "remark": "Fail"}
