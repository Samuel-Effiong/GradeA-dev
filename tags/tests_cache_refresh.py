"""S15 (owner go 2026-11-17, beta): a tag change refreshes the cached
assignment list and detail at once. The cache is not cleared between steps."""

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from tags import services
from tags._c1_test_fixtures import World, client_for, data, fire, flag_on
from tags.models import AssignmentTag, TagType

LIST = "assignments"


def rows(response):
    body = data(response)
    return body["results"] if isinstance(body, dict) and "results" in body else body


@flag_on
class TagCacheRefreshTests(TestCase):
    def setUp(self):
        cache.clear()
        self.w = World().build()
        self.api = client_for(self.w.teacher)
        self.tag, _ = services.get_or_create_tag(self.w.teacher, TagType.UNIT, "Cells")
        self.a1 = self.w.a1

    def listing(self, query=""):
        return rows(fire(self.api, "get", f"{LIST}{query}"))

    def detail(self):
        return data(fire(self.api, "get", f"{LIST}/{self.a1.pk}"))

    def names(self, row):
        return [t["name"] for t in row["tags"]]

    def row(self, listing):
        return next(r for r in listing if r["id"] == str(self.a1.pk))

    def prime(self, query=""):
        self.listing()
        self.detail()
        self.listing(query)

    def test_setting_tags_shows_at_once(self):
        by_tag = f"?tags={self.tag.pk}"
        self.prime(by_tag)
        self.assertEqual(self.listing(by_tag), [])
        response = fire(
            self.api,
            "put",
            f"assignments/{self.a1.pk}/tags",
            {"tag_ids": [str(self.tag.pk)]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.names(self.row(self.listing())), ["Cells"])
        self.assertEqual(self.names(self.detail()), ["Cells"])
        self.assertEqual([r["id"] for r in self.listing(by_tag)], [str(self.a1.pk)])
        fire(self.api, "put", f"assignments/{self.a1.pk}/tags", {"tag_ids": []})
        self.assertEqual(self.names(self.detail()), [])
        self.assertEqual(self.listing(by_tag), [])

    def test_renaming_shows_at_once(self):
        services.set_assignment_tags(self.a1, [self.tag.pk], teacher=self.w.teacher)
        self.prime()
        self.assertEqual(self.names(self.detail()), ["Cells"])
        response = fire(self.api, "patch", f"tags/{self.tag.pk}", {"name": "Genes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.names(self.row(self.listing())), ["Genes"])
        self.assertEqual(self.names(self.detail()), ["Genes"])

    def test_deleting_shows_at_once(self):
        services.set_assignment_tags(self.a1, [self.tag.pk], teacher=self.w.teacher)
        by_tag = f"?tags={self.tag.pk}"
        self.prime(by_tag)
        self.assertEqual(len(self.listing(by_tag)), 1)
        response = fire(self.api, "delete", f"tags/{self.tag.pk}?expected_count=1")
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertEqual(self.names(self.row(self.listing())), [])
        self.assertEqual(self.names(self.detail()), [])

    def test_a_rename_costs_the_same_for_one_assignment_or_many(self):
        def rename_cost(name):
            with CaptureQueriesContext(connection) as queries:
                services.rename_tag(self.tag, name)
            return len(queries)

        services.set_assignment_tags(self.a1, [self.tag.pk], teacher=self.w.teacher)
        one = rename_cost("Genes")
        for i in range(8):
            extra = self.w.assignment(self.w.course_a, f"More{i}")
            AssignmentTag.objects.create(assignment=extra, tag=self.tag)
        many = rename_cost("Plants")
        self.assertEqual(many, one)
