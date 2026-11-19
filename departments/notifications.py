"""Membership emails (owner Q8, plan 9b): a teacher is told when an admin
adds them to, or removes them from, a department.

Same path as the other transactional mail (`safe_delay(send_email_task, ...)`
with the shared notice template), queued only after the change commits. An
email problem never blocks or undoes the change: any error is logged with
ids only (never the address) and swallowed. No email for a reap, a delete or
a switch (not called from those paths).
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from AutoGrader.dispatch import safe_delay
from AutoGrader.tasks import send_email_task
from classrooms.services.notifications import TEMPLATE_COURSE_NOTICE

from . import wording

logger = logging.getLogger(__name__)


def _queue(teacher, department, subject_text, body_text):
    """Capture everything now, send after commit."""
    if not teacher.is_active:
        return
    teacher_id = str(teacher.pk)
    department_id = str(department.pk)
    address = teacher.email
    name = teacher.get_full_name()
    subject = subject_text.format(department=department.name)
    body = body_text.format(
        school=escape(department.school.name), department=escape(department.name)
    )
    merge_data = {
        "title": subject,
        "name": name,
        "current_year": timezone.now().year,
        "support_email": settings.SUPPORT_EMAIL,
        "content": body,
    }

    def send():
        try:
            safe_delay(
                send_email_task,
                subject=subject,
                message="",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[address],
                html_message=None,
                template_id=TEMPLATE_COURSE_NOTICE,
                merge_data=merge_data,
            )
        except Exception as error:  # an email must never undo the change
            logger.warning(
                "department email not queued: teacher=%s department=%s error=%s",
                teacher_id,
                department_id,
                type(error).__name__,
            )

    transaction.on_commit(send)


def send_added_to_department_email(teacher, department):
    _queue(teacher, department, wording.ADDED_SUBJECT, wording.ADDED_BODY)


def send_removed_from_department_email(teacher, department):
    _queue(teacher, department, wording.REMOVED_SUBJECT, wording.REMOVED_BODY)
