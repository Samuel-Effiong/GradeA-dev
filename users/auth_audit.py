"""AUTH_LOGIN events for every route that signs someone in (Epic A S1, plan 08 §2.2).

`/auth/login` records its own events in `CustomTokenObtainPairSerializer`.
The other doors that issue tokens or activate an account with a password -
email verification, password reset, password change, the two invitation
registrations and Google sign-in - use these helpers, so every success and
every failed attempt is one AUTH_LOGIN event with `metadata.auth_method`
saying which door.

Attribution (SM ruling 2026-09-29, also applied to `/auth/login`): a success
names the user as actor. A failed attempt never names the account holder as
its actor - the audit must not say someone attacked their own account. Its
actor is the request's signed-in user if there is one (change-password), else
ANONYMOUS; the targeted account is the TARGET, or null when the email or code
matches no account, so an unknown address is never stored. A throttled
request (429) is refused before the view runs and records nothing.
A failure is scoped to the targeted account's school, so that school's admin
can see attempts on its own accounts through the audit query API.

Never emit a failure inside a `transaction.atomic()` block that is about to
roll back: the event would roll back with it (see `audit.emitter`). Record
it after the block has exited.
"""

from django.contrib.auth.models import AnonymousUser

from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome, ErrorClass


def failure_actor(request):
    """Who made a failed attempt: the request's signed-in user, or an
    anonymous caller. Never the targeted account merely because it was
    targeted."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    return AnonymousUser()


def account_for_email(email):
    """The account an attempt was aimed at, or None. Never raises."""
    from users.models import CustomUser

    if not email:
        return None
    return CustomUser.objects.filter(email__iexact=str(email).strip()).first()


def sign_in_succeeded(request, user, method):
    emit(
        AuditAction.AUTH_LOGIN,
        actor=user,
        request=request,
        target_type="CustomUser",
        target_id=user.id,
        outcome=AuditOutcome.SUCCESS,
        metadata={"auth_method": method},
    )


def sign_in_failed(
    request,
    account,
    method,
    reason_code,
    *,
    denied=False,
    error_class=ErrorClass.USER,
    extra_metadata=None,
):
    emit(
        AuditAction.AUTH_LOGIN,
        actor=failure_actor(request),
        request=request,
        target_type="CustomUser",
        target_id=account.id if account is not None else None,
        # Scoped to the TARGET's school (SM ruling): that school's admin sees
        # attempts on its own accounts; another school's admin never does.
        school_id=getattr(account, "school_id", None),
        outcome=AuditOutcome.DENIED if denied else AuditOutcome.FAILURE,
        error_class=error_class,
        reason_code=reason_code,
        metadata={"auth_method": method, **(extra_metadata or {})},
    )
