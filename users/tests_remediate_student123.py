"""
H-3: remediate_student123_passwords.

The login-endpoint tests use the real ``auth/login`` route (never
force_login), so "the literal no longer authenticates" is proven where an
attacker would try it.
"""

import io
import json
import os
import tempfile
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LITERAL = "student123!"  # pragma: allowlist secret
OTHER = "a-real-password-9Q"  # pragma: allowlist secret
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CMD = "remediate_student123_passwords"


def make_student(email, password, **kw):
    kw.setdefault("is_active", True)
    return CustomUser.objects.create_user(
        email=email,
        password=password,
        first_name=email.split("@")[0].replace(".", " ").title(),
        last_name="Pupil",
        user_type=UserTypes.STUDENT,
        **kw,
    )


def run(*args):
    out = io.StringIO()
    call_command(CMD, *args, stdout=out)
    return out.getvalue()


def pw_snapshot():
    return dict(CustomUser.objects.values_list("email", "password"))


@override_settings(CACHES=LOCMEM)
class RemediationBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.report = os.path.join(self.tmp, "report.jsonl")
        self.teacher = CustomUser.objects.create_user(
            email="t@example.com",
            password=OTHER,
            first_name="T",
            last_name="T",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.bad1 = make_student("one@student.local", LITERAL)
        self.bad2 = make_student("real.person@gmail.com", LITERAL)
        self.good = make_student("good@example.com", OTHER)
        self.google = make_student("g@example.com", None)  # unusable already
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="A", course=self.course, status=AssignmentStatus.PUBLISHED
        )
        for s in (self.bad1, self.bad2, self.good):
            StudentCourse.objects.create(
                student=s,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            StudentSubmission.objects.create(
                assignment=self.assignment,
                student=s,
                answers={"q": "a"},
                score=50,
                score_percentage=50,
            )

    def login(self, email, password):
        cache.clear()
        return self.client.post(
            reverse("login"), {"email": email, "password": password}, format="json"
        )

    def report_lines(self):
        if not os.path.exists(self.report):
            return []
        with open(self.report) as f:
            return [json.loads(line) for line in f if line.strip()]


class FunctionalTests(RemediationBase):
    def test_dry_run_reports_and_writes_nothing(self):
        before = pw_snapshot()
        out = run("--report", self.report)
        self.assertEqual(pw_snapshot(), before)
        self.assertFalse(os.path.exists(self.report))
        self.assertIn("DRY-RUN", out)
        self.assertIn("Accounts with the literal password: 2", out)
        self.assertIn("with a non-@student.local email: 1", out)
        self.assertIn("would reset 2", out)

    def test_dry_run_does_not_rehash_an_outdated_hash(self):
        """CustomUser.check_password would upgrade+save a stale hash; the
        dry run must not."""
        with override_settings(
            PASSWORD_HASHERS=[
                "django.contrib.auth.hashers.PBKDF2PasswordHasher",
                "django.contrib.auth.hashers.MD5PasswordHasher",
            ]
        ):
            stale = make_password(LITERAL, hasher="pbkdf2_sha256")
            CustomUser.objects.filter(pk=self.bad1.pk).update(password=stale)
            before = pw_snapshot()
            run()
            self.assertEqual(pw_snapshot(), before)

    def test_execute_resets_only_matching_accounts(self):
        good_hash = CustomUser.objects.get(pk=self.good.pk).password
        run("--execute", "--report", self.report)
        for u in (self.bad1, self.bad2):
            u.refresh_from_db()
            self.assertFalse(u.has_usable_password())
            self.assertFalse(u.check_password(LITERAL))
        self.good.refresh_from_db()
        self.assertEqual(self.good.password, good_hash)
        self.assertTrue(self.good.check_password(OTHER))

    def test_no_deletes_and_nothing_else_changes(self):
        users_before = {
            u["email"]: {k: v for k, v in u.items() if k != "password"}
            for u in CustomUser.objects.values()
        }
        counts = (
            CustomUser.objects.count(),
            StudentCourse.objects.count(),
            StudentSubmission.objects.count(),
        )
        subs = list(StudentSubmission.objects.order_by("id").values())
        enrols = list(StudentCourse.objects.order_by("id").values())
        run("--execute", "--report", self.report)
        self.assertEqual(
            counts,
            (
                CustomUser.objects.count(),
                StudentCourse.objects.count(),
                StudentSubmission.objects.count(),
            ),
        )
        self.assertEqual(subs, list(StudentSubmission.objects.order_by("id").values()))
        self.assertEqual(enrols, list(StudentCourse.objects.order_by("id").values()))
        users_after = {
            u["email"]: {k: v for k, v in u.items() if k != "password"}
            for u in CustomUser.objects.values()
        }
        self.assertEqual(users_before, users_after)

    def test_idempotent(self):
        run("--execute", "--report", self.report)
        after_first = pw_snapshot()
        out = run("--execute", "--report", self.report)
        self.assertEqual(pw_snapshot(), after_first)
        self.assertIn("Accounts with the literal password: 0", out)
        self.assertIn("Reset: 0", out)
        self.assertEqual(len(self.report_lines()), 2)  # no duplicate records

    def test_audit_report_records_each_reset_without_secrets(self):
        run("--execute", "--report", self.report)
        lines = self.report_lines()
        self.assertEqual(
            {r["email"] for r in lines}, {"one@student.local", "real.person@gmail.com"}
        )
        raw = open(self.report).read()
        self.assertNotIn(LITERAL, raw)
        self.assertNotIn("pbkdf2_sha256$", raw)
        self.assertNotIn("md5$", raw)
        for r in lines:
            self.assertEqual(r["event"], "h3.student123.password_reset")
            self.assertEqual(r["new_state"], "unusable_password")
            self.assertIn("previous_hash_algorithm", r)

    def test_warns_when_a_matching_account_has_logged_in(self):
        from django.utils import timezone

        CustomUser.objects.filter(pk=self.bad1.pk).update(last_login=timezone.now())
        out = run()
        self.assertIn("WARNING: 1 matching account(s) have logged in", out)


class AdversarialLoginEndpointTests(RemediationBase):
    def test_literal_works_before_and_fails_on_login_endpoint_after(self):
        for email in ("one@student.local", "real.person@gmail.com"):
            self.assertEqual(
                self.login(email, LITERAL).status_code, status.HTTP_200_OK, email
            )
        # Those logins set last_login; put the fixtures back as prod has them.
        CustomUser.objects.update(last_login=None)

        run("--execute", "--report", self.report)

        for email in ("one@student.local", "real.person@gmail.com"):
            r = self.login(email, LITERAL)
            self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED, email)
            self.assertNotIn("access", r.json())

    def test_unaffected_account_can_still_log_in(self):
        run("--execute", "--report", self.report)
        self.assertEqual(
            self.login("good@example.com", OTHER).status_code, status.HTTP_200_OK
        )

    def test_reset_account_can_recover_via_a_new_password(self):
        run("--execute", "--report", self.report)
        u = CustomUser.objects.get(pk=self.bad2.pk)
        u.set_password(OTHER)
        u.save()
        self.assertEqual(
            self.login("real.person@gmail.com", OTHER).status_code, status.HTTP_200_OK
        )
        # ...and a re-run leaves that recovered account alone.
        run("--execute", "--report", self.report)
        self.assertEqual(
            self.login("real.person@gmail.com", OTHER).status_code, status.HTTP_200_OK
        )

    def test_nobody_authenticates_with_the_literal_after_execute(self):
        """The mutation target: remove the reset and this fails."""
        run("--execute", "--report", self.report)
        still = [u.email for u in CustomUser.objects.all() if u.check_password(LITERAL)]
        self.assertEqual(still, [])


class FailureTests(RemediationBase):
    def test_interrupted_mid_run_is_consistent_and_rerunnable(self):
        calls = {"n": 0}
        from django.db.models.query import QuerySet

        orig = QuerySet.update

        def boom(qs, **kw):
            if qs.model is CustomUser and "password" in kw:
                calls["n"] += 1
                if calls["n"] == 2:
                    raise KeyboardInterrupt("simulated kill")
            return orig(qs, **kw)

        with patch.object(QuerySet, "update", boom):
            with self.assertRaises(KeyboardInterrupt):
                run("--execute", "--report", self.report)

        states = {
            u.email: u.has_usable_password()
            for u in CustomUser.objects.filter(
                email__in=["one@student.local", "real.person@gmail.com"]
            )
        }
        # Exactly one reset committed, the other untouched (never half-written).
        self.assertEqual(sorted(states.values()), [False, True])
        self.assertEqual(len(self.report_lines()), 1)
        untouched = [e for e, usable in states.items() if usable][0]
        self.assertTrue(CustomUser.objects.get(email=untouched).check_password(LITERAL))

        out = run("--execute", "--report", self.report)
        self.assertIn("Reset: 1", out)
        self.assertFalse(
            any(u.check_password(LITERAL) for u in CustomUser.objects.all())
        )
        self.assertEqual(len(self.report_lines()), 2)

    def test_account_whose_password_changed_since_scan_is_not_clobbered(self):
        from django.db.models.query import QuerySet

        orig = QuerySet.update
        new_hash = make_password(OTHER)

        fired = []

        def race(qs, **kw):
            if qs.model is CustomUser and "password" in kw and not fired:
                fired.append(1)
                # The user sets a real password between scan and write.
                orig(CustomUser.objects.filter(pk=self.bad1.pk), password=new_hash)
            return orig(qs, **kw)

        with patch.object(QuerySet, "update", race):
            out = run("--execute", "--report", self.report)

        self.bad1.refresh_from_db()
        self.assertEqual(self.bad1.password, new_hash)
        self.assertTrue(self.bad1.check_password(OTHER))
        self.assertIn("Skipped (password changed since scan): 1", out)
