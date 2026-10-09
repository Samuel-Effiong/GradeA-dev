"""H-211 (asked for by the Senior Manager after H-180's mutant M16): a student's
upload is billed to the course's TEACHER and to nobody else.

H-180's tests pinned the direction from one side: a funded teacher and a
student whose own wallet is empty (every new user gets an empty wallet from
users/signals.py) => queued. This is the other side: the student's OWN wallet
is funded far above the estimate while the teacher's is far below it => the
upload is REFUSED with the student sentence, nothing is queued, and the
student's wallet is untouched. A door that billed "the richer of the two", or
fell back to the student's wallet when the teacher's is short, would let it
through (and the gate in the task would bill the TEACHER's wallet anyway).
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import CreditWallet
from students.models import BackgroundProcessingTask
from students.tests_upload_credit_door import (
    POOR,
    RICH,
    STUDENT_SENTENCE,
    _classroom,
    _file,
    _fund,
)


class StudentWalletIsNotTheOneBilledTest(APITestCase):
    def _upload(self, student, assignment):
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        return self.client.post(url, {"answer": _file()}, format="multipart")

    @patch("students.views.launch_processing_task")
    def test_a_rich_student_with_a_poor_teacher_is_refused_and_not_billed(self, launch):
        teacher, student, _, assignment = _classroom("student-rich", POOR)
        _fund(student, RICH)
        # the deciding values first: the two wallets are what the test says
        student_before = CreditWallet.objects.get(
            user=student
        ).total_remaining_credits()
        teacher_before = CreditWallet.objects.get(
            user=teacher
        ).total_remaining_credits()
        self.assertEqual(student_before, RICH)
        self.assertEqual(teacher_before, POOR)

        response = self._upload(student, assignment)

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["error"], STUDENT_SENTENCE)
        launch.assert_not_called()
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)
        self.assertEqual(
            CreditWallet.objects.get(user=student).total_remaining_credits(),
            student_before,
        )
        self.assertEqual(
            CreditWallet.objects.get(user=teacher).total_remaining_credits(),
            teacher_before,
        )
