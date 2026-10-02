from rest_framework import permissions

from users.models import UserTypes

from .models import School, SessionOwnerType


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
    Read: allowed for anyone authenticated (queryset scoping in
    SessionViewSet.get_queryset already restricts what's visible).

    Write (create/update/partial_update/destroy):
      - SUPER_ADMIN: always.
      - SCHOOL_ADMIN: only for SCHOOL sessions belonging to a school they
        administer.
      - TEACHER not currently under an active school license (individual
        track): only their own INDIVIDUAL sessions. This is keyed off
        is_under_license(), not off school_id, since school_id stays set
        even after a teacher is removed from a license or the license is
        cancelled.
      - TEACHER under an active school license: never — sessions are
        managed by their school admin.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if request.method in permissions.SAFE_METHODS:
            return True

        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            return True

        if user.user_type == UserTypes.SCHOOL_ADMIN:
            return True  # narrowed to their own school in has_object_permission

        if user.user_type == UserTypes.TEACHER:
            return not user.is_under_license()

        return False

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True

        user = request.user

        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            return True

        if obj.owner_type == SessionOwnerType.SCHOOL:
            if user.user_type != UserTypes.SCHOOL_ADMIN:
                return False
            return School.objects.filter(users=user, pk=obj.school_id).exists()

        # INDIVIDUAL session
        if user.user_type != UserTypes.TEACHER or user.is_under_license():
            return False
        return obj.teacher_id == user.id
