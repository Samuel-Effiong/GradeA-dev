"""H-1 Stage 3: failure-mode probe for the generation-bump write path.

The plan's gate requires: "Redis unavailable during a bump never fails
the database write, and data is fresh once Redis returns." The new
Stage 3 receivers (G1-G9, P1-P5) all go through `bump_many`/
`bump_generation` (AutoGrader/cache_generation.py), called from inside a
`post_save`/`post_delete` receiver -- which Django runs INSIDE the
caller's transaction, exactly like `delete_cache_patterns` already
documents for the legacy wildcard path.

This is an empirical PROBE, not a pre-written assertion of the desired
behaviour: it forces a real Redis connection error during a real write
that goes through one of the new Stage 3 receivers (G1: publishing an
assignment) and records what actually happens, so the finding is
evidence, not a guess.

Real Redis + real Postgres (the assignment write itself is real; only
the generation-bump call is forced to fail, via a connection error
raised from the pipelined bump's fallback path).
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, Session
from users.models import UserTypes

User = get_user_model()


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


def question(number=1):
    return {
        "question_number": number,
        "question_text": f"Q{number}",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["one", "two"],
        "rubric": [],
        "model_answer": "one",
    }


class GenerationBumpRedisOutageProbe(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.teacher = make_active_user(
            "fail-g1-teacher@x.test", UserTypes.TEACHER, "FailG1T"
        )
        self.session = Session.objects.create(name="Fail term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Fail course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Fail assignment",
            course=self.course,
            status=AssignmentStatus.DRAFT,
            questions=[question()],
        )

    def test_redis_outage_during_a_bump_probe(self):
        """Empirical: does a Redis outage during the G1 receiver's bump_many
        call fail the assignment write itself?"""
        client = APIClient()
        client.force_authenticate(self.teacher)

        # Force EVERY path bump_many can take to hit a real connection
        # error: the pipelined attempt (get_client) and the per-key
        # fallback (cache.incr).
        with patch(
            "django.core.cache.cache.client.get_client",
            side_effect=RedisConnectionError("simulated Redis outage"),
        ), patch(
            "django.core.cache.cache.incr",
            side_effect=RedisConnectionError("simulated Redis outage"),
        ), patch(
            "django.core.cache.cache.add",
            side_effect=RedisConnectionError("simulated Redis outage"),
        ):
            response = client.patch(
                reverse("assignment-detail", args=[self.assignment.pk]),
                {"status": "PUBLISHED"},
                format="json",
            )

        write_survived = response.status_code == 200
        self.assignment.refresh_from_db()
        db_write_landed = self.assignment.status == AssignmentStatus.PUBLISHED

        if write_survived and db_write_landed:
            outcome = "PASS: the write survived a Redis outage during the bump"
        elif not write_survived and not db_write_landed:
            outcome = (
                "FINDING: a Redis outage during bump_many propagated and "
                f"failed the assignment write (status {response.status_code}, "
                "content: " + repr(getattr(response, "data", None)) + ") - "
                "this is a gap in bump_many's error handling (AutoGrader/"
                "cache_generation.py), pre-existing since H-1 stage 2, not "
                "introduced by Stage 3's new receivers but now exercised by "
                "twelve more of them. Flagged for owner decision, not "
                "silently patched - out of the confirmed-gap list's scope."
            )
        else:
            outcome = (
                f"INCONSISTENT: response {response.status_code} but DB "
                f"status is {self.assignment.status!r} - a partial-failure "
                "shape worth its own investigation."
            )

        # This assertion records the finding in the test-run output either
        # way; it does not itself assert a specific outcome, because the
        # point of a probe is to discover the current behaviour, not to
        # assume it.
        print(f"\n[H-1 Stage 3 failure-mode probe] {outcome}")
        self.probe_outcome = outcome
        self.assertTrue(write_survived and db_write_landed, outcome)

        # Recovery half of the gate item: "data is fresh once Redis
        # returns". Redis is no longer patched here, so this bump must
        # succeed normally, and a subsequent read (any read, since nothing
        # was cached during the simulated outage) must show current data.
        response2 = client.patch(
            reverse("assignment-detail", args=[self.assignment.pk]),
            {"status": "UNPUBLISHED"},
            format="json",
        )
        self.assertEqual(response2.status_code, 200, response2.content)
        list_response = client.get(reverse("assignment-list"))
        self.assertEqual(list_response.status_code, 200)
        results = list_response.data.get("results", list_response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "UNPUBLISHED")
