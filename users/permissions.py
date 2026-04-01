from django.apps import apps
from rest_framework import permissions
from rest_framework.exceptions import ParseError

from users.models import UserTypes


class HasCreditBalance(permissions.BasePermission):
    """
    Custom permission to check if the user (or their teacher) has enough credits.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Super admins have unlimited credits for now
        if request.user.user_type == UserTypes.SUPER_ADMIN:
            return True

        # Determine the user whose wallet we should check
        target_user = request.user
        is_student = request.user.user_type == UserTypes.STUDENT

        if is_student:
            # For students, we check their teacher's wallet
            teacher = self._get_teacher_for_request(request, view)
            if teacher:
                target_user = teacher
            else:
                # If we cannot find a teacher, we default to the student (who likely has 0 credits)
                # or we could allow it. But the requirement is to check "your teacher".
                pass

        # Check credits
        if (
            not hasattr(target_user, "credit_wallet")
            or target_user.credit_wallet.total_remaining_credits() <= 0
        ):
            if is_student:
                message = (
                    "<b>Insufficient Credits:</b> Your Credit Wallet is currently empty. "
                    "Please contact your teacher to top up credits to continue with AI Task."
                )
            else:
                message = (
                    "<b>Insufficient Credits:</b> Your Credit Wallet is currently empty. "
                    "Please top up your credits to continue with grading or AI tasks."
                )
            raise ParseError(message)

        return True

    def _get_teacher_for_request(self, request, view):
        """
        Attempt to find the teacher associated with the current request.
        """
        assignment_id = view.kwargs.get("assignment_id")
        course_id = view.kwargs.get("course_id") or view.kwargs.get(
            "id"
        )  # Some views use 'id' for course
        submission_id = view.kwargs.get("submission_id") or view.kwargs.get("pk")

        if assignment_id:
            Assignment = apps.get_model("assignments", "Assignment")
            try:
                assignment = Assignment.objects.select_related("course__teacher").get(
                    id=assignment_id
                )
                return assignment.course.teacher
            except Assignment.DoesNotExist:
                return None

        if course_id:
            Course = apps.get_model("classrooms", "Course")
            try:
                course = Course.objects.select_related("teacher").get(id=course_id)
                return course.teacher
            except Course.DoesNotExist:
                return None

        if submission_id:
            StudentSubmission = apps.get_model("students", "StudentSubmission")
            try:
                submission = StudentSubmission.objects.select_related(
                    "assignment__course__teacher"
                ).get(id=submission_id)
                return submission.assignment.course.teacher
            except StudentSubmission.DoesNotExist:
                return None

        return None
