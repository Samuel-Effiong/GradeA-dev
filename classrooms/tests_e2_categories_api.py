"""Epic E2: the grade-category API (plan §4) and the assignment category
fields on every door that makes or edits an assignment (Q3, W4, W6).

Wording is the owner-approved text (OWNER_SHEET W1-W13), asserted verbatim.
"""

import threading
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient, APITestCase

from assignments.category_fields import resolve_upload_category
from assignments.file_uploads import upload_assignment_file
from assignments.models import Assignment
from audit.enums import AuditAction
from audit.models import AuditEvent
from classrooms import grade_categories
from classrooms.models import (
    CategoryTemplate,
    CategoryTemplateOwner,
    Course,
    CourseCategory,
    School,
)
from users.models import UserTypes

from .tests_e2_fixtures import (
    categories_off,
    categories_on,
    enroll,
    fund_wallet,
    grade,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
    make_user,
    past,
    stored_grade,
)

D = Decimal
W1_90 = "Category weights must add up to 100%. Right now they add up to 90%."
W5_ONE = (
    "1 assignments have no category, so they don't count toward the grade. "
    "Choose a category for each one."
)
W6 = "Choose a category for this assignment. This course uses grade categories."
W11 = "This student isn't in this course."


def _errors(response):
    """Every message string in an error body, however the renderer nests it."""
    found = []

    def walk(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(response.data)
    walk(getattr(response, "json", lambda: {})() if response.content else {})
    return found


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


class _Api(APITestCase):
    def setUp(self):
        self.teacher = make_teacher()
        self.course = make_course(self.teacher)
        self.client.force_authenticate(self.teacher)

    def put_categories(self, items, course=None):
        return self.client.put(
            f"/api/v1/course/{(course or self.course).pk}/categories",
            {"categories": items},
            format="json",
        )

    def set_categories(self):
        grade_categories.set_course_categories(
            self.course,
            [
                {"name": "Exam", "weight_percent": "60"},
                {"name": "Homework", "weight_percent": "40"},
            ],
            actor=self.teacher,
        )
        self.exam = CourseCategory.objects.get(course=self.course, name="Exam")
        self.hw = CourseCategory.objects.get(course=self.course, name="Homework")


ROUTES = [
    ("get", "/api/v1/course/{course}/categories"),
    ("put", "/api/v1/course/{course}/categories"),
    ("post", "/api/v1/course/{course}/categories/preview"),
    ("get", "/api/v1/course/{course}/grade-breakdown"),
    ("get", "/api/v1/course/{course}/grading-policy"),
    ("patch", "/api/v1/course/{course}/grading-policy"),
    ("post", "/api/v1/assignments/{assignment}/excused"),
    ("delete", "/api/v1/assignments/{assignment}/excused"),
    ("get", "/api/v1/category-templates"),
    ("post", "/api/v1/category-templates"),
    ("get", "/api/v1/category-templates/{template}"),
]


@categories_off
class DarkRoutesTest(_Api):
    def test_every_route_answers_as_an_unknown_url_while_dark(self):
        assignment = make_assignment(self.course)
        for client in (self.client, APIClient()):
            for method, route in ROUTES:
                url = route.format(
                    course=self.course.pk,
                    assignment=assignment.pk,
                    template=uuid.uuid4(),
                )
                response = getattr(client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 404, f"{method} {url}")

    def test_course_and_assignment_payloads_carry_no_new_field(self):
        assignment = make_assignment(self.course)
        detail = self.client.get(f"/api/v1/assignments/{assignment.pk}")
        self.assertNotIn("category", detail.data)
        self.assertNotIn("relative_weight", detail.data)
        course = self.client.get(f"/api/v1/course/{self.course.pk}")
        self.assertNotIn("missing_counts_as_zero", course.data)


@categories_on
class CourseCategoriesApiTest(_Api):
    def test_put_replaces_the_set_and_is_audited(self):
        response = self.put_categories(
            [
                {"name": "Exam", "weight_percent": "60"},
                {"name": "Homework", "weight_percent": "40"},
            ]
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [(c["name"], c["weight_percent"]) for c in response.data["categories"]],
            [("Exam", "60.00"), ("Homework", "40.00")],
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.GRADE_CATEGORIES_CHANGE, actor_id=self.teacher.pk
            ).exists()
        )

    def test_weights_must_add_up_to_100_w1(self):
        response = self.put_categories(
            [
                {"name": "Exam", "weight_percent": "50"},
                {"name": "Homework", "weight_percent": "40"},
            ]
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(W1_90, _errors(response))
        self.assertFalse(CourseCategory.objects.filter(course=self.course).exists())

    def test_names_are_unique_in_a_course_w2(self):
        response = self.put_categories(
            [
                {"name": "Exam", "weight_percent": "50"},
                {"name": " exam ", "weight_percent": "50"},
            ]
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'This course already has a category called "exam".', _errors(response)
        )

    def test_weight_range_w3(self):
        response = self.put_categories(
            [
                {"name": "Exam", "weight_percent": "120"},
                {"name": "Homework", "weight_percent": "-20"},
            ]
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Each category weight must be between 0% and 100%.", _errors(response)
        )

    def test_empty_set_removes_categories_and_keeps_assignments(self):
        self.set_categories()
        assignment = make_assignment(self.course, category=self.exam)
        response = self.put_categories([])
        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.category)
        self.assertFalse(CourseCategory.objects.filter(course=self.course).exists())

    def test_ids_keep_assignments_and_names_can_swap(self):
        self.set_categories()
        assignment = make_assignment(self.course, category=self.exam)
        response = self.put_categories(
            [
                {"id": str(self.exam.pk), "name": "Homework", "weight_percent": "60"},
                {"id": str(self.hw.pk), "name": "Exam", "weight_percent": "40"},
            ]
        )
        self.assertEqual(response.status_code, 200, response.data)
        assignment.refresh_from_db()
        self.assertEqual(assignment.category_id, self.exam.pk)
        self.assertEqual(assignment.category.name, "Homework")

    def test_another_teachers_course_is_not_found(self):
        other = make_course(make_teacher())
        self.assertEqual(self.put_categories([], course=other).status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/v1/course/{other.pk}/categories").status_code, 404
        )

    def test_students_get_nothing(self):
        self.client.force_authenticate(make_student())
        response = self.client.get(f"/api/v1/course/{self.course.pk}/categories")
        self.assertEqual(response.status_code, 403)

    def test_policy_warns_about_uncategorized_work_w5_w13(self):
        self.set_categories()
        make_assignment(self.course, "Loose")
        response = self.client.get(f"/api/v1/course/{self.course.pk}/grading-policy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["warnings"], [W5_ONE])
        self.assertEqual(
            response.data["rules"],
            [
                "Excused work doesn't count toward the grade.",
                # Missing work counts by default (owner 2026-11-19).
                "Missing work counts as 0.",
                "Work that isn't graded yet doesn't count.",
            ],
        )

    def test_policy_patch_sets_the_missing_rule_and_recomputes(self):
        student = make_student()
        enroll(student, self.course)
        grade(student, make_assignment(self.course, due_date=past()), 80)
        make_assignment(self.course, "Missed", due_date=past())
        url = f"/api/v1/course/{self.course.pk}/grading-policy"
        response = self.client.patch(
            url, {"missing_counts_as_zero": False}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["rules"][1],
            "Missing work doesn't count until it's handed in.",
        )
        self.assertEqual(stored_grade(student, self.course), D("80.00"))
        response = self.client.patch(
            url, {"missing_counts_as_zero": True}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["rules"][1], "Missing work counts as 0.")
        self.assertEqual(stored_grade(student, self.course), D("40.00"))

    def test_preview_writes_nothing(self):
        self.set_categories()
        student = make_student()
        enroll(student, self.course)
        grade(student, make_assignment(self.course, category=self.exam), 70)
        grade(student, make_assignment(self.course, category=self.hw), 90)
        named = AuditEvent.objects.exclude(action=AuditAction.STATE_CHANGE).count()
        response = self.client.post(
            f"/api/v1/course/{self.course.pk}/categories/preview",
            {
                "categories": [
                    {"id": str(self.exam.pk), "name": "Exam", "weight_percent": "50"},
                    {"id": str(self.hw.pk), "name": "Homework", "weight_percent": "50"},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["students"][0]["current"], "78.00")
        self.assertEqual(response.data["students"][0]["proposed"], "80.00")
        self.assertEqual(stored_grade(student, self.course), D("78.00"))
        # Nothing named is recorded (the generic request record aside).
        self.assertEqual(
            AuditEvent.objects.exclude(action=AuditAction.STATE_CHANGE).count(), named
        )

    def test_breakdown_for_an_enrolled_student(self):
        self.set_categories()
        student = make_student()
        enroll(student, self.course)
        grade(student, make_assignment(self.course, category=self.exam), 70)
        response = self.client.get(
            f"/api/v1/course/{self.course.pk}/grade-breakdown?student={student.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["final_grade"], "70.00")
        self.assertEqual(
            sum(D(c["contribution"]) for c in response.data["categories"]), D("70.00")
        )

    def test_breakdown_of_a_student_not_in_the_course_w11(self):
        response = self.client.get(
            f"/api/v1/course/{self.course.pk}/grade-breakdown?student={uuid.uuid4()}"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(W11, _errors(response))


@categories_on
class ExcuseApiTest(_Api):
    def setUp(self):
        super().setUp()
        self.student = make_student()
        enroll(self.student, self.course)
        self.assignment = make_assignment(self.course)
        self.url = f"/api/v1/assignments/{self.assignment.pk}/excused"

    def test_excuse_then_remove(self):
        first = self.client.post(self.url, {"student": str(self.student.pk)})
        self.assertEqual(first.status_code, 201)
        again = self.client.post(self.url, {"student": str(self.student.pk)})
        self.assertEqual(again.status_code, 200)
        gone = self.client.delete(f"{self.url}?student={self.student.pk}")
        self.assertEqual(gone.status_code, 204)
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditAction.WORK_EXCUSED).count(), 1
        )
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditAction.EXCUSE_REMOVED).count(), 1
        )

    def test_a_student_not_in_the_course_w11(self):
        response = self.client.post(self.url, {"student": str(make_student().pk)})
        self.assertEqual(response.status_code, 400)
        self.assertIn(W11, _errors(response))

    def test_another_teachers_assignment_is_not_found(self):
        other = make_assignment(make_course(make_teacher()))
        response = self.client.post(
            f"/api/v1/assignments/{other.pk}/excused", {"student": str(self.student.pk)}
        )
        self.assertEqual(response.status_code, 404)


@categories_on
class CategoryTemplateApiTest(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name=f"School {uuid.uuid4().hex[:6]}")
        self.teacher = make_teacher(school=self.school)
        self.admin = make_user(UserTypes.SCHOOL_ADMIN, school=self.school)
        self.items = [
            {"name": "Exam", "weight_percent": "70"},
            {"name": "Homework", "weight_percent": "30"},
        ]

    def create(self, user, name="Standard", items=None):
        self.client.force_authenticate(user)
        return self.client.post(
            "/api/v1/category-templates",
            {"name": name, "items": items or self.items},
            format="json",
        )

    def test_teacher_and_school_admin_templates_q5(self):
        mine = self.create(self.teacher)
        self.assertEqual(mine.status_code, 201, mine.data)
        self.assertEqual(mine.data["owner_type"], "TEACHER")
        school = self.create(self.admin, "Standard secondary")
        self.assertEqual(school.status_code, 201, school.data)
        self.assertEqual(school.data["owner_type"], "SCHOOL")
        self.assertEqual(
            AuditEvent.objects.filter(
                action=AuditAction.CATEGORY_TEMPLATE_CHANGE
            ).count(),
            2,
        )
        self.client.force_authenticate(self.teacher)
        listed = self.client.get("/api/v1/category-templates")
        self.assertEqual(
            sorted(t["name"] for t in listed.data), ["Standard", "Standard secondary"]
        )
        # A teacher reads the school's template but cannot change it.
        response = self.client.patch(
            f"/api/v1/category-templates/{school.data['id']}",
            {"name": "Mine now"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_duplicate_name_w12(self):
        self.create(self.teacher)
        response = self.create(self.teacher)
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'You already have a template called "Standard".', _errors(response)
        )

    def test_items_add_up_to_100(self):
        response = self.create(
            self.teacher, items=[{"name": "Exam", "weight_percent": "90"}]
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(W1_90, _errors(response))

    def test_another_schools_admin_cannot_see_it(self):
        made = self.create(self.admin)
        other = make_user(
            UserTypes.SCHOOL_ADMIN,
            school=School.objects.create(name=f"Other {uuid.uuid4().hex[:6]}"),
        )
        self.client.force_authenticate(other)
        response = self.client.get(f"/api/v1/category-templates/{made.data['id']}")
        self.assertEqual(response.status_code, 404)

    def test_students_cannot_use_templates(self):
        self.client.force_authenticate(make_student())
        self.assertEqual(self.client.get("/api/v1/category-templates").status_code, 403)

    def test_a_new_course_is_seeded_from_a_template(self):
        template = CategoryTemplate.objects.create(
            name="Std",
            owner_type=CategoryTemplateOwner.TEACHER,
            teacher=self.teacher,
            items=self.items,
            created_by_id=self.teacher.pk,
        )
        course = make_course(self.teacher)
        self.client.force_authenticate(self.teacher)
        response = self.client.post(
            "/api/v1/course",
            {
                "name": "Seeded",
                "session": str(course.session_id),
                "template_id": str(template.pk),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        seeded = Course.objects.get(pk=response.data["id"])
        self.assertEqual(
            list(
                seeded.grade_categories.order_by("position").values_list(
                    "name", "weight_percent"
                )
            ),
            [("Exam", D("70.00")), ("Homework", D("30.00"))],
        )

    def test_someone_elses_template_is_refused(self):
        other = make_teacher()
        template = CategoryTemplate.objects.create(
            name="Theirs",
            owner_type=CategoryTemplateOwner.TEACHER,
            teacher=other,
            items=self.items,
            created_by_id=other.pk,
        )
        course = make_course(self.teacher)
        self.client.force_authenticate(self.teacher)
        response = self.client.post(
            "/api/v1/course",
            {
                "name": "Seeded",
                "session": str(course.session_id),
                "template_id": str(template.pk),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)


@categories_on
class AssignmentCategoryDoorsTest(_Api):
    """W4 and W6 on the doors that make or edit an assignment (Q3)."""

    def setUp(self):
        super().setUp()
        self.set_categories()

    def test_json_create_must_pick_a_category_w6(self):
        response = self.client.post(
            "/api/v1/assignments",
            {"course": str(self.course.pk), "raw_input": "Q1. 2+2?", "title": "T"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(W6, _errors(response))
        self.assertFalse(Assignment.objects.filter(course=self.course).exists())

    @patch(
        "assignments.views.AssignmentProcessingService.update_assignment_from_extraction",
        side_effect=lambda user, assignment, *a, **k: assignment,
    )
    def test_json_create_with_a_category(self, _extract):
        response = self.client.post(
            "/api/v1/assignments",
            {
                "course": str(self.course.pk),
                "raw_input": "Q1. 2+2?",
                "title": "T",
                "category": str(self.exam.pk),
                "relative_weight": "2",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 202, response.data)
        made = Assignment.objects.get(course=self.course)
        self.assertEqual(
            (made.category_id, made.relative_weight), (self.exam.pk, D("2.00"))
        )

    def test_create_async_must_pick_a_category_w6(self):
        response = self.client.post(
            "/api/v1/assignments/create-async",
            {"course": str(self.course.pk), "raw_input": "Q1. 2+2?", "title": "T"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(W6, _errors(response))

    def test_patch_sets_category_and_weight_and_refuses_bad_values(self):
        assignment = make_assignment(self.course, category=self.exam)
        url = f"/api/v1/assignments/{assignment.pk}"
        ok = self.client.patch(
            url, {"category": str(self.hw.pk), "relative_weight": "1.5"}, format="json"
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        assignment.refresh_from_db()
        self.assertEqual(
            (assignment.category_id, assignment.relative_weight),
            (self.hw.pk, D("1.50")),
        )
        zero = self.client.patch(url, {"relative_weight": "0"}, format="json")
        self.assertEqual(zero.status_code, 400)
        self.assertIn(
            "An assignment's weight in its category must be more than 0.", _errors(zero)
        )
        cleared = self.client.patch(url, {"category": None}, format="json")
        self.assertEqual(cleared.status_code, 400)
        self.assertIn(W6, _errors(cleared))
        foreign = CourseCategory.objects.create(
            course=make_course(self.teacher), name="Elsewhere", weight_percent=100
        )
        wrong = self.client.patch(url, {"category": str(foreign.pk)}, format="json")
        self.assertEqual(wrong.status_code, 400)

    def test_patch_of_other_fields_leaves_an_old_uncategorized_assignment_alone(self):
        assignment = make_assignment(self.course, "Old")
        response = self.client.patch(
            f"/api/v1/assignments/{assignment.pk}", {"title": "Older"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_upload_without_a_category_is_refused_before_any_charge_w6(self):
        fund_wallet(self.teacher)
        for route in ("upload", "upload-async"):
            with patch(
                "assignments.services.AssignmentProcessingService.prepare_ai_content"
            ) as prepare:
                response = self.client.post(
                    f"/api/v1/assignments/{route}",
                    {
                        "course": str(self.course.pk),
                        "assignments": SimpleUploadedFile(
                            "a.pdf", b"%PDF-1.4", content_type="application/pdf"
                        ),
                    },
                    format="multipart",
                )
            self.assertEqual(response.status_code, 400, route)
            self.assertIn(W6, _errors(response), route)
            prepare.assert_not_called()

    def test_upload_category_is_checked_against_the_course(self):
        self.assertEqual(
            resolve_upload_category(self.course, str(self.exam.pk)), self.exam
        )
        other = CourseCategory.objects.create(
            course=make_course(self.teacher), name="X", weight_percent=100
        )
        for bad in (str(other.pk), "not-an-id"):
            with self.assertRaises(DRFValidationError):
                resolve_upload_category(self.course, bad)

    @patch(
        "assignments.file_uploads.AssignmentProcessingService.extract_assignment_data"
    )
    @patch("assignments.file_uploads.AssignmentProcessingService.prepare_ai_content")
    def test_upload_carries_the_category_to_the_saved_draft(self, prepare, extract):
        prepare.return_value = [{"type": "text", "text": "x"}]
        extract.return_value = {
            "course": str(self.course.pk),
            "title": "Uploaded",
            "total_points": 10,
            "question_count": 1,
            "assignment_type": "OBJECTIVE",
            "questions": [question()],
            "raw_input": "uploaded",
        }
        outcome = upload_assignment_file(
            self.teacher,
            SimpleUploadedFile("a.pdf", b"%PDF-1.4", content_type="application/pdf"),
            "prompt",
            course=self.course,
            sha256=uuid.uuid4().hex * 2,
            category=self.hw,
        )
        self.assertEqual(outcome.assignment.category_id, self.hw.pk)

    @patch("assignments.tasks.upload_assignment_file")
    @patch("assignments.tasks.AssignmentProcessingService.rebuild_uploaded_file")
    def test_background_upload_passes_the_category_on(self, rebuild, upload):
        from assignments.tasks import upload_assignment_async

        rebuild.return_value = object()
        upload.side_effect = Exception("stop after the call")
        upload_assignment_async.apply(
            kwargs={
                "user_id": str(self.teacher.id),
                "course_id": str(self.course.id),
                "file_payload": {
                    "name": "a.pdf",
                    "content_type": "application/pdf",
                    "content_b64": "JVBERi0xLjQ=",
                },
                "prompt_text": "p",
                "file_name": "a.pdf",
                "category_id": str(self.exam.pk),
            }
        )
        self.assertEqual(upload.call_args.kwargs["category"], self.exam)


class ConcurrentCategoryPutTest(TransactionTestCase):
    """NFR-REL-09: two weight sets saved at once leave one whole, valid set."""

    @categories_on
    def test_two_puts_at_once_leave_one_valid_set(self):
        teacher = make_teacher()
        course = make_course(teacher)
        sets = [
            [
                {"name": "A", "weight_percent": "50"},
                {"name": "B", "weight_percent": "50"},
            ],
            [
                {"name": "C", "weight_percent": "20"},
                {"name": "D", "weight_percent": "30"},
                {"name": "E", "weight_percent": "50"},
            ],
        ]
        barrier = threading.Barrier(2)
        errors = []

        def save(items):
            try:
                barrier.wait(timeout=10)
                grade_categories.set_course_categories(course, items, actor=teacher)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=save, args=(items,)) for items in sets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        names = sorted(
            CourseCategory.objects.filter(course=course).values_list("name", flat=True)
        )
        self.assertIn(names, [["A", "B"], ["C", "D", "E"]])
        self.assertEqual(
            sum(
                CourseCategory.objects.filter(course=course).values_list(
                    "weight_percent", flat=True
                )
            ),
            D("100.00"),
        )
