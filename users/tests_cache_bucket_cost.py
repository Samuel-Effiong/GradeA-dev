"""What the gap-#4 bump costs on the credit-consumption hot path.

Every AI credit spend writes at least one CreditBucket, and the bucket
receiver now looks up the wallet owner's payload viewers (teachers, school
admins) and the superadmins before bumping. This measures the queries that
adds to one `consume_credits` call and proves the count does not grow with
the size of the school: more courses taught, more students, more admins.

Queries are counted through `connection.execute_wrapper`, not
`connection.queries`, which is capped and reset by the test client.

Real Redis + real Postgres.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, transaction
from django.test import TransactionTestCase

from AutoGrader.tests_cache_matrix_support import legacy_wildcards_disabled
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from users.models import UserTypes

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


class BucketBumpQueryCostTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.enterContext(legacy_wildcards_disabled())
        self.school = School.objects.create(name="Cost school")
        self.teacher = make_active_user(
            "bc-t@x.test", UserTypes.TEACHER, "BcT", school=self.school
        )
        make_active_user(
            "bc-a@x.test", UserTypes.SCHOOL_ADMIN, "BcA", school=self.school
        )
        make_active_user("bc-s@x.test", UserTypes.SUPER_ADMIN, "BcS", is_superuser=True)
        self.wallet = CreditWallet.objects.get(user=self.teacher)
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=10_000_000,
            used_credits=0,
        )
        self.task = 0

    def consume_queries(self):
        """Queries issued by one consume_credits call, as AI grading makes it."""
        self.task += 1
        queries = []

        def count(execute, sql, params, many, context):
            queries.append(sql)
            return execute(sql, params, many, context)

        wallet = CreditWallet.objects.get(pk=self.wallet.pk)
        with connection.execute_wrapper(count):
            with transaction.atomic():
                wallet.consume_credits(
                    amount=1_000,
                    feature="Grading Assignment",
                    task_id=f"bc-task-{self.task}",
                )
        return len(queries)

    def viewer_lookups_removed(self):
        return (
            patch("users.signals.user_payload_viewer_scopes", lambda *a, **k: []),
            patch("users.signals.superadmin_user_ids", lambda *a, **k: []),
        )

    def grow_the_school(self, factor):
        """Courses taught, students enrolled and admins, `factor` of each."""
        for n in range(factor):
            make_active_user(
                f"bc-a{n}@x.test", UserTypes.SCHOOL_ADMIN, f"A{n}", school=self.school
            )
            term = Session.objects.create(name=f"T{n}", teacher=self.teacher)
            course = Course.objects.create(
                name=f"C{n}", teacher=self.teacher, session=term
            )
            for m in range(5):
                student = make_active_user(
                    f"bc-s{n}-{m}@x.test", UserTypes.STUDENT, f"S{n}x{m}"
                )
                StudentCourse.objects.create(
                    student=student,
                    course=course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                )

    def test_the_viewer_lookups_add_a_constant_number_of_queries(self):
        without_patches = self.viewer_lookups_removed()
        with without_patches[0], without_patches[1]:
            baseline = self.consume_queries()
        small = self.consume_queries()

        self.grow_the_school(20)
        large = self.consume_queries()

        added = small - baseline
        print(
            f"\n[bucket bump cost] consume_credits queries: without viewer "
            f"lookups={baseline}, with={small} (+{added}), after the school "
            f"grew 20x in courses, students and admins={large}",
            flush=True,
        )
        self.assertLessEqual(added, 4, "the viewer lookups cost more than planned")
        self.assertEqual(large, small, "the bump's queries grew with the school")
