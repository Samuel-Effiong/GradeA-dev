"""
The H-47 production exposure query must run against the real schema and
pick out the right rows. It's handed to the founder to run on production,
so a column typo would only surface there.
"""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from users.models import RegistrationMethod, UserTypes

User = get_user_model()

SQL = (
    Path(settings.BASE_DIR)
    / "docs/evidence/register-student-token-scope/prod_exposure_query.sql"
)


def _selects():
    """The SELECT statements, without the BEGIN/ROLLBACK wrapper (the test
    already runs inside a transaction)."""
    body = "\n".join(
        line
        for line in SQL.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )
    return [
        s.strip() for s in body.split(";") if s.strip().upper().startswith("SELECT")
    ]


class ExposureQueryTests(TestCase):
    def _user(self, email, user_type, **kw):
        return User.objects.create_user(
            email=email,
            password=None,
            first_name="X",
            last_name="Y",
            user_type=user_type,
            registration_method=RegistrationMethod.EMAIL,
            **kw,
        )

    def test_both_selects_run_and_find_the_expected_rows(self):
        verified = timezone.now()
        taken_over = self._user(
            "t@gmail.com", UserTypes.TEACHER, is_active=True, email_verified_at=verified
        )
        self._user(
            "s@student.local",
            UserTypes.STUDENT,
            is_active=True,
            email_verified_at=verified,
        )
        self._user(
            "p@gmail.com",
            UserTypes.TEACHER,
            is_active=False,
            activation_token="123456",
            activation_expires=timezone.now() + timezone.timedelta(minutes=10),
        )
        self._user(
            "r@student.local",
            UserTypes.STUDENT,
            is_active=False,
            activation_token="654321",
            activation_expires=timezone.now() - timezone.timedelta(hours=1),
        )
        candidates_sql, pool_sql = _selects()

        with connection.cursor() as cur:
            cur.execute(candidates_sql)
            candidates = cur.fetchall()
            cur.execute(pool_sql)
            pool = {row[0]: row[1:] for row in cur.fetchall()}

        self.assertEqual([row[0] for row in candidates], [taken_over.id])
        self.assertEqual(pool["TEACHER"], (1, 1))
        self.assertEqual(pool["STUDENT"], (0, 1))

    def test_no_query_selects_personal_data_or_secrets(self):
        select_lists = [s.lower().split("from", 1)[0] for s in _selects()]
        self.assertEqual(len(select_lists), 2)
        for cols in select_lists:
            for column in (
                "email",
                "first_name",
                "last_name",
                "password",
                "activation_token",
            ):
                # Whole column names only: email_verified_at is fine.
                self.assertIsNone(re.search(rf"\bu\.{column}\b", cols), column)
