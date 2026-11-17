"""S13 (owner go 2026-11-17, beta): `tags` filter on the teacher's
assignment list. Match ALL given tags; only the teacher's own tags count;
off while TAGS_ENABLED is off; no duplicate rows; no per-row queries."""

import uuid

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from tags._c1_test_fixtures import World, client_for, data, flag_off, flag_on
from tags.models import AssignmentTag, Tag, TagType

LIST = "/api/v1/assignments"


def ids(response):
    body = data(response)
    rows = body["results"] if isinstance(body, dict) and "results" in body else body
    return [row["id"] for row in rows]


@flag_on
class TagFilterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.w = World().build()
        self.api = client_for(self.w.teacher)
        self.t1 = self.tag(self.w.teacher, "Cells")
        self.t2 = self.tag(self.w.teacher, "Genes")
        self.link(self.w.a1, self.t1, self.t2)
        self.link(self.w.a2, self.t1)

    def tag(self, teacher, name, tag_type=TagType.UNIT):
        return Tag.objects.create(
            owner_type="TEACHER",
            teacher=teacher,
            tag_type=tag_type,
            name=name,
            normalized_name=name.lower(),
        )

    def link(self, assignment, *tags):
        for tag in tags:
            AssignmentTag.objects.create(assignment=assignment, tag=tag)

    def get(self, query, client=None):
        return (client or self.api).get(f"{LIST}?{query}")

    def test_one_tag(self):
        response = self.get(f"tags={self.t1.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(ids(response), [str(self.w.a1.pk), str(self.w.a2.pk)])

    def test_two_tags_must_all_match(self):
        response = self.get(f"tags={self.t1.pk},{self.t2.pk}")
        self.assertEqual(ids(response), [str(self.w.a1.pk)])

    def test_comma_and_repeated_forms_agree(self):
        comma = ids(self.get(f"tags={self.t1.pk},{self.t2.pk}"))
        repeated = ids(self.get(f"tags={self.t1.pk}&tags={self.t2.pk}"))
        self.assertEqual(comma, repeated)
        self.assertEqual(comma, [str(self.w.a1.pk)])

    def test_repeated_form_is_not_served_from_the_one_tag_cache(self):
        one = ids(self.get(f"tags={self.t2.pk}"))
        both = ids(self.get(f"tags={self.t1.pk}&tags={self.t2.pk}"))
        last_only = ids(self.get(f"tags={self.t2.pk}"))
        self.assertEqual(one, last_only)
        self.assertEqual(both, [str(self.w.a1.pk)])
        self.assertEqual(ids(self.get(f"tags={self.t2.pk}&tags={self.t1.pk}")), both)

    def test_duplicates_count_once(self):
        response = self.get(f"tags={self.t1.pk},{self.t1.pk}")
        self.assertCountEqual(ids(response), [str(self.w.a1.pk), str(self.w.a2.pk)])

    def test_another_teachers_tag_gives_an_empty_list(self):
        foreign = self.tag(self.w.other_teacher, "Forces")
        self.link(self.w.b1, foreign)
        self.assertEqual(ids(self.get(f"tags={foreign.pk}")), [])
        # Mixed with one of mine, it still cannot match.
        self.assertEqual(ids(self.get(f"tags={self.t1.pk},{foreign.pk}")), [])

    def test_an_unknown_id_gives_an_empty_list(self):
        response = self.get(f"tags={uuid.uuid4()}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ids(response), [])

    def test_a_bad_uuid_is_a_400(self):
        response = self.get("tags=not-a-uuid")
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("error", response.json())

    def test_combines_with_course_and_search(self):
        other_course = self.w.course(self.w.teacher, "Chemistry")
        extra = self.w.assignment(other_course, "Cells lab")
        self.link(extra, self.t1)
        both = ids(self.get(f"tags={self.t1.pk}"))
        self.assertEqual(len(both), 3)
        by_course = ids(self.get(f"tags={self.t1.pk}&course={other_course.pk}"))
        self.assertEqual(by_course, [str(extra.pk)])
        by_search = ids(self.get(f"tags={self.t1.pk}&search=Genetics"))
        self.assertEqual(by_search, [str(self.w.a2.pk)])
        ordered = ids(self.get(f"tags={self.t1.pk}&ordering=-title"))
        self.assertEqual(ordered, list(reversed(ids(self.get(f"tags={self.t1.pk}")))))

    def test_no_duplicate_rows(self):
        many = [self.tag(self.w.teacher, f"Extra{i}") for i in range(3)]
        self.link(self.w.a1, *many)
        query = ",".join(str(t.pk) for t in [self.t1, self.t2, *many])
        self.assertEqual(ids(self.get(f"tags={query}")), [str(self.w.a1.pk)])

    def test_query_count_does_not_grow_with_assignments(self):
        def count():
            cache.clear()
            with CaptureQueriesContext(connection) as queries:
                self.get(f"tags={self.t1.pk},{self.t2.pk}")
            return len(queries)

        before = count()
        for i in range(5):
            extra = self.w.assignment(self.w.course_a, f"More{i}")
            self.link(extra, self.t1, self.t2)
        self.assertEqual(count(), before)

    def test_ignored_for_students_and_admins(self):
        student = client_for(self.w.student)
        plain = ids(student.get(LIST))
        self.assertEqual(ids(self.get(f"tags={self.t1.pk}", student)), plain)
        admin = client_for(self.w.superadmin)
        self.assertEqual(
            ids(self.get(f"tags={self.t1.pk}", admin)), ids(admin.get(LIST))
        )
        # Not even a bad id is refused for them.
        self.assertEqual(self.get("tags=not-a-uuid", student).status_code, 200)

    def test_swagger_documents_the_parameter(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        names = {
            p["name"]
            for p in schema["paths"]["/api/v1/assignments"]["get"]["parameters"]
        }
        self.assertIn("tags", names)


@flag_off
class TagFilterOffTests(TestCase):
    def test_ignored_while_the_flag_is_off(self):
        cache.clear()
        w = World().build()
        api = client_for(w.teacher)
        plain = ids(api.get(LIST))
        self.assertEqual(len(plain), 2)
        tag = Tag.objects.create(
            owner_type="TEACHER",
            teacher=w.teacher,
            tag_type=TagType.UNIT,
            name="Cells",
            normalized_name="cells",
        )
        AssignmentTag.objects.create(assignment=w.a1, tag=tag)
        for query in (f"tags={tag.pk}", "tags=not-a-uuid", f"tags={uuid.uuid4()}"):
            response = api.get(f"{LIST}?{query}")
            self.assertEqual(response.status_code, 200)
            self.assertCountEqual(ids(response), plain)
