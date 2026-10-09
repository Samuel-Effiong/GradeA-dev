"""PROBE (not a regression test): does a bump inside a transaction let a
concurrent read cache pre-commit data under the NEW generation?

`bump_many` runs from `post_save`. When the save is inside
`transaction.atomic`, the bump lands before the commit. A reader in that
window reads the bumped generation but the old committed rows, and would
cache old data under a key that no later bump invalidates.

The interleaving is forced, not left to timing. The writer's bump
releases the reader and holds the writer's transaction open until the
reader has cached its response; then the writer commits.

Write path: POST course/<pk>/students (`enroll_student_by_email`, which
runs inside `transaction.atomic`). Read: the teacher's cached
course-detail, which lists the roster.

Legacy wildcards disabled. Real Redis + real Postgres, separate DB
connections per thread.
"""

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

import classrooms.signals
from AutoGrader.cache_generation import bump_many as real_bump_many
from AutoGrader.tests_cache_matrix_support import STALE, FreshnessMatrixMixin, Read
from classrooms.models import Course, Session
from classrooms.tests_support_add_by_email import add_by_email
from users.models import UserTypes

User = get_user_model()


class CommitRaceProbe(FreshnessMatrixMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.enterContext(
            patch("classrooms.services.notifications.safe_delay", lambda *a, **k: None)
        )
        self.teacher = User.objects.create_user(
            email="race-t@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            first_name="Race",
            last_name="Teacher",
        )
        self.student = User.objects.create_user(
            email="race-s@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
            first_name="Race",
            last_name="Student",
        )
        term = Session.objects.create(name="Race term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Race course", teacher=self.teacher, session=term
        )
        self.detail = reverse("course-detail", args=[self.course.pk])

    def _enroll_racing_a_read(self, defer_to_commit=False):
        bumped = threading.Event()
        reader_cached = threading.Event()
        writer_ident = {}
        errors = []

        def gated_bump(scopes):
            in_writer = threading.get_ident() == writer_ident.get("id")
            if defer_to_commit and in_writer:
                # Control: the same interleaving, but the bump waits for the
                # commit, as a transaction.on_commit bump would.
                from django.db import transaction

                scopes = list(scopes)
                transaction.on_commit(lambda: real_bump_many(scopes))
                result = None
            else:
                result = real_bump_many(scopes)
            if in_writer and not bumped.is_set():
                bumped.set()
                # Hold the writer's transaction open until the reader has
                # cached what it saw.
                if not reader_cached.wait(timeout=30):
                    errors.append("reader never cached")
            return result

        def writer():
            writer_ident["id"] = threading.get_ident()
            try:
                client = APIClient()
                client.force_authenticate(self.teacher)
                response = client.post(
                    reverse("course-students", args=[self.course.pk]),
                    add_by_email(self.student.email),
                )
                if response.status_code not in (200, 201):
                    errors.append(f"enroll failed: {response.status_code}")
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(repr(exc))
            finally:
                connections.close_all()

        def reader():
            try:
                if not bumped.wait(timeout=30):
                    errors.append("writer never bumped")
                    return
                client = APIClient()
                client.force_authenticate(self.teacher)
                client.get(self.detail)
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(repr(exc))
            finally:
                reader_cached.set()
                connections.close_all()

        with patch.object(classrooms.signals, "bump_many", gated_bump):
            threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            stuck = [t for t in threads if t.is_alive()]
            self.assertEqual(stuck, [], "a race thread never finished")
        self.assertEqual(errors, [], errors)

    def _probe(self, label, defer_to_commit):
        result = self.run_matrix(
            f"enroll by email racing a teacher read ({label})",
            [Read("teacher course-detail", self.teacher, self.detail)],
            lambda: self._enroll_racing_a_read(defer_to_commit=defer_to_commit),
        )
        outcome = result.outcomes[0]
        print(
            f"\n[H-1 Stage 3 commit-race probe] {label}: verdict={outcome.verdict}",
            flush=True,
        )
        self.assertIn(outcome.verdict, (STALE, "FRESH"), result.table())

    def test_probe_bump_inside_the_transaction(self):
        self._probe("bump in post_save, as in the code", defer_to_commit=False)

    def test_probe_control_bump_deferred_to_commit(self):
        self._probe("control: bump deferred to on_commit", defer_to_commit=True)
