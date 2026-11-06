"""Epic C1: the tag service, the only writer of tags and links
(plan 11 §3.3). Behavior, audit (FR-A-01) and zero-credit (FR-B-10)."""

import importlib
import json
import uuid

from django.http import Http404
from django.test import TestCase

from audit.enums import AuditAction
from audit.models import AuditEvent
from AutoGrader.reason_codes import CodedError
from billing import zero_credit
from billing.testing.zero_credit import assert_operation_is_zero_credit
from tags._c1_fixtures import World
from tags.models import AssignmentTag, LinkSource, Tag, TagType

# Not built yet when the expectations are committed (red first).
services = importlib.import_module("tags.services")
TAGS_SET = AuditAction("ASSIGNMENT_TAGS_SET")


def events(action):
    return list(AuditEvent.objects.filter(action=action).order_by("occurred_at"))


class _ServiceCase(TestCase):
    def setUp(self):
        self.w = World().build()

    def make(self, name="Cells", tag_type=TagType.UNIT, teacher=None):
        tag, _ = services.get_or_create_tag(teacher or self.w.teacher, tag_type, name)
        return tag

    def assertCode(self, code, fn):
        with self.assertRaises(CodedError) as caught:
            fn()
        self.assertEqual(caught.exception.reason_code, code)

    def assertNoNames(self, event):
        # Audit allow-list: ids, counts and labels, never text a person wrote.
        text = json.dumps([event.metadata, event.before, event.after])
        for word in ("Cells", "cells", "Genetics", "genetics", "Cell Biology"):
            self.assertNotIn(word, text)


class GetOrCreateTest(_ServiceCase):
    def test_create_then_reuse_by_normalized_name(self):
        tag, created = services.get_or_create_tag(self.w.teacher, TagType.UNIT, "Cells")
        self.assertTrue(created)
        for typed in ("cells", " CELLS ", "Cells\u00a0"):
            with self.subTest(typed=typed):
                again, created = services.get_or_create_tag(
                    self.w.teacher, TagType.UNIT, typed
                )
                self.assertFalse(created)
                self.assertEqual(again.pk, tag.pk)
        self.assertEqual(Tag.objects.count(), 1)

    def test_the_first_spelling_wins(self):
        self.make("Cell biology")
        self.make("CELL   BIOLOGY")
        self.assertEqual(Tag.objects.get().name, "Cell biology")

    def test_fields_of_a_new_tag(self):
        tag = self.make("  Cell   Biology ")
        self.assertEqual(tag.name, "Cell Biology")
        self.assertEqual(tag.normalized_name, "cell biology")
        self.assertEqual(tag.teacher, self.w.teacher)
        self.assertEqual(tag.created_by_id, self.w.teacher.id)

    def test_scope_is_the_teacher_and_the_type(self):
        mine = self.make("Cells")
        self.assertNotEqual(self.make("Cells", TagType.SUBJECT).pk, mine.pk)
        self.assertNotEqual(
            self.make("Cells", teacher=self.w.other_teacher).pk, mine.pk
        )

    def test_bad_names_are_refused_and_nothing_is_written(self):
        self.assertCode(
            "TAG_NAME_MISSING",
            lambda: services.get_or_create_tag(self.w.teacher, TagType.UNIT, "  "),
        )
        self.assertCode(
            "TAG_NAME_TOO_LONG",
            lambda: services.get_or_create_tag(self.w.teacher, TagType.UNIT, "x" * 101),
        )
        self.assertFalse(Tag.objects.exists())

    def test_a_lesson_tag_is_refused(self):
        with self.assertRaises(ValueError):
            services.get_or_create_tag(self.w.teacher, "LESSON", "Cells")
        self.assertFalse(Tag.objects.exists())

    def test_create_is_audited_once_and_reuse_not_at_all(self):
        tag = self.make("Cells")
        self.make("CELLS")
        [event] = events(AuditAction.TAG_CREATE)
        self.assertEqual(event.actor_id, self.w.teacher.id)
        self.assertEqual(event.target_type, "Tag")
        self.assertEqual(event.target_id, tag.pk)
        self.assertEqual(event.metadata, {"tag_type": "UNIT"})
        self.assertNoNames(event)


class RenameTest(_ServiceCase):
    def test_rename(self):
        tag = self.make("Cells")
        services.rename_tag(tag, "  Cell   Structure ")
        tag.refresh_from_db()
        self.assertEqual(
            (tag.name, tag.normalized_name), ("Cell Structure", "cell structure")
        )

    def test_a_casing_change_keeps_the_key(self):
        tag = self.make("cells")
        services.rename_tag(tag, "CELLS")
        tag.refresh_from_db()
        self.assertEqual((tag.name, tag.normalized_name), ("CELLS", "cells"))

    def test_a_collision_is_refused_not_merged(self):
        tag = self.make("Cells")
        self.make("Genetics")
        self.assertCode("TAG_NAME_TAKEN", lambda: services.rename_tag(tag, " genetics"))
        tag.refresh_from_db()
        self.assertEqual(tag.name, "Cells")
        self.assertEqual(Tag.objects.count(), 2)

    def test_the_collision_message_is_the_approved_wording(self):
        tag = self.make("Cells")
        self.make("Genetics")
        with self.assertRaises(CodedError) as caught:
            services.rename_tag(tag, "genetics")
        self.assertEqual(
            str(caught.exception), 'You already have a Unit tag named "Genetics".'
        )
        # SM (i): the type is the code in params and the existing TagType
        # label in the message; the name is the stored spelling, not the
        # typed one.
        self.assertEqual(
            caught.exception.params, {"tag_type": "UNIT", "name": "Genetics"}
        )

    def test_the_collision_message_uses_the_type_label(self):
        tag = self.make("Biology", TagType.SUBJECT)
        self.make("Chemistry", TagType.SUBJECT)
        with self.assertRaises(CodedError) as caught:
            services.rename_tag(tag, "CHEMISTRY")
        self.assertEqual(
            str(caught.exception),
            f'You already have a {TagType.SUBJECT.label} tag named "Chemistry".',
        )
        self.assertEqual(caught.exception.params["tag_type"], "SUBJECT")

    def test_no_collision_across_type_or_teacher(self):
        tag = self.make("Cells")
        self.make("Genetics", TagType.SUBJECT)
        self.make("Genetics", teacher=self.w.other_teacher)
        services.rename_tag(tag, "Genetics")
        tag.refresh_from_db()
        self.assertEqual(tag.name, "Genetics")

    def test_bad_names_are_refused(self):
        tag = self.make("Cells")
        self.assertCode("TAG_NAME_MISSING", lambda: services.rename_tag(tag, ""))
        self.assertCode(
            "TAG_NAME_TOO_LONG", lambda: services.rename_tag(tag, "y" * 101)
        )

    def test_rename_is_audited_without_names(self):
        tag = self.make("Cells")
        services.rename_tag(tag, "Genetics")
        [event] = events(AuditAction.TAG_RENAME)
        self.assertEqual((event.actor_id, event.target_id), (self.w.teacher.id, tag.pk))
        self.assertEqual(event.metadata, {"tag_type": "UNIT"})
        self.assertNoNames(event)


class ImpactAndDeleteTest(_ServiceCase):
    def setUp(self):
        super().setUp()
        self.tag = self.make("Cells")
        services.set_assignment_tags(self.w.a1, [self.tag.pk], teacher=self.w.teacher)
        services.set_assignment_tags(self.w.a2, [self.tag.pk], teacher=self.w.teacher)

    def test_impact_counts_links(self):
        self.assertEqual(services.tag_impact(self.tag), 2)
        self.assertEqual(services.tag_impact(self.make("Unused")), 0)

    def test_delete_with_the_previewed_count(self):
        self.assertEqual(services.delete_tag(self.tag, expected_count=2), 2)
        self.assertFalse(Tag.objects.filter(pk=self.tag.pk).exists())
        self.assertFalse(AssignmentTag.objects.exists())
        self.assertEqual(self.w.course_a.assignments.count(), 2)

    def test_a_stale_count_is_refused_and_nothing_is_deleted(self):
        for stale in (0, 1, 3):
            with self.subTest(expected=stale):
                self.assertCode(
                    "TAG_CHANGED_SINCE_CHECK",
                    lambda stale=stale: services.delete_tag(
                        self.tag, expected_count=stale
                    ),
                )
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())
        self.assertEqual(AssignmentTag.objects.count(), 2)
        self.assertFalse(events(AuditAction.TAG_DELETE))

    def test_delete_is_audited_with_the_count(self):
        tag_id = self.tag.pk
        services.delete_tag(self.tag, expected_count=2)
        [event] = events(AuditAction.TAG_DELETE)
        self.assertEqual((event.actor_id, event.target_id), (self.w.teacher.id, tag_id))
        self.assertEqual(event.metadata, {"tag_type": "UNIT", "affected_count": 2})
        self.assertNoNames(event)


class SetAssignmentTagsTest(_ServiceCase):
    def ids(self, assignment):
        return set(
            AssignmentTag.objects.filter(assignment=assignment).values_list(
                "tag_id", flat=True
            )
        )

    def test_the_set_replaces_exactly(self):
        cells, genetics, biology = (
            self.make("Cells"),
            self.make("Genetics"),
            self.make("Biology", TagType.SUBJECT),
        )
        services.set_assignment_tags(
            self.w.a1, [cells.pk, genetics.pk], teacher=self.w.teacher
        )
        self.assertEqual(self.ids(self.w.a1), {cells.pk, genetics.pk})
        result = services.set_assignment_tags(
            self.w.a1, [genetics.pk, biology.pk], teacher=self.w.teacher
        )
        self.assertEqual(self.ids(self.w.a1), {genetics.pk, biology.pk})
        self.assertEqual({tag.pk for tag in result}, {genetics.pk, biology.pk})
        services.set_assignment_tags(self.w.a1, [], teacher=self.w.teacher)
        self.assertEqual(self.ids(self.w.a1), set())

    def test_several_tags_of_one_type(self):  # FR-C-04
        subjects = [
            self.make(n, TagType.SUBJECT) for n in ("Biology", "Science", "STEM")
        ]
        services.set_assignment_tags(
            self.w.a1, [t.pk for t in subjects], teacher=self.w.teacher
        )
        self.assertEqual(self.ids(self.w.a1), {t.pk for t in subjects})

    def test_backfilled_links_are_replaced_too_and_kept_ones_keep_their_source(self):
        kept, dropped = self.make("Cells"), self.make("Genetics")
        for tag in (kept, dropped):
            AssignmentTag.objects.create(
                assignment=self.w.a1, tag=tag, source=LinkSource.TOPIC_BACKFILL
            )
        services.set_assignment_tags(self.w.a1, [kept.pk], teacher=self.w.teacher)
        self.assertEqual(
            list(AssignmentTag.objects.values_list("tag_id", "source")),
            [(kept.pk, LinkSource.TOPIC_BACKFILL)],
        )

    def test_duplicate_ids_count_once(self):
        tag = self.make("Cells")
        services.set_assignment_tags(
            self.w.a1, [tag.pk, tag.pk], teacher=self.w.teacher
        )
        self.assertEqual(AssignmentTag.objects.count(), 1)

    def test_another_teachers_tag_is_not_found_and_nothing_changes(self):
        mine = self.make("Cells")
        services.set_assignment_tags(self.w.a1, [mine.pk], teacher=self.w.teacher)
        theirs = self.make("Cells", teacher=self.w.other_teacher)
        for bad in (theirs.pk, uuid.uuid4()):
            with self.subTest(tag=bad):
                with self.assertRaises(Http404):
                    services.set_assignment_tags(
                        self.w.a1, [mine.pk, bad], teacher=self.w.teacher
                    )
                self.assertEqual(self.ids(self.w.a1), {mine.pk})

    def test_the_cap_is_fifty(self):
        self.assertEqual(services.MAX_TAGS_PER_ASSIGNMENT, 50)
        tags = [self.make(f"Unit {i}") for i in range(51)]
        services.set_assignment_tags(
            self.w.a1, [t.pk for t in tags[:50]], teacher=self.w.teacher
        )
        self.assertEqual(len(self.ids(self.w.a1)), 50)
        self.assertCode(
            "TOO_MANY_TAGS",
            lambda: services.set_assignment_tags(
                self.w.a2, [t.pk for t in tags], teacher=self.w.teacher
            ),
        )
        self.assertEqual(self.ids(self.w.a2), set())

    def test_set_is_audited_with_before_and_after_ids(self):
        cells, genetics = self.make("Cells"), self.make("Genetics")
        services.set_assignment_tags(self.w.a1, [cells.pk], teacher=self.w.teacher)
        services.set_assignment_tags(self.w.a1, [genetics.pk], teacher=self.w.teacher)
        first, second = events(TAGS_SET)
        self.assertEqual(second.actor_id, self.w.teacher.id)
        self.assertEqual(
            (second.target_type, second.target_id), ("Assignment", self.w.a1.pk)
        )
        self.assertEqual(second.before, {"tag_ids": [str(cells.pk)]})
        self.assertEqual(second.after, {"tag_ids": [str(genetics.pk)]})
        self.assertEqual(second.metadata, {"course_id": str(self.w.course_a.pk)})
        self.assertEqual(first.before, {"tag_ids": []})
        self.assertNoNames(second)

    def test_the_audit_keeps_all_fifty_ids(self):
        tags = [self.make(f"Unit {i}") for i in range(50)]
        services.set_assignment_tags(
            self.w.a1, [t.pk for t in tags], teacher=self.w.teacher
        )
        [event] = events(TAGS_SET)
        self.assertEqual(
            sorted(event.after["tag_ids"]), sorted(str(t.pk) for t in tags)
        )

    def test_the_audit_label(self):
        self.assertEqual(TAGS_SET.label, "Assignment tags changed")


class ResolveInScopeTest(_ServiceCase):
    """For E2's copy (§0.3): the same tag in the destination owner's scope."""

    def test_same_owner_gets_the_same_tag(self):
        tag = self.make("Cells")
        self.assertEqual(services.resolve_in_scope(tag, self.w.teacher).pk, tag.pk)

    def test_another_owner_reuses_their_own_spelling(self):
        tag = self.make("Cells")
        theirs = self.make("CELLS", teacher=self.w.other_teacher)
        self.assertEqual(
            services.resolve_in_scope(tag, self.w.other_teacher).pk, theirs.pk
        )

    def test_another_owner_without_one_gets_a_new_tag(self):
        tag = self.make("Cell Biology", TagType.SUBJECT)
        made = services.resolve_in_scope(tag, self.w.other_teacher)
        self.assertNotEqual(made.pk, tag.pk)
        self.assertEqual(
            (made.teacher, made.tag_type, made.name, made.normalized_name),
            (self.w.other_teacher, TagType.SUBJECT, "Cell Biology", "cell biology"),
        )


class ZeroCreditTest(_ServiceCase):
    """FR-B-10: every tag operation is provably zero-credit."""

    OPERATIONS = (
        "tag.get_or_create",
        "tag.rename",
        "tag.impact",
        "tag.delete",
        "tag.set_assignment_tags",
        "tag.resolve_in_scope",
    )

    def test_every_operation_is_registered(self):
        for name in self.OPERATIONS:
            with self.subTest(operation=name):
                self.assertIn(name, zero_credit.ZERO_CREDIT_OPERATIONS)

    def test_get_or_create(self):
        assert_operation_is_zero_credit(
            self,
            "tag.get_or_create",
            lambda: services.get_or_create_tag(self.w.teacher, TagType.UNIT, "Cells"),
        )

    def test_rename(self):
        tag = self.make("Cells")
        assert_operation_is_zero_credit(
            self, "tag.rename", lambda: services.rename_tag(tag, "Genetics")
        )

    def test_impact(self):
        tag = self.make("Cells")
        assert_operation_is_zero_credit(
            self, "tag.impact", lambda: services.tag_impact(tag)
        )

    def test_delete(self):
        tag = self.make("Cells")
        assert_operation_is_zero_credit(
            self, "tag.delete", lambda: services.delete_tag(tag, expected_count=0)
        )

    def test_set_assignment_tags(self):
        tag = self.make("Cells")
        assert_operation_is_zero_credit(
            self,
            "tag.set_assignment_tags",
            lambda: services.set_assignment_tags(
                self.w.a1, [tag.pk], teacher=self.w.teacher
            ),
        )

    def test_resolve_in_scope(self):
        tag = self.make("Cells")
        assert_operation_is_zero_credit(
            self,
            "tag.resolve_in_scope",
            lambda: services.resolve_in_scope(tag, self.w.other_teacher),
        )
