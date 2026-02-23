from celery import shared_task, states

# from celery.exceptions import Ignore
from django.utils import timezone

from ai_processor.services import ai_processor
from students.models import StudentSubmission

from .models import Assignment
from .serializers import AssignmentSerializer


@shared_task(bind=True)
def grade_all_submissions(self, assignment_id):

    submissions = StudentSubmission.objects.filter(assignment_id=assignment_id)
    submissions_count = submissions.count()

    self.update_state(
        state="PROGRESS",
        meta={
            "current": 0,
            "total": submissions_count,
            "percent": 0,
            "step": "Initializing",
        },
    )

    assignment = Assignment.objects.get(id=assignment_id)

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
                assignment.questions, answer_json
            )

            user_prompt = f"""
            Student Name: {submission.student.get_full_name()}
            Course: {assignment.course}


            Grading Result:

            {grading_result}

            Return a formatted response
            """

            formatted_grade = ai_processor.formatted_grade(user_prompt)

            grading_score = grading_result["grading_summary"]["total_score"]
            grading_confidence = grading_result["grading_confidence"]

            print(f"grading_score: {grading_score}")

            submission.score = grading_score
            submission.feedback = grading_result
            submission.grading_confidence = grading_confidence
            submission.formatted_grade = formatted_grade
            submission.graded_at = timezone.now()

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

    # assignment.grading_status = "COMPLETED"
    # assignment.save()

    return {"status": "Completed", "assignment_id": assignment_id}


@shared_task(bind=True)
def extract_assignment_background_task(self, assignment_id, content):
    try:
        self.update_state(
            state="PROGRESS", meta={"step": "Extracting assignment content"}
        )

        print("Extracting assignment content")

        assignment = Assignment.objects.get(id=assignment_id)

        extraction_started_at = timezone.now()
        assignment_questions = ai_processor.extract_assignment_with_retry(
            content, max_retries=3
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
