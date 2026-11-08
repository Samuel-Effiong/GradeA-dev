"""Epic C1: the tables and their database rules (plan 11 §3.1, §4).

Every constraint is proved at the database: rows are written with
`objects.create` / raw `bulk_create`, bypassing the service, so a missing
constraint cannot hide behind service-side checks.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from students.models import StudentSubmission
from tags._c1_test_fixtures import World
from tags.models import AssignmentTag, LinkSource, OwnerType, Tag, TagType


class TagConstraintTest(TestCase):
    def setUp(self):
        self.w = World().build()

    def tag(self, teacher=None, tag_type=TagType.UNIT, name="Cells", key=None, **extra):
        return Tag.objects.create(
            teacher=teacher or self.w.teacher,
            tag_type=tag_type,
            name=name,
            normalized_name=key if key is not None else name.lower(),
            **extra,
        )

    def assertRefused(self, make):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make()

    def test_the_c1_types_and_owner(self):
        self.assertEqual(set(TagType.values), {"SUBJECT", "UNIT"})
        self.assertEqual(set(OwnerType.values), {"TEACHER"})
        self.assertEqual(set(LinkSource.values), {"USER", "TOPIC_BACKFILL"})
        tag = self.tag()
        self.assertEqual(tag.owner_type, OwnerType.TEACHER)
        self.assertIsNone(tag.created_by_id)

    def test_one_tag_per_teacher_type_and_normalized_name(self):
        self.tag(name="Cells")
        self.assertRefused(lambda: self.tag(name="CELLS", key="cells"))

    def test_the_same_name_is_fine_for_another_type_or_teacher(self):
        self.tag(name="Cells")
        self.tag(name="Cells", tag_type=TagType.SUBJECT)
        self.tag(name="Cells", teacher=self.w.other_teacher)
        self.assertEqual(Tag.objects.filter(normalized_name="cells").count(), 3)

    def test_a_lesson_tag_is_refused_by_the_database(self):
        self.assertRefused(lambda: self.tag(tag_type="LESSON"))

    def test_an_unknown_owner_type_is_refused_by_the_database(self):
        self.assertRefused(lambda: self.tag(owner_type="DEPARTMENT"))

    def test_a_teacher_tag_needs_a_teacher(self):
        self.assertRefused(
            lambda: Tag.objects.create(
                teacher=None,
                tag_type=TagType.UNIT,
                name="Cells",
                normalized_name="cells",
            )
        )

    def test_an_empty_normalized_name_is_refused_by_the_database(self):
        self.assertRefused(lambda: self.tag(name="Cells", key=""))

    def test_one_link_per_assignment_and_tag(self):
        tag = self.tag()
        AssignmentTag.objects.create(assignment=self.w.a1, tag=tag)
        self.assertRefused(
            lambda: AssignmentTag.objects.create(
                assignment=self.w.a1, tag=tag, source=LinkSource.TOPIC_BACKFILL
            )
        )

    def test_a_link_is_user_made_by_default(self):
        link = AssignmentTag.objects.create(assignment=self.w.a1, tag=self.tag())
        self.assertEqual(link.source, LinkSource.USER)


class DeleteDirectionTest(TestCase):
    """The H-297 lesson: deleting a tag removes links, never assignments."""

    def setUp(self):
        self.w = World().build()
        self.tag = Tag.objects.create(
            teacher=self.w.teacher,
            tag_type=TagType.UNIT,
            name="Cells",
            normalized_name="cells",
        )
        for assignment in (self.w.a1, self.w.a2):
            AssignmentTag.objects.create(assignment=assignment, tag=self.tag)
        StudentSubmission.objects.create(
            student=self.w.student, assignment=self.w.a1, answers={}
        )

    def test_deleting_a_tag_keeps_every_assignment_and_submission(self):
        assignments = self.w.course_a.assignments.count()
        submissions = StudentSubmission.objects.count()
        self.tag.delete()
        self.assertEqual(self.w.course_a.assignments.count(), assignments)
        self.assertEqual(StudentSubmission.objects.count(), submissions)
        self.assertFalse(AssignmentTag.objects.exists())

    def test_deleting_an_assignment_removes_only_its_links(self):
        self.w.a1.delete()
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())
        self.assertEqual(
            list(AssignmentTag.objects.values_list("assignment_id", flat=True)),
            [self.w.a2.id],
        )

    def test_deleting_a_teacher_removes_their_tags(self):
        # A teacher with no courses: deleting one whose courses hold
        # assignments fails today in assignments/signals.py, before any tag
        # code runs (pre-existing, reported to the SM; NOTES deviation).
        leaving = self.w.user("TEACHER", "Lee")
        Tag.objects.create(
            teacher=leaving,
            tag_type=TagType.SUBJECT,
            name="Biology",
            normalized_name="biology",
        )
        leaving.delete()
        self.assertEqual(list(Tag.objects.all()), [self.tag])
