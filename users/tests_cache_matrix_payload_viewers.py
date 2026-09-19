"""H-1 Stage 3, gaps #3, #4 and #5: other viewers' cached `users/<pk>`.

`CustomUserSerializer` renders a user's row with `bio`, the nested
`settings` and the nested `credit_wallet`, and `UserCacheMixin` caches every
`users/<pk>` read under the VIEWER's own generation. So a change the user's
own generation covers can still leave another viewer's copy stale:

* gap #3 - a student usually has no school of their own. Their school admin
  sees them through enrollments -> course -> teacher -> school, but the
  admin fan-out used only the student's own school, so the admin's cached
  `users/<student>` outlived a rename or a Settings save;
* gap #4 - a credit bucket write bumped only the wallet owner, so the
  school admin's and superadmin's cached `users/<teacher>` kept the old
  balance;
* gap #5 - `bio` was treated as private, but every viewer's `users/<pk>`
  renders it.

FIXED: `user_payload_viewer_scopes` (users/signals.py) names those viewers,
and the Settings, CreditBucket and payload-only CustomUser paths bump them.
Each test checks the stale viewer is now FRESH and that school B's admin,
who sees none of it, is UNAFFECTED.

Mutations run the way production runs them: the real `PATCH` endpoints for
bio and Settings, `CreditWallet.consume_credits` inside `transaction.atomic`
as AI grading calls it, and a model save for the student rename (no API
caller may rename a student; the Django admin form saves the model).

Legacy wildcards disabled. Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from AutoGrader.tests_cache_matrix_support import (
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from users.models import Settings, UserTypes

User = get_user_model()


def make_active_user(email, user_type, first_name, **extra):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
        **extra,
    )


class PayloadViewerFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.assertTrue(
            self.enterContext(legacy_wildcards_disabled()),
            "no legacy module was patched",
        )
        self.school_a = School.objects.create(name="PV School A")
        self.school_b = School.objects.create(name="PV School B")
        self.admin_a = make_active_user(
            "pv-aa@x.test", UserTypes.SCHOOL_ADMIN, "PvAA", school=self.school_a
        )
        self.teacher_a = make_active_user(
            "pv-ta@x.test", UserTypes.TEACHER, "PvTA", school=self.school_a
        )
        self.admin_b = make_active_user(
            "pv-ab@x.test", UserTypes.SCHOOL_ADMIN, "PvAB", school=self.school_b
        )
        self.teacher_b = make_active_user(
            "pv-tb@x.test", UserTypes.TEACHER, "PvTB", school=self.school_b
        )
        self.superadmin = make_active_user(
            "pv-su@x.test", UserTypes.SUPER_ADMIN, "PvSu", is_superuser=True
        )
        # No school of their own: school A's admin sees them only through
        # teacher A's course.
        self.student = make_active_user("pv-st@x.test", UserTypes.STUDENT, "PvSt")
        term = Session.objects.create(name="PV term", teacher=self.teacher_a)
        course = Course.objects.create(
            name="PV course", teacher=self.teacher_a, session=term
        )
        StudentCourse.objects.create(
            student=self.student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

    def detail(self, user):
        return reverse("user-detail", args=[user.pk])

    def isolation_read(self):
        return Read(
            "school B admin: users/<teacher B>",
            self.admin_b,
            self.detail(self.teacher_b),
        )

    def patch_as(self, user, url, data):
        client = APIClient()
        client.force_authenticate(user)
        response = client.patch(url, data, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_gap3_student_rename_refreshes_the_school_admin(self):
        def rename():
            student = User.objects.get(pk=self.student.pk)
            student.first_name = "Renamedpv"
            student.save(update_fields=["first_name"])

        result = self.run_matrix(
            "student renamed (model save)",
            [
                Read(
                    "school A admin: users/<student>",
                    self.admin_a,
                    self.detail(self.student),
                ),
                self.isolation_read(),
            ],
            rename,
        )
        self.assert_no_stale(result, expect_changed=["school A admin: users/<student>"])

    def test_gap3_student_settings_save_refreshes_teacher_and_school_admin(self):
        settings_pk = Settings.objects.get(user=self.student).pk
        result = self.run_matrix(
            "student saves Settings",
            [
                Read(
                    "school A admin: users/<student>",
                    self.admin_a,
                    self.detail(self.student),
                ),
                Read(
                    "teacher A: users/<student>",
                    self.teacher_a,
                    self.detail(self.student),
                ),
                self.isolation_read(),
            ],
            lambda: self.patch_as(
                self.student,
                reverse("settings-detail", args=[settings_pk]),
                {"theme": "DARK"},
            ),
        )
        self.assert_no_stale(
            result,
            expect_changed=[
                "school A admin: users/<student>",
                "teacher A: users/<student>",
            ],
        )

    def test_gap4_credit_consumption_refreshes_admin_and_superadmin(self):
        wallet = CreditWallet.objects.get(user=self.teacher_a)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=100_000,
            used_credits=0,
        )

        def consume():
            with transaction.atomic():
                CreditWallet.objects.get(pk=wallet.pk).consume_credits(
                    amount=7_000, feature="Grading Assignment", task_id="pv-task-1"
                )

        result = self.run_matrix(
            "teacher A spends credits",
            [
                Read(
                    "school A admin: users/<teacher A>",
                    self.admin_a,
                    self.detail(self.teacher_a),
                ),
                Read(
                    "superadmin: users/<teacher A>",
                    self.superadmin,
                    self.detail(self.teacher_a),
                ),
                self.isolation_read(),
            ],
            consume,
        )
        self.assert_no_stale(
            result,
            expect_changed=[
                "school A admin: users/<teacher A>",
                "superadmin: users/<teacher A>",
            ],
        )

    def test_gap5_bio_edit_refreshes_the_school_admin(self):
        result = self.run_matrix(
            "teacher A edits own bio",
            [
                Read(
                    "school A admin: users/<teacher A>",
                    self.admin_a,
                    self.detail(self.teacher_a),
                ),
                Read(
                    "superadmin: users/<teacher A>",
                    self.superadmin,
                    self.detail(self.teacher_a),
                ),
                self.isolation_read(),
            ],
            lambda: self.patch_as(
                self.teacher_a, self.detail(self.teacher_a), {"bio": "A new bio"}
            ),
        )
        self.assert_no_stale(
            result,
            expect_changed=[
                "school A admin: users/<teacher A>",
                "superadmin: users/<teacher A>",
            ],
        )
