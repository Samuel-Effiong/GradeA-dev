"""Epic B1b-7 D7-e (08b §1.6, plan §9 item 9; NOTES §7.8-§7.9, ruling
§7.19): `ai_job_id` on the in-request AI views.

The rule (§7.19): `ai_job_id` is the id of the ONE job this request
settled. FAILED attempts (their hold returned, nothing charged) do not
count, so a retry that ends in one settlement names that job. More than one
settled job (chunked extraction), or none, gives null. The key is always
present on a success body, except a student's (§7.25: students upload
answers has none; students partial_update leaves it out for a student
caller). `upload_assignment` puts it on each
`successful[]` item, one job per file; failed items carry none.

The view tests stand in for each view's AI engine with a real billed call
through execute_graded_task (the provider mocked), so the request settles a
real job and the test checks the view hands back its id.

    python manage.py test billing.tests.test_b1b7_ai_job_id
"""

import importlib
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor, ai_processor
from assignments.models import Assignment, AssignmentStatus
from assignments.services import AssignmentProcessingService
from assignments.tests_upload_batch_billing import png
from billing.models import AIJob, AIJobStatus, CreditWallet
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    fresh,
    real_billed_call,
    remaining,
)
from classrooms.models import Course, EnrollmentStatusType, School, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

PROVIDER = "_AIProcessor__ai_model"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ABSENT = "absent"
TOKENS = 1_000
CREDITS = 10_000_000


def holds():
    return importlib.import_module("billing.holds")


def job_ref():
    return importlib.import_module("billing.job_ref")


def user(tag, user_type, **extra):
    return CustomUser.objects.create_user(
        email=f"b1b7.jobid.{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=user_type,
        is_active=True,
        **extra,
    )


def settled_ids(teacher):
    return [
        str(pk)
        for pk in AIJob.objects.filter(wallet__user=teacher, status=AIJobStatus.SETTLED)
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)
    ]


@override_settings(BILLING_HOLDS_ENABLED=True, CACHES=LOCMEM)
class ViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = ai_ready_teacher("b1b7.jobid.teacher@example.com", CREDITS)
        self.course = Course.objects.create(name="B1b-7 C", teacher=self.teacher)
        self.student = user("student", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment = Assignment.objects.create(
            title="B1b-7 A",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "2 + 2?",
                    "points": 10,
                    "model_answer": "4",
                }
            ],
        )
        provider = patch.object(AIProcessor, PROVIDER, return_value=ai_response(TOKENS))
        provider.start()
        self.addCleanup(provider.stop)

    def bill(self, unit):
        """One real billed call for the teacher, inside a metered unit (as
        the grading and answer engines run) or as a single call."""
        if unit:
            with holds().metered_unit():
                real_billed_call(self.teacher)
        else:
            real_billed_call(self.teacher)

    def submission(self):
        return StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "4"}],
            attempt_count=1,
        )

    def call(self, who, method, url, data, fmt="json"):
        self.client.force_authenticate(user=who)
        return getattr(self.client, method)(url, data, format=fmt)

    def assertNamesTheSettledJob(self, response):
        self.assertLess(response.status_code, 300, response.content[:400])
        ids = settled_ids(self.teacher)
        self.assertEqual(len(ids), 1, "fixture: one job settled")
        self.assertEqual(response.data.get("ai_job_id", ABSENT), ids[0])

    def assertNoJobIdForAStudent(self, response):
        self.assertLess(response.status_code, 300, response.content[:400])
        self.assertEqual(len(settled_ids(self.teacher)), 1, "fixture: one job settled")
        self.assertNotIn("ai_job_id", response.data)

    # --- students ---------------------------------------------------------

    def test_students_grade(self):
        submission = self.submission()

        def engine(_user, sub):
            self.bill(unit=True)
            return sub

        with patch("students.views.grade_engine", side_effect=engine):
            response = self.call(
                self.teacher,
                "post",
                reverse("student-submission-grade", args=[str(submission.id)]),
                {},
            )
        self.assertNamesTheSettledJob(response)

    def test_students_upload_answers(self):
        def engine(assignment, _content, student):
            self.bill(unit=True)
            return StudentSubmission.objects.create(
                assignment=assignment,
                student=student,
                answers=[{"question_number": 1, "answer_html": "4"}],
                attempt_count=1,
            )

        answer = SimpleUploadedFile("answer.png", png(200), content_type="image/png")
        with patch(
            "students.views.upload_answers_engine", side_effect=engine
        ), patch.object(
            AssignmentProcessingService, "prepare_ai_content", return_value=[]
        ):
            response = self.call(
                self.student,
                "post",
                reverse(
                    "student-submission-upload-answers", args=[str(self.assignment.id)]
                ),
                {"answer": answer},
                fmt="multipart",
            )
        self.assertNoJobIdForAStudent(response)

    def edit(self, who):
        submission = self.submission()

        def engine(_user, sub, _raw):
            self.bill(unit=True)
            return sub

        with patch(
            "students.views.update_submission_from_raw_text", side_effect=engine
        ):
            return self.call(
                who,
                "patch",
                reverse("student-submission-detail", args=[str(submission.id)]),
                {"raw_input": "4"},
            )

    def test_students_partial_update_by_the_teacher(self):
        self.assertNamesTheSettledJob(self.edit(self.teacher))

    def test_students_partial_update_by_the_student_has_no_job_id(self):
        # §7.25: a student never sees a billing job's id.
        self.assertNoJobIdForAStudent(self.edit(self.student))

    # --- assignments ------------------------------------------------------

    def extraction(self):
        def engine(_user, assignment, _content, **_kwargs):
            self.bill(unit=False)
            return assignment

        return patch.object(
            AssignmentProcessingService,
            "update_assignment_from_extraction",
            side_effect=engine,
        )

    def test_assignments_partial_update(self):
        with self.extraction():
            response = self.call(
                self.teacher,
                "patch",
                reverse("assignment-detail", args=[str(self.assignment.id)]),
                {"raw_input": "Question 1: 2 + 2?"},
            )
        self.assertNamesTheSettledJob(response)

    def test_assignments_create(self):
        with self.extraction():
            response = self.call(
                self.teacher,
                "post",
                reverse("assignment-list"),
                {
                    "title": "B1b-7 created",
                    "course": str(self.course.id),
                    "raw_input": "Question 1: 2 + 2?",
                },
            )
        self.assertNamesTheSettledJob(response)

    def test_assignments_generate_assignment_from_prompt(self):
        def engine(*_args, **_kwargs):
            self.bill(unit=False)
            return {
                "needs_clarification": True,
                "self_assessment": "<p>Which topic?</p>",
                "questions": [],
            }

        with patch.object(
            ai_processor,
            "generate_assignment_from_prompt_with_retry",
            side_effect=engine,
        ):
            response = self.call(
                self.teacher,
                "post",
                reverse("assignment-generate", args=[str(self.course.id)]),
                {"prompt": "A short quiz on addition"},
            )
        self.assertNamesTheSettledJob(response)

    def upload(self, names):
        def engine(_user, uploaded_file, _prompt, **_kwargs):
            if uploaded_file.name.startswith("bad"):
                raise ValueError("unreadable")
            self.bill(unit=True)
            return SimpleNamespace(
                assignment=Assignment.objects.create(
                    course=self.course, title=uploaded_file.name
                ),
                already_uploaded=False,
            )

        files = [
            SimpleUploadedFile(name, png(200 + i), content_type="image/png")
            for i, name in enumerate(names)
        ]
        with patch("assignments.views.upload_assignment_file", side_effect=engine):
            return self.call(
                self.teacher,
                "post",
                reverse("assignment-upload"),
                {"course": str(self.course.id), "assignments": files},
                fmt="multipart",
            )

    def test_assignments_upload_two_files_give_two_ids(self):
        response = self.upload(["one.png", "two.png"])
        self.assertEqual(response.status_code, 201, response.content[:400])
        ids = settled_ids(self.teacher)
        self.assertEqual(len(ids), 2, "fixture: one job per file")
        self.assertEqual([item.get("ai_job_id", ABSENT) for item in response.data], ids)
        self.assertNotEqual(ids[0], ids[1])

    def test_assignments_upload_207_only_successful_items_have_it(self):
        response = self.upload(["good.png", "bad.png"])
        self.assertEqual(response.status_code, 207, response.content[:400])
        ids = settled_ids(self.teacher)
        self.assertEqual(len(ids), 1, "fixture: one file settled")
        self.assertEqual(
            response.data["successful"][0].get("ai_job_id", ABSENT), ids[0]
        )
        self.assertNotIn("ai_job_id", response.data["failed"][0])
        self.assertEqual(set(response.data), {"successful", "failed", "summary"})

    # --- the chat assistants ----------------------------------------------

    def chat(self, who, url_name, target):
        def engine(*_args, **_kwargs):
            self.bill(unit=False)
            return "ok"

        with patch.object(target, "custom_ai_prompt_retry", side_effect=engine):
            return self.call(
                who, "post", reverse(url_name), {"prompt": "How is it going?"}
            )

    def test_billing_custom_ai_prompt(self):
        superadmin = user("super", UserTypes.SUPER_ADMIN, is_superuser=True)
        response = self.chat(superadmin, "analytics-custom-ai-prompt", ai_processor)
        self.assertNamesTheSettledJob(response)

    def test_dashboard_super_admin_custom_ai_prompt(self):
        superadmin = user("super2", UserTypes.SUPER_ADMIN, is_superuser=True)
        response = self.chat(superadmin, "dashboard-custom-ai-prompt", ai_processor)
        self.assertNamesTheSettledJob(response)

    def test_dashboard_school_admin_custom_ai_prompt(self):
        school = School.objects.create(name="B1b-7 school")
        admin = user("schooladmin", UserTypes.SCHOOL_ADMIN, school=school)
        response = self.chat(admin, "school-admin-custom-ai-prompt", ai_processor)
        self.assertNamesTheSettledJob(response)

    def test_dashboard_teacher_custom_ai_prompt(self):
        response = self.chat(
            self.teacher, "teacher-admin-custom-ai-prompt", ai_processor
        )
        self.assertNamesTheSettledJob(response)


@override_settings(BILLING_HOLDS_ENABLED=True)
class RuleTests(TestCase):
    """The rule itself, through the real execute_graded_task outside any
    unit, where each provider call opens its own SINGLE_CALL job."""

    def setUp(self):
        self.teacher = ai_ready_teacher("b1b7.jobid.rule@example.com", CREDITS)

    def calls(self, outcomes):
        """One call per outcome: a response, or an exception the provider
        raises (the call then fails and its hold is returned)."""
        with patch.object(AIProcessor, PROVIDER, side_effect=outcomes):
            for _ in outcomes:
                try:
                    call_ai(self.teacher)
                except RuntimeError:
                    pass

    def jobs(self, status):
        return AIJob.objects.filter(wallet__user=self.teacher, status=status)

    def test_a_failed_attempt_carries_no_charge(self):
        # §7.19 (5): confirmed before the rule relies on it.
        before = remaining(CreditWallet.objects.get(user=self.teacher))
        self.calls([RuntimeError("provider down")])
        job = self.jobs(AIJobStatus.FAILED).get()
        self.assertEqual(job.consumed_credits, 0)
        self.assertEqual(self.jobs(AIJobStatus.SETTLED).count(), 0)
        wallet = fresh(CreditWallet.objects.get(user=self.teacher))
        self.assertEqual(wallet.reserved_credits, 0, "the hold was not returned")
        self.assertEqual(remaining(wallet), before, "a failed attempt was charged")

    def test_a_retry_then_success_names_the_settled_job(self):
        with job_ref().tracking() as jobs:
            self.calls([RuntimeError("provider down"), ai_response(TOKENS)])
        self.assertEqual(self.jobs(AIJobStatus.FAILED).count(), 1, "fixture")
        settled = self.jobs(AIJobStatus.SETTLED).get()
        self.assertEqual(job_ref().settled_job_id(jobs), str(settled.pk))

    def test_all_attempts_failed_is_null(self):
        with job_ref().tracking() as jobs:
            self.calls([RuntimeError("down"), RuntimeError("still down")])
        self.assertEqual(self.jobs(AIJobStatus.FAILED).count(), 2, "fixture")
        self.assertIsNone(job_ref().settled_job_id(jobs))

    def test_two_settled_jobs_chunked_is_null(self):
        with job_ref().tracking() as jobs:
            self.calls([ai_response(TOKENS), ai_response(TOKENS)])
        self.assertEqual(self.jobs(AIJobStatus.SETTLED).count(), 2, "fixture")
        self.assertIsNone(job_ref().settled_job_id(jobs))
