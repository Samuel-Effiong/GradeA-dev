# billing/context.py
from threading import local

_license_invitation_context = local()


def set_license_invitation_context(value: bool = True) -> None:
    """Set the flag to indicate we are creating a user for a license invitation."""
    _license_invitation_context.is_license_invite = value


def get_license_invitation_context() -> bool:
    """Return True if we are currently in a license invitation flow."""
    return getattr(_license_invitation_context, "is_license_invite", False)


def clear_license_invitation_context() -> None:
    """Clear the flag after the operation."""
    if hasattr(_license_invitation_context, "is_license_invite"):
        del _license_invitation_context.is_license_invite
