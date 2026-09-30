"""Epic A S4: before/after values (plan 08 §5, SM rulings R1-R6 and the
follow-up rulings). Every route and action gives exactly the expected
before/after pair; an untracked change gives nothing; no student text,
name or email reaches an event; an audit failure never fails the write.
"""

import json
import threading
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import grade_engine_async
from audit import history
from audit.context import request_audit_state
from audit.enums import ActorRole, AuditAction
from audit.metadata import BEFORE_AFTER_ALLOWLIST, METADATA_ALLOWLIST
from audit.models import AuditEvent
from audit.tests_metadata import FORBIDDEN_FRAGMENTS
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()
PW = "History-test-pw-1"  # pragma: allowlist secret
SENTINEL = "SENTINEL-S4-7f3a private student words"
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ESSAY = [
    {
        "question_number": 1,
        "question_text": "Discuss.",
        "question_type": "ESSAY",
        "points": 20,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Great", "points": 20},
            {"level": "good", "description": "Good", "points": 15},
            {"level": "fair", "description": "Fair", "points": 10},
            {"level": "poor", "description": "Poor", "points": 0},
        ],
        "model_answer": "A model essay.",
    }
]


def make_user(email, user_type=UserTypes.TEACHER, **extra):
    fields = {"is_active": True, "email_verified_at": timezone.now(), **extra}
    return User.objects.create_user(
        email=email,
        password=PW,
        first_name="Hist",
        last_name="Ory",
        user_type=user_type,
        **fields,
    )


def events(action):
    return AuditEvent.objects.filter(action=action)


def everything_recorded():
    """Every stored event, whole, as one string: for the PII sentinel."""
    return json.dumps(list(AuditEvent.objects.values()), default=str)


class World:
    """A teacher with credits, a school, a course, an essay assignment and a
    graded, unpublished, review-flagged submission whose answers and
    feedback carry the PII sentinel."""

    def __init__(self, prefix, graded=True):
        self.school = School.objects.create(name=f"{prefix} School")
        self.teacher = make_user(f"{prefix}.teacher@example.com", school=self.school)
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="History", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="Essay",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=ESSAY,
        )
        self.student = self.new_student(prefix)
        self.submission = self.new_submission(self.student, graded=graded)

    def new_student(self, prefix):
        return make_user(
            f"{prefix}.student@example.com", UserTypes.STUDENT, school=self.school
        )

    def new_submission(self, student, graded=True):
        fields = {}
        if graded:
            fields = {
                "score": Decimal("15.00"),
                "score_percentage": Decimal("75.00"),
                "max_points": 20,
                "graded_at": timezone.now(),
                "needs_review": True,
            }
        return StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": f"<p>{SENTINEL}</p>"}],
            feedback={"note": SENTINEL},
            **fields,
        )


# ---------------------------------------------------------------- registry


class RegistryTests(TestCase):
    def test_every_tracked_field_is_allowed_for_its_action_and_real(self):
        self.assertEqual(history.registry_problems(), [])

    def test_no_recorded_key_names_a_person_or_their_work(self):
        for action, keys in BEFORE_AFTER_ALLOWLIST.items():
            for key in keys:
                for fragment in FORBIDDEN_FRAGMENTS:
                    self.assertNotIn(fragment, key, f"{action}: {key}")

    def test_text_credentials_and_token_epoch_are_never_tracked(self):
        never = {
            "token_epoch",
            "password",
            "email",
            "first_name",
            "last_name",
            "answers",
            "raw_input",
            "feedback",
            "ai_feedback",
            "formatted_grade",
            "review_reasons",
            "ai_summary",
            "activation_token",
        }
        for spec in history.REGISTRY:
            self.assertFalse(set(spec.fields) & never, spec.model)

    def test_every_history_action_has_its_allow_lists(self):
        for spec in history.REGISTRY:
            self.assertIn(spec.action, BEFORE_AFTER_ALLOWLIST)
            self.assertTrue(
                {"changed_fields", "source"} <= METADATA_ALLOWLIST[spec.action],
                spec.action,
            )
            self.assertTrue(set(spec.metadata) <= METADATA_ALLOWLIST[spec.action])

    def test_an_action_without_an_entry_carries_no_before_or_after(self):
        from audit.emitter import emit

        event = emit(
            AuditAction.TAG_CREATE,
            target_type="Tag",
            before={"score": 1},
            after={"score": 2},
        )
        self.assertEqual((event.before, event.after), ({}, {}))


# ---------------------------------------------------------------- grades


class GradeChangeRouteTests(APITestCase):
    def setUp(self):
        self.world = World("grade")
        self.submission = self.world.submission
        self.client.force_authenticate(user=self.world.teacher)

    def the_grade_change(self):
        found = events(AuditAction.GRADE_CHANGE)
        self.assertEqual(found.count(), 1)
        self.assertFalse(events(AuditAction.STATE_CHANGE).exists())
        event = found.get()
        self.assertEqual(event.actor_id, self.world.teacher.id)
        self.assertEqual(event.target_type, "StudentSubmission")
        self.assertEqual(event.target_id, self.submission.id)
        self.assertEqual(event.school_id, self.world.school.id)
        self.assertEqual(event.retention_class, "STUDENT_RECORD")
        self.assertEqual(event.metadata["assignment_id"], str(self.world.assignment.id))
        self.assertEqual(event.metadata["student_id"], str(self.world.student.id))
        return event

    def test_update_grade_records_the_score_it_replaced(self):
        response = self.client.patch(
            reverse(
                "student-submission-update-grade", kwargs={"pk": self.submission.pk}
            ),
            data={"score": 10},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = self.the_grade_change()
        self.assertEqual(event.metadata["source"], "save")
        self.assertEqual(event.before["score"], "15.00")
        self.assertEqual(event.after["score"], "10.00")
        self.assertEqual(
            (event.before["needs_review"], event.after["needs_review"]), (True, False)
        )
        self.assertEqual(set(event.before), set(event.metadata["changed_fields"]))
        self.assertLessEqual(
            set(event.after), BEFORE_AFTER_ALLOWLIST[AuditAction.GRADE_CHANGE]
        )
        self.assertNotIn(SENTINEL, everything_recorded())

    def test_publish_records_the_flip_once(self):
        url = reverse(
            "student-submission-publish-grade", kwargs={"pk": self.submission.pk}
        )
        with patch("students.views.notify_student_of_graded_submission"):
            self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)
            self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)

        event = self.the_grade_change()
        self.assertEqual(event.metadata["source"], "bulk")
        self.assertEqual(
            (event.before, event.after),
            ({"is_published": False}, {"is_published": True}),
        )
        self.assertEqual(event.metadata["changed_fields"], ["is_published"])

    def test_mark_reviewed_records_needs_review_only(self):
        """SM R1: the actor is the 'by', the event time the 'at'; the
        resolution text in review_reasons is never recorded."""
        url = reverse(
            "student-submission-mark-reviewed", kwargs={"pk": self.submission.pk}
        )
        self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)

        event = self.the_grade_change()
        self.assertEqual(
            (event.before, event.after),
            ({"needs_review": True}, {"needs_review": False}),
        )
        self.assertNotIn("confirmed", everything_recorded())

    def test_an_untracked_or_unchanged_save_records_nothing(self):
        self.submission.formatted_grade = SENTINEL
        self.submission.save(update_fields=["formatted_grade"])
        self.submission.refresh_from_db()
        self.submission.save()
        self.assertFalse(events(AuditAction.GRADE_CHANGE).exists())

    def test_a_deleted_submission_is_a_grade_change_with_no_after(self):
        """SM R5."""
        with request_audit_state(SimpleNamespace(user=self.world.teacher)):
            self.submission.delete()

        event = self.the_grade_change()
        self.assertEqual(event.metadata["source"], "delete")
        self.assertIsNone(event.after)
        self.assertEqual(event.before["score"], "15.00")
        self.assertEqual(event.before["is_published"], False)
        self.assertNotIn(SENTINEL, everything_recorded())

    def test_the_grade_is_saved_when_the_audit_store_is_down(self):
        """Gate 5 / FR-A-11."""
        with patch.object(
            AuditEvent.objects, "create", side_effect=RuntimeError("down")
        ):
            response = self.client.patch(
                reverse(
                    "student-submission-update-grade", kwargs={"pk": self.submission.pk}
                ),
                data={"score": 10},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.score, Decimal("10.00"))


class PublishAllTests(APITestCase):
    def test_publish_all_gives_one_grade_change_per_student_published(self):
        """D3: 30 students published, 30 events; an already-published and
        an ungraded submission give none."""
        world = World("publish-all")
        world.submission.is_published = True
        world.submission.save(update_fields=["is_published"])
        published_before = set(AuditEvent.objects.values_list("pk", flat=True))
        fresh = [
            world.new_submission(world.new_student(f"pa{index}")).id
            for index in range(30)
        ]
        world.new_submission(world.new_student("pa-ungraded"), graded=False)
        self.client.force_authenticate(user=world.teacher)

        with patch("assignments.views.notify_student_of_graded_submission"):
            response = self.client.post(
                reverse(
                    "assignment-publish-all-grades", kwargs={"pk": world.assignment.pk}
                )
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        new = events(AuditAction.GRADE_CHANGE).exclude(pk__in=published_before)
        self.assertEqual(sorted(new.values_list("target_id", flat=True)), sorted(fresh))
        for event in new:
            self.assertEqual(event.actor_id, world.teacher.id)
            self.assertEqual(event.after, {"is_published": True})
        self.assertFalse(events(AuditAction.STATE_CHANGE).exists())


# ---------------------------------------------------------------- AI grading


def _ai_response(payload):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = "primary-model"
    return response


def _grader(**kwargs):
    return _ai_response(
        {
            "question_evaluations": [
                {
                    "question_number": 1,
                    "question_text": "Discuss.",
                    "score_awarded": 15,
                    "max_points": 20,
                    "level_achieved": "good",
                    "evaluation_rationale": "Because.",
                    "evidence_quotes": ["words"],
                }
            ],
            "grading_summary": {},
            "grading_confidence": 95,
            "overall_performance_analysis": "ok",
            "recommendations": [],
        }
    )


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class AIGradingTests(APITestCase):
    """SM note 2 and R2: one AI grading gives exactly one GRADING_COMPLETED
    carrying the grade's before/after, and zero GRADE_CHANGE - on the
    synchronous route and on the Celery task."""

    def setUp(self):
        self.world = World("ai", graded=False)
        self.submission = self.world.submission

    def the_completed_event(self):
        self.assertFalse(events(AuditAction.GRADE_CHANGE).exists())
        found = events(AuditAction.GRADING_COMPLETED)
        self.assertEqual(found.count(), 1)
        event = found.get()
        self.assertEqual(event.after["score"], "15.00")
        self.assertIn("graded_at", event.after)
        self.assertIsNone(event.before["graded_at"])
        self.assertLessEqual(
            set(event.after), BEFORE_AFTER_ALLOWLIST[AuditAction.GRADING_COMPLETED]
        )
        self.assertNotIn(SENTINEL, everything_recorded())
        return event

    def test_the_synchronous_route_records_one_completed_event_for_the_requester(self):
        self.client.force_authenticate(user=self.world.teacher)
        with patch.object(AIProcessor, "execute_graded_task", side_effect=_grader):
            response = self.client.post(
                reverse("student-submission-grade", kwargs={"pk": self.submission.pk})
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = self.the_completed_event()
        self.assertEqual(event.actor_id, self.world.teacher.id)
        # R2's condition: it replaces S1's generic event for the request.
        self.assertFalse(events(AuditAction.STATE_CHANGE).exists())

    def test_the_celery_task_records_one_completed_event(self):
        with patch.object(AIProcessor, "execute_graded_task", side_effect=_grader):
            outcome = grade_engine_async.apply(
                args=(str(self.world.teacher.id), str(self.submission.id))
            )

        self.assertTrue(outcome.successful(), outcome.result)
        self.the_completed_event()


# ---------------------------------------------------------------- roster


@override_settings(CACHES=LOCMEM_CACHE)
class SignInActivationTests(APITestCase):
    """SM R3: AUTH_LOGIN plus one ROSTER_CHANGE per activated enrolment,
    naming the student who signed in; a second sign-in records nothing."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)
        self.world = World("signin", graded=False)
        self.courses = [self.world.course] + [
            Course.objects.create(
                name=f"Extra {index}",
                teacher=self.world.teacher,
                session=self.world.course.session,
            )
            for index in range(1)
        ]

    def pending(self, student):
        for course in self.courses:
            StudentCourse.objects.create(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.PENDING,
            )

    def activations(self):
        return events(AuditAction.ROSTER_CHANGE).filter(metadata__source="bulk")

    def assert_activated_by(self, student):
        found = self.activations()
        self.assertEqual(found.count(), len(self.courses))
        for event in found:
            self.assertEqual(event.actor_id, student.id)
            self.assertEqual(event.actor_role, ActorRole.STUDENT)
            self.assertEqual(event.before, {"enrollment_status": "PENDING"})
            self.assertEqual(event.after, {"enrollment_status": "ENROLLED"})
            self.assertEqual(event.metadata["student_id"], str(student.id))
        self.assertFalse(events(AuditAction.STATE_CHANGE).exists())

    def test_a_password_sign_in_activates_and_names_the_student(self):
        student = self.world.student
        self.pending(student)

        def sign_in(ip):
            return APIClient_post(
                self.client,
                reverse("login"),
                {"email": student.email, "password": PW},
                ip,
            )

        self.assertEqual(sign_in("10.4.0.1").status_code, 200)
        self.assert_activated_by(student)
        self.assertEqual(events(AuditAction.AUTH_LOGIN).count(), 1)

        self.assertEqual(sign_in("10.4.0.2").status_code, 200)
        self.assertEqual(self.activations().count(), len(self.courses))

    def test_a_google_sign_in_activates_and_names_the_student(self):
        """Google finishing an unverified student's registration: one
        ROSTER_CHANGE per promoted enrolment, and the account's own
        is_active flip - all naming the student (SM rulings)."""
        student = make_user(
            "signin.google@gmail.com",
            UserTypes.STUDENT,
            school=self.world.school,
            is_active=False,
            email_verified_at=None,
        )
        self.pending(student)

        with patch("requests.post") as post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as verify:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "expires_in": 3600,
            }
            verify.return_value = {
                "email": student.email,
                "email_verified": True,
                "given_name": "Goo",
                "family_name": "Gle",
            }
            response = self.client.post(
                reverse("auth-google-auth"), {"code": "oauth-code"}, format="json"
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assert_activated_by(student)
        flip = events(AuditAction.PERMISSION_CHANGE).get(target_id=student.id)
        self.assertEqual(flip.actor_id, student.id)
        self.assertEqual(
            (flip.before["is_active"], flip.after["is_active"]), (False, True)
        )


def APIClient_post(client, url, data, ip):
    return client.post(url, data, format="json", REMOTE_ADDR=ip)


# ---------------------------------------------------------------- users


@override_settings(CACHES=LOCMEM_CACHE)
class PermissionChangeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

    def test_email_verification_names_the_account_that_proved_the_code(self):
        """SM ruling: the activation is the account's own action."""
        user = make_user(
            "perm.verify@example.com",
            is_active=False,
            email_verified_at=None,
            activation_token="123456",
            activation_expires=timezone.now() + timedelta(minutes=15),
        )
        response = self.client.post(
            reverse("auth-verify"),
            {"email": user.email, "token": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        flip = events(AuditAction.PERMISSION_CHANGE).get()
        self.assertEqual(flip.actor_id, user.id)
        self.assertEqual(flip.target_id, user.id)
        self.assertEqual(
            (flip.before, flip.after), ({"is_active": False}, {"is_active": True})
        )

    def test_admin_actions_record_each_user_they_change(self):
        admin = User.objects.create_superuser(
            email="perm.admin@example.com", password=PW
        )
        active = make_user("perm.active@example.com")
        inactive = make_user("perm.inactive@example.com", is_active=False)
        before = set(AuditEvent.objects.values_list("pk", flat=True))
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin:users_customuser_changelist"),
            {
                "action": "deactivate_users",
                "_selected_action": [str(active.pk), str(inactive.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        new = events(AuditAction.PERMISSION_CHANGE).exclude(pk__in=before)
        self.assertEqual(list(new.values_list("target_id", flat=True)), [active.id])
        event = new.get()
        self.assertEqual(event.actor_id, admin.id)
        self.assertEqual(
            (event.before, event.after), ({"is_active": True}, {"is_active": False})
        )
        self.assertFalse(
            events(AuditAction.STATE_CHANGE).exclude(pk__in=before).exists()
        )

    def test_a_role_change_is_recorded_and_a_session_revoke_is_not(self):
        user = make_user("perm.role@example.com")
        user.user_type = UserTypes.SCHOOL_ADMIN
        user.save()
        user.revoke_all_sessions()
        user.set_password("Another-pw-2")  # pragma: allowlist secret
        user.save()

        found = events(AuditAction.PERMISSION_CHANGE)
        self.assertEqual(found.count(), 1)
        self.assertEqual(
            (found.get().before, found.get().after),
            ({"user_type": "TEACHER"}, {"user_type": "SCHOOL_ADMIN"}),
        )

    def test_only_a_privileged_account_is_recorded_when_created(self):
        """SM ruling 4: ordinary accounts are covered by ACCOUNT_REGISTER
        and the roster flows."""
        make_user("perm.teacher@example.com")
        make_user("perm.student@example.com", UserTypes.STUDENT)
        admin = User.objects.create_superuser(
            email="perm.super@example.com", password=PW
        )

        created = events(AuditAction.PERMISSION_CHANGE).get()
        self.assertEqual(created.target_id, admin.id)
        self.assertIsNone(created.before)
        self.assertTrue(created.after["is_superuser"])

    def test_a_deleted_user_keeps_only_allow_listed_fields(self):
        """SM R5's condition: role, status and ids only - never an email,
        a name or a password hash."""
        school = School.objects.create(name="Delete School")
        user = make_user("perm.deleted@example.com", school=school)
        password_hash = user.password

        user.delete()

        event = events(AuditAction.PERMISSION_CHANGE).get()
        self.assertIsNone(event.after)
        self.assertLessEqual(
            set(event.before), BEFORE_AFTER_ALLOWLIST[AuditAction.PERMISSION_CHANGE]
        )
        self.assertEqual(event.before["school_id"], str(school.id))
        recorded = everything_recorded()
        for private in ("perm.deleted@example.com", "Hist", "Ory", password_hash):
            self.assertNotIn(private, recorded)


# ---------------------------------------------------------------- subscriptions


class SubscriptionChangeTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Licence School")
        self.admin = make_user(
            "sub.admin@example.com", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="S4 Licence",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.licence = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=plan,
            max_seats=5,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )

    def test_a_seat_count_change_is_recorded(self):
        self.licence.max_seats = 8
        self.licence.save(update_fields=["max_seats"])

        event = events(AuditAction.SUBSCRIPTION_CHANGE).get(metadata__source="save")
        self.assertEqual(event.target_type, "LicenseSubscription")
        self.assertEqual(event.school_id, self.school.id)
        self.assertEqual(
            (event.before, event.after), ({"max_seats": 5}, {"max_seats": 8})
        )

    def test_a_seat_taken_and_released_is_recorded(self):
        """SM R4: an active allocation is a seat in use."""
        teacher = make_user("sub.teacher@example.com", school=self.school)
        seat = SchoolCreditAllocation.objects.create(
            license_subscription=self.licence,
            user=teacher,
            monthly_allocation=1000,
            is_active=True,
        )
        seat.is_active = False
        seat.save()

        seats = events(AuditAction.SUBSCRIPTION_CHANGE).filter(
            target_type="SchoolCreditAllocation"
        )
        self.assertEqual(
            sorted(
                (e.metadata["source"], json.dumps(e.after, sort_keys=True))
                for e in seats
            ),
            sorted(
                [
                    (
                        "create",
                        json.dumps(
                            {"is_active": True, "license_id": str(self.licence.id)},
                            sort_keys=True,
                        ),
                    ),
                    ("save", json.dumps({"is_active": False}, sort_keys=True)),
                ]
            ),
        )


# ---------------------------------------------------------------- record_bulk


class RecordBulkTests(TestCase):
    def setUp(self):
        self.world = World("bulk")

    def test_it_returns_the_update_count_and_skips_unchanged_rows(self):
        other = self.world.new_submission(self.world.new_student("bulk2"))
        other.is_published = True
        other.save(update_fields=["is_published"])
        before = set(AuditEvent.objects.values_list("pk", flat=True))

        count = history.record_bulk(
            StudentSubmission.objects.filter(assignment=self.world.assignment),
            is_published=True,
        )

        self.assertEqual(count, 2)
        new = events(AuditAction.GRADE_CHANGE).exclude(pk__in=before)
        self.assertEqual(
            list(new.values_list("target_id", flat=True)), [self.world.submission.id]
        )

    def test_an_untracked_bulk_write_records_nothing(self):
        history.record_bulk(
            StudentSubmission.objects.filter(pk=self.world.submission.pk),
            grading_state="RUNNING",
        )
        self.assertFalse(events(AuditAction.GRADE_CHANGE).exists())

    def test_the_actor_is_keyword_only_and_used_as_given(self):
        with self.assertRaises(TypeError):
            history.record_bulk(  # type: ignore[misc]
                StudentSubmission.objects.all(), self.world.teacher
            )
        history.record_bulk(
            StudentSubmission.objects.filter(pk=self.world.submission.pk),
            actor=self.world.student,
            is_published=True,
        )
        self.assertEqual(
            events(AuditAction.GRADE_CHANGE).get().actor_id, self.world.student.id
        )

    def test_a_distinct_or_annotated_queryset_is_accepted(self):
        """Postgres refuses FOR UPDATE with DISTINCT or an aggregate; the
        admin changelist can hand either over (v2's early warning)."""
        from django.db.models import Count

        distinct = StudentSubmission.objects.filter(
            assignment__course=self.world.course
        ).distinct()
        annotated = StudentSubmission.objects.filter(
            pk=self.world.submission.pk
        ).annotate(n=Count("assignment"))

        self.assertEqual(history.record_bulk(distinct, is_published=True), 1)
        self.assertEqual(history.record_bulk(annotated, needs_review=False), 1)
        self.assertEqual(events(AuditAction.GRADE_CHANGE).count(), 2)

    def test_an_untracked_model_is_refused(self):
        with self.assertRaises(ValueError):
            history.record_bulk(Course.objects.all(), name="x")


class ActorRuleTests(TestCase):
    def test_an_authenticated_request_names_its_user_not_the_system(self):
        """SM R6's guard: this stays red while the actor is a SYSTEM shim."""
        user = make_user("actor.rule@example.com")
        with request_audit_state(SimpleNamespace(user=user)):
            self.assertEqual(history._actor(), user)

    def test_outside_a_request_the_actor_is_the_system(self):
        self.assertIsNone(history._actor())

    def test_acting_as_names_the_given_user_for_the_block_only(self):
        user = make_user("actor.acting@example.com")
        with history.acting_as(user):
            self.assertEqual(history._actor(), user)
        self.assertIsNone(history._actor())


# ---------------------------------------------------------------- Gate 3


class ConcurrentGradeWritesTests(TransactionTestCase):
    """A publish racing an update-grade on the same submission: each writer
    records its own change, with before/after that match what it wrote."""

    def test_a_publish_and_a_score_save_each_record_their_own_change(self):
        world = World("race")
        submission_id = world.submission.id
        barrier = threading.Barrier(2, timeout=30)
        errors = []

        def publish():
            try:
                barrier.wait()
                history.record_bulk(
                    StudentSubmission.objects.filter(
                        pk=submission_id, is_published=False
                    ),
                    is_published=True,
                )
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                connection.close()

        def rescore():
            try:
                copy = StudentSubmission.objects.get(pk=submission_id)
                barrier.wait()
                copy.score = Decimal("12.00")
                copy.save(update_fields=["score"])
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=publish), threading.Thread(target=rescore)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [])
        changes = {
            tuple(e.metadata["changed_fields"]): (e.before, e.after)
            for e in events(AuditAction.GRADE_CHANGE)
        }
        self.assertEqual(
            changes,
            {
                ("is_published",): ({"is_published": False}, {"is_published": True}),
                ("score",): ({"score": "15.00"}, {"score": "12.00"}),
            },
        )
