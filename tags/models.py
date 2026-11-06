"""Epic C1 tags (plan 11 §3.1): a teacher's own Subject and Unit tags, and
the links that file assignments under them.

Delete direction (the H-297 lesson): deleting a tag removes its links,
never an assignment; deleting an assignment removes its links; deleting a
teacher removes their tags. C2 (department tags) and C3 (lesson tags) widen
the two CHECK constraints additively.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class TagType(models.TextChoices):
    SUBJECT = "SUBJECT", "Subject"
    UNIT = "UNIT", "Unit"


class OwnerType(models.TextChoices):
    TEACHER = "TEACHER", "Teacher"


class LinkSource(models.TextChoices):
    USER = "USER", "User"
    TOPIC_BACKFILL = "TOPIC_BACKFILL", "Topic backfill"


class Tag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tag_type = models.CharField(max_length=16, choices=TagType.choices)
    #: As the teacher typed it, cleaned (tags.normalize). The first spelling
    #: wins; a later spelling with the same key reuses this tag.
    name = models.CharField(max_length=100)
    #: The uniqueness key (tags.normalize, FR-C-08).
    normalized_name = models.CharField(max_length=100)
    owner_type = models.CharField(
        max_length=16, choices=OwnerType.choices, default=OwnerType.TEACHER
    )
    #: Null only for owner types still to come (C2).
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        related_name="tags",
    )
    #: Who made it, captured as a value, not a link. Null when the topic
    #: backfill made it.
    created_by_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "tag_type", "normalized_name"],
                condition=Q(owner_type="TEACHER"),
                name="tags_tag_teacher_type_name_uniq",
            ),
            models.CheckConstraint(
                condition=Q(owner_type="TEACHER", teacher__isnull=False),
                name="tags_tag_teacher_owner_ck",
            ),
            models.CheckConstraint(
                condition=Q(tag_type__in=["SUBJECT", "UNIT"]),
                name="tags_tag_type_ck",
            ),
            models.CheckConstraint(
                condition=~Q(normalized_name=""),
                name="tags_tag_normalized_name_not_empty_ck",
            ),
        ]

    def __str__(self):
        return f"{self.get_tag_type_display()} tag {self.pk}"


class AssignmentTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(
        "assignments.Assignment",
        on_delete=models.CASCADE,
        related_name="tag_links",
    )
    tag = models.ForeignKey(
        Tag, on_delete=models.CASCADE, related_name="assignment_links"
    )
    #: TOPIC_BACKFILL links are the ones `backfill_topic_tags --reverse`
    #: removes. A set that keeps one leaves it TOPIC_BACKFILL (SM, C1 (ii)).
    source = models.CharField(
        max_length=16, choices=LinkSource.choices, default=LinkSource.USER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "tag"], name="tags_assignmenttag_uniq"
            ),
        ]

    def __str__(self):
        return f"AssignmentTag {self.pk}"
