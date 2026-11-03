"""H-282: the upload door's super admin check uses both flags.

A super admin needs BOTH user_type SUPER_ADMIN and is_superuser (H-19), as
at IsSuperAdmin, HasCreditBalance and execute_graded_task. The door's two
functions checked user_type only, so a half-set-up account (is_superuser
unticked) was let through. Now it is treated like any other non-student: its
own wallet is checked, and a short or empty wallet is refused with the usual
402 and the school sentence. It is not a TEACHER, so the refusal is not the
H-281 individual one.

Today every route that calls the door is IsTeacher or IsStudent, so a super
admin of either kind is refused with a 403 before the door. That is pinned
here as a characterization test, green before and after.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE, InsufficientCreditsError
from billing.models import CreditWallet
from students.models import BackgroundProcessingTask
from students.tests_upload_credit_door import POOR, _classroom, _file, _fund, _user
from users.models import UserTypes

DEAR = 60_000  # the door's estimate for one file, pinned: above POOR


def half_set_up_super_admin(tag="h282-half"):
    user = _user(tag, UserTypes.SUPER_ADMIN)
    user.is_superuser = False
    user.save(update_fields=["is_superuser"])
    return user


def real_super_admin(tag="h282-real"):
    user = _user(tag, UserTypes.SUPER_ADMIN)
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    return user


def _with_wallet(user, credits):
    """A wallet of exactly `credits` (0 means an existing, empty wallet)."""
    if credits:
        _fund(user, credits)
    else:
        CreditWallet.objects.get_or_create(user=user)
    return user


def _estimate():
    return patch.object(AIProcessor, "estimate_messages_cost", return_value=DEAR)


class WhoIsARealSuperAdmin(TestCase):
    def test_both_flags_is(self):
        self.assertIs(real_super_admin().is_real_super_admin(), True)

    def test_a_half_set_up_super_admin_is_not(self):
        self.assertIs(half_set_up_super_admin().is_real_super_admin(), False)

    def test_a_teacher_with_is_superuser_ticked_is_not(self):
        teacher = _user("h282-super-teacher", UserTypes.TEACHER)
        teacher.is_superuser = True
        teacher.save(update_fields=["is_superuser"])
        self.assertIs(teacher.is_real_super_admin(), False)

    def test_a_plain_teacher_is_not(self):
        self.assertIs(
            _user("h282-teacher", UserTypes.TEACHER).is_real_super_admin(), False
        )


class TheSingleDoor(TestCase):
    """upload_refusal_if_unaffordable."""

    def _door(self, user):
        from assignments.upload_door import upload_refusal_if_unaffordable

        with _estimate():
            return upload_refusal_if_unaffordable(user, None, [_file()], "p")

    def assertRefusedWithTheSchoolSentence(self, response):
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertEqual(response.data["error"], INSUFFICIENT_CREDITS_MESSAGE)

    def test_a1_a_half_set_up_super_admin_with_a_short_wallet_is_refused(self):
        user = _with_wallet(half_set_up_super_admin(), POOR)
        self.assertRefusedWithTheSchoolSentence(self._door(user))

    def test_a2_a_half_set_up_super_admin_with_an_empty_wallet_is_refused(self):
        user = _with_wallet(half_set_up_super_admin(), 0)
        self.assertRefusedWithTheSchoolSentence(self._door(user))

    def test_b1_a_real_super_admin_still_skips_the_door(self):
        user = _with_wallet(real_super_admin(), POOR)
        self.assertIsNone(self._door(user))


class TheBatchDoor(TestCase):
    """parked_files."""

    def _door(self, user):
        from assignments.upload_door import parked_files

        with _estimate():
            return parked_files(user, None, [_file()], "p")

    def assertParkedUntagged(self, parked):
        self.assertEqual(list(parked), [0])
        self.assertIsInstance(parked[0], InsufficientCreditsError)
        self.assertIs(parked[0].individual, False)

    def test_a3_a_half_set_up_super_admin_with_a_short_wallet_is_parked(self):
        user = _with_wallet(half_set_up_super_admin(), POOR)
        self.assertParkedUntagged(self._door(user))

    def test_a4_a_half_set_up_super_admin_with_an_empty_wallet_is_parked(self):
        user = _with_wallet(half_set_up_super_admin(), 0)
        self.assertParkedUntagged(self._door(user))

    def test_b2_a_real_super_admin_still_skips_the_door(self):
        user = _with_wallet(real_super_admin(), POOR)
        self.assertEqual(self._door(user), {})


class TheRoutesRefuseASuperAdminFirst(APITestCase):
    """Characterization, green before and after: no route reaches the door
    for a super admin of either kind."""

    @patch("students.views.launch_processing_task")
    def test_r1_the_batch_route(self, launch):
        _, _, _, assignment = _classroom("h282-r1", POOR)
        user = _with_wallet(half_set_up_super_admin("h282-r1-half"), POOR)
        self.client.force_authenticate(user=user)

        response = self.client.post(
            reverse(
                "student-submission-batch-upload",
                kwargs={"assignment_id": assignment.pk},
            ),
            {"answers": [_file(name="s0.png")]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        launch.assert_not_called()
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)

    @patch("assignments.views.launch_processing_task")
    def test_r2_the_assignment_route(self, launch):
        _, _, course, _ = _classroom("h282-r2", POOR)
        user = _with_wallet(half_set_up_super_admin("h282-r2-half"), POOR)
        self.client.force_authenticate(user=user)

        response = self.client.post(
            reverse("assignment-upload-async"),
            {"course": str(course.id), "assignments": [_file(name="paper.png")]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        launch.assert_not_called()
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)
