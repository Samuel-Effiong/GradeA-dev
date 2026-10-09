"""H-148: the body of an add by email, for tests that are not about the name.

The add-by-email route requires the student's first and last name since
H-148. A test that only cares that a student is added sends
`add_by_email(email)`: the address, and a name made from the address, so
that two different addresses in one course do not collide with the
one-exact-name-per-course rule. No test in this module.
"""


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
    body.update(overrides)
    return body
