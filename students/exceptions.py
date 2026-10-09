from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES, CodedError
from billing.errors import InsufficientCreditsError


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


class RubricMissingError(CodedError):
    """#7 RUBRIC_MISSING (409): the assignment has nothing to grade against.
    Refused before any claim, AI call or charge (08a F5; students.grading_gates)."""

    reason_code = ReasonCode.RUBRIC_MISSING
    status_code = REASON_CODES[ReasonCode.RUBRIC_MISSING].http_status


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


# H-180. The one sentence a STUDENT reads when their teacher's wallet cannot
# pay for the upload (at the door, and on the polled status of a task the gate
# refused). It names no balance, amount, credit or wallet.
STUDENT_UPLOAD_NOT_PROCESSED = (
    "Your answers were not submitted. Your teacher's account can't process "
    "uploads right now. Please keep your file and try again later, or let "
    "your teacher know."
)


class StudentUploadNotProcessedError(InsufficientCreditsError):
    """
    A student's upload was refused because their teacher's wallet cannot pay
    for it (H-180). Stands in, on a tracked task a STUDENT polls, for the
    InsufficientCreditsError whose own text carries the balance and the
    estimate. An InsufficientCreditsError like any other (it is logged and
    never retried as a refusal), except that AutoGrader.error_messages
    passes ITS message through instead of the generic credit sentence.
    """


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


class InsufficientCreditsMidBatchError(CodedError, InsufficientCreditsError):
    """INSUFFICIENT_CREDITS_MID_BATCH (FR-A-07 S7c, 08a §4.5): credits ran out
    after part of a batch had already run. Params: completed, total.

    Also an InsufficientCreditsError, so every credit-refusal path already
    treats it as one: never retried (PERMANENT_AI_REFUSALS), never charged,
    and refunded like any refusal. Its message is the catalogue's, never
    the wallet's own text (balance, estimate)."""

    reason_code = ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH


class CourseNotReachableError(Exception):
    """H-38 at run time: the teacher a grading run acts for can no longer
    reach the submission's course (removed from the school whose session it
    sits in). Raised before any provider call, so nothing is charged.
    Uncoded (SM ruling), and its message is a plain "not found": never the
    removal, the course or the school. User-facing."""

    def __init__(self, message="This course wasn't found."):
        super().__init__(message)
