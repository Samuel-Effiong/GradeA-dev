"""H-19: unmetered AI and the credit gate require BOTH superadmin flags.

Two checks trusted `user_type == SUPER_ADMIN` alone:

  * users/permissions.py HasCreditBalance returned True before looking at any
    wallet;
  * ai_processor/services.py AIProcessor.execute_graded_task took its
    "unmetered, unrestricted" branch - no tier gate, no credit consumption.

So an account with user_type=SUPER_ADMIN and is_superuser=False ran billed AI
for free. No HTTP route lets such an account reach the AI call (each has a
role gate first), but background work that loads course.teacher does -
weekly course summaries, auto-grade on due date, scheduled grading, student
summaries - and the account is easy to produce: a real superadmin PATCHes a
school-less teacher to user_type=SUPER_ADMIN (is_superuser is untouched and
the account keeps its courses), or unticks is_superuser in Django admin.

Both account shapes are produced here through those real write paths - the
users API and a Django admin change-form POST - never by setting flags on a
fixture row. The provider call is stubbed (it is exercised for real, once,
in the evidence record); billing rows are read from the real tables.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.forms import MultiWidget
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor, ai_processor
from assignments.tests_security import enroll, make_course, make_student
from billing.access_control import AIFeatureNotAvailableError
from billing.models import CreditBucket, CreditBucketType, CreditLedger, CreditUsageLog
from classrooms.models import StudentCourse
from classrooms.tasks import student_summary_async
from users.models import CustomUser, UserTypes

PASSWORD = "password123"  # pragma: allowlist secret
PROVIDER = "_AIProcessor__ai_model"


def provider_reply(text="A steady term with strong quiz results."):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10, total_tokens=20),
    )


if TYPE_CHECKING:
    from rest_framework.test import APITestCase as _MixinBase
else:
    _MixinBase = object


class SuperadminShapesMixin(_MixinBase):
    """Real superadmin, and two type-only accounts made the ways production can.

    A mixin so the threaded tests (APITransactionTestCase) build the accounts
    through exactly the same write paths.
    """

    def build_superadmin_shapes(self):
        cache.clear()
        self.addCleanup(cache.clear)

        # A real superadmin: createsuperuser, then promoted in Django admin.
        self.superadmin = CustomUser.objects.create_superuser(
            email="h19-real-sa@example.com", password=PASSWORD
        )
        self.admin_save(
            self.superadmin, self.superadmin, user_type=UserTypes.SUPER_ADMIN
        )

        # Type-only shape 1: a school-less teacher promoted through the users
        # API by the real superadmin. is_superuser stays False.
        self.promoted = CustomUser.objects.create_user(
            email="h19-promoted@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
        )
        self.promoted_course = make_course(self.promoted, "Promoted Teacher Course")
        self.superadmin.refresh_from_db()  # the admin save changed user_type
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.promoted.pk}),
            {"user_type": UserTypes.SUPER_ADMIN},
            format="json",
        )
        self.assertEqual(
            response.status_code, status.HTTP_200_OK, response.content[:300]
        )
        self.client.force_authenticate(user=None)

        # Type-only shape 2: a real superadmin with is_superuser unticked in
        # Django admin (a partial demotion).
        self.demoted = CustomUser.objects.create_superuser(
            email="h19-demoted@example.com", password=PASSWORD
        )
        self.admin_save(self.superadmin, self.demoted, user_type=UserTypes.SUPER_ADMIN)
        self.admin_save(self.superadmin, self.demoted, is_superuser=False)

        for user in (self.superadmin, self.promoted, self.demoted):
            user.refresh_from_db()
        self.type_only = (self.promoted, self.demoted)

    def admin_save(self, actor, target, **changes):
        """Submit target's Django admin change form, as a person would."""
        self.client.force_login(actor)
        url = reverse("admin:users_customuser_change", args=[target.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        form = page.context["adminform"].form

        data: dict[str, Any] = {}
        for name, field in form.fields.items():
            value = form[name].value()
            widget = field.widget
            if field.disabled or name == "password":
                continue
            if isinstance(widget, MultiWidget):
                parts = widget.decompress(value) if value is not None else []
                for i in range(len(widget.widgets)):
                    part = parts[i] if i < len(parts) else None
                    data[f"{name}_{i}"] = "" if part is None else str(part)
            elif isinstance(value, bool):
                if value:
                    data[name] = "on"
            elif isinstance(value, (list, tuple)):
                data[name] = [str(v) for v in value]
            elif value is None:
                data[name] = ""
            else:
                data[name] = str(value)

        for name, value in changes.items():
            if isinstance(value, bool):
                if value:
                    data[name] = "on"
                else:
                    data.pop(name, None)
            else:
                data[name] = str(value)
        data["_save"] = "Save"

        response = self.client.post(url, data)
        self.assertEqual(
            response.status_code,
            302,
            f"admin save refused: {getattr(response.context, 'get', lambda k: None)('errors')}",
        )
        self.client.logout()

    def billing_rows(self, user):
        return (
            CreditUsageLog.objects.filter(user_id=user.id).count(),
            CreditLedger.objects.filter(user_id=user.id).count(),
        )


class SuperadminShapesFixture(SuperadminShapesMixin, APITestCase):
    def setUp(self):
        self.build_superadmin_shapes()


class AccountShapesTest(SuperadminShapesFixture):
    def test_the_production_write_paths_produce_the_expected_flags(self):
        self.assertEqual(
            (self.superadmin.user_type, self.superadmin.is_superuser),
            (UserTypes.SUPER_ADMIN, True),
        )
        for user in self.type_only:
            with self.subTest(user=user.email):
                self.assertEqual(
                    (user.user_type, user.is_superuser), (UserTypes.SUPER_ADMIN, False)
                )


class CreditGateBothFlagsTest(SuperadminShapesFixture):
    """HasCreditBalance, over HTTP. PATCH submissions/<pk> is gated only by
    [IsAuthenticated, HasCreditBalance], so it isolates this permission."""

    def url(self):
        return reverse("student-submission-detail", kwargs={"pk": uuid4()})

    def test_type_only_account_with_empty_wallet_is_refused_by_the_credit_gate(self):
        for user in self.type_only:
            with self.subTest(user=user.email):
                self.client.force_authenticate(user=user)
                response = self.client.patch(
                    self.url(), {"raw_input": "x"}, format="json"
                )
                # HasCreditBalance now raises EmptyWalletError (H-24,
                # billing/refusals.py) instead of the old ParseError, so an
                # empty wallet is a clean 402 refusal, not a 400 - see
                # users/tests_credit_balance_permission.py for the same
                # reconciliation.
                self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
                self.assertEqual(
                    response.json()["error"]["field_errors"]["code"],
                    "insufficient_credits",
                )
                self.assertEqual(self.billing_rows(user), (0, 0))

    def test_type_only_account_with_real_credit_passes_the_credit_gate(self):
        # Not refused outright: it is judged on its own wallet like anyone.
        for user in self.type_only:
            with self.subTest(user=user.email):
                CreditBucket.objects.create(
                    wallet=user.credit_wallet,
                    bucket_type=CreditBucketType.MONTHLY,
                    total_credits=50,
                    used_credits=0,
                )
                self.client.force_authenticate(user=user)
                response = self.client.patch(
                    self.url(), {"raw_input": "x"}, format="json"
                )
                # Past the credit gate; the queryset then hides the row.
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_createsuperuser_account_does_not_bypass_the_credit_gate(self):
        # The other single-flag shape: is_superuser True, user_type left at
        # TEACHER (exactly what `manage.py createsuperuser` makes). It must be
        # judged on its wallet like anyone else.
        django_admin = CustomUser.objects.create_superuser(
            email="h19-credit-djadmin@example.com", password=PASSWORD
        )
        self.assertEqual(
            (django_admin.user_type, django_admin.is_superuser),
            (UserTypes.TEACHER, True),
        )
        self.client.force_authenticate(user=django_admin)
        response = self.client.patch(self.url(), {"raw_input": "x"}, format="json")
        # See test_type_only_account_with_empty_wallet_is_refused_by_the_credit_gate:
        # EmptyWalletError (H-24) -> 402, not the old ParseError -> 400.
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(
            response.json()["error"]["field_errors"]["code"], "insufficient_credits"
        )
        self.assertEqual(self.billing_rows(django_admin), (0, 0))

    def test_real_superadmin_still_bypasses_the_credit_gate(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(self.url(), {"raw_input": "x"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn("Insufficient Credits", response.content.decode())


class UnmeteredAIBothFlagsTest(SuperadminShapesFixture):
    """execute_graded_task, through a real public service method."""

    def setUp(self):
        super().setUp()
        self.student = make_student("h19-student@example.com")
        enroll(self.student, self.promoted_course)

    def test_type_only_account_is_refused_before_any_provider_call(self):
        for user in self.type_only:
            with self.subTest(user=user.email):
                with patch.object(
                    AIProcessor, PROVIDER, return_value=provider_reply()
                ) as provider:
                    with self.assertRaises(AIFeatureNotAvailableError):
                        ai_processor.generate_student_summary(
                            user, self.student, self.promoted_course
                        )
                provider.assert_not_called()
                self.assertEqual(self.billing_rows(user), (0, 0))

    def test_real_superadmin_is_still_unmetered(self):
        with patch.object(
            AIProcessor, PROVIDER, return_value=provider_reply()
        ) as provider:
            summary = ai_processor.generate_student_summary(
                self.superadmin, self.student, self.promoted_course
            )
        provider.assert_called_once()
        self.assertEqual(summary, "A steady term with strong quiz results.")
        self.assertEqual(self.billing_rows(self.superadmin), (0, 0))


class CeleryPathBothFlagsTest(SuperadminShapesFixture):
    """student_summary_async: the background path that loads a teacher by id
    and is reachable for an account that owns courses."""

    def setUp(self):
        super().setUp()
        self.student = make_student("h19-task-student@example.com")
        self.enrollment = enroll(self.student, self.promoted_course)

    def run_task(self, teacher):
        with patch.object(
            AIProcessor, PROVIDER, return_value=provider_reply()
        ) as provider:
            result = student_summary_async.apply(
                args=[
                    str(self.student.id),
                    str(teacher.id),
                    str(self.promoted_course.id),
                ]
            )
        return result, provider

    def test_type_only_course_owner_task_fails_cleanly_without_billing(self):
        result, provider = self.run_task(self.promoted)

        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, AIFeatureNotAvailableError)
        provider.assert_not_called()
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.ai_summary)
        self.assertEqual(self.billing_rows(self.promoted), (0, 0))

    def test_real_superadmin_task_still_runs_unmetered(self):
        result, provider = self.run_task(self.superadmin)

        self.assertTrue(result.successful(), result.result)
        provider.assert_called_once()
        self.assertEqual(
            StudentCourse.objects.get(pk=self.enrollment.pk).ai_summary,
            "A steady term with strong quiz results.",
        )
        self.assertEqual(self.billing_rows(self.superadmin), (0, 0))
