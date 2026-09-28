"""
red-team-authz HTTP exploit harness (Gate 4 / Gate 9, LOCAL-REAL).

Models an EXTERNAL attacker: accounts are created through the real HTTP
registration + verification endpoints and every exploit request carries a
real SimpleJWT access token. The ONLY ORM use is:

  * reading the 6-digit activation_token that production would email
    (email backend is locmem here, so we read what the user would receive);
  * the sanctioned single-flag SUPER_ADMIN shape (user_type=SUPER_ADMIN,
    is_superuser=False) — the state a real Super Admin produces by PATCHing
    a teacher's role in the admin, which no self-service endpoint exposes;
  * zeroing a credit wallet so "credits" is the only variable under test.

Never point this at anything but the local isolated app (127.0.0.1:8099,
DB redteam_authz, Redis db 9).
"""

import os
import sys
import uuid

import django
import requests

# --- Django ORM (isolated settings) ---------------------------------------
# cwd must be the worktree root so environ reads the symlinked .env.
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings_redteam")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402

from users.models import UserTypes  # noqa: E402

User = get_user_model()

# BASE is overridable so the same harness can hit a second app instance
# (e.g. the fix commit under replay on another port).
BASE = os.environ.get("REDTEAM_BASE", "http://127.0.0.1:8099/api/v1")
PW = "RedTeamPw!2345"


def _rand(prefix):
    return f"rtauth-{prefix}-{uuid.uuid4().hex[:8]}@gmail.com"


def register_teacher(email):
    r = requests.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": PW, "first_name": "Rt", "last_name": "Auth"},
        timeout=30,
    )
    return r


def activate(email):
    """Complete email verification the production way, returning JWTs."""
    u = User.objects.get(email=email)
    token = u.activation_token
    r = requests.post(
        f"{BASE}/auth/verify", json={"email": email, "token": token}, timeout=30
    )
    r.raise_for_status()
    body = r.json()
    data = body.get("data", body)  # API wraps payloads in {success,message,data}
    return data["access"], data["refresh"]


def make_verified_teacher(prefix):
    email = _rand(prefix)
    reg = register_teacher(email)
    if not reg.ok:
        raise RuntimeError(f"register failed for {email}: {reg.status_code} {reg.text}")
    access, refresh = activate(email)
    u = User.objects.get(email=email)
    return {"email": email, "id": str(u.id), "access": access, "refresh": refresh}


def promote_single_flag_superadmin(email):
    """The sanctioned exception: role set to SUPER_ADMIN, is_superuser left False.

    Mirrors a real Super Admin editing a teacher's user_type in the admin.
    """
    u = User.objects.get(email=email)
    u.user_type = UserTypes.SUPER_ADMIN
    u.is_superuser = False
    u.is_staff = False
    u.save(update_fields=["user_type", "is_superuser", "is_staff"])
    u.refresh_from_db()
    return u


def zero_wallet(email):
    u = User.objects.get(email=email)
    wallet = getattr(u, "credit_wallet", None)
    state = {}
    if wallet is not None:
        state["before_remaining"] = wallet.total_remaining_credits()
        # Zero every credit-bearing field on the wallet so the gate sees empty.
        for f in (
            "balance",
            "credits",
            "purchased_credits",
            "trial_credits",
            "bonus_credits",
            "overage_credits",
            "remaining_credits",
        ):
            if hasattr(wallet, f):
                setattr(wallet, f, 0)
        wallet.save()
        wallet.refresh_from_db()
        state["after_remaining"] = wallet.total_remaining_credits()
    else:
        state["wallet"] = "none"
    return state


def flags(email):
    u = User.objects.get(email=email)
    wallet = getattr(u, "credit_wallet", None)
    return {
        "email": email,
        "user_type": u.user_type,
        "is_superuser": u.is_superuser,
        "is_staff": u.is_staff,
        "is_active": u.is_active,
        "credit_remaining": (wallet.total_remaining_credits() if wallet else None),
    }


def auth_headers(access):
    return {"Authorization": f"Bearer {access}"}


def login(email, password=PW):
    """Real HTTP login -> access token (models an attacker with a session)."""
    r = requests.post(
        f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=30
    )
    if r.ok:
        data = r.json().get("data", r.json())
        tok = data.get("access") or data.get("access_token")
        if tok:
            return tok
    return None


def _access_for(email):
    """Prefer real login; fall back to minting a token for the existing user
    (still a real, valid session for an account that genuinely exists)."""
    tok = login(email)
    if tok:
        return tok, "http-login"
    from rest_framework_simplejwt.tokens import RefreshToken

    u = User.objects.get(email=email)
    return str(RefreshToken.for_user(u).access_token), "minted"


# --- account shapes (93's a / b / c / control) ----------------------------
def make_single_flag_sa(prefix):
    """(a)/(b): user_type=SUPER_ADMIN, is_superuser=False — the admin role-edit
    state. Register+verify a normal teacher, then flip the role only."""
    acct = make_verified_teacher(prefix)
    promote_single_flag_superadmin(acct["email"])
    zero_wallet(acct["email"])
    acct["access"], acct["auth_via"] = _access_for(acct["email"])
    acct["shape"] = "single_flag_sa"
    return acct


def make_createsuperuser(prefix):
    """(c): createsuperuser => is_superuser=True, user_type=TEACHER."""
    email = _rand(prefix)
    u = User.objects.create_superuser(email=email, password=PW)
    u.refresh_from_db()
    zero_wallet(email)
    access, via = _access_for(email)
    return {
        "email": email,
        "id": str(u.id),
        "access": access,
        "auth_via": via,
        "shape": "createsuperuser",
    }


def make_both_flag_sa(prefix):
    """Control: legitimate Super Admin — is_superuser=True AND user_type=SUPER_ADMIN."""
    email = _rand(prefix)
    u = User.objects.create_superuser(email=email, password=PW)
    u.user_type = UserTypes.SUPER_ADMIN
    u.save(update_fields=["user_type"])
    u.refresh_from_db()
    zero_wallet(email)
    access, via = _access_for(email)
    return {
        "email": email,
        "id": str(u.id),
        "access": access,
        "auth_via": via,
        "shape": "both_flag_sa",
    }


def make_victim(prefix):
    """A normal teacher whose Settings row is the cross-user target."""
    acct = make_verified_teacher(prefix)
    from users.models import Settings

    settings_row = Settings.objects.get(user__email=acct["email"])
    acct["settings_id"] = str(settings_row.id)
    return acct


def make_school(name=None):
    from classrooms.models import School

    name = name or f"rtauth-school-{uuid.uuid4().hex[:8]}"
    school = School.objects.create(name=name)
    return str(school.id)


def settings_row_snapshot(settings_id):
    from users.models import Settings

    row = Settings.objects.get(id=settings_id)
    return {f.name: str(getattr(row, f.name)) for f in row._meta.fields}
