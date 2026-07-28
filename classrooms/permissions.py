from rest_framework import permissions

from users.models import UserTypes

from .models import SessionOwnerType


class IsTeacher(permissions.BasePermission):
    message = "You must be a teacher to access this endpoint."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type == UserTypes.TEACHER
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsTeacherOrReadOnly(permissions.BasePermission):
    message = (
        "Custom permission to only allow teachers to create/edit sessions. "
        "Students can only view sessions they are enrolled in."
    )

    def has_permission(self, request, view):
        # Read permissions are allowed to any authenticated user
        if request.method in permissions.SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)

        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type == UserTypes.TEACHER
        )


class IsStudent(permissions.BasePermission):
    message = "You must be a student to access this endpoint."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type == UserTypes.STUDENT
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsNotStudent(permissions.BasePermission):
    message = "You must not be a student to access this endpoint."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type != UserTypes.STUDENT
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsSuperAdmin(permissions.BasePermission):
    message = "You must be a superadmin to access this endpoint."
    """
    Allows access only to superadmins
    """

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type == UserTypes.SUPER_ADMIN
            and request.user.is_superuser
        )


class IsSchoolAdmin(permissions.BasePermission):
    message = "You must be a school admin to access this endpoint."
    """
    Allows access only to school admins
    """

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.user_type == UserTypes.SCHOOL_ADMIN
        )


class IsTeacherOrStudent(permissions.BasePermission):
    message = "You must be a teacher or student to access this endpoint."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (
                request.user.user_type == UserTypes.TEACHER
                or request.user.user_type == UserTypes.STUDENT
            )
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class CanManageSession(permissions.BasePermission):
    """
    - Super admins: full access.
    - School admins: can create/update/delete only SCHOOL-owned sessions of their school.
    - Teachers with no school (individual): can create/update/delete only their own INDIVIDUAL sessions.
    - Teachers with a school: read-only on school-owned sessions; never write.
    """

    def has_permission(self, request, view):
        # Read-only is allowed for all authenticated users
        if request.method in permissions.SAFE_METHODS:
            return True

        user = request.user
        # Super admin can do anything
        if user.user_type == UserTypes.SUPER_ADMIN:
            return True

        # School admin can create school sessions
        if user.user_type == UserTypes.SCHOOL_ADMIN:
            return True

        # Teacher – only if they do NOT belong to a school
        if user.user_type == UserTypes.TEACHER and user.school is None:
            return True

        # All other cases (teacher with school, student, etc.) – no write
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user

        # Read-only always allowed (filtered queryset already restricts visibility)
        if request.method in permissions.SAFE_METHODS:
            return True

        # Super admin
        if user.user_type == UserTypes.SUPER_ADMIN:
            return True

        # School admin: can only manage SCHOOL-owned sessions of their school
        if user.user_type == UserTypes.SCHOOL_ADMIN:
            return (
                obj.owner_type == SessionOwnerType.SCHOOL
                and obj.school_id in user.schools_managed()  # see helper below
            )

        # Individual teacher: can manage only their own INDIVIDUAL sessions
        if user.user_type == UserTypes.TEACHER and user.school is None:
            return (
                obj.owner_type == SessionOwnerType.INDIVIDUAL
                and obj.teacher_id == user.id
            )

        return False
