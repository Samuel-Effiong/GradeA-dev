class CannotAssociateStudentError(Exception):
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
