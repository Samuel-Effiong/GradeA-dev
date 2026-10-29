"""Reading query-string values the way DRF reads request bodies.

A bare `value.lower() in ("1", "true")` let `?at_risk=yes` silently mean
false and `?at_risk=maybe` filter to the students NOT at risk (H-233). Both
teacher rosters (classrooms my-students, dashboard all-students) read their
`at_risk` filter through `boolean_query_param` instead, so the two accept
exactly the words DRF's BooleanField accepts everywhere else.
"""

from rest_framework import serializers
from rest_framework.exceptions import ValidationError


def boolean_query_param(request, param_name):
    """True or False from the query string, or None if not given.

    Absent or empty means not given. Otherwise the value is parsed by DRF's
    BooleanField (true/false, 1/0, yes/no, on/off, t/f, y/n, in any letter
    case); anything else is the caller's 400, naming the parameter.
    """
    value = request.query_params.get(param_name)
    if value in (None, ""):
        return None
    try:
        return serializers.BooleanField().to_internal_value(value)
    except ValidationError:
        raise ValidationError(
            {param_name: [f"{value!r} is not true or false."]}
        ) from None
