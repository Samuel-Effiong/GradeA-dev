"""PROBE (not a regression test): decide the two gate failures by behaviour.

Strict gate on 1dd2173 failed two item-7 precision tests:
* a Settings save now bumps the student's teacher and school;
* a teacher rename now bumps the school admin.

`CustomUserSerializer` nests `settings` and `credit_wallet`, and it backs
every `UserCacheMixin` `users/<pk>` read, keyed on the VIEWER's own
generation. This probe warms those reads, runs each mutation through the
real endpoint (or model write, for the credit bucket), and reports the
verdict for every viewer with the current bumps, and with the bump in
question removed.

Legacy wildcards disabled. Real Redis + real Postgres.
"""

from contextlib import ExitStack
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
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


def _rename(user):
    user.first_name = "Renamed"
    user.save(update_fields=["first_name"])


class UserFanoutDecisionProbe(FreshnessMatrixMixin, TransactionTestCase):
    reset_sequences = True

    def _user(self, email, user_type, first, **extra):
        return User.objects.create_user(
            email=email,
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=user_type,
            is_active=True,
            first_name=first,
            last_name=user_type.title(),
            **extra,
        )

    def _fixture(self, tag):
        f = {}
        f["school_a"] = School.objects.create(name=f"Probe A {tag}")
        f["school_b"] = School.objects.create(name=f"Probe B {tag}")
        f["admin_a"] = self._user(
            f"pr-{tag}-aa@x.test", UserTypes.SCHOOL_ADMIN, "PrAA", school=f["school_a"]
        )
        f["teacher_a"] = self._user(
            f"pr-{tag}-ta@x.test", UserTypes.TEACHER, "PrTA", school=f["school_a"]
        )
        f["student"] = self._user(f"pr-{tag}-st@x.test", UserTypes.STUDENT, "PrSt")
        f["superadmin"] = self._user(
            f"pr-{tag}-su@x.test", UserTypes.SUPER_ADMIN, "PrSu", is_superuser=True
        )
        f["admin_b"] = self._user(
            f"pr-{tag}-ab@x.test", UserTypes.SCHOOL_ADMIN, "PrAB", school=f["school_b"]
        )
        f["teacher_b"] = self._user(
            f"pr-{tag}-tb@x.test", UserTypes.TEACHER, "PrTB", school=f["school_b"]
        )
        term = Session.objects.create(name=f"Probe {tag}", teacher=f["teacher_a"])
        course = Course.objects.create(
            name=f"Probe {tag}", teacher=f["teacher_a"], session=term
        )
        StudentCourse.objects.create(
            student=f["student"],
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return f

    def _reads(self, f):
        return [
            Read(
                "admin A: users/<teacher A>",
                f["admin_a"],
                reverse("user-detail", args=[f["teacher_a"].pk]),
            ),
            Read(
                "admin A: users/<student>",
                f["admin_a"],
                reverse("user-detail", args=[f["student"].pk]),
            ),
            Read(
                "teacher A: users/<student>",
                f["teacher_a"],
                reverse("user-detail", args=[f["student"].pk]),
            ),
            Read("teacher A: me", f["teacher_a"], reverse("user-me")),
            Read("student: me", f["student"], reverse("user-me")),
            Read("superadmin: users list", f["superadmin"], reverse("user-list")),
            Read(
                "superadmin: users/<teacher A>",
                f["superadmin"],
                reverse("user-detail", args=[f["teacher_a"].pk]),
            ),
            Read(
                "admin A: dash summary (sch)",
                f["admin_a"],
                "/api/v1/school-admin/dashboard/summary",
            ),
            Read(
                "admin A: dash students (sch)",
                f["admin_a"],
                "/api/v1/school-admin/dashboard/students",
            ),
            Read(
                "admin A: dash teachers (sch)",
                f["admin_a"],
                "/api/v1/school-admin/dashboard/teachers",
            ),
            Read(
                "admin B: users/<teacher B>",
                f["admin_b"],
                reverse("user-detail", args=[f["teacher_b"].pk]),
            ),
        ]

    def _mutations(self, f):
        def patch_as(user, url, data):
            client = APIClient()
            client.force_authenticate(user)
            response = client.patch(url, data, format="json")
            assert response.status_code == 200, response.content

        return {
            "M1 teacher A renames self": lambda: patch_as(
                f["teacher_a"],
                reverse("user-detail", args=[f["teacher_a"].pk]),
                {"first_name": "Renamed"},
            ),
            # No API caller may rename a student (the serializer refuses it
            # for everyone); the Django admin form saves the model.
            "M2 student renamed via model save": lambda: _rename(f["student"]),
            "M3 student saves Settings": lambda: patch_as(
                f["student"],
                reverse(
                    "settings-detail",
                    args=[Settings.objects.get(user=f["student"]).pk],
                ),
                {"theme": "DARK"},
            ),
            "M5 teacher A edits own bio": lambda: patch_as(
                f["teacher_a"],
                reverse("user-detail", args=[f["teacher_a"].pk]),
                {"bio": "A new bio"},
            ),
            "M4 credit grant to teacher A": lambda: CreditBucket.objects.create(
                wallet=CreditWallet.objects.get(user=f["teacher_a"]),
                bucket_type=CreditBucketType.MANUAL_GRANT,
                total_credits=100_000,
                used_credits=0,
            ),
        }

    # What each variant removes. `None` = the committed code as gated.
    VARIANTS = {
        "M1 teacher A renames self": {
            "school-admin fan-out removed": "users.signals.school_admin_user_ids",
        },
        "M3 student saves Settings": {
            "Settings viewer fan-out removed": "users.signals.viewer_scopes_for_users",
        },
    }

    def test_probe(self):
        cache.clear()
        self.enterContext(legacy_wildcards_disabled())
        lines = ["", "[H-1 Stage 3 user fan-out decision probe]"]
        run = 0
        for name in self._mutations(self._fixture("names")):
            variants = {"as gated": None, **self.VARIANTS.get(name, {})}
            for variant, target in variants.items():
                run += 1
                cache.clear()
                f = self._fixture(f"r{run}")
                mutate = self._mutations(f)[name]
                with ExitStack() as stack:
                    if target:
                        stack.enter_context(patch(target, lambda *a, **k: []))
                    result = self.run_matrix(
                        f"{name} [{variant}]", self._reads(f), mutate
                    )
                block = [f"--- {name} [{variant}]"]
                for o in result.outcomes:
                    block.append(
                        f"    {o.label:<32} {o.verdict:<10} "
                        f"status {o.before[0]} -> cached {o.cached_after[0]} / truth {o.truth_after[0]}"
                    )
                print("\n".join(lines + block), flush=True)
                lines = []
