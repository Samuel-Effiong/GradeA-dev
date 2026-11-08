"""Epic C1, FR-C-09: course topics become Unit tags (plan 11 §3.5).

    manage.py backfill_topic_tags [--dry-run] [--course <id>] [--reverse]

Forward, idempotent: every topic whose course has a teacher becomes that
teacher's Unit tag (found or created, by the FR-C-08 normalized name, with
no `created_by_id`), and every assignment filed under the topic gets a
TOPIC_BACKFILL link to it. Two topics with the same name in two of one
teacher's courses become one tag (counted as reused). A rerun creates
nothing it already made and completes an interrupted run.

--reverse rolls a bad backfill back: it removes every TOPIC_BACKFILL link,
then every tag with no `created_by_id` that has no link left. A backfilled
link stays TOPIC_BACKFILL even when a teacher keeps it while setting an
assignment's tags (keeping it doesn't show the teacher chose it), so the
reverse removes it too. A link the teacher added, and any tag a teacher
made, are never touched (SM ruling (ii)).

Neither direction writes Topic or Assignment.topic, and neither depends on
TAGS_ENABLED. --dry-run runs everything in a transaction that is rolled
back, so it reports exactly what a real run would do. The last line is a
parseable summary starting with "RESULT ".
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from assignments.models import Assignment
from audit.emitter import emit
from audit.enums import AuditAction
from AutoGrader.reason_codes import CodedError
from classrooms.models import Topic
from tags.models import AssignmentTag, LinkSource, OwnerType, Tag, TagType
from tags.normalize import clean_tag_name

FORWARD_KEYS = (
    "tags_created",
    "tags_reused",
    "links_created",
    "links_present",
    "skipped_no_course",
    "skipped_no_teacher",
    "skipped_bad_name",
    # A name with symbols (TAG_NAME_HAS_SYMBOLS): counted apart (SM, C1 (a)).
    "skipped_symbols",
)
REVERSE_KEYS = ("links_removed", "tags_removed")


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = (
        "Turn course topics into Unit tags (FR-C-09). Idempotent. --reverse "
        "removes every TOPIC_BACKFILL link, including one a teacher kept when "
        "setting an assignment's tags, and the backfill's tags left unused."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", dest="dry_run")
        parser.add_argument("--course", help="Only this course's topics (an id).")
        parser.add_argument(
            "--reverse",
            action="store_true",
            help=(
                "Roll the backfill back: remove every TOPIC_BACKFILL link (a "
                "backfilled link a teacher kept is still TOPIC_BACKFILL and is "
                "removed), then the backfill's tags that have no link left."
            ),
        )

    def handle(self, *args, dry_run=False, course=None, reverse=False, **options):
        course_id = self._course_id(course)
        counts = {}
        try:
            with transaction.atomic():
                counts = (
                    self._reverse(course_id) if reverse else self._forward(course_id)
                )
                if dry_run:
                    raise _DryRun
        except _DryRun:
            pass
        keys = REVERSE_KEYS if reverse else FORWARD_KEYS
        if dry_run:
            self.stdout.write("Dry run: rolled back, nothing was written.")
        self.stdout.write("RESULT " + " ".join(f"{k}={counts[k]}" for k in keys))

    @staticmethod
    def _course_id(course):
        if course is None:
            return None
        try:
            return uuid.UUID(str(course))
        except ValueError:
            raise CommandError(
                f"--course must be a course id, got {course!r}"
            ) from None

    def _forward(self, course_id):
        counts = dict.fromkeys(FORWARD_KEYS, 0)
        topics = Topic.objects.select_related("course").order_by("created_at", "id")
        if course_id is not None:
            topics = topics.filter(course_id=course_id)
        for topic in topics:
            course = topic.course
            if course is None:
                counts["skipped_no_course"] += 1
                continue
            if course.teacher_id is None:
                counts["skipped_no_teacher"] += 1
                continue
            try:
                display, key = clean_tag_name(topic.name)
            except CodedError as refused:
                symbols = refused.reason_code == "TAG_NAME_HAS_SYMBOLS"
                counts["skipped_symbols" if symbols else "skipped_bad_name"] += 1
                continue
            tag, created = self._unit_tag(course.teacher_id, display, key)
            counts["tags_created" if created else "tags_reused"] += 1
            assignment_ids = list(
                Assignment.objects.filter(topic=topic).values_list("id", flat=True)
            )
            present = set(
                AssignmentTag.objects.filter(
                    tag=tag, assignment_id__in=assignment_ids
                ).values_list("assignment_id", flat=True)
            )
            new = [a for a in assignment_ids if a not in present]
            AssignmentTag.objects.bulk_create(
                [
                    AssignmentTag(
                        assignment_id=a, tag=tag, source=LinkSource.TOPIC_BACKFILL
                    )
                    for a in new
                ],
                ignore_conflicts=True,
            )
            counts["links_created"] += len(new)
            counts["links_present"] += len(present)
        return counts

    @staticmethod
    def _unit_tag(teacher_id, display, key):
        scope = {
            "owner_type": OwnerType.TEACHER,
            "teacher_id": teacher_id,
            "tag_type": TagType.UNIT,
            "normalized_name": key,
        }
        existing = Tag.objects.filter(**scope).first()
        if existing is not None:
            return existing, False
        tag = Tag.objects.create(**scope, name=display, created_by_id=None)
        emit(
            AuditAction.TAG_CREATE,
            target_type="Tag",
            target_id=tag.pk,
            metadata={"tag_type": TagType.UNIT.value},
        )
        return tag, True

    def _reverse(self, course_id):
        links = AssignmentTag.objects.filter(source=LinkSource.TOPIC_BACKFILL)
        if course_id is not None:
            links = links.filter(assignment__course_id=course_id)
        links_removed, _ = links.delete()
        orphans = (
            Tag.objects.filter(created_by_id__isnull=True)
            .annotate(links=Count("assignment_links"))
            .filter(links=0)
        )
        tags_removed = 0
        for tag in orphans:
            tag_id, tag_type = tag.pk, tag.tag_type
            tag.delete()
            tags_removed += 1
            emit(
                AuditAction.TAG_DELETE,
                target_type="Tag",
                target_id=tag_id,
                metadata={"tag_type": tag_type, "affected_count": 0},
            )
        return {"links_removed": links_removed, "tags_removed": tags_removed}
