"""Epic B1a: the student is told when their late door upload is refused
(owner ruling relayed by the SM 2026-11-08; NOTES §17).

The student gets STUDENT_UPLOAD_NOT_PROCESSED, unchanged, which names no
balance or credits. The teacher's email stays off until B1b.
"""

import re
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from billing.errors import InsufficientCreditsError
from billing.testing.holds import (
    ai_response,
    call_ai,
    estimate_for,
    holds,
    make_item,
    make_job,
)
from billing.tests.test_holds_door import PROVIDER, wallet_of
from billing.tests.test_holds_entry import classroom
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED

SEND = "AutoGrader.tasks.send_email_task.delay"


@override_settings(BILLING_HOLDS_ENABLED=True)
class LateDoorStudentNoticeTests(APITestCase):
    def late_door_refusal(self, tag, by_student=True):
        """A swept door row comes back; its new UPLOAD job cannot hold."""
        teacher, student, _, _, _ = classroom(tag, estimate_for() + 1_000)
        requester = student if by_student else teacher
        wallet = wallet_of(teacher)
        old = make_job(
            wallet, "UPLOAD", total_items=1, status="RUNNING", requested_by=requester
        )
        row = make_item(
            old, file_name="paper-7.png", status="FAILURE", requested_by=requester
        )
        holds().reserve(wallet, 50, old, item=row)
        holds().fail_item(old, item=row)
        holds().reserve(wallet, estimate_for(), make_job(wallet))  # spend it
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(100)):
            with self.assertRaises(InsufficientCreditsError):
                with holds().metered_unit(processing_task_id=row.id, reason="t"):
                    call_ai(teacher)
        return teacher, student

    def test_l1_the_student_is_emailed_the_existing_notice(self):
        with patch(SEND) as send:
            _, student = self.late_door_refusal("ls1")
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [student.email])
        self.assertEqual(kwargs["message"], STUDENT_UPLOAD_NOT_PROCESSED)

    def test_l2_the_notice_names_no_balance_or_credits(self):
        with patch(SEND) as send:
            _, student = self.late_door_refusal("ls2")
        messages = [
            c.kwargs["message"]
            for c in send.call_args_list
            if c.kwargs.get("recipient_list") == [student.email]
        ]
        self.assertEqual(len(messages), 1)
        text = messages[0].lower()
        for word in ("credit", "balance", "wallet"):
            self.assertNotIn(word, text)
        self.assertIsNone(re.search(r"\d", text))

    def test_l3_the_teacher_is_still_not_emailed(self):
        with patch(SEND) as send, self.assertLogs("billing.holds", "WARNING") as logs:
            teacher, _ = self.late_door_refusal("ls3")
        recipients = [c.kwargs.get("recipient_list") for c in send.call_args_list]
        self.assertNotIn([teacher.email], recipients)
        self.assertTrue(any("no email was sent" in line for line in logs.output))

    def test_l4_a_teacher_upload_sends_no_email(self):
        with patch(SEND) as send:
            self.late_door_refusal("ls4", by_student=False)
        send.assert_not_called()
