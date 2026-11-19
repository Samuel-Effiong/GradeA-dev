"""Slice 5: department delete with the content choice (plan 8; row T24).
The registry is empty in F1, so the choice flow runs against a test-only
content kind kept in memory."""

import uuid

from audit.enums import AuditAction
from audit.models import AuditEvent
from departments import registry
from departments.models import Department, DepartmentMembership
from departments.tests_api_admin import AdminApiBase, events
from departments.tests_factories import add_member, make_department


class DummyContent:
    """Counts per department id; move and delete act on the dict."""

    def __init__(self):
        self.items = {}
        self.fail_move = False

    def kind(self):
        return registry.ContentKind("dummy", self.count, self.move, self.delete)

    def count(self, department):
        return self.items.get(department.id, 0)

    def move(self, department, target):
        n = self.items.pop(department.id, 0)
        self.items[target.id] = self.items.get(target.id, 0) + n
        if self.fail_move:
            raise RuntimeError("boom")
        return n

    def delete(self, department):
        return self.items.pop(department.id, 0)


class DeleteWithContentTests(AdminApiBase):
    def setUp(self):
        super().setUp()
        self.content = DummyContent()
        registry.register(self.content.kind())
        self.addCleanup(registry.unregister, "dummy")
        self.dept = make_department(self.school, "Maths")
        self.target = make_department(self.school, "Science")
        self.content.items[self.dept.id] = 3

    def delete(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = self.url_detail(self.dept) + (f"?{query}" if query else "")
        return self.client.delete(url)

    def assertNothingChanged(self):
        self.assertTrue(Department.objects.filter(pk=self.dept.pk).exists())
        self.assertEqual(self.content.items, {self.dept.id: 3})
        self.assertFalse(events(AuditAction.DEPARTMENT_DELETE))

    def test_content_without_a_choice_is_a_409_with_the_count_and_changes_nothing(
        self,
    ):
        add_member(self.dept, self.t1)
        response = self.delete()
        self.assertCoded(response, "DEPARTMENT_CONTENT_CHOICE_REQUIRED", 409)
        self.assertEqual(response.data["params"]["item_count"], 3)
        self.assertNothingChanged()
        self.assertTrue(DepartmentMembership.objects.exists())

    def test_an_unknown_choice_counts_as_no_choice(self):
        self.assertCoded(
            self.delete(content="maybe"), "DEPARTMENT_CONTENT_CHOICE_REQUIRED", 409
        )
        self.assertNothingChanged()

    def test_delete_choice_removes_the_content_and_logs_one_event(self):
        add_member(self.dept, self.t1)
        response = self.delete(content="delete")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Department.objects.filter(pk=self.dept.pk).exists())
        self.assertEqual(self.content.items, {})
        (event,) = events(AuditAction.DEPARTMENT_DELETE)
        self.assertEqual(event.metadata["item_count"], 3)
        self.assertEqual(event.metadata["delete_choice"], "delete")
        self.assertEqual(event.metadata["member_count"], 1)

    def test_reassign_moves_the_content_to_the_target(self):
        response = self.delete(content="reassign", target=self.target.id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Department.objects.filter(pk=self.dept.pk).exists())
        self.assertEqual(self.content.items, {self.target.id: 3})
        (event,) = events(AuditAction.DEPARTMENT_DELETE)
        self.assertEqual(event.metadata["delete_choice"], "reassign")
        self.assertEqual(event.metadata["target_department_id"], str(self.target.id))

    def test_a_bad_target_is_refused_the_same_way_and_changes_nothing(self):
        foreign = make_department(self.other_school, "Foreign")
        bodies = []
        for target in (
            self.dept.id,
            foreign.id,
            uuid.uuid4(),
            "not-a-uuid",
        ):
            response = self.delete(content="reassign", target=target)
            self.assertCoded(response, "DEPARTMENT_REASSIGN_TARGET_INVALID", 400)
            bodies.append((response.data["error"], response.data["reason_code"]))
            self.assertNothingChanged()
        self.assertEqual(len(set(bodies)), 1)
        self.assertCoded(
            self.delete(content="reassign"), "DEPARTMENT_REASSIGN_TARGET_INVALID", 400
        )

    def test_a_failing_move_rolls_everything_back(self):
        self.content.fail_move = True
        response = self.delete(content="reassign", target=self.target.id)
        self.assertEqual(response.status_code, 500)
        self.assertTrue(Department.objects.filter(pk=self.dept.pk).exists())
        self.assertFalse(events(AuditAction.DEPARTMENT_DELETE))

    def test_empty_department_needs_no_choice(self):
        empty = make_department(self.school, "Empty")
        response = self.client.delete(self.url_detail(empty))
        self.assertEqual(response.status_code, 200)
        (event,) = events(AuditAction.DEPARTMENT_DELETE)
        self.assertEqual(event.metadata["item_count"], 0)
        self.assertEqual(event.metadata["delete_choice"], "none")


class SchoolDeleteTests(AdminApiBase):
    def test_a_school_hard_delete_takes_departments_and_memberships_not_the_log(self):
        dept = make_department(self.school, "Maths")
        add_member(dept, self.t1)
        field = Department._meta.get_field("school")
        self.assertEqual(field.remote_field.on_delete.__name__, "CASCADE")
        self.client.delete(self.url_detail(dept))
        dept = make_department(self.school, "Again")
        add_member(dept, self.t2)
        log_before = AuditEvent.objects.count()
        self.school.delete()
        self.assertFalse(Department.objects.filter(pk=dept.pk).exists())
        self.assertFalse(DepartmentMembership.objects.filter(teacher=self.t2).exists())
        self.assertGreaterEqual(AuditEvent.objects.count(), log_before)
