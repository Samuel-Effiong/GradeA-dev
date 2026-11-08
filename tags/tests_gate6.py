"""Epic C1, Gate 6 (EXPECTATIONS.md): the tag list with 500 tags costs the
same number of queries as the list with 2 (no query per tag or per link).

Each tag is on one or two assignments, so `usage_count` is real work. Pages
are the house page-number pagination (max_page_size 100).
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from tags._c1_test_fixtures import World, client_for, data, fire, flag_on
from tags.models import AssignmentTag, OwnerType, Tag, TagType

MANY = 500


@flag_on
class FiveHundredTagsTest(TestCase):
    def setUp(self):
        self.w = World().build()

    def _tags(self, teacher, count):
        tags = Tag.objects.bulk_create(
            Tag(
                tag_type=TagType.UNIT if i % 2 else TagType.SUBJECT,
                name=f"Unit {i:03d}",
                normalized_name=f"unit {i:03d}",
                owner_type=OwnerType.TEACHER,
                teacher=teacher,
                created_by_id=teacher.pk,
            )
            for i in range(count)
        )
        links = [AssignmentTag(assignment=self.w.a1, tag=t) for t in tags]
        links += [AssignmentTag(assignment=self.w.a2, tag=t) for t in tags[::3]]
        AssignmentTag.objects.bulk_create(links)
        return tags

    def _queries(self, client, url):
        with CaptureQueriesContext(connection) as captured:
            response = fire(client, "get", url)
        self.assertEqual(response.status_code, 200, response.content[:400])
        return len(captured), data(response)

    def test_the_query_count_is_flat_from_2_to_500_tags(self):
        self._tags(self.w.teacher, MANY)
        self._tags(self.w.other_teacher, 2)
        many, few = client_for(self.w.teacher), client_for(self.w.other_teacher)
        fire(many, "get", "tags?page_size=1")  # warm, not measured
        fire(few, "get", "tags?page_size=1")
        urls = [
            "tags?page_size=100",
            "tags?page_size=100&page=5",
            "tags?page_size=100&tag_type=UNIT",
            "tags?page_size=100&q=unit",
        ]
        counts = {}
        for url in urls:
            n_few, _ = self._queries(few, url.replace("&page=5", ""))
            n_many, body = self._queries(many, url)
            counts[url] = (n_few, n_many)
            self.assertEqual(n_few, n_many, url)
            self.assertLessEqual(len(body["results"]), 100)
        print(f"\nC1_GATE6 tags={MANY} queries(2 tags, {MANY} tags): {counts}")

    def test_the_500_tags_page_through_completely_and_in_order(self):
        self._tags(self.w.teacher, MANY)
        client = client_for(self.w.teacher)
        seen = []
        for page in range(1, 6):
            body = data(fire(client, "get", f"tags?page_size=100&page={page}"))
            seen += body["results"]
        self.assertEqual(len(seen), MANY)
        self.assertEqual(len({row["id"] for row in seen}), MANY)
        counts = [row["usage_count"] for row in seen]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(counts.count(2), len(range(0, MANY, 3)))
