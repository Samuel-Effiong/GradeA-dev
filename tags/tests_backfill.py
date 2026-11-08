"""Epic C1, FR-C-09: course topics become Unit tags (plan 11 §3.5).

`manage.py backfill_topic_tags [--dry-run] [--course <id>] [--reverse]`:
idempotent, reversible, never writes Topic or Assignment.topic, and does
not depend on the flag. Its last line is a parseable summary:

    RESULT tags_created=.. tags_reused=.. links_created=.. links_present=..
           skipped_no_course=.. skipped_no_teacher=.. skipped_bad_name=..
    RESULT links_removed=.. tags_removed=..          (with --reverse)
"""

import importlib
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from assignments.models import Assignment
from classrooms.models import Course, Topic
from tags._c1_fixtures import World, flag_off
from tags.models import AssignmentTag, LinkSource, Tag, TagType

# Not built yet when the expectations are committed (red first).
services = importlib.import_module("tags.services")


def run(**options):
    out = StringIO()
    call_command("backfill_topic_tags", stdout=out, **options)
    last = out.getvalue().strip().splitlines()[-1]
    assert last.startswith("RESULT "), out.getvalue()
    return {k: int(v) for k, v in (pair.split("=") for pair in last.split()[1:])}


def state():
    return (
        sorted(
            Tag.objects.values_list("teacher_id", "tag_type", "normalized_name", "name")
        ),
        sorted(
            AssignmentTag.objects.values_list(
                "assignment_id", "tag__normalized_name", "source"
            )
        ),
    )


def topics_and_filing():
    return (
        sorted(Topic.objects.values_list("id", "name", "course_id")),
        sorted(Assignment.objects.values_list("id", "topic_id")),
    )


@flag_off  # the command must not depend on the flag
class BackfillTest(TestCase):
    def setUp(self):
        self.w = World().build()
        w = self.w
        self.cells = Topic.objects.create(name="Cells", course=w.course_a)
        self.genetics = Topic.objects.create(name="Genetics", course=w.course_a)
        self.forces = Topic.objects.create(name="Forces", course=w.course_b)
        # The same name in another of teacher A's courses: one Unit tag.
        self.course_a2 = w.course(w.teacher, "Biology II")
        self.cells_again = Topic.objects.create(name=" CELLS ", course=self.course_a2)
        self.a3 = w.assignment(self.course_a2, "Mitosis")
        for assignment, topic in (
            (w.a1, self.cells),
            (w.a2, self.genetics),
            (w.b1, self.forces),
            (self.a3, self.cells_again),
        ):
            assignment.topic = topic
            assignment.save(update_fields=["topic"])
        self.unfiled = w.assignment(w.course_a, "Unfiled")
        self.filing = topics_and_filing()

    def test_forward(self):
        result = run()
        self.assertEqual(
            result,
            {
                "tags_created": 3,
                "tags_reused": 1,
                "links_created": 4,
                "links_present": 0,
                "skipped_no_course": 0,
                "skipped_no_teacher": 0,
                "skipped_bad_name": 0,
            },
        )
        tags = {(t.teacher_id, t.normalized_name): t for t in Tag.objects.all()}
        self.assertEqual(
            set(tags),
            {
                (self.w.teacher.id, "cells"),
                (self.w.teacher.id, "genetics"),
                (self.w.other_teacher.id, "forces"),
            },
        )
        for tag in tags.values():
            self.assertEqual(tag.tag_type, TagType.UNIT)
            self.assertIsNone(tag.created_by_id)
        cells = tags[(self.w.teacher.id, "cells")]
        self.assertEqual(
            set(cells.assignment_links.values_list("assignment_id", flat=True)),
            {self.w.a1.id, self.a3.id},
        )
        self.assertEqual(
            set(AssignmentTag.objects.values_list("source", flat=True)),
            {LinkSource.TOPIC_BACKFILL},
        )
        self.assertFalse(AssignmentTag.objects.filter(assignment=self.unfiled).exists())
        self.assertEqual(topics_and_filing(), self.filing)

    def test_a_second_run_changes_nothing(self):
        run()
        before = state()
        result = run()
        self.assertEqual(
            (result["tags_created"], result["links_created"], result["links_present"]),
            (0, 0, 4),
        )
        self.assertEqual(state(), before)

    def test_an_interrupted_run_is_completed_by_a_rerun(self):  # Gate 5
        run(course=str(self.w.course_a.id))
        self.assertEqual(AssignmentTag.objects.count(), 2)
        result = run()
        self.assertEqual((result["links_created"], result["links_present"]), (2, 2))
        self.assertEqual(AssignmentTag.objects.count(), 4)

    def test_reuses_a_tag_the_teacher_already_has(self):
        mine, _ = services.get_or_create_tag(self.w.teacher, TagType.UNIT, "cells")
        run()
        self.assertEqual(
            Tag.objects.filter(teacher=self.w.teacher, normalized_name="cells")
            .get()
            .pk,
            mine.pk,
        )

    def test_dry_run_writes_nothing_and_reports_the_same(self):
        dry = run(dry_run=True)
        self.assertEqual(state(), ([], []))
        self.assertEqual(dry, run())

    def test_course_option_limits_the_run(self):
        result = run(course=str(self.w.course_b.id))
        self.assertEqual((result["tags_created"], result["links_created"]), (1, 1))
        self.assertEqual(
            list(Tag.objects.values_list("normalized_name", flat=True)), ["forces"]
        )

    def test_skips_are_reported_with_their_reason(self):
        orphan = Topic.objects.create(name="Orphan", course=None)
        loose = Course.objects.create(name="No teacher", teacher=None)
        no_teacher = Topic.objects.create(name="Lost", course=loose)
        blank = Topic.objects.create(name="​⁠", course=self.w.course_a)
        for topic in (orphan, no_teacher, blank):
            assignment = Assignment.objects.create(
                title=f"For {topic.pk}",
                course=topic.course or self.w.course_a,
                topic=topic,
                total_points=5,
            )
            self.assertIsNotNone(assignment.pk)
        result = run()
        self.assertEqual(
            (
                result["skipped_no_course"],
                result["skipped_no_teacher"],
                result["skipped_bad_name"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(result["links_created"], 4)
        self.assertNotIn("skipped_symbols", result)

    def test_a_topic_name_with_symbols_is_copied(self):  # owner, 2026-11-08
        topic = Topic.objects.create(name="Q&A \U0001f52c", course=self.w.course_a)
        assignment = Assignment.objects.create(
            title="Lab", course=self.w.course_a, topic=topic, total_points=5
        )
        result = run()
        self.assertEqual(result["skipped_bad_name"], 0)
        tag = Tag.objects.get(normalized_name="q&a \U0001f52c")
        self.assertEqual(tag.name, "Q&A \U0001f52c")
        self.assertTrue(
            AssignmentTag.objects.filter(assignment=assignment, tag=tag).exists()
        )
        topic.refresh_from_db()
        self.assertEqual(topic.name, "Q&A \U0001f52c")

    def test_a_curly_apostrophe_topic_becomes_the_straight_tag(self):  # 2026-11-08
        mine, _ = services.get_or_create_tag(
            self.w.teacher, TagType.UNIT, "Newton's Laws"
        )
        topic = Topic.objects.create(name="Newton\u2019s Laws", course=self.w.course_a)
        assignment = Assignment.objects.create(
            title="Forces lab", course=self.w.course_a, topic=topic, total_points=5
        )
        result = run()
        self.assertEqual(result["skipped_bad_name"], 0)
        tag = Tag.objects.get(teacher=self.w.teacher, normalized_name="newton's laws")
        self.assertEqual((tag.pk, tag.name), (mine.pk, "Newton's Laws"))
        self.assertTrue(
            AssignmentTag.objects.filter(assignment=assignment, tag=tag).exists()
        )
        self.assertFalse(Tag.objects.filter(name__contains="\u2019").exists())
        topic.refresh_from_db()
        self.assertEqual(topic.name, "Newton\u2019s Laws")

    def test_a_new_tag_from_a_curly_topic_is_stored_straight(self):
        topic = Topic.objects.create(name="Hooke\u2019s Law", course=self.w.course_a)
        Assignment.objects.create(
            title="Springs", course=self.w.course_a, topic=topic, total_points=5
        )
        run()
        tag = Tag.objects.get(teacher=self.w.teacher, normalized_name="hooke's law")
        self.assertEqual(tag.name, "Hooke's Law")
        topic.refresh_from_db()
        self.assertEqual(topic.name, "Hooke\u2019s Law")

    def test_reverse_removes_exactly_what_the_backfill_made(self):
        run()
        user_tag, _ = services.get_or_create_tag(self.w.teacher, TagType.UNIT, "Mine")
        genetics = Tag.objects.get(normalized_name="genetics")
        # A teacher's own link to a backfilled tag keeps that tag.
        services.set_assignment_tags(
            self.unfiled, [user_tag.pk, genetics.pk], teacher=self.w.teacher
        )
        result = run(reverse=True)
        self.assertEqual(result, {"links_removed": 4, "tags_removed": 2})
        self.assertEqual(
            sorted(Tag.objects.values_list("normalized_name", flat=True)),
            ["genetics", "mine"],
        )
        self.assertEqual(
            set(AssignmentTag.objects.values_list("tag_id", "source")),
            {(user_tag.pk, LinkSource.USER), (genetics.pk, LinkSource.USER)},
        )
        self.assertEqual(topics_and_filing(), self.filing)

    def test_reverse_twice_is_harmless(self):
        run()
        run(reverse=True)
        self.assertEqual(run(reverse=True), {"links_removed": 0, "tags_removed": 0})
