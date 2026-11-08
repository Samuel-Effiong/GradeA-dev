"""NFR-CMP-04 step 2: `GET students/<id>/data-export` (students/data_export.py).

While STUDENT_DATA_TOOLS_ENABLED is off (the default) the route answers
exactly what an unknown URL answers, from `dispatch()`, before
authentication runs. When it is on, every refusal (an unknown id, a target
that is not a student, a caller outside the scope, the student themselves)
gets that same answer, so the route never tells a caller whether a student
exists.
"""

from django.conf import settings
from django.http import HttpResponse
from django.utils.http import content_disposition_header
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from audit.emitter import emit
from AutoGrader.handlers import json_404
from students import data_export
from users.models import CustomUser, UserTypes


class StudentDataExportView(APIView):
    permission_classes = (IsAuthenticated,)
    # Not "head": Django serves HEAD through get(), which would build the
    # zip and record an export nobody downloaded (SM review).
    http_method_names = ["get", "options"]

    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "STUDENT_DATA_TOOLS_ENABLED", False):
            return json_404(request)
        return super().dispatch(request, *args, **kwargs)

    def handle_exception(self, exc):
        # An anonymous caller (or a bad or expired token) gets the
        # unknown-URL answer too, not 401: a 401 would say the route exists.
        # Authentication runs inside DRF's initial(), after dispatch() starts,
        # so this is the first place the caller is known (SM review).
        if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
            return json_404(self.request)
        return super().handle_exception(exc)

    def get(self, request, pk):
        student = CustomUser.objects.filter(pk=pk, user_type=UserTypes.STUDENT).first()
        if student is None:
            return json_404(request)
        scope = data_export.export_scope(request.user, student)
        if scope is None:
            return json_404(request)
        content, metadata = data_export.build_export(student, scope)
        emit(
            data_export.EXPORT_AUDIT_ACTION,
            actor=request.user,
            target_type="CustomUser",  # SM G3: the approved "User account" label
            target_id=student.pk,
            request=request,
            school_id=student.school_id,
            metadata=metadata,
        )
        response = HttpResponse(content, content_type="application/zip")
        # Never None for an attachment; `or` only satisfies the typing.
        response["Content-Disposition"] = (
            content_disposition_header(
                as_attachment=True, filename=data_export.EXPORT_NAMES["download"]
            )
            or "attachment"
        )
        return response
