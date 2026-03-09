from celery import shared_task, states

# from celery.exceptions import Ignore
from django.utils import timezone

from ai_processor.services import ai_processor
from students.models import StudentSubmission
from users.models import CustomUser

from .models import Assignment
from .serializers import AssignmentSerializer


@shared_task(bind=True)
def grade_all_submissions(self, user_id, assignment_id):

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

    print("Assignment", assignment)

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
            answer_json = submission.get_answer()

            print("About to start grading")

            grading_result = ai_processor.extract_grade_with_retry(
                user, assignment.questions, answer_json
            )

            user_prompt = f"""
            Student Name: {submission.student.get_full_name()}
            Course: {assignment.course}


            Grading Result:

            {grading_result}

            Return a formatted response
            """

            formatted_grade = ai_processor.formatted_grade(user, user_prompt)

            grading_score = grading_result["grading_summary"]["total_score"]
            grading_confidence = grading_result["grading_confidence"]

            print(f"grading_score: {grading_score}")

            submission.score = grading_score
            submission.feedback = grading_result
            submission.grading_confidence = grading_confidence
            submission.formatted_grade = formatted_grade

            print(f"grading_confidence: {grading_confidence}")

            submission.ai_score = grading_score
            submission.ai_graded_at = timezone.now()

            submission.save()

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
def extract_assignment_background_task(self, user, assignment_id, content):
    try:
        self.update_state(
            state="PROGRESS", meta={"step": "Extracting assignment content"}
        )

        print("Extracting assignment content")

        assignment = Assignment.objects.get(id=assignment_id)

        extraction_started_at = timezone.now()
        assignment_questions = ai_processor.extract_assignment_with_retry(
            user, content, max_retries=3
        )
        extraction_completed_at = timezone.now()

        self.update_state(state="PROGRESS", meta={"step": "Saving assignment content"})

        assignment_questions["ai_generated"] = True
        ai_raw_payload = {
            "title": (
                assignment.title if assignment.title else assignment_questions["title"]
            ),
            "instructions": assignment_questions["instructions"],
            "questions": assignment_questions["questions"],
        }

        print("Saving assignment content")

        assignment_questions["ai_raw_payload"] = ai_raw_payload
        assignment_questions["extraction_started_at"] = extraction_started_at
        assignment_questions["extraction_completed_at"] = extraction_completed_at

        serializer = AssignmentSerializer(
            assignment, data=assignment_questions, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

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
            content, submission.assignment.questions, max_retries=3
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
def grade_engine_async(self, submission_id):
    try:
        self.update_state(state="PROGRESS", meta={"step": "Retrieving submission"})
        submission = StudentSubmission.objects.select_related("assignment").get(
            id=submission_id
        )

        self.update_state(state="PROGRESS", meta={"step": "Grading"})
        submission = grade_engine(submission)

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
        formatted_grade = ai_processor.formatted_grade(prompt)

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


@shared_task(bind=True)
def upload_answers_engine_async(self, assignment_id, content, user_id):
    try:
        self.update_state(state="PROGRESS", meta={"step": "Retrieving requirements"})

        assignment = Assignment.objects.get(id=assignment_id)
        user = CustomUser.objects.get(id=user_id)

        self.update_state(state="PROGRESS", meta={"step": "Extracting answers"})
        submission = upload_answers_engine(assignment, content, user)

        return {
            "status": states.SUCCESS,
            "submission_id": submission.id,
            "message": "Answers extracted successfully",
        }
    except Exception:
        raise


@shared_task()
def formatted_grade_async(submission_id, user_prompt):
    try:
        submission = StudentSubmission.objects.get(id=submission_id)
        formatted_grade = ai_processor.formatted_grade(user_prompt)
        submission.formatted_grade = formatted_grade
        submission.save(update_fields=["formatted_grade"])

        return {
            "status": states.SUCCESS,
            "submission_id": submission_id,
            "message": "Grade formatted successfully",
        }
    except Exception:
        raise
