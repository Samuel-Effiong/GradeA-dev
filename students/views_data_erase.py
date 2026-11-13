"""NFR-CMP-04 step 3: `POST students/<id>/erase` (students/data_erase.py).

Dark until STUDENT_DATA_TOOLS_ENABLED, like the export. Every refusal by
scope (an unknown id, a target that is not a student, a caller outside the
scope, the student themselves, anonymous, a bad token) gets the unknown-URL
answer. A full erasure that the data refuses (staff data, a wallet that is
not empty, billing records) answers its coded 409 and changes nothing.
"""

from rest_framework.response import Response

from AutoGrader.handlers import json_404
from students import data_erase
from students.views_data_export import StudentDataToolsView
from users.models import CustomUser, UserTypes


class StudentDataEraseView(StudentDataToolsView):
    # POST only: GET and HEAD must never erase (and answer 405).
    http_method_names = ["post", "options"]

    def post(self, request, pk):
        student = CustomUser.objects.filter(pk=pk, user_type=UserTypes.STUDENT).first()
        if student is None:
            return json_404(request)
        scope = data_erase.erase_scope(request.user, student)
        if scope is None:
            return json_404(request)
        result = data_erase.erase_student(
            student.pk, scope, by=request.user, request=request
        )
        return Response(result)
