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
import uuid
from functools import partial

from django.core.files.uploadedfile import UploadedFile
from django.db import connection, transaction
from rest_framework import status
from rest_framework.response import Response

from ai_processor.services import ai_processor
from assignments.services import AssignmentProcessingService
from AutoGrader.reason_codes import credit_refusal_message
from AutoGrader.safe_logging import describe_error_for_log
from AutoGrader.uploads import validate_upload_size
from billing import holds as credit_holds
from billing.errors import InsufficientCreditsError, is_individual_teacher
from billing.models import HoldState
from billing.refusals import log_refusal
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED
from students.models import BackgroundProcessingTask, BackgroundTaskStatus
from students.task_tracking import attach_celery_task, mark_processing_task_failure
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


def _balance(wallet):
    """What the door compares with: spendable credit (total less what other
    jobs hold) when credit holds are on (plan 08 §3.2), the total otherwise."""
    if credit_holds.holds_enabled():
        return credit_holds.spendable_credits(wallet)
    return wallet.total_remaining_credits()


def _refusal_response(request_user, refusal):
    if request_user.user_type == UserTypes.STUDENT:
        message = STUDENT_UPLOAD_NOT_PROCESSED
    else:
        message = credit_refusal_message(refusal)
    return Response(
        {"error": message, "code": "insufficient_credits"},
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


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


def upload_refusal_if_unaffordable(
    request_user, assignment, uploaded_files, prompt, estimates_out=None
):
    """None when every file may be queued; otherwise the 402 Response, with
    the sentence the caller may read: the fixed one for a student, the usual
    generic credit message for a teacher. Nothing is queued by this function."""
    if request_user.is_real_super_admin():
        return None
    billed = _billed_user(request_user, assignment)
    wallet = getattr(billed, "credit_wallet", None)
    if wallet is None:
        return None
    balance = _balance(wallet)
    for index, uploaded_file in enumerate(uploaded_files):
        estimate = _estimate_for(uploaded_file, prompt)
        if estimates_out is not None:
            estimates_out[index] = estimate
        if estimate is None or balance >= estimate:
            continue
        refusal = InsufficientCreditsError(
            f"Upload needs ~{estimate} credits, balance {balance}",
            individual=is_individual_teacher(billed),
        )
        log_refusal(logger, "Upload door", refusal)
        return _refusal_response(request_user, refusal)
    return None


def parked_files(request_user, assignment, uploaded_files, prompt, estimates_out=None):
    """H-223: {index: the refusal} for each file of a teacher's batch that
    does not fit, walking the files in upload order with a running balance
    (a file that fits takes its estimate off what is left). Empty when every
    file may be queued; a file the door cannot read or estimate, a missing
    wallet and a super admin are left to the task, as above. Nothing is
    queued by this function."""
    if request_user.is_real_super_admin():
        return {}
    billed = _billed_user(request_user, assignment)
    wallet = getattr(billed, "credit_wallet", None)
    if wallet is None:
        return {}
    left = _balance(wallet)
    parked = {}
    for index, uploaded_file in enumerate(uploaded_files):
        if not isinstance(uploaded_file, UploadedFile):
            continue  # its own item in the route (FR-A-07)
        estimate = _estimate_for(uploaded_file, prompt)
        if estimates_out is not None:
            estimates_out[index] = estimate
        if estimate is None:
            continue
        if estimate <= left:
            left -= estimate
            continue
        parked[index] = InsufficientCreditsError(
            f"Upload needs ~{estimate} credits, {left} left in the batch",
            individual=is_individual_teacher(billed),
        )
        log_refusal(logger, "Upload door (parked)", parked[index])
    return parked


def batch_refusal_response(billed_user):
    """The 402 a teacher reads when no file of the batch fits (H-223): the
    usual credit message, or the individual-teacher one when `billed_user`
    (whose wallet the batch would be billed to) is one (H-281)."""
    refusal = InsufficientCreditsError(individual=is_individual_teacher(billed_user))
    return Response(
        {"error": credit_refusal_message(refusal), "code": "insufficient_credits"},
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


def launch_at_door(launch, task_callable, processing_task, *args, **kwargs):
    """Launch a door file's task and return the task id the response carries.

    Flag off: today's direct call. Flag on (plan 08 M5, NOTES §15g): the
    Celery id is chosen now and written on the row, and the message goes out
    only when the transaction commits, so no worker starts before the hold
    exists. `launch` is the caller's own launch_processing_task, so tests
    that patch it by name still apply; partial binds this file's arguments."""
    if not credit_holds.holds_enabled():
        return launch(task_callable, processing_task, *args, **kwargs).id
    celery_task_id = str(uuid.uuid4())
    attach_celery_task(processing_task.id, celery_task_id)
    send = partial(
        launch,
        task_callable,
        processing_task,
        *args,
        _celery_task_id=celery_task_id,
        **kwargs,
    )
    if connection.in_atomic_block:
        # Under an outer transaction the message goes at its commit, where an
        # error would stop the later files' callbacks (NOTES §15g addendum).
        transaction.on_commit(_DoorLaunchOnCommit(send, processing_task.id))
    else:
        # Autocommit: on_commit runs it now, and a broker failure still
        # reaches the view (503), as in 327ce23c.
        transaction.on_commit(send)
    return celery_task_id


class _DoorLaunchOnCommit:
    """A door file's on-commit send under an outer atomic. Its own failure
    is settled here and logged without its text (H-208), and nothing is
    re-raised, so the other files' callbacks still run. It does not assume
    launch_processing_task already accounted for the failure."""

    def __init__(self, send, processing_task_id):
        self.send = send
        self.processing_task_id = processing_task_id

    def __call__(self):
        try:
            self.send()
        except Exception as exc:
            self._settle_failure(exc)
            logger.error(
                "Door launch for task %s failed on commit: %s",
                self.processing_task_id,
                describe_error_for_log(exc),
            )

    def _settle_failure(self, exc):
        row = (
            BackgroundProcessingTask.objects.select_related("job")
            .filter(pk=self.processing_task_id)
            .first()
        )
        if row is None:
            return
        if row.finished_at is None and row.status != BackgroundTaskStatus.FAILURE:
            mark_processing_task_failure(row.id, exc)
        if row.job_id is not None and row.hold_state == HoldState.HELD:
            credit_holds.fail_item(row.job, item=row)


class DoorHoldRefused(Exception):
    """A door hold did not fit, raised inside the request's transaction so
    the queued row rolls back; `response` is the usual 402."""

    def __init__(self, response):
        super().__init__("door hold refused")
        self.response = response


class DoorHold:
    """Epic B1a (plan 08 §3.2, E-7): one request's UPLOAD job, opened at the
    first file it holds. A no-op while credit holds are off, for a super
    admin, without a wallet, or for a file the door could not estimate."""

    def __init__(self, request_user, assignment, total_items):
        self.request_user = request_user
        self.assignment = assignment
        self.total_items = max(total_items, 1)
        self.job = None
        self.billed = None
        self.active = credit_holds.holds_enabled() and not (
            request_user.is_real_super_admin()
        )
        if self.active:
            self.billed = _billed_user(request_user, assignment)
            if getattr(self.billed, "credit_wallet", None) is None:
                self.active = False

    def hold(self, row, estimate):
        """True when the file may be launched."""
        if not self.active or not estimate:
            return True
        if self.job is None:
            self.job = credit_holds.open_door_job(
                billed_user=self.billed,
                requested_by=self.request_user,
                total_items=self.total_items,
                assignment=self.assignment,
            )
        return credit_holds.hold_door_item(self.job, row, estimate)

    def refusal(self, estimate):
        error = InsufficientCreditsError(
            f"Upload needs ~{estimate} credits, more than is free to hold",
            individual=is_individual_teacher(self.billed),
        )
        log_refusal(logger, "Upload door (hold)", error)
        return error

    def refusal_response(self, estimate):
        return _refusal_response(self.request_user, self.refusal(estimate))
