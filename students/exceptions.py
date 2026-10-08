class CannotAssociateStudentError(Exception):
    pass


# H-133. A student is not told that a grade exists before the teacher
# releases it (founder's rule, 2026-10-06). The rules that close a paper to
# changes stay; these are the two sentences a STUDENT is given for them.
# Neither names grading. A teacher is still told the reason in words.
#   * closed for good (the paper is graded, released or not);
#   * closed for now (it is being graded, or an earlier upload of it is
#     still being processed: a student cannot tell the two apart).
SUBMISSION_CLOSED_FOR_STUDENT = "This submission can no longer be changed."
SUBMISSION_BUSY_FOR_STUDENT = (
    "This submission can't be changed right now. Please try again later."
)
# Each of the four refusals also carries a stable `code` (below), sent
# beside the sentence, so a client need not match on words. The names say
# what the caller can do, never why.


class SubmissionAlreadyGradedError(Exception):
    """
    Product rule (owner, 2026-09-13): once a student's submission for an
    assignment has been successfully graded, that student may not submit
    again for that assignment. Raised by students.services on every
    server-side submission path (sync upload, async upload, edit
    re-extraction) so the rule holds regardless of what the frontend does.
    User-facing: AutoGrader.error_messages passes the message through.
    """

    code = "submission_closed"


class SubmissionBeingGradedError(Exception):
    """
    Product decision (owner, 2026-09-14, H-13): while a grading run holds a
    live claim on a student's submission, no upload - the student's own or
    a teacher's proxy upload - may replace its answers. The alternative
    (accepting the upload) left a row whose answers were newer than the
    grade that then closed it. User-facing.
    """

    code = "submission_busy"


class SubmissionLimitReachedError(ValueError):
    """
    A student has used all of their allowed submission attempts on an
    assignment (students.services.upload_answers_engine). Subclasses
    ValueError so existing `except ValueError` handlers keep working; it
    is a distinct type so AutoGrader.error_messages can show the student
    the real reason instead of the generic "we couldn't process your
    submission" fallback.
    """

    code = "submission_attempts_used"


class TaskCancelledError(Exception):
    pass


class SubmissionAnswersUnreadableError(Exception):
    """
    The submission's stored answers hold nothing the system can read (H-165:
    a value that is not a list of objects), so it is not sent for grading:
    no paid call, no charge. Raised by students.services.grade_engine,
    which puts the paper in the teacher's review queue first. The way
    round is readable answers: a new upload, or the edit by text.
    User-facing: AutoGrader.error_messages passes the message through.
    """

    code = "submission_answers_unreadable"


class SubmissionGradingInProgressError(Exception):
    """
    Raised when grade_engine can't acquire the grading claim on a
    submission because another worker or request already holds it (a
    Celery redelivery racing the still-running original, or a genuine
    double-click). Not a failure — see students.services.grade_engine and
    _claim_submission_for_grading.
    """

    pass


class AssignmentNotOpenError(Exception):
    """The assignment is not PUBLISHED, so it does not accept submissions
    or submission edits. User-facing."""

    pass


class SubmissionProcessingInProgressError(Exception):
    """
    A tracked answer-extraction task for this submission (or for this
    student on this assignment) is still PENDING/STARTED. A second request
    while it runs - typically a client retrying after a proxy timeout -
    must not queue a second billed extraction. User-facing.
    """

    code = "submission_busy"
