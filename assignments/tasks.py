from celery import shared_task, states
from django.db import transaction

# from celery.exceptions import Ignore
from django.utils import timezone

from ai_processor.services import ai_processor
from classrooms.models import Course, Topic
from students.exceptions import CannotAssociateStudentError
from students.models import BatchUploadSession, StudentSubmission
from students.serializers import StudentSubmissionSerializer
from students.services import grade_engine, upload_answers_engine
from users.models import CustomUser, UserTypes

from .models import Assignment
from .serializers import AssignmentSerializer
from .services import AssignmentProcessingService


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
def grade_engine_async(self, user_id, submission_id):
    try:
        self.update_state(state="PROGRESS", meta={"step": "Retrieving submission"})
        submission = StudentSubmission.objects.select_related("assignment").get(
            id=submission_id
        )

        user = CustomUser.objects.get(id=user_id)

        self.update_state(state="PROGRESS", meta={"step": "Grading"})
        submission = grade_engine(user, submission)

        self.update_state(state="PROGRESS", meta={"step": "Saving"})
        submission.save()

        self.update_state(state="PROGRESS", meta={"step": "Completed"})
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
    self, assignment_id, content, user_id, session_id, file_name
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

        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "SUCCESS", submission_id=submission.id)

        return {
            "status": states.SUCCESS,
            "submission_id": submission.id,
            "message": "Answers extracted successfully",
        }
    except CannotAssociateStudentError as exc:
        session = BatchUploadSession.objects.get(id=session_id)
        session.update_result(file_name, "FAILED", error=str(exc))
        return {
            "status": states.FAILURE,
            "submission_id": submission.id,
            "message": "Cannot associate student",
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


@shared_task(bind=True)
def upload_assignment_async(self, *, user_id, course_id, topic_id=None, files_payload):
    try:
        self.update_state(state="PROGRESS", meta={"step": "Loading context"})

        user = CustomUser.objects.get(id=user_id)
        course = Course.objects.get(id=course_id, teacher=user)
        topic = Topic.objects.get(id=topic_id) if topic_id else None

        prompt_text = """
        Analyze the image of an educational assignment and return a JSON

        IMPORTANT: Return only valid JSON matching the required structure.
        Do not include any explanatory text before or after the JSON
        """

        result = []
        total_files = len(files_payload)

        for index, file_payload in enumerate(files_payload, start=1):
            self.update_state(
                state="PROGRESS",
                meta={
                    "step": "Extracting assignment",
                    "current": index,
                    "total": total_files,
                    "percent": (
                        int((index - 1) / total_files * 100) if total_files else 0
                    ),
                    "file_name": file_payload.get("name"),
                },
            )

            uploaded_file = AssignmentProcessingService.rebuild_uploaded_file(
                file_payload
            )
            content = AssignmentProcessingService.prepare_ai_content(
                uploaded_file, prompt_text
            )
            assignment_questions = AssignmentProcessingService.extract_assignment_data(
                user,
                content,
                course=course,
                topic=topic,
                generate_raw_input=True,
                upload=True,
            )
            result.append(assignment_questions)

        self.update_state(state="PROGRESS", meta={"step": "Saving assignments"})

        with transaction.atomic():
            serializer = AssignmentSerializer(data=result, many=True)
            serializer.is_valid(raise_exception=True)
            assignments = serializer.save()

        assignment_ids = [str(assignment.id) for assignment in assignments]

        self.update_state(state="PROGRESS", meta={"step": "Completed", "percent": 100})

        return {
            "status": states.SUCCESS,
            "message": "Assignments upload extraction successfully",
            "assignment_ids": assignment_ids,
            "created_count": len(assignment_ids),
        }
    except Exception as e:
        self.update_state(
            state=states.FAILURE,
            meta={
                "message": "Assignment upload extraction failed",
                "error": str(e),
            },
        )

        raise
