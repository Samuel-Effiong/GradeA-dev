"""API documentation for the grade-category routes (drf-spectacular).
Docs only: nothing here is read at request time."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers


def many(serializer, **kwargs):
    """The same inline serializer as a list."""
    return type(serializer)(many=True, **kwargs)


CATEGORIES_TAG = "Grade Categories"
ASSIGNMENTS_TAG = "Assignments"

ERROR_BODY = inline_serializer(
    "GradeCategoryError",
    {"detail": serializers.CharField(help_text="The message to show.")},
)

FLAG_OFF = (
    "While the grade-categories switch is off every route answers as an unknown "
    "URL (404), before sign-in."
)
COURSE_WHO = (
    "Teachers only, for a course they can reach; any other course is the house 404, "
    "never a 403. Students get nothing here."
)
TEMPLATE_WHO = (
    "A teacher manages their own templates and reads their school's; a school "
    "admin manages their school's. Anyone else gets 403."
)

ITEM = inline_serializer(
    "CategoryItemBody",
    {
        "id": serializers.UUIDField(
            required=False,
            allow_null=True,
            help_text="Send an existing category's id to keep it; omit for a new one.",
        ),
        "name": serializers.CharField(max_length=100),
        "weight_percent": serializers.DecimalField(max_digits=5, decimal_places=2),
    },
)
CATEGORY_SET_BODY = inline_serializer(
    "CategorySetBody",
    {
        "categories": many(ITEM),
    },
)
CATEGORY_ROW = inline_serializer(
    "CategoryRow",
    {
        "id": serializers.UUIDField(),
        "name": serializers.CharField(),
        "weight_percent": serializers.DecimalField(max_digits=5, decimal_places=2),
        "position": serializers.IntegerField(),
    },
)
POLICY = inline_serializer(
    "GradingPolicy",
    {
        "uses_categories": serializers.BooleanField(),
        "missing_counts_as_zero": serializers.BooleanField(),
        "uncategorized_count": serializers.IntegerField(),
        "rules": serializers.ListField(
            child=serializers.CharField(), help_text="The approved sentences."
        ),
        "warnings": serializers.ListField(child=serializers.CharField()),
    },
)
CATEGORY_SET_REPLY = inline_serializer(
    "CategorySetReply",
    {"categories": many(CATEGORY_ROW), "policy": POLICY},
)
PREVIEW_REPLY = inline_serializer(
    "CategoryPreviewReply",
    {
        "students": inline_serializer(
            "CategoryPreviewStudent",
            {
                "student_id": serializers.UUIDField(),
                "current": serializers.CharField(allow_null=True),
                "proposed": serializers.CharField(allow_null=True),
            },
            many=True,
        ),
        "class_average": serializers.DictField(
            help_text="`current` and `proposed` class averages as decimal strings."
        ),
    },
)
BREAKDOWN_REPLY = inline_serializer(
    "GradeBreakdownReply",
    {
        "final_grade": serializers.CharField(allow_null=True),
        "categories": inline_serializer(
            "GradeBreakdownCategory",
            {
                "id": serializers.UUIDField(allow_null=True),
                "name": serializers.CharField(allow_null=True),
                "weight_percent": serializers.CharField(),
                "grade": serializers.CharField(allow_null=True),
                "contribution": serializers.CharField(allow_null=True),
                "counted": serializers.BooleanField(),
            },
            many=True,
        ),
        "uncategorized": inline_serializer(
            "GradeBreakdownUncategorized",
            {
                "assignment_id": serializers.UUIDField(),
                "title": serializers.CharField(),
            },
            many=True,
        ),
        "excused_count": serializers.IntegerField(),
        "missing_count": serializers.IntegerField(),
        "missing_counts_as_zero": serializers.BooleanField(),
    },
)
POLICY_BODY = inline_serializer(
    "GradingPolicyBody", {"missing_counts_as_zero": serializers.BooleanField()}
)
EXCUSE_BODY = inline_serializer(
    "ExcuseBody",
    {"student": serializers.UUIDField(help_text="A student in the course.")},
)
EXCUSE_REPLY = inline_serializer(
    "ExcuseReply",
    {
        "assignment_id": serializers.UUIDField(),
        "student_id": serializers.UUIDField(),
        "excused": serializers.BooleanField(help_text="Always true."),
        "excused_at": serializers.DateTimeField(),
    },
)
TEMPLATE_ITEM = inline_serializer(
    "CategoryTemplateItem",
    {
        "name": serializers.CharField(max_length=100),
        "weight_percent": serializers.DecimalField(max_digits=5, decimal_places=2),
    },
)
TEMPLATE_BODY = inline_serializer(
    "CategoryTemplateBody",
    {"name": serializers.CharField(), "items": many(TEMPLATE_ITEM)},
)
TEMPLATE_PATCH_BODY = inline_serializer(
    "CategoryTemplatePatchBody",
    {
        "name": serializers.CharField(required=False),
        "items": many(TEMPLATE_ITEM, required=False),
    },
)
TEMPLATE = inline_serializer(
    "CategoryTemplate",
    {
        "id": serializers.UUIDField(),
        "name": serializers.CharField(),
        "owner_type": serializers.CharField(help_text="`TEACHER` or `SCHOOL`."),
        "items": many(TEMPLATE_ITEM),
        "created_at": serializers.DateTimeField(),
        "updated_at": serializers.DateTimeField(),
    },
)


def _course_errors(*extra):
    out = {
        403: OpenApiResponse(ERROR_BODY, description="Not a teacher."),
        404: OpenApiResponse(ERROR_BODY, description="Course not reachable; flag off."),
    }
    out.update(dict(extra))
    return out


BAD = OpenApiResponse(
    description="Validation error: the weights must add up to 100 (or there are "
    "none), names 1 to 100 characters and not repeated, or the student is not in "
    "the course."
)

get_categories = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="course_categories_retrieve",
    summary="Get a course's grade categories",
    description=f"The categories, in order, with the course's grading policy. "
    f"{COURSE_WHO} {FLAG_OFF}",
    responses={200: CATEGORY_SET_REPLY, **_course_errors()},
)
put_categories = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="course_categories_update",
    summary="Replace a course's grade categories",
    description=f"The whole set is replaced in one step. The weights add up to 100, "
    f"or the set is empty. {COURSE_WHO} {FLAG_OFF}",
    request=CATEGORY_SET_BODY,
    responses={200: CATEGORY_SET_REPLY, **_course_errors((400, BAD))},
)
preview_categories = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="course_categories_preview_create",
    summary="Preview the grades a proposed set of categories would give",
    description=f"Every enrolled student's grade now and under the proposed set. "
    f"Writes nothing. {COURSE_WHO} {FLAG_OFF}",
    request=CATEGORY_SET_BODY,
    responses={200: PREVIEW_REPLY, **_course_errors((400, BAD))},
)
grade_breakdown = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="course_grade_breakdown_retrieve",
    summary="Get one student's grade by category",
    description=f"The contributions add up to the final grade. {COURSE_WHO} "
    f"{FLAG_OFF}",
    parameters=[
        OpenApiParameter(
            "student",
            OpenApiTypes.UUID,
            OpenApiParameter.QUERY,
            required=True,
            description="A student enrolled in the course.",
        )
    ],
    responses={200: BREAKDOWN_REPLY, **_course_errors((400, BAD))},
)
get_policy = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="course_grading_policy_retrieve",
    summary="Get how a course counts excused, missing and ungraded work",
    description=f"The rules in the approved words, with a warning when assignments "
    f"are uncategorized. {COURSE_WHO} {FLAG_OFF}",
    responses={200: POLICY, **_course_errors()},
)
patch_policy = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="course_grading_policy_partial_update",
    summary="Set whether missing work counts as zero",
    description=f"When the setting changes, the course's grades are recomputed. "
    f"{COURSE_WHO} {FLAG_OFF}",
    request=POLICY_BODY,
    responses={200: POLICY, **_course_errors((400, BAD))},
)
excuse = extend_schema(
    tags=[ASSIGNMENTS_TAG],
    operation_id="assignments_excused_create",
    summary="Excuse a student from an assignment",
    description=f"The student's grade is recomputed. 201 the first time, 200 if "
    f"already excused. {COURSE_WHO} {FLAG_OFF}",
    request=EXCUSE_BODY,
    responses={200: EXCUSE_REPLY, 201: EXCUSE_REPLY, **_course_errors((400, BAD))},
)
unexcuse = extend_schema(
    tags=[ASSIGNMENTS_TAG],
    operation_id="assignments_excused_destroy",
    summary="Take back an excuse",
    description=f"The student is named in the query (`?student=`) or the body. "
    f"The student's grade is recomputed. {COURSE_WHO} {FLAG_OFF}",
    parameters=[
        OpenApiParameter(
            "student",
            OpenApiTypes.UUID,
            OpenApiParameter.QUERY,
            required=False,
            description="The student, when not sent in the body.",
        )
    ],
    request=None,
    responses={
        204: OpenApiResponse(description="Removed."),
        **_course_errors((400, BAD)),
    },
)


def _template_errors(*extra):
    out = {
        403: OpenApiResponse(
            ERROR_BODY, description="Not allowed to manage this template."
        ),
        404: OpenApiResponse(
            ERROR_BODY, description="Not a template you can see; flag off."
        ),
    }
    out.update(dict(extra))
    return out


TEMPLATE_BAD = OpenApiResponse(
    description="Validation error: the weights must add up to 100, names 1 to 100 "
    "characters, or you already have a template of that name."
)

list_templates = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="category_templates_list",
    summary="List category templates",
    description=f"The templates you can make a course from. {TEMPLATE_WHO} {FLAG_OFF}",
    responses={200: many(TEMPLATE), **_template_errors()},
)
create_template = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="category_templates_create",
    summary="Create a category template",
    description=f"Owned by the teacher, or by the school for a school admin. "
    f"{TEMPLATE_WHO} {FLAG_OFF}",
    request=TEMPLATE_BODY,
    responses={201: TEMPLATE, **_template_errors((400, TEMPLATE_BAD))},
)
get_template = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="category_templates_retrieve",
    summary="Get a category template",
    description=f"{TEMPLATE_WHO} {FLAG_OFF}",
    responses={200: TEMPLATE, **_template_errors()},
)
patch_template = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="category_templates_partial_update",
    summary="Change a category template",
    description=f"Send only what changes. {TEMPLATE_WHO} {FLAG_OFF}",
    request=TEMPLATE_PATCH_BODY,
    responses={200: TEMPLATE, **_template_errors((400, TEMPLATE_BAD))},
)
delete_template = extend_schema(
    tags=[CATEGORIES_TAG],
    operation_id="category_templates_destroy",
    summary="Delete a category template",
    description=f"Courses already made from it are not changed. {TEMPLATE_WHO} "
    f"{FLAG_OFF}",
    responses={204: OpenApiResponse(description="Deleted."), **_template_errors()},
)
