# from datetime import datetime
from html import escape

# from django.utils import timezone


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
