from celery import shared_task
from django.utils import timezone

from ai_processor.services import ai_processor
from assignments.models import Assignment
from students.models import StudentSubmission


@shared_task(bind=True)
def grade_all_submissions(self, assignment_id):
    submissions = StudentSubmission.objects.filter(assignment_id=assignment_id)

    assignment = Assignment.objects.get(id=assignment_id)

    for submission in submissions:
        try:
            answer_json = submission.get_answer()

            grading_result = ai_processor.extract_grade_with_retry(
                assignment.questions, answer_json
            )

            # print(grading_result)

            grading_score = grading_result["grading_summary"]["total_score"]
            grading_confidence = grading_result["grader_meta_analysis"][
                "grading_confidence"
            ]

            print(grading_score)

            submission.score = grading_score
            submission.feedback = grading_result
            submission.grading_confidence = grading_confidence

            print(grading_confidence)

            submission.ai_score = grading_score
            submission.ai_graded_at = timezone.now()

            submission.save()
            print("Assignment saved")
        except Exception:
            continue

    assignment.grading_status = "COMPLETED"
    assignment.save()

    return "Assignment Graded"
