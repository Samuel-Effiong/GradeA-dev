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

from .models import StudentCourse, teacher_course_access_q


def _at_risk_serializer(request):
    # Imported here, not at module level: students.serializers imports
    # classrooms.models, and classrooms.filters sits next to classrooms.views
    # which students.serializers does not need - keeping this import local
    # avoids giving the two apps a load-order dependency on each other.
    from students.serializers import StudentListSerializer

    return StudentListSerializer(context={"request": request})


class MyStudentsFilter(django_filters.FilterSet):
    """Query parameters for `StudentCourseViewSet.my_students`.

    The parameter names are the ones the endpoint has always accepted, so
    existing clients keep working.
    """

    enrollments__course = django_filters.UUIDFilter(method="filter_own_course")
    enrollments__course__session = django_filters.UUIDFilter(
        method="filter_own_session"
    )
    # Against the `final_grade` annotation StudentCourseViewSet.get_queryset
    # adds for this action (the same "relevant enrollment" grade the row
    # displays), not a model field - a plain NumberFilter targets it fine.
    min_grade = django_filters.NumberFilter(field_name="final_grade", lookup_expr="gte")
    max_grade = django_filters.NumberFilter(field_name="final_grade", lookup_expr="lte")
    # Risk is Python logic (StudentRiskEvaluator), not a SQL-expressible
    # condition - see filter_at_risk.
    at_risk = django_filters.BooleanFilter(method="filter_at_risk")

    class Meta:
        model = CustomUser
        fields = []

    def _through_own_enrollments(self, queryset, **lookup):
        # "Own" is the H-38 rule, not bare `course__teacher=user`: a course in
        # a school the teacher was removed from is no longer theirs to filter
        # by, or the filter answers "is my student in that school course?".
        # The helper ANDs `course__teacher=user`, so it can only narrow H-22.
        return queryset.filter(
            Exists(
                StudentCourse.objects.filter(
                    teacher_course_access_q(self.request.user, prefix="course__"),
                    student=OuterRef("pk"),
                    **lookup,
                )
            )
        )

    def filter_own_course(self, queryset, name, value):
        return self._through_own_enrollments(queryset, course_id=value)

    def filter_own_session(self, queryset, name, value):
        return self._through_own_enrollments(queryset, course__session_id=value)

    def filter_at_risk(self, queryset, name, value):
        # The one serializer instance this uses is the SAME field the
        # response displays (StudentListSerializer.get_at_risk) - the
        # filter can never disagree with the "at_risk" a row shows, because
        # it IS that computation, just evaluated before pagination. The
        # queryset is already filtered/scoped by this point (course,
        # session, search, grade) and its prefetches are already built, so
        # materialising it here costs no extra queries - only the Python
        # risk evaluation itself, once per student in scope.
        serializer = _at_risk_serializer(self.request)
        students = list(queryset)
        matching_ids = [
            student.pk
            for student in students
            if serializer.get_at_risk(student) == value
        ]
        return queryset.filter(pk__in=matching_ids)
