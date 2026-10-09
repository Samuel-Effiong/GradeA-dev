"""
THE PRINCIPLE (H-164 / H-203, Senior Manager's ruling 2026-10-08):

    An account with admin power that has never been verified can be entered
    only with its password; no road that proves only control of a mailbox (a
    reset code, a verification code, a Google identity) signs it in, verifies
    it or activates it.

A road that signs in, verifies or activates an account WITHOUT its password
calls `holds_admin_power` first and refuses an account that has admin power
and a null `email_verified_at`, answering as it answers a failed attempt on
that road. Every such road is on the named list pinned by
`users/tests_roads_that_sign_in_or_activate.py`; a new one fails that test
until someone decides what the principle says about it.
"""


def holds_admin_power(user):
    """True if ANY of the three marks of an admin account is set.

    They can differ: `create_superuser` sets is_staff and is_superuser and
    leaves user_type at its default, TEACHER (H-19 read the same shape), and
    the Django admin ticks the flags independently of the type."""
    from users.models import UserTypes

    return bool(
        user.is_staff or user.is_superuser or user.user_type == UserTypes.SUPER_ADMIN
    )
