"""
H-180: the door of the three upload-async routes.

A student's answer file, a teacher's batch of answer files and a teacher's
assignment files are queued as billed tasks. The permission class refuses a
wallet at 0 or below; the billing gate in AIProcessor.execute_graded_task,
which runs later inside the task, refuses a balance below the call's ESTIMATE
(the file's tokens plus a flat 20,000) as a final refusal. A student whose
teacher's wallet sat between the two was told "Answer Extraction Started" and
lost their file.

This door asks the same question earlier. For each file it builds the content
the task will build (AssignmentProcessingService.prepare_ai_content), asks the
SAME method the gate asks (AIProcessor.estimate_messages_cost) and compares
the answer with the balance the gate reads (the wallet's
total_remaining_credits). Its number is a LOWER bound of the task's (the task
adds its system prompt, the assignment's questions and the roster), so the
door only turns away an upload the task would certainly refuse; the gate in
the task stays the authority.

Said plainly, as limits:
  * the window between the door and the task stays: another job can spend the
    balance after the door and before the gate (Epic B's hold closes it);
  * a wallet between the door's number and the task's number still loses the
    upload, as before;
  * a file the door cannot read, a missing wallet and a super admin are left
    to the task and the permission class: "cannot estimate" is not "certainly
    refused";
  * the file is converted in the request (an image compressed, a PDF turned
    into one image per page), which the batch routes used to leave to the
    task; the cost is measured in the evidence.
"""

import logging

from rest_framework import status
from rest_framework.response import Response

from ai_processor.services import ai_processor
from assignments.services import AssignmentProcessingService
from AutoGrader.uploads import validate_upload_size
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE, InsufficientCreditsError
from billing.refusals import log_refusal
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED
from users.models import UserTypes

logger = logging.getLogger(__name__)


def _billed_user(request_user, assignment):
    """Whose wallet the task will be billed to, as the gate resolves it: the
    course's teacher for a student, the caller otherwise. None when it cannot
    be said."""
    if request_user.user_type == UserTypes.STUDENT:
        course = getattr(assignment, "course", None)
        return getattr(course, "teacher", None)
    return request_user


def _estimate_for(uploaded_file, prompt):
    """The shared method's number for the file, or None when the file cannot
    be read here (the task turns that into its own final refusal)."""
    try:
        validate_upload_size(uploaded_file)
        content = AssignmentProcessingService.prepare_ai_content(uploaded_file, prompt)
        return ai_processor.estimate_messages_cost(
            None, None, [{"role": "user", "content": content}]
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "cannot say"
        logger.warning(
            "Upload door could not estimate %s: %s",
            uploaded_file.name,
            type(exc).__name__,
        )
        return None
    finally:
        # The caller reads the same file again to build the task's payload.
        uploaded_file.seek(0)


def upload_refusal_if_unaffordable(request_user, assignment, uploaded_files, prompt):
    """None when every file may be queued; otherwise the 402 Response, with
    the sentence the caller may read: the fixed one for a student, the usual
    generic credit message for a teacher. Nothing is queued by this function."""
    if request_user.user_type == UserTypes.SUPER_ADMIN:
        return None
    billed = _billed_user(request_user, assignment)
    wallet = getattr(billed, "credit_wallet", None)
    if wallet is None:
        return None
    balance = wallet.total_remaining_credits()
    for uploaded_file in uploaded_files:
        estimate = _estimate_for(uploaded_file, prompt)
        if estimate is None or balance >= estimate:
            continue
        log_refusal(
            logger,
            "Upload door",
            InsufficientCreditsError(
                f"Upload needs ~{estimate} credits, balance {balance}"
            ),
        )
        if request_user.user_type == UserTypes.STUDENT:
            message = STUDENT_UPLOAD_NOT_PROCESSED
        else:
            message = INSUFFICIENT_CREDITS_MESSAGE
        return Response(
            {"error": message, "code": "insufficient_credits"},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )
    return None
