"""H-148: the body of an add by email, for tests that are not about the name.

The add-by-email route requires the student's first and last name since
H-148. A test that only cares that a student is added sends
`add_by_email(email)`: the address, and a name made from the address, so
that two different addresses in one course do not collide with the
one-exact-name-per-course rule. No test in this module.

H-247: one email is one student with one name, so a typed name that is not
an existing account's own is refused. For an address that already has a
named account, the body carries that account's name, as a teacher adding
that student would type it.
"""


def _stored_name(email):
    from django.contrib.auth import get_user_model

    account = (
        get_user_model()
        .objects.filter(email__iexact=str(email).strip())
        .values("first_name", "middle_name", "last_name")
        .first()
    )
    if not account or not (account["first_name"] or account["last_name"]):
        return {}
    return {part: value or "" for part, value in account.items()}


def add_by_email(email, **overrides):
    local, _, domain = str(email).partition("@")
    # Letters kept, digits turned into letters, so "pending1" and
    # "pending2" are different names.
    first = "".join(
        c if c.isalpha() else "abcdefghij"[int(c)] if c.isdigit() else ""
        for c in local.lower()
    )
    last = "".join(c for c in domain.split(".")[0].lower() if c.isalpha())
    body = {
        "email": email,
        "first_name": (first or "student").ljust(2, "x").capitalize(),
        "last_name": (last or "added").ljust(2, "x").capitalize(),
    }
    body.update(_stored_name(email))
    body.update(overrides)
    return body
