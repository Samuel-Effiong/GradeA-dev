from html import escape

from django.utils import timezone

from ai_processor.services import ai_processor
from assignments.services import AssignmentProcessingService
from users.models import CustomUser

from .exceptions import CannotAssociateStudentError
from .models import StudentSubmission

# from .serializers import StudentSubmissionSerializer


def student_submission_to_html(submission) -> str:
    """
    Converts student submission JSON into a globally standard HTML format
    suitable for rich-text editors (ProseMirror, TinyMCE, Quill, CKEditor, etc).
    """

    def safe(val):
        return escape(str(val)) if val else ""

    student_name = submission.student.get_full_name()
    student_id = submission.student_id

    meta_html = f"""
    <section>

        {submission.assignment.title}
        <p><strong>Due Date:</strong> {submission.assignment.due_date}</p>


        <h3>Student Information</h3>
        <p><strong>Name:</strong> {safe(student_name)}</p>
        <p><strong>Student ID:</strong> {safe(student_id)}</p>

        <h3>Submission Metadata</h3>
        <p><strong>Submitted At:</strong> {safe(submission.submission_date.strftime("%Y-%m-%d"))}</p>
        <p><strong>Graded At:</strong>
        {safe(submission.graded_at.strftime("%Y-%m-%d")) if submission.graded_at else "Not graded yet"}</p>
        <p><strong>Score:</strong>
        {safe(submission.score) if submission.score is not None else "Not graded yet"}</p>
    </section>
    <hr/>
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


def grade_engine(user, submission):
    from assignments.tasks import formatted_grade_async

    answer_json = submission.get_answer()
    submission.ai_graded_at = timezone.now()

    grading = ai_processor.extract_grade_with_retry(
        user,
        submission.assignment.questions,
        answer_json,
        assignment_model=submission.assignment,
    )

    submission.ai_grading_completed_at = timezone.now()

    user_prompt = f"""
    Student Name: {submission.student.get_full_name()}
    Course: {submission.assignment.course}


    Grading Result:

    {grading}

    Return a formatted response
    """

    formatted_grade_async.delay(str(submission.id), user_prompt)

    grading_score = grading["grading_summary"]["total_score"]
    grading_confidence = grading["grading_confidence"]

    print(f"grading_score: {grading_score}")

    submission.score = grading_score
    submission.feedback = grading
    submission.grading_confidence = grading_confidence
    submission.graded_at = timezone.now()

    submission.ai_score = grading_score

    submission.save()

    return submission


def upload_answers_engine(assignment, content, request_user, is_proxy_upload=False):
    assignment_context = f"""
    This is the Assignment Context to use in properly extracting the student submissions
    {assignment.questions}
    """

    student_submission = ai_processor.extract_answer_with_retry(
        request_user,
        content,
        assignment_context,
        assignment_model=assignment,
        max_retries=3,
    )

    if student_submission is not None:
        target_student = request_user

        if is_proxy_upload:
            identified_name = student_submission.get("student_name")

            if not identified_name:

                raise CannotAssociateStudentError(
                    "Could not identify or associate a student with this paper"
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
                raise ValueError(
                    "Could not identify or associate a student with this paper"
                )

        # Handle duplicates
        submission, created = StudentSubmission.objects.get_or_create(
            assignment=assignment,
            student=target_student,
            defaults={"answers": student_submission.get("answers")},
        )

        if not created:
            # If it already exists, update the answers
            submission.answers = student_submission.get("answers", submission.answers)
            submission.save()

        # student_submission.update(
        #     {
        #         "assignment": assignment.id,
        #         "student": target_student.id,
        #     }
        # )
        #
        # serializer = StudentSubmissionSerializer(data=student_submission)
        # serializer.is_valid(raise_exception=True)
        # submission = serializer.save()

        answer_html = student_submission_to_html(submission)
        submission.raw_input = AssignmentProcessingService.html_to_prosemirror_json(
            answer_html
        )
        submission.save()

    return submission
