"""Keys a student never sees (Epic B1b-7 §7.25, B1b-8 §8.2 Q8-2).

Some routes answer both a student and staff. A key that is staff-only (a
billing job's id, how long a kept file stays) is left out of a student's
body here, in one place, rather than ad hoc in each view.
"""

import functools


def is_student_request(args):
    """Whether the request among a view's arguments is a student's."""
    from users.models import UserTypes

    for arg in args:
        user = getattr(arg, "user", None)
        if user is not None and hasattr(arg, "method"):
            return getattr(user, "user_type", None) == UserTypes.STUDENT
    return False


def staff_only_keys(*keys):
    """A view whose 2xx JSON-object body drops `keys` for a student."""

    def decorate(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            response = view(*args, **kwargs)
            data = getattr(response, "data", None)
            if (
                200 <= response.status_code < 300
                and isinstance(data, dict)
                and is_student_request(args)
            ):
                for key in keys:
                    data.pop(key, None)
            return response

        return wrapped

    return decorate
