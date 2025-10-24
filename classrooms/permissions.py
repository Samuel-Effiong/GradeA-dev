from rest_framework import permissions

from users.models import UserTypes


class IsTeacher(permissions.BasePermission):
    message = "You must be a teacher to access this endpoint."

    def has_permission(self, request, view):
        user = request.user
        return user.user_type == UserTypes.TEACHER

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsStudent(permissions.BasePermission):
    message = "You must be a student to access this endpoint."

    def has_permission(self, request, view):
        user = request.user
        return user.user_type == UserTypes.STUDENT

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
