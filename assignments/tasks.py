from celery import shared_task
from django.utils import timezone

from ai_processor.services import ai_processor
from assignments.models import Assignment
from students.models import StudentSubmission


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
                state="FAILURE", meta={"error": str(e), "detail": stack_trace_str}
            )
            raise

    # assignment.grading_status = "COMPLETED"
    # assignment.save()

    return {"status": "Completed", "assignment_id": assignment_id}


# @shared_task(bind=True)
# def extract_assignment(self, content):
#     assignment_questions = ai_processor.extract_assignment(content)

#     return assignment_questions
