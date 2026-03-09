from rest_framework import permissions

from users.models import UserTypes


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

        # Write permissions are only allowed to teachers
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
