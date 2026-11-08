"""Epic C1 ships dark (plan 11 §3.4, NFR-MNT-05; SM ruling 1).

The tag routes are always registered (so the S2 audit sweep sees them),
but while TAGS_ENABLED is off each answers exactly what an unknown URL
answers, before authentication or permissions run: no 401 or 403 that
would reveal the route. The assignment responses are pinned byte for byte
in assignments/tests_c1_dark_snapshot.py.
"""

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditEvent
from tags._c1_test_fixtures import API, TAG_ROUTES, World, client_for, fire, flag_off
from tags.models import AssignmentTag, Tag, TagType


class FlagDefaultTest(TestCase):
    def test_the_flag_is_off_by_default(self):
        self.assertIs(getattr(settings, "TAGS_ENABLED", None), False)

    def test_every_tag_route_is_registered(self):
        for name, kwargs in (
            ("tag-list", {}),
            ("tag-detail", {"pk": "00000000-0000-0000-0000-000000000001"}),
            ("tag-impact", {"pk": "00000000-0000-0000-0000-000000000001"}),
            ("assignment-tags", {"pk": "00000000-0000-0000-0000-000000000001"}),
        ):
            with self.subTest(route=name):
                self.assertTrue(reverse(name, kwargs=kwargs).startswith(API))


@flag_off
class DarkRoutesTest(TestCase):
    def setUp(self):
        self.w = World().build()
        self.tag = Tag.objects.create(
            teacher=self.w.teacher,
            tag_type=TagType.UNIT,
            name="Cells",
            normalized_name="cells",
        )

    def unknown(self, client, method):
        return fire(client, method, "c1-no-such-route/x", {"name": "Cells"})

    def test_every_route_answers_like_an_unknown_url_for_everyone(self):
        for who in (None, self.w.student, self.w.teacher):
            client = client_for(who)
            for method, template, _ in TAG_ROUTES:
                path = template.format(tag=self.tag.pk, assignment=self.w.a1.pk)
                label = getattr(who, "user_type", "ANONYMOUS")
                with self.subTest(who=label, method=method, path=path):
                    reference = self.unknown(client, method)
                    response = fire(
                        client,
                        method,
                        path,
                        {"tag_type": "UNIT", "name": "Genetics", "tag_ids": []},
                    )
                    self.assertEqual(reference.status_code, 404)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.content, reference.content)
                    self.assertEqual(
                        response.get("Content-Type"), reference.get("Content-Type")
                    )

    def test_nothing_is_written_while_dark(self):
        teacher = client_for(self.w.teacher)
        fire(teacher, "post", "tags", {"tag_type": "UNIT", "name": "Genetics"})
        fire(teacher, "patch", f"tags/{self.tag.pk}", {"name": "Renamed"})
        fire(teacher, "delete", f"tags/{self.tag.pk}?expected_count=0")
        fire(
            teacher,
            "put",
            f"assignments/{self.w.a1.pk}/tags",
            {"tag_ids": [str(self.tag.pk)]},
        )
        self.assertEqual(list(Tag.objects.values_list("name", flat=True)), ["Cells"])
        self.assertFalse(AssignmentTag.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action__startswith="TAG_").exists())
        self.assertFalse(
            AuditEvent.objects.filter(action="ASSIGNMENT_TAGS_SET").exists()
        )

    def test_assignment_responses_carry_no_tags_field(self):
        teacher = client_for(self.w.teacher)
        detail = fire(teacher, "get", f"assignments/{self.w.a1.pk}").json()["data"]
        self.assertNotIn("tags", detail)
        for row in fire(teacher, "get", "assignments").json()["data"]["results"]:
            self.assertNotIn("tags", row)
