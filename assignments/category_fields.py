"""Epic E2: an assignment's `category` and `relative_weight` on the doors
that make or edit one (FR-E-03/05, Q3; SM 2026-11-18 point 4).

Present only while GRADE_CATEGORIES_ENABLED is on, so a dark build's
requests and responses are unchanged. The rules, one place for every door:

* the category must be one of the assignment's course's categories;
* a relative weight must be above 0 (W4);
* in a course that uses categories, a NEW assignment must pick one, and an
  edit may not clear it or move the assignment to such a course without one
  (W6). The JSON create doors, PATCH, the file uploads (a multipart field,
  carried to the saved draft) and the AI draft save all apply it. Only a
  copy whose category name has no match lands uncategorized (X-6); the
  grading-policy warning (W5) names those.
"""

import uuid

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from classrooms import grade_service
from classrooms.grade_categories import W4_RELATIVE_WEIGHT, W6_CATEGORY_REQUIRED
from classrooms.models import CourseCategory

FIELDS = ("category", "relative_weight")


def enabled():
    return grade_service.categories_enabled()


def _not_found(value):
    # DRF's own PrimaryKeyRelatedField message, so no new wording.
    return {"category": ['Invalid pk "' + str(value) + '" - object does not exist.']}


def add_fields(serializer):
    """Add the two fields to a request serializer, while the flag is on."""
    if not enabled():
        return
    serializer.fields["category"] = serializers.PrimaryKeyRelatedField(
        queryset=CourseCategory.objects.filter(course__isnull=False),
        required=False,
        allow_null=True,
    )
    serializer.fields["relative_weight"] = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False
    )


def _course_uses_categories(course):
    return CourseCategory.objects.filter(course=course).exists()


def validate(data, course, *, instance=None):
    """Check `data`'s category fields for an assignment in `course` (new
    when `instance` is None). Clears a category that belongs to the course
    an edit moves the assignment away from. Returns `data`."""
    if not enabled() or course is None:
        return data
    weight = data.get("relative_weight")
    if weight is not None and weight <= 0:
        raise ValidationError({"relative_weight": [W4_RELATIVE_WEIGHT]})

    moving = instance is not None and instance.course_id != course.pk
    if "category" in data:
        category = data["category"]
    elif instance is not None and not moving:
        category = instance.category
    else:
        category = None
        if moving and instance.category_id is not None:
            data["category"] = None

    if category is not None and category.course_id != course.pk:
        raise ValidationError(_not_found(category.pk))
    must_pick = instance is None or "category" in data or moving
    if must_pick and category is None and _course_uses_categories(course):
        raise ValidationError({"category": [W6_CATEGORY_REQUIRED]})
    return data


def pop(validated_data):
    """The category fields, removed from `validated_data` (for doors that
    pass the rest on as model or snapshot fields)."""
    return {
        field: validated_data.pop(field) for field in FIELDS if field in validated_data
    }


def picked(validated_data):
    return {field: validated_data[field] for field in FIELDS if field in validated_data}


def apply(instance, fields):
    for field, value in fields.items():
        setattr(instance, field, value)


def resolve_upload_category(course, value):
    """The category a file upload names (a multipart field), checked before
    anything is charged; None when the course uses no categories and none
    was named. W6 when it uses them and none was named."""
    if not enabled():
        return None
    value = value.strip() if isinstance(value, str) else value
    if not value:
        if _course_uses_categories(course):
            raise ValidationError({"category": [W6_CATEGORY_REQUIRED]})
        return None
    try:
        category_id = uuid.UUID(str(value))
    except ValueError:
        category_id = None
    category = (
        CourseCategory.objects.filter(course=course, pk=category_id).first()
        if category_id
        else None
    )
    if category is None:
        raise ValidationError(_not_found(value))
    return category


def upload_category_for_task(course, category_id):
    """The category a background upload carries (the id the request
    checked), or None. A category deleted since leaves the draft
    uncategorized rather than failing the paid extraction."""
    if not category_id:
        return None
    return CourseCategory.objects.filter(course=course, pk=category_id).first()
