import logging

from celery import shared_task, states
from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from ai_processor.services import ai_processor

# from celery.exceptions import Ignore
from AutoGrader.tasks import send_email_task
from classrooms.models import Course, EnrollmentStatusType, Topic
from students.exceptions import (
    CannotAssociateStudentError,
    SubmissionGradingInProgressError,
    TaskCancelledError,
)
from students.models import BatchUploadSession, BatchUploadType, StudentSubmission
from students.serializers import StudentSubmissionSerializer
from students.services import (
    GRADING_TASK_TIME_LIMIT_SECONDS,
    grade_engine,
    upload_answers_engine,
)
from students.task_tracking import (
    cancellable_final_save,
    cleanup_cancelled_task_artifacts,
    ensure_task_not_cancelled,
    get_processing_task_by_id,
    lock_processing_task_for_final_save,
    mark_processing_task_cancelled,
    mark_processing_task_failure,
    mark_processing_task_started,
    mark_processing_task_success,
    merge_task_meta,
    update_processing_task,
)
from users.models import CustomUser, UserTypes

from .models import Assignment, AssignmentStatus
from .serializers import AssignmentSerializer
from .services import AssignmentProcessingService

# from django.db import transaction


logger = logging.getLogger(__name__)


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

    # assignment = Assignment.objects.get(id=assignment_id)

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

        #
        #
        # extraction_started_at = timezone.now()
        # assignment_questions = ai_processor.extract_assignment_with_retry(
        #     user, content, max_retries=3
        # )
        # extraction_completed_at = timezone.now()
        #
        # self.update_state(state="PROGRESS", meta={"step": "Saving assignment content"})
        #
        # assignment_questions["ai_generated"] = True
        # ai_raw_payload = {
        #     "title": (
        #         assignment.title if assignment.title else assignment_questions["title"]
        #     ),
        #     "instructions": assignment_questions["instructions"],
        #     "questions": assignment_questions["questions"],
        # }
        #
        # print("Saving assignment content")
        #
        # assignment_questions["ai_raw_payload"] = ai_raw_payload
        # assignment_questions["extraction_started_at"] = extraction_started_at
        # assignment_questions["extraction_completed_at"] = extraction_completed_at
        #
        # serializer = AssignmentSerializer(
        #     assignment, data=assignment_questions, partial=True
        # )
        # serializer.is_valid(raise_exception=True)
        # serializer.save()

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


@shared_task(bind=True)
def extract_answer_background_task(
    self, submission_id, content, processing_task_id=None
):
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Extracting answer content"}
        )
        self.update_state(state="PROGRESS", meta={"step": "Extracting answer content"})

        print("Extracting answer content")

        submission = StudentSubmission.objects.get(id=submission_id)

        extraction_started_at = timezone.now()
        ensure_task_not_cancelled(processing_task_id)
        answer_json = ai_processor.extract_answer_with_retry(
            submission.student,
            content,
            submission.assignment.questions,
            assignment_model=submission.assignment,
            max_retries=3,
            processing_task_id=processing_task_id,
        )
        extraction_completed_at = timezone.now()

        self.update_state(state="PROGRESS", meta={"step": "Saving answer content"})
        update_processing_task(
            processing_task_id, meta={"step": "Saving answer content"}
        )

        submission.answer = answer_json
        submission.extraction_started_at = extraction_started_at
        submission.extraction_completed_at = extraction_completed_at

        serializer = StudentSubmissionSerializer(
            submission, data=answer_json, partial=True
        )
        serializer.is_valid(raise_exception=True)
        with cancellable_final_save(processing_task_id):
            serializer.save()

        print("Answer saved successfully")
        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Answer extracted successfully",
                "submission_id": str(submission.id),
            },
        )

        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Answer extracted successfully",
        }
    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id, meta={"step": "Answer extraction cancelled"}
        )
        raise
    except Exception as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Answer extraction failed", "submission_id": submission_id},
            fallback_message=(
                "We couldn't extract the answers from this submission. The "
                "file may be corrupted or in an unsupported format — please "
                "try re-uploading, or contact support if this continues."
            ),
        )
        raise


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

        self.update_state(state="PROGRESS", meta={"step": "Grading"})
        update_processing_task(processing_task_id, meta={"step": "Grading"})
        ensure_task_not_cancelled(processing_task_id)
        # grade_engine performs the final (cancellation-guarded) save
        # itself; a second full save here would race formatted_grade_async's
        # write to the same row and clobber formatted_grade (H4).
        submission = grade_engine(
            user, submission, processing_task_id=processing_task_id
        )

        self.update_state(state="PROGRESS", meta={"step": "Completed"})
        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Completed",
                "submission_id": str(submission.id),
                "batch_id": str(batch_id) if batch_id else None,
            },
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
        # original - a clean skip, not a failure. Finish SUCCESS so Celery
        # doesn't retry it and the user isn't shown an error for a run that
        # is, in fact, happening.
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
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Grading failed", "submission_id": submission_id},
            fallback_message=(
                "We couldn't grade this submission. Please try again, or "
                "contact support if this continues."
            ),
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


@shared_task(bind=True, max_retries=3)
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
        content = AssignmentProcessingService.prepare_ai_content(uploaded_file, prompt)

        self.update_state(state="PROGRESS", meta={"step": "Extracting answers"})
        update_processing_task(processing_task_id, meta={"step": "Extracting answers"})
        ensure_task_not_cancelled(processing_task_id)
        submission = upload_answers_engine(
            assignment=assignment,
            content=content,
            request_user=user,
            is_proxy_upload=is_teacher,
            processing_task_id=processing_task_id,
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
    except CannotAssociateStudentError as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Student association failed", "assignment_id": assignment_id},
        )
        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(exc))
        return {
            "status": states.FAILURE,
            "message": "Cannot Identify or Associate Student with this Paper",
        }
    except Exception as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Answer extraction failed", "assignment_id": assignment_id},
            fallback_message=(
                "We couldn't process this submission upload. Please check "
                "the file and try again, or contact support if this "
                "continues."
            ),
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=3) from Exception

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(exc))
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
        # self.update_state(state="PROGRESS", meta={"step": "Loading context"})

        user = CustomUser.objects.get(id=user_id)
        course = Course.objects.get(id=course_id, teacher=user)
        topic = Topic.objects.get(id=topic_id) if topic_id else None

        update_processing_task(
            processing_task_id, meta={"step": "Preparing assignment content"}
        )
        ensure_task_not_cancelled(processing_task_id)
        uploaded_file = AssignmentProcessingService.rebuild_uploaded_file(file_payload)
        content = AssignmentProcessingService.prepare_ai_content(
            uploaded_file, prompt_text
        )

        update_processing_task(
            processing_task_id, meta={"step": "Extracting assignment"}
        )
        ensure_task_not_cancelled(processing_task_id)
        assignment_questions = AssignmentProcessingService.extract_assignment_data(
            user,
            content,
            course=course,
            topic=topic,
            generate_raw_input=True,
            upload=True,
            processing_task_id=processing_task_id,
        )

        # self.update_state(state="PROGRESS", meta={"step": "Saving assignments"})
        update_processing_task(processing_task_id, meta={"step": "Saving assignment"})
        with transaction.atomic():
            processing_task = lock_processing_task_for_final_save(processing_task_id)
            serializer = AssignmentSerializer(data=assignment_questions)
            serializer.is_valid(raise_exception=True)
            assignment = serializer.save()

            if processing_task:
                processing_task.assignment = assignment
                processing_task.meta = merge_task_meta(
                    processing_task.meta,
                    {"assignment_id": str(assignment.id)},
                )
                processing_task.save(update_fields=["assignment", "meta", "updated_at"])

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
                "step": "Assignment uploaded successfully",
                "assignment_id": str(assignment.id),
                "file_name": file_name,
            },
        )
        return {
            "status": states.SUCCESS,
            "assignment_id": str(assignment.id),
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
        # if self.request.retries == self.max_retries:
        #     raise self.retry(exc=e, countdown=3) from Exception

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(e))
        raise


@shared_task(bind=True, max_retries=3)
def grade_batch_async(
    self, user_id, assignment_id, batch_id=None, processing_task_id=None
):
    submissions = StudentSubmission.objects.filter(
        assignment_id=assignment_id, graded_at__isnull=True
    )

    # Clear assignment-level scheduling info and create BatchUploadSession if missing
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

    for submission in submissions:
        ensure_task_not_cancelled(processing_task_id)
        grade_engine_async.delay(
            user_id,
            str(submission.id),
            batch_id=batch_id,
        )
        print(f"Starting grading of Submission {submission.student.get_full_name}")


@shared_task(name="assignments.tasks.auto_grade_due_assignment")
def auto_grade_due_assignment(assignment_id):
    try:
        assignment = Assignment.objects.get(id=assignment_id)

        if not assignment.auto_grade_on_due_date:
            return "Auto grade disabled."

        ungraded_submissions = assignment.submissions.filter(graded_at__isnull=True)

        if not ungraded_submissions.exists():
            return "No ungraded submissions."

        session = BatchUploadSession.objects.create(
            teacher=assignment.course.teacher,
            course=assignment.course,
            task_type=BatchUploadType.GRADE,
            total_files=ungraded_submissions.count(),
        )

        for submission in ungraded_submissions:
            grade_engine_async.delay(
                str(assignment.course.teacher.id),
                str(submission.id),
                batch_id=str(session.id),
            )

        return f"Auto-grading started for {ungraded_submissions.count()} submissions."
    except Exception as e:
        import traceback

        return f"Error: {str(e)} {traceback.format_exc()}"


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
