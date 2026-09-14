class CannotAssociateStudentError(Exception):
    pass


class SubmissionAlreadyGradedError(Exception):
    """
    Product rule (owner, 2026-09-13): once a student's submission for an
    assignment has been successfully graded, that student may not submit
    again for that assignment. Raised by students.services on every
    server-side submission path (sync upload, async upload, edit
    re-extraction) so the rule holds regardless of what the frontend does.
    User-facing: AutoGrader.error_messages passes the message through.
    """

    pass


class SubmissionBeingGradedError(Exception):
    """
    Product decision (owner, 2026-09-14, H-13): while a grading run holds a
    live claim on a student's submission, no upload - the student's own or
    a teacher's proxy upload - may replace its answers. The alternative
    (accepting the upload) left a row whose answers were newer than the
    grade that then closed it. User-facing.
    """

    pass


class SubmissionLimitReachedError(ValueError):
    """
    A student has used all of their allowed submission attempts on an
    assignment (students.services.upload_answers_engine). Subclasses
    ValueError so existing `except ValueError` handlers keep working; it
    is a distinct type so AutoGrader.error_messages can show the student
    the real reason instead of the generic "we couldn't process your
    submission" fallback.
    """

    pass


class TaskCancelledError(Exception):
    pass


class SubmissionGradingInProgressError(Exception):
    """
    Raised when grade_engine can't acquire the grading claim on a
    submission because another worker or request already holds it (a
    Celery redelivery racing the still-running original, or a genuine
    double-click). Not a failure — see students.services.grade_engine and
    _claim_submission_for_grading.
    """

    pass
