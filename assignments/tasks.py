from celery import shared_task, states
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from ai_processor.services import ai_processor

# from celery.exceptions import Ignore
from AutoGrader.tasks import send_email_task
from classrooms.models import Course, EnrollmentStatusType, Topic
from students.exceptions import CannotAssociateStudentError
from students.models import BatchUploadSession, BatchUploadType, StudentSubmission
from students.serializers import StudentSubmissionSerializer
from students.services import grade_engine, upload_answers_engine
from users.models import CustomUser, UserTypes

from .models import Assignment, AssignmentStatus
from .serializers import AssignmentSerializer
from .services import AssignmentProcessingService

# from django.db import transaction


@shared_task(bind=True)
def grade_all_submissions(self, user_id, assignment_id):
    """love God"""
    submissions = StudentSubmission.objects.filter(assignment_id=assignment_id)
    submissions_count = submissions.count()

    user = CustomUser.objects.get(id=user_id)

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
        self.update_state(
            state="PROGRESS",
            meta={
                "current": index,
                "total": submissions_count,
                "percent": (index) / submissions_count * 100,
                "step": "Grading",
            },
        )
        try:
            submission = grade_engine(user, submission)
            print(f"Assignment saved: {index + 1}/{submissions_count}")
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
            raise

    return {"status": "Completed", "assignment_id": assignment_id}


@shared_task(bind=True)
def extract_assignment_background_task(
    self, user_id, assignment_id, content, raw_input=None, keep_existing_title=True
):
    print(
        {
            "user_id": user_id,
            "assignment_id": assignment_id,
            "keep_existing_title": keep_existing_title,
        }
    )
    try:
        self.update_state(
            state="PROGRESS", meta={"step": "Extracting assignment content"}
        )

        print("Extracting assignment content")

        assignment = Assignment.objects.get(id=assignment_id)
        user = CustomUser.objects.get(id=user_id)

        assignment = AssignmentProcessingService.update_assignment_from_extraction(
            user,
            assignment,
            content,
            raw_input=raw_input,
            keep_existing_title=keep_existing_title,
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

        return {
            "status": states.SUCCESS,
            "assignment_id": assignment_id,
            "message": "Assignment extracted successfully",
        }
    except Exception:
        raise


@shared_task(bind=True)
def update_assignment_background_task(
    self,
    user_id,
    assignment_id,
    content,
    raw_input=None,
    topic_id=None,
):
    """
    Async re-extraction task triggered when a teacher updates an assignment
    with new raw_input content. Non-AI fields (title, status, due_date, etc.)
    are saved synchronously in the view before this task fires.
    """
    try:
        self.update_state(
            state="PROGRESS", meta={"step": "Extracting updated assignment content"}
        )

        assignment = Assignment.objects.get(id=assignment_id)
        user = CustomUser.objects.get(id=user_id)

        topic = None
        if topic_id:
            from classrooms.models import Topic as TopicModel

            topic = TopicModel.objects.filter(id=topic_id).first()

        assignment = AssignmentProcessingService.update_assignment_from_extraction(
            user,
            assignment,
            content,
            topic=topic,
            raw_input=raw_input,
        )

        return {
            "status": states.SUCCESS,
            "assignment_id": assignment_id,
            "message": "Assignment updated and re-extracted successfully",
        }
    except Exception:
        raise


@shared_task(bind=True)
def extract_answer_background_task(self, submission_id, content):
    try:
        self.update_state(state="PROGRESS", meta={"step": "Extracting answer content"})

        print("Extracting answer content")

        submission = StudentSubmission.objects.get(id=submission_id)

        extraction_started_at = timezone.now()
        answer_json = ai_processor.extract_answer_with_retry(
            submission.student,
            content,
            submission.assignment.questions,
            assignment_model=submission.assignment,
            max_retries=3,
        )
        extraction_completed_at = timezone.now()

        self.update_state(state="PROGRESS", meta={"step": "Saving answer content"})

        submission.answer = answer_json
        submission.extraction_started_at = extraction_started_at
        submission.extraction_completed_at = extraction_completed_at

        serializer = StudentSubmissionSerializer(
            submission, data=answer_json, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        print("Answer saved successfully")

        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Answer extracted successfully",
        }
    except Exception:
        raise


@shared_task(bind=True)
def grade_engine_async(self, user_id, submission_id, batch_id=None):
    try:
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
        submission = grade_engine(user, submission)

        self.update_state(state="PROGRESS", meta={"step": "Saving"})
        submission.save()

        self.update_state(state="PROGRESS", meta={"step": "Completed"})

        if batch_id:
            session = BatchUploadSession.objects.get(id=batch_id)
            session.update_result(
                f"{user.get_full_name()} Submission",
                "SUCCESS",
                batch_type=BatchUploadType.GRADE,
                submission_id=submission.id,
            )
        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Grading completed successfully",
        }
    except Exception:
        raise


@shared_task(bind=True)
def format_grade(self, submission_id, prompt):
    try:
        self.update_state(state="PROGRESS", meta={"step": "Retrieving submission"})

        submission = StudentSubmission.objects.get(id=submission_id)

        self.update_state(state="PROGRESS", meta={"step": "Formatting grade"})
        formatted_grade = ai_processor.formatted_grade(
            submission.student, prompt, assignment_model=submission.assignment
        )

        self.update_state(state="PROGRESS", meta={"step": "Saving formatted grade"})
        submission.formatted_grade = formatted_grade
        submission.save()

        self.update_state(
            state="PROGRESS", meta={"step": "Grade formatted successfully"}
        )
        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Grade formatted successfully",
        }
    except Exception:
        raise


@shared_task(bind=True, max_retries=3)
def upload_answers_engine_async(
    self, assignment_id, content, user_id, session_id=None, file_name=None
):
    try:
        self.update_state(state="PROGRESS", meta={"step": "Retrieving requirements"})

        assignment = Assignment.objects.get(id=assignment_id)
        user = CustomUser.objects.get(id=user_id)

        is_teacher = user.user_type == UserTypes.TEACHER

        self.update_state(state="PROGRESS", meta={"step": "Extracting answers"})
        submission = upload_answers_engine(
            assignment=assignment,
            content=content,
            request_user=user,
            is_proxy_upload=is_teacher,
        )

        if session_id:
            session = BatchUploadSession.objects.get(id=session_id)
            session.update_result(
                file_name,
                "SUCCESS",
                batch_type=BatchUploadType.SUBMISSION,
                submission_id=submission.id,
            )

        return {
            "status": states.SUCCESS,
            "submission_id": str(submission.id),
            "message": "Answers extracted successfully",
        }

    except CannotAssociateStudentError as exc:
        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(exc))
        return {
            "status": states.FAILURE,
            "message": "Cannot Identify or Associate Student with this Paper",
        }
    except Exception as exc:
        if self.request.retries == self.max_retries:
            raise self.retry(exc=exc, countdown=3) from Exception

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(exc))
        raise exc


@shared_task()
def formatted_grade_async(submission_id, user_prompt):
    try:
        submission = StudentSubmission.objects.get(id=submission_id)
        formatted_grade = ai_processor.formatted_grade(
            submission.student, user_prompt, assignment_model=submission.assignment
        )
        submission.formatted_grade = formatted_grade
        submission.save(update_fields=["formatted_grade"])

        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Grade formatted successfully",
        }
    except Exception:
        raise


@shared_task(bind=True, max_retries=3, soft_time_limit=1800, time_limit=2100)
def upload_assignment_async(
    self,
    *,
    user_id,
    course_id,
    topic_id=None,
    session_id=None,
    content=None,
    file_name=None,
):
    try:
        # self.update_state(state="PROGRESS", meta={"step": "Loading context"})

        user = CustomUser.objects.get(id=user_id)
        course = Course.objects.get(id=course_id, teacher=user)
        topic = Topic.objects.get(id=topic_id) if topic_id else None

        assignment_questions = AssignmentProcessingService.extract_assignment_data(
            user,
            content,
            course=course,
            topic=topic,
            generate_raw_input=True,
            upload=True,
        )

        # self.update_state(state="PROGRESS", meta={"step": "Saving assignments"})
        serializer = AssignmentSerializer(data=assignment_questions)
        serializer.is_valid(raise_exception=True)
        assignment = serializer.save()

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(
            file_name,
            "SUCCESS",
            batch_type=BatchUploadType.ASSIGNMENT,
            assignment_id=assignment.id,
        )

        return {
            "status": states.SUCCESS,
            "assignment_id": str(assignment.id),
            "message": "Assignment uploaded successfully",
        }

    except Exception as e:
        # if self.request.retries == self.max_retries:
        #     raise self.retry(exc=e, countdown=3) from Exception

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(e))
        raise


@shared_task(bind=True, max_retries=3)
def grade_batch_async(self, user_id, assignment_id, batch_id=None):
    submissions = StudentSubmission.objects.filter(assignment_id=assignment_id)

    # Clear assignment-level scheduling info
    try:
        assignment = Assignment.objects.get(id=assignment_id)
        if assignment.scheduled_grading_at or assignment.grading_task_name:
            assignment.scheduled_grading_at = None
            assignment.grading_task_name = None
            assignment.save(update_fields=["scheduled_grading_at", "grading_task_name"])
    except Assignment.DoesNotExist:
        pass

    for submission in submissions:
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

        reminder_label = "24 hours" if hours_before == 24 else "1 hour"
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

        students = CustomUser.objects.filter(
            user_type=UserTypes.STUDENT,
            enrollments__course=assignment.course,
            enrollments__enrollment_status=EnrollmentStatusType.ENROLLED,
            settings__notify_assignment_due_reminder=True,
        ).exclude(email__iendswith="@student.local")

        for student in students.distinct():
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

        return f"Queued {notifications_sent} assignment due reminder emails."
    except Exception as e:
        import traceback

        return f"Error: {str(e)} {traceback.format_exc()}"
