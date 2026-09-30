from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES, CodedError


class CannotAssociateStudentError(Exception):
    """A teacher's (proxy) upload could not be attributed to a student.

    FR-A-06 (S6c): raised as one of the two coded subclasses below, each with
    its own reason code. Subclassing keeps them upload refusals
    (assignments.tasks.UPLOAD_REFUSALS, never retried) and user-facing.
    """


class StudentNameUnmatchedError(CodedError, CannotAssociateStudentError):
    """#1 MISSING_STUDENT_NAME (422): no name on the paper, a name that
    matches nobody (the paper's own text is quoted, never stored data), or
    one that matches more than one enrolled student."""

    reason_code = ReasonCode.MISSING_STUDENT_NAME
    status_code = REASON_CODES[ReasonCode.MISSING_STUDENT_NAME].http_status


class StudentNotOnRosterError(CodedError, CannotAssociateStudentError):
    """#2 STUDENT_NOT_ON_ROSTER (422): the name is one of the uploading
    teacher's OWN students who isn't enrolled in this course (pending,
    withdrawn, or in another of their courses). Never another teacher's."""

    reason_code = ReasonCode.STUDENT_NOT_ON_ROSTER
    status_code = REASON_CODES[ReasonCode.STUDENT_NOT_ON_ROSTER].http_status


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

    pass


class CourseNotReachableError(Exception):
    """H-38 at run time: the teacher a grading run acts for can no longer
    reach the submission's course (removed from the school whose session it
    sits in). Raised before any provider call, so nothing is charged.
    Uncoded (SM ruling), and its message is a plain "not found": never the
    removal, the course or the school. User-facing."""

    def __init__(self, message="This course wasn't found."):
        super().__init__(message)
