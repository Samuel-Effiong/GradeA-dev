"""API documentation for the department routes (drf-spectacular). Docs only:
nothing here is read at request time."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers

from .serializers import (
    AddMembersSerializer,
    AdminMemberRowSerializer,
    DepartmentRenameSerializer,
    DepartmentRowSerializer,
    DepartmentWriteSerializer,
    MemberEditSerializer,
    MyDepartmentSerializer,
    RosterRowSerializer,
)
from .wording import MAX_MEMBERS_PER_DEPARTMENT, MAX_TEACHERS_PER_ADD

ADMIN_TAG = "School Admin Departments"
TEACHER_TAG = "Departments"

ERROR_BODY = inline_serializer(
    "DepartmentError",
    {
        "error": serializers.CharField(help_text="The one sentence to show."),
        "reason_code": serializers.CharField(help_text="Stable UPPER_SNAKE code."),
        "error_class": serializers.CharField(),
        "remediation": serializers.CharField(allow_null=True),
        "retryable": serializers.BooleanField(),
        "params": serializers.DictField(help_text="Whitelisted scalar values."),
        "reference": serializers.CharField(help_text="The request id."),
    },
)

DETAIL_BODY = inline_serializer(
    "DepartmentDetail",
    {
        "id": serializers.UUIDField(),
        "name": serializers.CharField(),
        "description": serializers.CharField(),
        "member_count": serializers.IntegerField(),
        "created_at": serializers.DateTimeField(),
        "members": AdminMemberRowSerializer(many=True),
    },
)
ADD_RESULT = inline_serializer(
    "DepartmentMembersAdded",
    {
        "successful": serializers.IntegerField(),
        "failed": serializers.IntegerField(),
        "added": inline_serializer(
            "DepartmentMemberAdded",
            {
                "teacher_id": serializers.UUIDField(),
                "status": serializers.CharField(help_text="Always `added`."),
            },
            many=True,
        ),
        "errors": inline_serializer(
            "DepartmentMemberAddFailure",
            {
                "teacher_id": serializers.CharField(),
                "status": serializers.CharField(help_text="Always `failed`."),
                "error": serializers.CharField(),
                "reason_code": serializers.CharField(
                    help_text="`TEACHER_NOT_ON_LICENCE` (the same answer for an "
                    "unknown, student, admin, other-school or deactivated id), "
                    "`DEPARTMENT_ALREADY_MEMBER` or `DEPARTMENT_TOO_MANY_TEACHERS`."
                ),
            },
            many=True,
        ),
    },
)
EDIT_RESULT = inline_serializer(
    "DepartmentMemberEdited",
    {"membership_id": serializers.UUIDField(), "can_edit": serializers.BooleanField()},
)
DELETE_RESULT = inline_serializer(
    "DepartmentDeleted",
    {"deleted": serializers.BooleanField(), "member_count": serializers.IntegerField()},
)

FLAG_OFF = (
    "While the departments switch is off every route answers as an unknown URL "
    "(404), before sign-in."
)
ADMIN_WHO = (
    "School admins only, for their own school. A teacher, student or super admin "
    "gets 403; a department of another school, an unknown id and a malformed id "
    "all get the same house 404 body."
)
TEACHER_WHO = (
    "Teachers who are on a school's current license only. Anyone else gets the "
    "house 404 (or an empty list). "
)


def _errors(*extra):
    out = {
        403: OpenApiResponse(ERROR_BODY, description="Not a school admin."),
        404: OpenApiResponse(
            ERROR_BODY, description="Unknown, malformed or other-school id; flag off."
        ),
    }
    out.update(dict(extra))
    return out


def _bad_request(description):
    return (400, OpenApiResponse(ERROR_BODY, description=description))


def _name_errors():
    return _bad_request(
        "`DEPARTMENT_NAME_INVALID` (name must be 1 to 100 characters) or "
        "`DEPARTMENT_NAME_TAKEN` (a department of that name, ignoring case and "
        "spacing, already exists at the school)."
    )


list_departments = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_departments_list",
    summary="List my school's departments",
    description=f"Every department of the admin's school with its member count, "
    f"ordered by name. {ADMIN_WHO} {FLAG_OFF}",
    responses={200: DepartmentRowSerializer(many=True), **_errors()},
)
create_department = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_departments_create",
    summary="Create a department",
    description=f"Names are 1 to 100 characters and unique per school ignoring case "
    f"and spacing. One audit event. {ADMIN_WHO} {FLAG_OFF}",
    request=DepartmentWriteSerializer,
    responses={201: DepartmentRowSerializer, **_errors(_name_errors())},
)
get_department = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_departments_retrieve",
    summary="Get a department with its members",
    description=f"The department and its members (name, email, edit switch). "
    f"{ADMIN_WHO} {FLAG_OFF}",
    responses={200: DETAIL_BODY, **_errors()},
)
update_department = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_departments_partial_update",
    summary="Rename a department or change its description",
    description=f"Send only what changes. {ADMIN_WHO} {FLAG_OFF}",
    request=DepartmentRenameSerializer,
    responses={200: DepartmentRowSerializer, **_errors(_name_errors())},
)
delete_department = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_departments_destroy",
    summary="Delete a department",
    description=(
        "Removes the department and its memberships; one audit event. If the "
        "department still has content, send `content=delete` to delete it or "
        "`content=reassign&target=<department id>` to move it to another "
        "department of the same school; without a choice the answer is 409 "
        "`DEPARTMENT_CONTENT_CHOICE_REQUIRED` with `params.item_count` and "
        "nothing changes. All or nothing. " + ADMIN_WHO + " " + FLAG_OFF
    ),
    parameters=[
        OpenApiParameter(
            "content",
            OpenApiTypes.STR,
            OpenApiParameter.QUERY,
            enum=["delete", "reassign"],
            required=False,
            description="What to do with content. Ignored when there is none.",
        ),
        OpenApiParameter(
            "target",
            OpenApiTypes.UUID,
            OpenApiParameter.QUERY,
            required=False,
            description="With `content=reassign`: the department to move it to.",
        ),
    ],
    responses={
        200: DELETE_RESULT,
        **_errors(
            _bad_request(
                "`DEPARTMENT_REASSIGN_TARGET_INVALID`: the target is missing, is "
                "the department itself, or is not a department of this school."
            ),
            (
                409,
                OpenApiResponse(
                    ERROR_BODY,
                    description="`DEPARTMENT_CONTENT_CHOICE_REQUIRED`, with "
                    "`params.item_count`.",
                ),
            ),
        ),
    },
)
add_members = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_department_members_create",
    summary="Add teachers to a department",
    description=(
        f"Up to {MAX_TEACHERS_PER_ADD} teacher ids per request, "
        f"{MAX_MEMBERS_PER_DEPARTMENT} members per department. Partial success: "
        "each id is added or listed in `errors`. Each added teacher gets an "
        "email after the change is saved. " + ADMIN_WHO + " " + FLAG_OFF
    ),
    request=AddMembersSerializer,
    responses={
        200: ADD_RESULT,
        **_errors(
            _bad_request(
                f"`DEPARTMENT_BATCH_TOO_LARGE`: more than {MAX_TEACHERS_PER_ADD} ids."
            ),
            (
                409,
                OpenApiResponse(
                    ERROR_BODY,
                    description="`DEPARTMENT_TOO_MANY_TEACHERS`: the request would "
                    f"take the department past {MAX_MEMBERS_PER_DEPARTMENT}.",
                ),
            ),
        ),
    },
)
edit_member = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_department_members_partial_update",
    summary="Turn a member's editing on or off",
    description=f"The one edit switch for the member's department lessons and the "
    f"shared library. One PERMISSION_CHANGE audit event; none if nothing changes. "
    f"{ADMIN_WHO} {FLAG_OFF}",
    request=MemberEditSerializer,
    responses={200: EDIT_RESULT, **_errors()},
)
remove_member = extend_schema(
    tags=[ADMIN_TAG],
    operation_id="school_admin_department_members_destroy",
    summary="Remove a teacher from a department",
    description=f"The teacher is emailed after the change is saved. {ADMIN_WHO} "
    f"{FLAG_OFF}",
    responses={204: OpenApiResponse(description="Removed."), **_errors()},
)
my_departments = extend_schema(
    tags=[TEACHER_TAG],
    operation_id="departments_mine_list",
    summary="List the departments I belong to",
    description=TEACHER_WHO
    + "Returns only the teacher's own departments with their own edit switch; an "
    "empty list for an individual teacher. " + FLAG_OFF,
    responses={
        200: MyDepartmentSerializer(many=True),
        403: OpenApiResponse(ERROR_BODY, description="Not a teacher."),
        404: OpenApiResponse(ERROR_BODY, description="Flag off."),
    },
)
roster = extend_schema(
    tags=[TEACHER_TAG],
    operation_id="departments_roster_retrieve",
    summary="List the people in one of my departments",
    description=TEACHER_WHO
    + "Only a name and a role (`School admin` or `Teacher`) per person, school "
    "admins first. Nothing about courses, students, grades or credits. " + FLAG_OFF,
    responses={
        200: RosterRowSerializer(many=True),
        403: OpenApiResponse(ERROR_BODY, description="Not a teacher."),
        404: OpenApiResponse(
            ERROR_BODY, description="Not a member, unknown or malformed id; flag off."
        ),
    },
)
