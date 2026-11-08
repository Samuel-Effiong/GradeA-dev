"""Epic C1: the tag API with TAGS_ENABLED on (plan 11 §3.4, §5.1, §5.3).

Isolation (Gate 9): every lookup is the requesting teacher's own, so a
foreign id is a 404 with the existing "We couldn't find that item" text,
never a 403 that confirms it exists. Only the course's own teacher can tag
its assignments (SM ruling on D7: there are no co-teachers). Other roles
are refused.
"""

import importlib
import uuid

from django.test import TestCase

from audit.enums import AuditAction
from audit.models import AuditEvent
from tags._c1_fixtures import (
    TAG_ROUTES,
    World,
    client_for,
    data,
    envelope,
    fire,
    flag_on,
)
from tags.models import AssignmentTag, Tag, TagType

# Not built yet when the expectations are committed (red first).
services = importlib.import_module("tags.services")

NOT_FOUND = "We couldn't find that item. It may have been deleted."
NOT_FOUND_ASSIGNMENT = "We couldn't find that assignment. It may have been deleted."


@flag_on
class _ApiCase(TestCase):
    def setUp(self):
        self.w = World().build()
        self.api = client_for(self.w.teacher)

    def create(self, name, tag_type="UNIT", client=None):
        return fire(
            client or self.api, "post", "tags", {"tag_type": tag_type, "name": name}
        )

    def make(self, name, tag_type=TagType.UNIT, teacher=None):
        tag, _ = services.get_or_create_tag(teacher or self.w.teacher, tag_type, name)
        return tag

    def assertCoded(self, response, code, status):
        self.assertEqual(response.status_code, status, response.content[:400])
        self.assertEqual(envelope(response)["reason_code"], code)

    def assertNotFound(self, response):
        self.assertEqual(response.status_code, 404, response.content[:400])
        self.assertIn(NOT_FOUND, response.content.decode())


class CreateTest(_ApiCase):
    def test_create_then_reuse(self):  # FR-C-02: reuse without retyping
        first = self.create("Fractions")
        self.assertEqual(first.status_code, 201, first.content[:400])
        body = data(first)
        self.assertEqual(
            {k: body[k] for k in ("tag_type", "name")},
            {"tag_type": "UNIT", "name": "Fractions"},
        )
        again = self.create("  FRACTIONS ")
        self.assertEqual(again.status_code, 200, again.content[:400])
        self.assertEqual(data(again)["id"], body["id"])
        self.assertEqual(data(again)["name"], "Fractions")
        self.assertEqual(Tag.objects.count(), 1)

    def test_reuse_writes_no_tag_event_only_the_request_event(self):
        # SM ruling 2, as the audit layer really works: no TAG_CREATE on a
        # reuse; the S1 middleware still records the request itself once.
        self.create("Fractions")
        before = set(AuditEvent.objects.values_list("pk", flat=True))
        self.create("fractions")
        new = list(
            AuditEvent.objects.exclude(pk__in=before).values_list("action", flat=True)
        )
        self.assertNotIn(AuditAction.TAG_CREATE, new)
        self.assertEqual(new, [AuditAction.STATE_CHANGE])

    def test_bad_names(self):
        self.assertCoded(self.create("   "), "TAG_NAME_MISSING", 400)
        self.assertCoded(
            fire(self.api, "post", "tags", {"tag_type": "UNIT"}),
            "TAG_NAME_MISSING",
            400,
        )
        self.assertCoded(self.create("z" * 101), "TAG_NAME_TOO_LONG", 400)
        self.assertFalse(Tag.objects.exists())

    def test_the_approved_messages(self):
        self.assertEqual(envelope(self.create(""))["error"], "Enter a tag name.")
        self.assertEqual(
            envelope(self.create("z" * 101))["error"],
            "Tag names can be up to 100 characters.",
        )

    def test_symbols_and_emoji_are_accepted_as_typed(self):  # owner, 2026-11-08
        names = ("C# Basics", "Cells \U0001f52c", "Q&A", "Reading & Writing")
        for name in names:
            with self.subTest(name=name):
                response = self.create(name)
                self.assertEqual(response.status_code, 201, response.content[:400])
                self.assertEqual(data(response)["name"], name)
        self.assertEqual(Tag.objects.count(), len(names))

    def test_a_lesson_or_unknown_type_is_refused(self):  # FR-C-01, C1 part
        for tag_type in ("LESSON", "", "unit", None):
            with self.subTest(tag_type=tag_type):
                response = self.create("Cells", tag_type=tag_type)
                self.assertEqual(response.status_code, 400, response.content[:400])
        self.assertFalse(Tag.objects.exists())


class ListTest(_ApiCase):
    def setUp(self):
        super().setUp()
        self.cells = self.make("Cells")
        self.cellular = self.make("Cellular respiration")
        self.genetics = self.make("Genetics")
        self.biology = self.make("Biology", TagType.SUBJECT)
        self.make("Cells", teacher=self.w.other_teacher)
        services.set_assignment_tags(
            self.w.a1, [self.genetics.pk, self.cellular.pk], teacher=self.w.teacher
        )
        services.set_assignment_tags(
            self.w.a2, [self.genetics.pk], teacher=self.w.teacher
        )

    def names(self, query=""):
        response = fire(self.api, "get", "tags" + query)
        self.assertEqual(response.status_code, 200, response.content[:400])
        return [row["name"] for row in data(response)["results"]]

    def test_own_tags_only_by_link_count_then_name(self):
        self.assertEqual(
            self.names(), ["Genetics", "Cellular respiration", "Biology", "Cells"]
        )

    def test_rows_carry_the_link_count(self):
        response = fire(self.api, "get", "tags?tag_type=UNIT")
        rows = {row["name"]: row for row in data(response)["results"]}
        self.assertEqual(rows["Genetics"]["usage_count"], 2)
        self.assertEqual(rows["Cells"]["usage_count"], 0)
        self.assertEqual(
            set(rows["Genetics"]), {"id", "tag_type", "name", "usage_count"}
        )

    def test_filter_by_type(self):
        self.assertEqual(self.names("?tag_type=SUBJECT"), ["Biology"])

    def test_q_is_a_prefix_match_on_the_normalized_name(self):
        self.assertEqual(self.names("?q=CELL"), ["Cellular respiration", "Cells"])
        self.assertEqual(self.names("?q=%20cells"), ["Cells"])
        self.assertEqual(self.names("?q=ells"), [])

    def test_the_list_is_paginated(self):
        response = fire(self.api, "get", "tags?page_size=2")
        body = data(response)
        self.assertEqual(body["count"], 4)
        self.assertEqual(len(body["results"]), 2)

    def test_the_other_teacher_sees_only_theirs(self):
        response = fire(client_for(self.w.other_teacher), "get", "tags")
        self.assertEqual([r["name"] for r in data(response)["results"]], ["Cells"])


class RenameDeleteTest(_ApiCase):
    def setUp(self):
        super().setUp()
        self.tag = self.make("Cells")
        services.set_assignment_tags(self.w.a1, [self.tag.pk], teacher=self.w.teacher)
        services.set_assignment_tags(self.w.a2, [self.tag.pk], teacher=self.w.teacher)

    def test_rename(self):
        response = fire(
            self.api, "patch", f"tags/{self.tag.pk}", {"name": "Cell Biology"}
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(data(response)["name"], "Cell Biology")

    def test_rename_collision(self):
        self.make("Genetics")
        response = fire(self.api, "patch", f"tags/{self.tag.pk}", {"name": "GENETICS"})
        self.assertCoded(response, "TAG_NAME_TAKEN", 400)
        self.assertEqual(
            envelope(response)["error"], 'You already have a Unit tag named "Genetics".'
        )

    def test_rename_to_symbols_is_accepted(self):  # owner, 2026-11-08
        response = fire(self.api, "patch", f"tags/{self.tag.pk}", {"name": "Cells!"})
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.name, "Cells!")

    def test_impact(self):
        response = fire(self.api, "get", f"tags/{self.tag.pk}/impact")
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(data(response), {"affected_count": 2})

    def test_delete_with_the_count(self):
        response = fire(self.api, "delete", f"tags/{self.tag.pk}?expected_count=2")
        self.assertEqual(response.status_code, 204, response.content[:400])
        self.assertFalse(Tag.objects.exists())
        self.assertEqual(self.w.course_a.assignments.count(), 2)

    def test_delete_with_a_stale_count(self):
        response = fire(self.api, "delete", f"tags/{self.tag.pk}?expected_count=1")
        self.assertCoded(response, "TAG_CHANGED_SINCE_CHECK", 409)
        self.assertEqual(
            envelope(response)["error"],
            "This tag's assignments changed since you checked. Review the new "
            "count and try again.",
        )
        self.assertTrue(envelope(response)["retryable"])
        self.assertEqual(AssignmentTag.objects.count(), 2)

    def test_delete_without_a_count(self):
        for query in (
            "",
            "?expected_count=",
            "?expected_count=two",
            "?expected_count=-1",
        ):
            with self.subTest(query=query):
                response = fire(self.api, "delete", f"tags/{self.tag.pk}{query}")
                self.assertEqual(response.status_code, 400, response.content[:400])
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())


class AssignmentTagsTest(_ApiCase):
    def put(self, assignment, tag_ids, client=None):
        return fire(
            client or self.api,
            "put",
            f"assignments/{assignment.pk}/tags",
            {"tag_ids": [str(t) for t in tag_ids]},
        )

    def test_set_and_read_back(self):
        cells, biology = self.make("Cells"), self.make("Biology", TagType.SUBJECT)
        response = self.put(self.w.a1, [cells.pk, biology.pk])
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(
            sorted(data(response)["tags"], key=lambda t: t["name"]),
            [
                {"id": str(biology.pk), "tag_type": "SUBJECT", "name": "Biology"},
                {"id": str(cells.pk), "tag_type": "UNIT", "name": "Cells"},
            ],
        )

    def test_the_cap(self):
        tags = [self.make(f"Unit {i}") for i in range(51)]
        self.assertCoded(
            self.put(self.w.a1, [t.pk for t in tags]), "TOO_MANY_TAGS", 400
        )
        self.assertEqual(
            envelope(self.put(self.w.a1, [t.pk for t in tags]))["error"],
            "An assignment can have up to 50 tags.",
        )

    def test_ten_thousand_ids_are_refused_by_the_cap(self):  # Gate 4
        response = self.put(self.w.a1, [uuid.uuid4() for _ in range(10_000)])
        self.assertCoded(response, "TOO_MANY_TAGS", 400)

    def test_malformed_bodies(self):
        for body in (
            {},
            {"tag_ids": "x"},
            {"tag_ids": ["not-a-uuid"]},
            {"tag_ids": None},
        ):
            with self.subTest(body=body):
                response = fire(
                    self.api, "put", f"assignments/{self.w.a1.pk}/tags", body
                )
                self.assertEqual(response.status_code, 400, response.content[:400])

    def test_a_foreign_or_unknown_tag_is_not_found(self):
        mine = self.make("Cells")
        theirs = self.make("Cells", teacher=self.w.other_teacher)
        for bad in (theirs.pk, uuid.uuid4()):
            with self.subTest(tag=bad):
                self.assertNotFound(self.put(self.w.a1, [mine.pk, bad]))
        self.assertFalse(AssignmentTag.objects.exists())

    def test_only_the_courses_own_teacher_can_tag(self):
        # Teacher B's own tag on teacher A's assignment: 404, not 403.
        # The assignment form of the H-296 text (NOTES deviation 16).
        theirs = self.make("Forces", teacher=self.w.other_teacher)
        for response in (
            self.put(self.w.a1, [theirs.pk], client=client_for(self.w.other_teacher)),
            self.put(type("A", (), {"pk": uuid.uuid4()}), []),
        ):
            self.assertEqual(response.status_code, 404, response.content[:400])
            self.assertIn(NOT_FOUND_ASSIGNMENT, response.content.decode())
        self.assertFalse(AssignmentTag.objects.exists())


class IsolationTest(_ApiCase):
    """Gate 9: another teacher's tag, through every route, is a 404."""

    def test_every_route_on_a_foreign_tag_is_not_found(self):
        theirs = self.make("Cells", teacher=self.w.other_teacher)
        services.set_assignment_tags(
            self.w.b1, [theirs.pk], teacher=self.w.other_teacher
        )
        for method, path, body in (
            ("patch", f"tags/{theirs.pk}", {"name": "Mine now"}),
            ("get", f"tags/{theirs.pk}/impact", None),
            ("delete", f"tags/{theirs.pk}?expected_count=1", None),
        ):
            with self.subTest(method=method, path=path):
                self.assertNotFound(fire(self.api, method, path, body))
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, "Cells")
        self.assertEqual(AssignmentTag.objects.count(), 1)

    def test_other_roles_are_refused_on_every_route(self):
        tag = self.make("Cells")
        for user in (self.w.student, self.w.school_admin, self.w.superadmin):
            for method, template, _ in TAG_ROUTES:
                path = template.format(tag=tag.pk, assignment=self.w.a1.pk)
                with self.subTest(user=user.user_type, method=method, path=path):
                    response = fire(client_for(user), method, path, {"tag_ids": []})
                    self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertEqual(Tag.objects.count(), 1)
        self.assertFalse(AssignmentTag.objects.exists())

    def test_anonymous_is_refused_on_every_route(self):
        tag = self.make("Cells")
        for method, template, _ in TAG_ROUTES:
            path = template.format(tag=tag.pk, assignment=self.w.a1.pk)
            with self.subTest(method=method, path=path):
                response = fire(client_for(None), method, path)
                self.assertEqual(response.status_code, 401, response.content[:300])


class AuditPerRequestTest(_ApiCase):
    """S2 with the flag on: each tag write leaves exactly one event naming
    the requester (the named event when the service writes one, else the S1
    request event)."""

    def test_each_write_leaves_one_event_naming_the_teacher(self):
        tag = self.make("Cells")
        writes = (
            ("post", "tags", {"tag_type": "UNIT", "name": "Genetics"}, "TAG_CREATE"),
            ("post", "tags", {"tag_type": "UNIT", "name": "genetics"}, "STATE_CHANGE"),
            ("post", "tags", {"tag_type": "UNIT", "name": ""}, "STATE_CHANGE"),
            ("patch", f"tags/{tag.pk}", {"name": "Cell Biology"}, "TAG_RENAME"),
            (
                "put",
                f"assignments/{self.w.a1.pk}/tags",
                {"tag_ids": [str(tag.pk)]},
                "ASSIGNMENT_TAGS_SET",
            ),
            ("delete", f"tags/{tag.pk}?expected_count=0", None, "STATE_CHANGE"),
            ("delete", f"tags/{tag.pk}?expected_count=1", None, "TAG_DELETE"),
        )
        for method, path, body, action in writes:
            with self.subTest(method=method, path=path, action=action):
                before = set(AuditEvent.objects.values_list("pk", flat=True))
                fire(self.api, method, path, body)
                naming = list(
                    AuditEvent.objects.exclude(pk__in=before)
                    .filter(actor_id=self.w.teacher.pk)
                    .values_list("action", flat=True)
                )
                self.assertEqual(naming, [action])


class AssignmentFieldTest(_ApiCase):
    """The read-only `tags` field on the teacher's assignment responses."""

    def setUp(self):
        super().setUp()
        self.cells = self.make("Cells")
        services.set_assignment_tags(self.w.a1, [self.cells.pk], teacher=self.w.teacher)
        self.expected = [
            {"id": str(self.cells.pk), "tag_type": "UNIT", "name": "Cells"}
        ]

    def test_teacher_detail_and_list_carry_tags(self):
        detail = data(fire(self.api, "get", f"assignments/{self.w.a1.pk}"))
        self.assertEqual(detail["tags"], self.expected)
        rows = {
            r["id"]: r for r in data(fire(self.api, "get", "assignments"))["results"]
        }
        self.assertEqual(rows[str(self.w.a1.pk)]["tags"], self.expected)
        self.assertEqual(rows[str(self.w.a2.pk)]["tags"], [])

    def test_the_list_costs_no_query_per_row(self):
        for assignment in [self.w.assignment(self.w.course_a) for _ in range(5)]:
            services.set_assignment_tags(
                assignment, [self.cells.pk], teacher=self.w.teacher
            )
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        fire(self.api, "get", "assignments?page_size=1")  # warm, not measured
        with CaptureQueriesContext(connection) as few:
            fire(self.api, "get", "assignments?page_size=2")
        with CaptureQueriesContext(connection) as many:
            fire(self.api, "get", "assignments?page_size=7")
        self.assertEqual(len(few), len(many))

    def test_students_never_see_tags(self):
        student = client_for(self.w.student)
        detail = data(fire(student, "get", f"assignments/{self.w.a1.pk}"))
        self.assertNotIn("tags", detail)
        for row in data(fire(student, "get", "assignments"))["results"]:
            self.assertNotIn("tags", row)
