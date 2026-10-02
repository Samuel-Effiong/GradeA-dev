import logging
from datetime import timedelta

from celery import shared_task, states
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework.exceptions import ParseError

from ai_processor.services import GRADING_ASSIGNMENT_PROMPT, ai_processor
from audit import history
from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome, ErrorClass, ReasonCode
from AutoGrader.error_messages import (
    classify_infra_error,
    describe_background_task_error,
)
from AutoGrader.reason_codes import REASON_CODES, CodedError, reason_of
from AutoGrader.tasks import send_email_task
from billing.refusals import PERMANENT_AI_REFUSALS
from classrooms.models import (
    EnrollmentStatusType,
    Topic,
    reachable_courses,
    teacher_can_reach_course,
)
from students.exceptions import (
    AssignmentNotOpenError,
    CannotAssociateStudentError,
    CourseNotReachableError,
    RubricMissingError,
    SubmissionAlreadyGradedError,
    SubmissionBeingGradedError,
    SubmissionGradingInProgressError,
    SubmissionLimitReachedError,
    TaskCancelledError,
)
from students.grading_gates import rubric_missing
from students.models import (
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from students.services import (
    GRADING_TASK_TIME_LIMIT_SECONDS,
    emit_grading_completed,
    grade_engine,
    update_submission_from_raw_text,
    upload_answers_engine,
)
from students.task_access import teacher_may_reach
from students.task_tracking import (
    cancellable_final_save,
    claim_processing_task_start,
    cleanup_cancelled_task_artifacts,
    create_processing_task,
    ensure_batch_has_credits,
    ensure_task_not_cancelled,
    get_processing_task_by_id,
    launch_processing_task,
    mark_processing_task_cancelled,
    mark_processing_task_failure,
    mark_processing_task_started,
    mark_processing_task_success,
    record_refused_item,
    update_processing_task,
)
from users.models import CustomUser, UserTypes

from .exceptions import InvalidUploadFileError
from .file_uploads import sha256_of_upload_payload, upload_assignment_file
from .models import Assignment, AssignmentStatus
from .services import AssignmentProcessingService

logger = logging.getLogger(__name__)

#: H-38: stored on a grading run (and an auto-grade skip) refused because its
#: course is not reachable by the teacher it would run as. The same plain
#: "not found" as S7b's run-time check (SM ruling, N2): it doesn't tell the
#: reader that they once had access.
COURSE_NOT_FOUND = "This course wasn't found."

# Final answers about one upload - never retried, always reported with the
# exception's own (user-facing) message. See upload_answers_engine_async.
# A STARTED extraction claim older than this was left by a dead worker and
# may be taken over by a redelivery. Sized above the extraction's own retry
# budget (3 attempts of a multi-minute call is still well under this).
EXTRACTION_TASK_STALE_AFTER_SECONDS = 60 * 60

UPLOAD_REFUSALS = (
    AssignmentNotOpenError,
    CannotAssociateStudentError,
    InvalidUploadFileError,
    SubmissionAlreadyGradedError,
    SubmissionBeingGradedError,
    SubmissionLimitReachedError,
    # The plan doesn't include the feature, or the wallet can't pay: the
    # same answer on every attempt (billing/refusals.py).
    *PERMANENT_AI_REFUSALS,
)


@shared_task(bind=True)
def grade_all_submissions(self, user_id, assignment_id, processing_task_id=None):
    """
    Legacy bulk-grading task. No code dispatches this anymore (the
    grade-all view fans out per-submission grade_engine_async tasks, and
    scheduled batches use grade_batch_async) — kept only because a
    celery-beat PeriodicTask row could still reference it by dotted path.

    Scoped to UNGRADED submissions, matching every live bulk path: the old
    unfiltered query re-ran the full billed AI pipeline over already-graded
    submissions on every invocation.
    """
    submissions = StudentSubmission.objects.filter(
        assignment_id=assignment_id, graded_at__isnull=True
    )
    submissions_count = submissions.count()

    user = CustomUser.objects.get(id=user_id)
    ensure_task_not_cancelled(processing_task_id)
    mark_processing_task_started(
        processing_task_id,
        meta={
            "current": 0,
            "total": submissions_count,
            "percent": 0,
            "step": "Initializing",
        },
    )

    self.update_state(
        state="PROGRESS",
        meta={
            "current": 0,
            "total": submissions_count,
            "percent": 0,
            "step": "Initializing",
        },
    )

    for index, submission in enumerate(submissions):
        ensure_task_not_cancelled(processing_task_id)
        self.update_state(
            state="PROGRESS",
            meta={
                "current": index,
                "total": submissions_count,
                "percent": (index) / submissions_count * 100,
                "step": "Grading",
            },
        )
        update_processing_task(
            processing_task_id,
            meta={
                "current": index,
                "total": submissions_count,
                "percent": (
                    (index) / submissions_count * 100 if submissions_count else 0
                ),
                "step": "Grading",
            },
        )
        try:
            submission = grade_engine(
                user, submission, processing_task_id=processing_task_id
            )
            print(f"Assignment saved: {index + 1}/{submissions_count}")
        except TaskCancelledError:
            mark_processing_task_cancelled(
                processing_task_id,
                meta={
                    "current": index,
                    "total": submissions_count,
                    "step": "Cancelled",
                },
            )
            raise
        except Exception as e:
            import traceback

            stack_trace_str = traceback.format_exc()
            print(stack_trace_str)
            self.update_state(
                state=states.FAILURE,
                meta={
                    "error": str(e),
                    "assignment_id": assignment_id,
                    "current_submission_id": submission.id,
                    "detail": stack_trace_str,
                },
            )
            mark_processing_task_failure(
                processing_task_id,
                e,
                meta={
                    "current": index,
                    "total": submissions_count,
                    "step": "Failed",
                    "assignment_id": assignment_id,
                    "current_submission_id": str(submission.id),
                },
                fallback_message=(
                    "We couldn't finish grading all submissions. Please try "
                    "again, or contact support if this continues."
                ),
            )
            raise

    mark_processing_task_success(
        processing_task_id,
        meta={"current": submissions_count, "total": submissions_count, "percent": 100},
    )
    return {"status": "Completed", "assignment_id": assignment_id}


@shared_task(bind=True)
def extract_assignment_background_task(
    self,
    user_id,
    assignment_id,
    content,
    raw_input=None,
    keep_existing_title=True,
    processing_task_id=None,
):
    print(
        {
            "user_id": user_id,
            "assignment_id": assignment_id,
            "keep_existing_title": keep_existing_title,
        }
    )
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Extracting assignment content"}
        )
        self.update_state(
            state="PROGRESS", meta={"step": "Extracting assignment content"}
        )

        print("Extracting assignment content")

        assignment = Assignment.objects.get(id=assignment_id)
        user = CustomUser.objects.get(id=user_id)

        ensure_task_not_cancelled(processing_task_id)
        assignment = AssignmentProcessingService.update_assignment_from_extraction(
            user,
            assignment,
            content,
            raw_input=raw_input,
            keep_existing_title=keep_existing_title,
            processing_task_id=processing_task_id,
        )

        print("Assignment saved successfully")
        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Assignment extracted successfully",
                "assignment_id": str(assignment.id),
            },
        )

        return {
            "status": states.SUCCESS,
            "assignment_id": assignment_id,
            "message": "Assignment extracted successfully",
        }
    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id, meta={"step": "Assignment extraction cancelled"}
        )
        processing_task = get_processing_task_by_id(processing_task_id)
        cleanup_cancelled_task_artifacts(processing_task)
        raise
    except Exception as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={
                "step": "Assignment extraction failed",
                "assignment_id": assignment_id,
            },
            fallback_message=(
                "We couldn't extract the assignment content from your file. "
                "Please check the file and try again, or contact support if "
                "this continues."
            ),
        )
        raise


@shared_task(bind=True)
def update_assignment_background_task(
    self,
    user_id,
    assignment_id,
    content,
    raw_input=None,
    topic_id=None,
    processing_task_id=None,
):
    """
    Async re-extraction task triggered when a teacher updates an assignment
    with new raw_input content. Non-AI fields (title, status, due_date, etc.)
    are saved synchronously in the view before this task fires.
    """
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Extracting updated assignment content"}
        )
        self.update_state(
            state="PROGRESS", meta={"step": "Extracting updated assignment content"}
        )

        assignment = Assignment.objects.get(id=assignment_id)
        user = CustomUser.objects.get(id=user_id)

        topic = None
        if topic_id:
            from classrooms.models import Topic as TopicModel

            topic = TopicModel.objects.filter(id=topic_id).first()

        ensure_task_not_cancelled(processing_task_id)
        assignment = AssignmentProcessingService.update_assignment_from_extraction(
            user,
            assignment,
            content,
            topic=topic,
            raw_input=raw_input,
            processing_task_id=processing_task_id,
        )

        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Assignment updated and re-extracted successfully",
                "assignment_id": str(assignment.id),
            },
        )
        return {
            "status": states.SUCCESS,
            "assignment_id": assignment_id,
            "message": "Assignment updated and re-extracted successfully",
        }
    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id, meta={"step": "Assignment re-extraction cancelled"}
        )
        processing_task = get_processing_task_by_id(processing_task_id)
        cleanup_cancelled_task_artifacts(processing_task)
        raise
    except Exception as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={
                "step": "Assignment re-extraction failed",
                "assignment_id": assignment_id,
            },
            fallback_message=(
                "We couldn't re-extract the updated assignment content. "
                "Please try again, or contact support if this continues."
            ),
        )
        raise


@shared_task(bind=True, max_retries=3)
def extract_answer_background_task(
    self, submission_id, raw_input, user_id, processing_task_id=None
):
    """
    Asynchronous twin of the raw-text edit (PATCH submissions/<pk>): the
    edited ProseMirror text is re-extracted into answers off the request
    thread. Dispatched by StudentSubmissionViewSet.update_async. Same
    retry policy as upload_answers_engine_async: refusals are final and
    recorded verbatim; anything else is retried up to max_retries and only
    then recorded as a failure.
    """
    try:
        ensure_task_not_cancelled(processing_task_id)
        # The tracked row is the idempotency claim (there is no RUNNING
        # state on the submission for an extraction): a redelivery of this
        # message while the original execution is still running must not
        # run the billed extraction a second time.
        # A Celery retry (retries > 0) is this same execution continuing
        # and already holds the row; only a first delivery contends for it.
        if self.request.retries == 0 and not claim_processing_task_start(
            processing_task_id,
            stale_after=timedelta(seconds=EXTRACTION_TASK_STALE_AFTER_SECONDS),
            meta={"step": "Extracting answer content"},
        ):
            logger.info(
                "Redelivered answer-extraction task %s for submission %s skipped: "
                "the original delivery still holds tracked task %s.",
                self.request.id,
                submission_id,
                processing_task_id,
            )
            return {
                "status": states.SUCCESS,
                "submission_id": str(submission_id),
                "message": (
                    "This submission is already being processed by another "
                    "worker — duplicate run skipped."
                ),
            }
        self.update_state(state="PROGRESS", meta={"step": "Extracting answer content"})

        submission = StudentSubmission.objects.select_related("assignment").get(
            id=submission_id
        )
        user = CustomUser.objects.get(id=user_id)

        submission = update_submission_from_raw_text(
            user, submission, raw_input, processing_task_id=processing_task_id
        )

        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Answers extracted successfully",
                "submission_id": str(submission.id),
            },
        )
        return {
            "status": states.SUCCESS,
            "submission_id": str(submission.id),
            "message": "Answers extracted successfully",
        }
    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id, meta={"step": "Answer extraction cancelled"}
        )
        raise
    except UPLOAD_REFUSALS as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Submission edit refused", "submission_id": submission_id},
        )
        return {
            "status": states.FAILURE,
            "message": describe_background_task_error(exc),
        }
    except Exception as exc:
        if self.request.retries < self.max_retries:
            update_processing_task(
                processing_task_id,
                meta={
                    "step": (
                        f"Retrying ({self.request.retries + 1}/{self.max_retries})"
                    ),
                    "last_error": describe_background_task_error(exc),
                },
            )
            raise self.retry(exc=exc, countdown=3) from exc
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Answer extraction failed", "submission_id": submission_id},
            fallback_message=(
                "We couldn't extract the answers from this text. Please "
                "try again, or contact support if this continues."
            ),
        )
        raise


def _grading_failure_error_class(exc):
    """FR-A-05's fixed taxonomy, applied to what grade_engine_async's except
    block actually sees. A coded failure (FR-A-06) and the two AI refusals
    (credits, plan: the user's to resolve, so USER; they used to be
    misfiled as MODEL, 08a §2.2) carry their own class. PROVIDER_FAILURE
    is the provider's fault when its cause is a recognised infra failure
    (timeout, rate limit, dropped connection, 5xx), and the model's when
    it is not (unusable output). A teacher who lost access to the course
    (H-38; uncoded) is the user's case. Any other recognised infra failure
    is the provider's, and anything else an unclassified system fault."""
    reason = reason_of(exc)
    if reason is not None:
        code = reason[0]
        if (
            code == ReasonCode.PROVIDER_FAILURE
            and classify_infra_error(exc.__cause__) is None
        ):
            return ErrorClass.MODEL
        return REASON_CODES[code].error_class
    if isinstance(exc, CourseNotReachableError):
        return ErrorClass.USER
    if classify_infra_error(exc) is not None:
        return ErrorClass.PROVIDER
    return ErrorClass.SYSTEM


def _grading_failure_reason_code(exc):
    """The reason recorded on GRADING_FAILED: a coded failure's own code
    (FR-A-06), or the audit-only COURSE_NOT_REACHABLE for H-38's uncoded
    refusal (N3). That one is for the trail only; the client is still told
    the plain "not found"."""
    if isinstance(exc, CodedError):
        return exc.reason_code
    if isinstance(exc, CourseNotReachableError):
        return ReasonCode.COURSE_NOT_REACHABLE
    return None


def _course_school_id(course):
    """The school a course belongs to, or None for a course in a teacher's
    own (individual) session."""
    session = course.session if course is not None else None
    return session.school_id if session is not None else None


def _unreachable_course_school_id(exc, submission):
    """H-38 N3: the school a refused run's GRADING_FAILED is filed under.
    For the reachability refusal only, it is the course's school: the
    teacher removed from it has none left, and the school must find the
    refusal in its own trail. Any other failure returns None, which keeps
    the emitter's rule (the actor's school)."""
    if not isinstance(exc, CourseNotReachableError):
        return None
    if not isinstance(submission, StudentSubmission):
        return None
    try:
        return _course_school_id(submission.assignment.course)
    except Exception as error:
        # Never let the audit's own lookup break the failure handling it
        # sits in: the event is then filed by the emitter's rule.
        logger.error(
            "The course's school could not be read for the refused grading "
            "of submission %s: %s",
            submission.id,
            type(error).__name__,
        )
        return None


def _audit_unreachable_course_refusal(teacher_id, assignment, processing_task_id=None):
    """H-38 N3: one GRADING_FAILED for a batch or an auto-grade refused as a
    whole because the teacher it would run as can no longer reach the
    course. No submission was reached, so the event is about the teacher.
    The actor is the tracked task's requester when the run has one, else
    SYSTEM (as grade_engine_async's own event). It is filed under the
    course's school: the removed teacher no longer has one. Ids only."""
    # The lookups are guarded: emit() never raises, and nothing here may
    # turn the refusal into a task failure. On an error the event is still
    # written with whatever was read before it: nothing if the requester
    # lookup failed (the system's, no school), or the requester alone if
    # only the school lookup failed (the emitter then files it under the
    # requester's own school, which is none for a removed teacher).
    actor = school_id = None
    try:
        task = get_processing_task_by_id(processing_task_id)
        actor = task.requested_by if task else None
        school_id = _course_school_id(assignment.course)
    except Exception as error:
        logger.error(
            "The requester or school could not be read for the refused "
            "grading of assignment %s: %s",
            assignment.id,
            type(error).__name__,
        )
    emit(
        AuditAction.GRADING_FAILED,
        actor=actor,
        request=None,
        target_type="CustomUser",
        target_id=teacher_id,
        outcome=AuditOutcome.FAILURE,
        error_class=ErrorClass.USER,
        reason_code=ReasonCode.COURSE_NOT_REACHABLE,
        school_id=school_id,
        metadata={
            "assignment_id": str(assignment.id),
            "task_id": str(processing_task_id) if processing_task_id else None,
        },
    )


@shared_task(
    bind=True,
    # Hard kill point for a hung grading run. The grading claim's staleness
    # window (students.services.GRADING_CLAIM_STALE_AFTER) is derived from
    # this, so a RUNNING claim older than that window is guaranteed
    # abandoned - the worker holding it has been killed - and reclaiming it
    # can never double-bill a still-live run. soft_time_limit fires 60s
    # earlier so the normal failure path (mark task failed, release the
    # claim, refund in-flight charges) gets a chance to run before SIGKILL.
    soft_time_limit=GRADING_TASK_TIME_LIMIT_SECONDS - 60,
    time_limit=GRADING_TASK_TIME_LIMIT_SECONDS,
)
def grade_engine_async(
    self, user_id, submission_id, batch_id=None, processing_task_id=None
):
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Retrieving submission"}
        )
        # FR-A-07 (S7c): if the batch already ran out of credits, stop here,
        # before any provider call (never charged).
        ensure_batch_has_credits(processing_task_id)
        self.update_state(state="PROGRESS", meta={"step": "Retrieving submission"})
        submission = StudentSubmission.objects.select_related("assignment").get(
            id=submission_id
        )

        # Clear scheduling info if it exists
        if submission.scheduled_grading_at or submission.grading_task_name:
            submission.scheduled_grading_at = None
            submission.grading_task_name = None
            submission.save(update_fields=["scheduled_grading_at", "grading_task_name"])

        user = CustomUser.objects.get(id=user_id)
        # H-38, checked when the run starts, not only when it was requested:
        # a retry, a scheduled grading or a queued batch item must not grade
        # (or bill) for a teacher since removed from the course's school.
        # This is the one chokepoint every grading route, the scheduled
        # grading and the auto-grade beat go through. Merge-down of bundle 4
        # (SM ruling): Epic A keeps this coded refusal; beta's H-38 soft
        # return is intentionally not carried here. Ids only.
        # For a teacher the two halves agree; the reachable_courses half
        # keeps S7b's refusal of a non-teacher who isn't the course's teacher.
        if not teacher_may_reach(user, submission) or not (
            reachable_courses(user).filter(pk=submission.assignment.course_id).exists()
        ):
            logger.warning(
                "Grading refused (H-38): submission %s, user %s can no longer "
                "reach its course.",
                submission.id,
                user.id,
            )
            raise CourseNotReachableError()

        self.update_state(state="PROGRESS", meta={"step": "Grading"})
        update_processing_task(processing_task_id, meta={"step": "Grading"})
        ensure_task_not_cancelled(processing_task_id)
        # Epic A S4: the grade as stored before this run, for the
        # before/after on GRADING_COMPLETED.
        grade_before = history.snapshot(submission)
        # grade_engine performs the final (cancellation-guarded) save
        # itself; a second full save here would race formatted_grade_async's
        # write to the same row and clobber formatted_grade (H4).
        submission = grade_engine(
            user, submission, processing_task_id=processing_task_id
        )

        self.update_state(state="PROGRESS", meta={"step": "Completed"})
        completed_task = mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Completed",
                "submission_id": str(submission.id),
                "batch_id": str(batch_id) if batch_id else None,
            },
        )
        emit_grading_completed(
            submission,
            actor=completed_task.requested_by if completed_task else None,
            before=grade_before,
            task_id=processing_task_id,
        )

        if batch_id:
            session = BatchUploadSession.objects.get(id=batch_id)
            session.update_result(
                f"Submission for {submission.student.get_full_name()}",
                "SUCCESS",
                batch_type=BatchUploadType.GRADE,
                submission_id=submission.id,
            )
        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Grading completed successfully",
        }
    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id,
            meta={"step": "Grading cancelled", "submission_id": submission_id},
        )
        raise
    except SubmissionGradingInProgressError:
        # C3: this is a redelivered/duplicate task racing a still-running
        # original - a clean skip, not a failure. Return SUCCESS so Celery
        # doesn't retry it and the user isn't shown an error for a run that
        # is, in fact, happening.
        #
        # Which tracked task to touch depends on WHO the duplicate is:
        #
        # * A Redis redelivery of the ORIGINAL's own message carries the
        #   same Celery task id and the same processing_task_id. The
        #   tracked row belongs to the run that holds the claim, so this
        #   execution must leave it alone: marking it SUCCESS+skipped here
        #   told the frontend "done" with no grade, and then the terminal-
        #   status guard blocked the original's real FAILURE from ever
        #   being recorded.
        # * A genuinely separate dispatch (a second grade-async click, or a
        #   task racing the synchronous grade view) has its own tracked
        #   row, which nobody else will finish - so it is closed here as a
        #   skip.
        #
        # A tracked row with no celery id yet is treated as this delivery's
        # own: attach_celery_task runs right after publish, so an unset id
        # means the row was created microseconds ago for this very message,
        # and leaving a row open is recoverable while closing the wrong one
        # is not.
        tracked = get_processing_task_by_id(processing_task_id)
        own_delivery = tracked is not None and (
            tracked.celery_task_id in (None, "", str(self.request.id))
        )
        if own_delivery:
            logger.info(
                "Redelivered grading task %s for submission %s skipped: the "
                "original delivery still holds the claim; tracked task %s "
                "left to it.",
                self.request.id,
                submission_id,
                processing_task_id,
            )
        else:
            mark_processing_task_success(
                processing_task_id,
                meta={
                    "step": "Skipped — already being graded",
                    "skipped": True,
                    "submission_id": submission_id,
                },
            )
        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": (
                "This submission is already being graded by another worker — "
                "duplicate run skipped."
            ),
        }
    except Exception as exc:
        fallback_message = (
            "We couldn't grade this submission. Please try again, or "
            "contact support if this continues."
        )
        task = mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Grading failed", "submission_id": submission_id},
            fallback_message=fallback_message,
        )
        emit(
            AuditAction.GRADING_FAILED,
            actor=task.requested_by if task else None,
            request=None,
            target_type="StudentSubmission",
            target_id=submission_id,
            outcome=AuditOutcome.FAILURE,
            error_class=_grading_failure_error_class(exc),
            reason_code=_grading_failure_reason_code(exc),
            school_id=_unreachable_course_school_id(exc, locals().get("submission")),
            metadata={
                "assignment_id": (
                    str(task.assignment_id) if task and task.assignment_id else None
                ),
                "submission_id": str(submission_id),
                "task_id": str(processing_task_id) if processing_task_id else None,
                "prompt_version": GRADING_ASSIGNMENT_PROMPT.version,
            },
        )
        if batch_id:
            session = BatchUploadSession.objects.get(id=batch_id)
            submission_obj = locals().get("submission")
            file_name = (
                f"Submission for {submission_obj.student.get_full_name()}"
                if isinstance(submission_obj, StudentSubmission)
                else f"Submission {submission_id}"
            )
            session.update_result(
                file_name,
                "FAILED",
                # Auto-grade and grade_batch_async dispatch with no tracked
                # task: the batch row is the only place the reason lands.
                error=(
                    task.error
                    if task
                    else describe_background_task_error(exc, fallback_message)
                ),
                batch_type=BatchUploadType.GRADE,
                submission_id=submission_id,
            )
        raise


def _reconcile_formatted_grade_numbers(formatted, submission):
    """
    Force the student-facing formatted grade's numbers to agree with the
    authoritative stored grade. GRADE_FORMATTER's prompt forbids altering
    numbers, but nothing else guarantees an LLM restatement got them right —
    and this text is shown directly to students with no other cross-check.
    The overall score sentence is rebuilt deterministically from the stored
    columns, and each per-question max_score is overwritten from the stored
    feedback JSON. Purely corrective: unexpected shapes are left untouched.
    """
    if not isinstance(formatted, dict):
        return formatted

    summary = formatted.get("overall_performance_summary")
    if (
        isinstance(summary, dict)
        and submission.score is not None
        and submission.max_points
        and submission.score_percentage is not None
    ):
        summary["score_statement"] = (
            f"You scored {float(submission.score):g} out of "
            f"{float(submission.max_points):g} points, giving you a final "
            f"grade of {float(submission.score_percentage):.2f}%."
        )

    evaluations = (
        submission.feedback.get("question_evaluations", [])
        if isinstance(submission.feedback, dict)
        else []
    )
    authoritative = {
        ai_processor._question_number_key(ev.get("question_number")): ev
        for ev in evaluations
        if isinstance(ev, dict)
    }

    breakdown = formatted.get("question_by_question_breakdown")
    if isinstance(breakdown, list):
        for item in breakdown:
            if not isinstance(item, dict):
                continue
            ev = authoritative.get(
                ai_processor._question_number_key(item.get("question_number"))
            )
            if not ev:
                continue
            if "max_points" in ev:
                item["max_score"] = ev["max_points"]
            if "score_awarded" in item and "score_awarded" in ev:
                item["score_awarded"] = ev["score_awarded"]

    return formatted


@shared_task(bind=True)
def format_grade(self, submission_id, prompt, processing_task_id=None):
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Retrieving submission"}
        )
        self.update_state(state="PROGRESS", meta={"step": "Retrieving submission"})

        submission = StudentSubmission.objects.get(id=submission_id)

        self.update_state(state="PROGRESS", meta={"step": "Formatting grade"})
        update_processing_task(processing_task_id, meta={"step": "Formatting grade"})
        ensure_task_not_cancelled(processing_task_id)
        formatted_grade = ai_processor.formatted_grade(
            submission.student,
            prompt,
            assignment_model=submission.assignment,
            processing_task_id=processing_task_id,
        )

        self.update_state(state="PROGRESS", meta={"step": "Saving formatted grade"})
        update_processing_task(
            processing_task_id, meta={"step": "Saving formatted grade"}
        )
        submission.formatted_grade = _reconcile_formatted_grade_numbers(
            formatted_grade, submission
        )
        with cancellable_final_save(processing_task_id):
            submission.save()

        self.update_state(
            state="PROGRESS", meta={"step": "Grade formatted successfully"}
        )
        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Grade formatted successfully",
                "submission_id": str(submission.id),
            },
        )
        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Grade formatted successfully",
        }
    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id,
            meta={"step": "Formatted grade generation cancelled"},
        )
        raise
    except Exception as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Formatted grade generation failed"},
            fallback_message=(
                "We couldn't generate the formatted grade for this "
                "submission. Please try again, or contact support if this "
                "continues."
            ),
        )
        raise


# soft/hard limits sized against ai_processor.services.PDFService.MAX_PAGE_COUNT
# (300) and ANSWERS_EXTRACTION_PAGES_PER_CHUNK (3) - see the comments on
# both for the real-endpoint timing this is derived from. Re-derive this
# alongside those two if either changes; it must stay safely under
# CELERY_BROKER_TRANSPORT_OPTIONS' visibility_timeout=3600s in settings.py.
@shared_task(bind=True, max_retries=3, soft_time_limit=2700, time_limit=3000)
def upload_answers_engine_async(
    self,
    assignment_id,
    file_payload,
    prompt,
    user_id,
    session_id=None,
    file_name=None,
    processing_task_id=None,
):
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Retrieving requirements"}
        )
        self.update_state(state="PROGRESS", meta={"step": "Retrieving requirements"})

        assignment = Assignment.objects.get(id=assignment_id)
        user = CustomUser.objects.get(id=user_id)

        is_teacher = user.user_type == UserTypes.TEACHER

        self.update_state(
            state="PROGRESS", meta={"step": "Preparing submission content"}
        )
        update_processing_task(
            processing_task_id, meta={"step": "Preparing submission content"}
        )
        ensure_task_not_cancelled(processing_task_id)
        uploaded_file = AssignmentProcessingService.rebuild_uploaded_file(file_payload)
        try:
            content = AssignmentProcessingService.prepare_ai_content(
                uploaded_file, prompt
            )
        except InvalidUploadFileError:
            # A coded file refusal (FR-A-06 S6b) goes on as itself, so the
            # item keeps its reason code.
            raise
        except ParseError as exc:
            # An unreadable or mislabelled file fails the same way on every
            # attempt: a final refusal (UPLOAD_REFUSALS), never a retry.
            raise InvalidUploadFileError(exc.detail) from exc

        self.update_state(state="PROGRESS", meta={"step": "Extracting answers"})
        update_processing_task(processing_task_id, meta={"step": "Extracting answers"})
        ensure_task_not_cancelled(processing_task_id)
        # FR-A-07 (S7c): stop before the billed extraction if the batch
        # already ran out of credits.
        ensure_batch_has_credits(processing_task_id)
        outcome: dict = {}
        submission = upload_answers_engine(
            assignment=assignment,
            content=content,
            request_user=user,
            is_proxy_upload=is_teacher,
            processing_task_id=processing_task_id,
            file_name=file_name or getattr(uploaded_file, "name", None),
            upload_outcome=outcome,
        )

        if session_id:
            session = BatchUploadSession.objects.get(id=session_id)
            session.update_result(
                file_name,
                "SUCCESS",
                batch_type=BatchUploadType.SUBMISSION,
                submission_id=submission.id,
            )

        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Answers extracted successfully",
                "submission_id": str(submission.id),
                "assignment_id": assignment_id,
                # F3 (S6c): true when this upload overwrote an existing
                # ungraded submission; S7a lifts it into the per-item result.
                "replaced_existing": bool(outcome.get("replaced_existing")),
            },
        )
        return {
            "status": states.SUCCESS,
            "submission_id": str(submission.id),
            "message": "Answers extracted successfully",
        }

    except TaskCancelledError as exc:
        mark_processing_task_cancelled(
            processing_task_id,
            meta={
                "step": "Answer extraction cancelled",
                "assignment_id": assignment_id,
            },
        )
        if session_id:
            session = BatchUploadSession.objects.get(id=session_id)
            session.update_result(file_name, "CANCELLED", error=str(exc))
        raise
    except UPLOAD_REFUSALS as exc:
        # A refusal is a final answer about THIS upload (no student to
        # attach it to; the student is locked out of the assignment), not
        # a transient fault - retrying it three times would only re-bill
        # the extraction. Recorded with their user-facing message (never a
        # credit refusal's internal text) and reported as a non-retried
        # failure.
        message = describe_background_task_error(exc)
        task = mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Submission refused", "assignment_id": assignment_id},
        )
        if session_id:
            session = BatchUploadSession.objects.get(id=session_id)
            session.update_result(
                file_name, "FAILED", error=task.error if task else message
            )
        return {"status": states.FAILURE, "message": message}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            # Not a failure yet. Marking FAILURE here (as this used to)
            # made the tracked row terminal, so the retry's own success
            # could never be recorded - the UI showed a failed upload for
            # a submission that had in fact been created.
            update_processing_task(
                processing_task_id,
                meta={
                    "step": (
                        f"Retrying ({self.request.retries + 1}/{self.max_retries})"
                    ),
                    "last_error": describe_background_task_error(exc),
                },
            )
            raise self.retry(exc=exc, countdown=3) from exc

        task = mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Answer extraction failed", "assignment_id": assignment_id},
            fallback_message=(
                "We couldn't process this submission upload. Please check "
                "the file and try again, or contact support if this "
                "continues."
            ),
        )
        if session_id:
            session = BatchUploadSession.objects.get(id=session_id)
            # The classified, user-safe message - never the raw exception.
            session.update_result(
                file_name, "FAILED", error=task.error if task else None
            )
        raise exc


@shared_task()
def formatted_grade_async(submission_id, user_prompt, processing_task_id=None):
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Formatting grade"}
        )
        submission = StudentSubmission.objects.get(id=submission_id)
        formatted_grade = ai_processor.formatted_grade(
            submission.student,
            user_prompt,
            assignment_model=submission.assignment,
            processing_task_id=processing_task_id,
        )
        submission.formatted_grade = _reconcile_formatted_grade_numbers(
            formatted_grade, submission
        )
        with cancellable_final_save(processing_task_id):
            submission.save(update_fields=["formatted_grade"])

        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Grade formatted successfully",
                "submission_id": str(submission.id),
            },
        )
        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Grade formatted successfully",
        }
    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id,
            meta={"step": "Formatted grade generation cancelled"},
        )
        raise
    except Exception as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Formatted grade generation failed"},
            fallback_message=(
                "We couldn't generate the formatted grade for this "
                "submission. Please try again, or contact support if this "
                "continues."
            ),
        )
        raise


@shared_task(bind=True, max_retries=3, soft_time_limit=1800, time_limit=2100)
def upload_assignment_async(
    self,
    *,
    user_id,
    course_id,
    topic_id=None,
    session_id=None,
    file_payload=None,
    prompt_text=None,
    file_name=None,
    processing_task_id=None,
):
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Loading assignment context"}
        )

        user = CustomUser.objects.get(id=user_id)
        course = reachable_courses(user).get(id=course_id)
        topic = Topic.objects.get(id=topic_id) if topic_id else None

        update_processing_task(
            processing_task_id, meta={"step": "Preparing assignment content"}
        )
        ensure_task_not_cancelled(processing_task_id)
        uploaded_file = AssignmentProcessingService.rebuild_uploaded_file(file_payload)
        # Checks the file, extracts and saves it with its charges refunded if
        # it fails, and answers a file this course already has an assignment
        # for with that assignment instead of extracting it again.
        outcome = upload_assignment_file(
            user,
            uploaded_file,
            prompt_text,
            course=course,
            topic=topic,
            sha256=sha256_of_upload_payload(file_payload),
            processing_task_id=processing_task_id,
        )
        assignment = outcome.assignment

        ensure_task_not_cancelled(processing_task_id)

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(
            file_name,
            "SUCCESS",
            batch_type=BatchUploadType.ASSIGNMENT,
            assignment_id=assignment.id,
        )

        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": (
                    "This file was already uploaded; the existing assignment was kept"
                    if outcome.already_uploaded
                    else "Assignment uploaded successfully"
                ),
                "assignment_id": str(assignment.id),
                "file_name": file_name,
                "already_uploaded": outcome.already_uploaded,
            },
        )
        return {
            "status": states.SUCCESS,
            "assignment_id": str(assignment.id),
            "already_uploaded": outcome.already_uploaded,
            "message": "Assignment uploaded successfully",
        }

    except TaskCancelledError as exc:
        mark_processing_task_cancelled(
            processing_task_id,
            meta={"step": "Assignment upload cancelled", "file_name": file_name},
        )
        processing_task = get_processing_task_by_id(processing_task_id)
        cleanup_cancelled_task_artifacts(processing_task)
        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "CANCELLED", error=str(exc))
        raise
    except Exception as e:
        mark_processing_task_failure(
            processing_task_id,
            e,
            meta={"step": "Assignment upload failed", "file_name": file_name},
            fallback_message=(
                "We couldn't upload and save this assignment. Please check "
                "the file and try again, or contact support if this "
                "continues."
            ),
        )

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(e))
        raise


def _refuse_batch_without_rubric(assignment, submissions, session, actor):
    """
    The run-time RUBRIC_MISSING check for a scheduled or automatic batch
    (S6d): the rubric was there when grading was scheduled (the route
    checks) but is gone now. Nothing is dispatched, claimed or charged.
    Each ungraded submission becomes a refused tracked item in S7a's
    per-item shape (item_index 1..n, reason_code RUBRIC_MISSING), is
    recorded FAILED in the batch's legacy results as a failed tracked
    item also is, and is audited with its code (the SM's ruling on
    S6d x S7a).
    """
    refusal = RubricMissingError()
    for item_index, submission in enumerate(submissions, start=1):
        item = None
        if actor is not None:
            item = record_refused_item(
                requested_by=actor,
                task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
                error=refusal,
                batch_session=session,
                assignment=assignment,
                submission=submission,
                file_name=f"Submission for {submission.student.get_full_name()}",
                item_index=item_index,
            )
        if session is not None:
            session.update_result(
                f"Submission for {submission.student.get_full_name()}",
                "FAILED",
                error=str(refusal),
                batch_type=BatchUploadType.GRADE,
                submission_id=submission.id,
            )
        emit(
            AuditAction.GRADING_FAILED,
            actor=actor,
            request=None,
            target_type="StudentSubmission",
            target_id=submission.id,
            outcome=AuditOutcome.FAILURE,
            error_class=REASON_CODES[ReasonCode.RUBRIC_MISSING].error_class,
            reason_code=ReasonCode.RUBRIC_MISSING,
            metadata={
                "assignment_id": str(assignment.id),
                "submission_id": str(submission.id),
                "task_id": str(item.id) if item is not None else None,
                "prompt_version": GRADING_ASSIGNMENT_PROMPT.version,
            },
        )
    logger.warning(
        "Grading of assignment %s refused at run time: RUBRIC_MISSING "
        "(%d submission(s) not dispatched).",
        assignment.id,
        len(submissions),
    )


@shared_task(bind=True, max_retries=3)
def grade_batch_async(
    self, user_id, assignment_id, batch_id=None, processing_task_id=None
):
    submissions = StudentSubmission.objects.filter(
        assignment_id=assignment_id, graded_at__isnull=True
    )

    # Clear assignment-level scheduling info and create BatchUploadSession if missing
    # H-38: refuse the whole batch up front when the requester can no longer
    # reach the course (grade_engine_async also refuses each item).
    batch_user = CustomUser.objects.filter(id=user_id).first()
    batch_assignment = (
        Assignment.objects.select_related("course").filter(id=assignment_id).first()
    )
    if (
        batch_user is not None
        and batch_assignment is not None
        and not teacher_may_reach(batch_user, batch_assignment)
    ):
        logger.warning(
            "Batch grading refused (H-38): assignment %s, user %s can no longer "
            "reach its course.",
            assignment_id,
            user_id,
        )
        _audit_unreachable_course_refusal(
            batch_user.id, batch_assignment, processing_task_id
        )
        return COURSE_NOT_FOUND

    try:
        assignment = Assignment.objects.get(id=assignment_id)
        if assignment.scheduled_grading_at or assignment.grading_task_name:
            assignment.scheduled_grading_at = None
            assignment.grading_task_name = None
            assignment.save(update_fields=["scheduled_grading_at", "grading_task_name"])

        if not batch_id and submissions.exists():
            user = CustomUser.objects.get(id=user_id)
            session = BatchUploadSession.objects.create(
                teacher=user,
                course=assignment.course,
                task_type=BatchUploadType.GRADE,
                total_files=submissions.count(),
            )
            batch_id = str(session.id)
    except Exception as e:
        logger.error(f"Failed to clear scheduling info or create session: {e}")
        pass

    # S6d x S7a (the SM's ruling): the run-time RUBRIC_MISSING re-check
    # (the rubric was removed after scheduling) comes before any item is
    # dispatched. Each submission is recorded as a refused tracked item in
    # S7a's per-item shape, with no AI call, claim or charge.
    batch_assignment = Assignment.objects.filter(id=assignment_id).first()
    if batch_assignment is not None and rubric_missing(batch_assignment.questions):
        _refuse_batch_without_rubric(
            batch_assignment,
            list(submissions.select_related("student")),
            (
                BatchUploadSession.objects.filter(id=batch_id).first()
                if batch_id
                else None
            ),
            CustomUser.objects.filter(id=user_id).first(),
        )
        return "Refused: RUBRIC_MISSING"

    ensure_task_not_cancelled(processing_task_id)
    if submissions.exists():
        # With no session (its creation failed above, and was logged) the
        # items are still tracked, just not grouped: grading is never dropped.
        _dispatch_tracked_grading(
            CustomUser.objects.get(id=user_id),
            Assignment.objects.get(id=assignment_id),
            submissions,
            (
                BatchUploadSession.objects.filter(id=batch_id).first()
                if batch_id
                else None
            ),
        )


def _dispatch_tracked_grading(teacher, assignment, submissions, session):
    """Grade each submission as a tracked batch item, as grade-all does
    (FR-A-07 S7a): the session then answers in session-results' per-item
    shape, with codes, instead of the legacy results list. Items are
    numbered 1..n in dispatch order."""
    for item_index, submission in enumerate(submissions, start=1):
        processing_task = create_processing_task(
            requested_by=teacher,
            task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
            batch_session=session,
            assignment=assignment,
            submission=submission,
            file_name=f"Submission for {submission.student.get_full_name()}",
            meta={"step": "Queued for batch grading"},
            item_index=item_index,
        )
        launch_processing_task(
            grade_engine_async,
            processing_task,
            str(teacher.id),
            str(submission.id),
            batch_id=str(session.id) if session else None,
        )
        emit(
            AuditAction.GRADING_REQUESTED,
            actor=teacher,
            target_type="StudentSubmission",
            target_id=submission.id,
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "assignment_id": str(assignment.id),
                "submission_id": str(submission.id),
                "task_id": str(processing_task.id),
                "task_type": BackgroundTaskType.BATCH_SUBMISSION_GRADING,
            },
        )
        logger.info("Starting grading of submission %s", submission.id)


@shared_task(name="assignments.tasks.auto_grade_due_assignment")
def auto_grade_due_assignment(assignment_id):
    try:
        assignment = Assignment.objects.get(id=assignment_id)

        if not assignment.auto_grade_on_due_date:
            return "Auto grade disabled."

        ungraded_submissions = assignment.submissions.filter(graded_at__isnull=True)

        if not ungraded_submissions.exists():
            return "No ungraded submissions."

        # H-38: the course's teacher (a permanent owner) may have left the
        # school. Grading the school's students in their name - and billing
        # them - is exactly what removal must stop. Ids only.
        if not teacher_can_reach_course(assignment.course.teacher, assignment.course):
            logger.warning(
                "Auto-grade skipped (H-38): assignment %s, course %s is no "
                "longer reachable by its teacher %s.",
                assignment.id,
                assignment.course_id,
                assignment.course.teacher_id,
            )
            _audit_unreachable_course_refusal(assignment.course.teacher_id, assignment)
            return COURSE_NOT_FOUND

        session = BatchUploadSession.objects.create(
            teacher=assignment.course.teacher,
            course=assignment.course,
            task_type=BatchUploadType.GRADE,
            total_files=ungraded_submissions.count(),
        )

        # S6d x S7a: the run-time re-check, before any item is dispatched
        # (see grade_batch_async).
        if rubric_missing(assignment.questions):
            _refuse_batch_without_rubric(
                assignment,
                list(ungraded_submissions.select_related("student")),
                session,
                assignment.course.teacher,
            )
            return "Refused: RUBRIC_MISSING"

        _dispatch_tracked_grading(
            assignment.course.teacher, assignment, ungraded_submissions, session
        )

        return f"Auto-grading started for {ungraded_submissions.count()} submissions."
    except Exception:
        # Logged with its traceback; the task result (Celery's backend) never
        # carries exception text (QA-ERR-03).
        logger.exception("Auto-grading could not start for %s", assignment_id)
        return "Error: auto-grading could not start."


@shared_task(name="assignments.tasks.send_assignment_due_reminder")
def send_assignment_due_reminder(assignment_id, hours_before):
    try:
        assignment = Assignment.objects.select_related("course", "course__teacher").get(
            id=assignment_id
        )

        if not assignment.due_date or assignment.status != AssignmentStatus.PUBLISHED:
            return "Assignment is not eligible for due date reminders."

        reminder_label = {24: "24 hours", 1: "1 hour"}.get(hours_before)
        if reminder_label is None:
            return f"Invalid reminder offset: {hours_before}"

        due_date_display = timezone.localtime(assignment.due_date).strftime(
            "%B %d, %Y at %I:%M %p"
        )

        teacher = assignment.course.teacher
        notifications_sent = 0

        if (
            teacher
            and teacher.email
            and hasattr(teacher, "settings")
            and teacher.settings.notify_assignment_due_reminder
        ):
            try:
                teacher_html = render_to_string(
                    "email/assignment_due_reminder.html",
                    {
                        "recipient": teacher,
                        "assignment": assignment,
                        "course": assignment.course,
                        "due_date_display": due_date_display,
                        "reminder_label": reminder_label,
                        "is_teacher": True,
                    },
                )

                send_email_task.delay(
                    subject=(
                        f"Assignment due reminder: "
                        f"{assignment.title or assignment.course.name}"
                    ),
                    message=(
                        f"Reminder: {assignment.title or 'An assignment'} "
                        f"is due in {reminder_label}."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[teacher.email],
                    html_message=teacher_html,
                )
                notifications_sent += 1
            except Exception:
                logger.exception(
                    "Failed to queue due reminder email for teacher",
                    extra={
                        "assignment_id": str(assignment.id),
                        "teacher_id": str(teacher.id),
                        "hours_before": hours_before,
                    },
                )

        students = (
            CustomUser.objects.filter(
                user_type=UserTypes.STUDENT,
                enrollments__course=assignment.course,
                enrollments__enrollment_status=EnrollmentStatusType.ENROLLED,
                settings__notify_assignment_due_reminder=True,
            )
            .exclude(email__iendswith="@student.local")
            .exclude(submissions__assignment=assignment)
        )

        for student in students.distinct():
            try:
                student_html = render_to_string(
                    "email/assignment_due_reminder.html",
                    {
                        "recipient": student,
                        "assignment": assignment,
                        "course": assignment.course,
                        "due_date_display": due_date_display,
                        "reminder_label": reminder_label,
                        "is_teacher": False,
                    },
                )

                send_email_task.delay(
                    subject=(
                        f"Assignment due reminder: "
                        f"{assignment.title or assignment.course.name}"
                    ),
                    message=(
                        f"Reminder: {assignment.title or 'An assignment'} "
                        f"is due in {reminder_label}."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[student.email],
                    html_message=student_html,
                )
                notifications_sent += 1
            except Exception:
                logger.exception(
                    "Failed to queue due reminder email for student",
                    extra={
                        "assignment_id": str(assignment.id),
                        "student_id": str(student.id),
                        "hours_before": hours_before,
                    },
                )

        return f"Queued {notifications_sent} assignment due reminder emails."
    except Exception as e:
        import traceback

        return f"Error: {str(e)} {traceback.format_exc()}"


@shared_task(name="assignments.tasks.send_new_assignment_posted_notification")
def send_new_assignment_posted_notification(assignment_id):
    try:
        assignment = Assignment.objects.select_related(
            "course", "course__teacher", "topic"
        ).get(id=assignment_id)

        if assignment.status != AssignmentStatus.PUBLISHED:
            return "Assignment is not published."

        students = (
            CustomUser.objects.filter(
                user_type=UserTypes.STUDENT,
                enrollments__course=assignment.course,
                enrollments__enrollment_status=EnrollmentStatusType.ENROLLED,
                settings__notify_new_assignment_posted=True,
            )
            .exclude(email__isnull=True)
            .exclude(email="")
            .exclude(email__iendswith="@student.local")
            .distinct()
        )

        due_date_display = (
            timezone.localtime(assignment.due_date).strftime("%B %d, %Y at %I:%M %p")
            if assignment.due_date
            else "No due date set"
        )
        notifications_sent = 0

        for student in students:
            try:
                html_message = render_to_string(
                    "email/new_assignment_posted.html",
                    {
                        "student": student,
                        "assignment": assignment,
                        "course": assignment.course,
                        "teacher": assignment.course.teacher,
                        "due_date_display": due_date_display,
                    },
                )

                send_email_task.delay(
                    subject=(
                        f"New assignment posted: "
                        f"{assignment.title or assignment.course.name}"
                    ),
                    message=(
                        f"A new assignment, {assignment.title or 'Untitled Assignment'}, "
                        f"has been posted for {assignment.course.name}. "
                        f"Due date: {due_date_display}."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[student.email],
                    html_message=html_message,
                )
                notifications_sent += 1
            except Exception:
                logger.exception(
                    "Failed to queue new assignment notification for student",
                    extra={
                        "assignment_id": str(assignment.id),
                        "student_id": str(student.id),
                    },
                )

        return f"Queued {notifications_sent} new assignment notification email(s)."
    except Exception as e:
        import traceback

        return f"Error: {str(e)} {traceback.format_exc()}"


@shared_task(
    bind=True,
    name="assignments.tasks.prerender_assignment_pdfs",
    max_retries=5,
    default_retry_delay=60,
)
def prerender_assignment_pdfs(self, assignment_id):
    """
    Render and cache both PDF views of a freshly published assignment.

    Publishing is the moment a whole class opens the same assignment at
    once, and it is also the one moment the cache is guaranteed cold - a
    newly published assignment has never been rendered. Warming it here
    turns that burst into cache hits. Single-flight already cut a measured
    30-simultaneous-request burst from 30 renders to 1; doing the render
    before anyone asks cuts it to 0.

    Best effort by design: this only warms a cache, so nothing it does is
    required for a download to work. A failure is logged and dropped
    rather than retried forever, with one exception - if the renderer is
    shedding load, the work is genuinely worth deferring, so back off and
    try again later. Pre-rendering must never compete with real users for
    render capacity.
    """
    from assignments.pdf_cache import get_cached_pdf, get_or_render
    from assignments.pdf_document import render_assignment_pdf
    from assignments.pdf_renderer import PDFRendererBusy, PDFRendererUnavailable

    try:
        assignment = Assignment.objects.select_related("course__teacher").get(
            id=assignment_id
        )
    except Assignment.DoesNotExist:
        return "Assignment no longer exists."

    if assignment.status != AssignmentStatus.PUBLISHED:
        return "Assignment is not published."

    if not assignment.questions:
        return "Assignment has no questions to render."

    warmed = []
    for view_type, include_rubric in (("student", False), ("teacher", True)):
        if get_cached_pdf(assignment, view_type) is not None:
            continue
        try:
            get_or_render(
                assignment,
                view_type,
                lambda inc=include_rubric: render_assignment_pdf(assignment, inc),
            )
            warmed.append(view_type)
        except PDFRendererUnavailable as exc:
            # This process can never render (gevent-patched threading -
            # see pdf_renderer._gevent_patched). Retrying would just hit
            # the same wall forever, so stop, and stop for both views.
            logger.warning(
                "[PDF] pre-render unavailable in this process, skipping "
                "assignment %s: %s",
                assignment_id,
                exc,
            )
            return "Pre-render unavailable in this process (skipped)."
        except PDFRendererBusy as exc:
            logger.info(
                "[PDF] pre-render deferred for assignment %s (%s view): %s",
                assignment_id,
                view_type,
                exc,
            )
            raise self.retry(exc=exc) from Exception
        except Exception:
            # A broken document should not keep a Celery worker busy
            # retrying; the download path will surface the real error to
            # the teacher who can act on it.
            logger.exception(
                "[PDF] pre-render failed for assignment %s (%s view)",
                assignment_id,
                view_type,
            )

    return f"Pre-rendered: {', '.join(warmed) or 'nothing (already cached)'}"
