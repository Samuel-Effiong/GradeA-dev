"""Filter sets whose lookups cross a tenancy boundary.

A plain `filterset_fields = {"enrollments__course": ["exact"]}` on a
student queryset joins EVERY enrollment the student has, not just the
requesting teacher's. Students are routinely shared between unrelated
teachers, so that filter answered "is my student also enrolled in this
other teacher's course?" - it returned the shared student for a course the
teacher cannot see. The filters here only ever match through the
requester's own courses, so a foreign id behaves exactly like one that
does not exist: zero rows.
"""

import django_filters
from django.db.models import Exists, OuterRef

from users.models import CustomUser

from .models import StudentCourse


class MyStudentsFilter(django_filters.FilterSet):
    """Query parameters for `StudentCourseViewSet.my_students`.

    The parameter names are the ones the endpoint has always accepted, so
    existing clients keep working.
    """

    enrollments__course = django_filters.UUIDFilter(method="filter_own_course")
    enrollments__course__session = django_filters.UUIDFilter(
        method="filter_own_session"
    )

    class Meta:
        model = CustomUser
        fields = []

    def _through_own_enrollments(self, queryset, **lookup):
        return queryset.filter(
            Exists(
                StudentCourse.objects.filter(
                    student=OuterRef("pk"),
                    course__teacher=self.request.user,
                    **lookup,
                )
            )
        )

    def filter_own_course(self, queryset, name, value):
        return self._through_own_enrollments(queryset, course_id=value)

    def filter_own_session(self, queryset, name, value):
        return self._through_own_enrollments(queryset, course__session_id=value)
