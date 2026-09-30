"""Epic A S1: apply each mutant, run the S1 tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import re
import subprocess
import sys

MW = "audit/middleware.py"
RA = "audit/request_audit.py"
EM = "audit/emitter.py"
CTX = "audit/context.py"
FLT = "audit/filters.py"
VIEWS = "users/views.py"

MUTANTS = {
    # --- generic event and exactly-one ---
    "G1_generic_never_written": (
        MW,
        "            if not state.named_emitted:\n",
        "            if False:\n",
    ),
    "G2_generic_always_written": (
        MW,
        "            if not state.named_emitted:\n",
        "            if True:\n",
    ),
    "G3_emitter_does_not_mark": (
        EM,
        "    mark_named_emitted()\n    _emit_alertable_metrics(action, outcome, fields)\n",
        "    _emit_alertable_metrics(action, outcome, fields)\n",
    ),
    "G4_marked_on_attempt_not_on_store": (
        EM,
        "    label = _safe_label(action)\n    try:\n        fields, dropped = _build(\n",
        "    mark_named_emitted()\n    label = _safe_label(action)\n"
        "    try:\n        fields, dropped = _build(\n",
    ),
    "G5_exclusions_ignored": (
        RA,
        "    return match.view_name not in EXCLUDED_ROUTES\n",
        "    return True\n",
    ),
    "G6_reads_recorded": (
        RA,
        'STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})\n',
        'STATE_CHANGING_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})\n',
    ),
    "G7_anonymous_recorded": (
        RA,
        "    if user is None or not user.is_authenticated:\n        return False\n",
        "    if user is None:\n        return False\n",
    ),
    "G8_throttled_recorded": (
        RA,
        "    if response.status_code == 429:\n        return False\n",
        "    if False:\n        return False\n",
    ),
    "G9_student_write_not_student_record": (
        RA,
        '            touches_student_record=getattr(user, "user_type", None) == "STUDENT",\n',
        "            touches_student_record=False,\n",
    ),
    "G10_request_state_leaks": (
        CTX,
        "        _request_state_var.reset(token)\n",
        "        pass\n",
    ),
    "G11_route_not_recorded": (
        RA,
        '                "route": match.view_name,\n',
        '                "route": "unknown",\n',
    ),
    "G12_route_filter_removed": (
        FLT,
        '    route = django_filters.CharFilter(field_name="metadata__route")\n',
        "",
    ),
    # --- auth doors ---
    "A1_verify_success_not_recorded": (
        VIEWS,
        '        sign_in_succeeded(request, user, "email_verification")\n',
        "",
    ),
    "A2_verify_wrong_code_not_recorded": (
        VIEWS,
        '                request, account_for_email(email), "email_verification", '
        '"INVALID_CODE"\n',
        '                None, None, "email_verification", "INVALID_CODE"\n',
    ),
    "A3_reset_lock_not_denied": (
        VIEWS,
        '            sign_in_failed(request, user, "password_reset", "RESET_LOCKED", denied=True)\n',
        '            sign_in_failed(request, user, "password_reset", "RESET_LOCKED")\n',
    ),
    "A4_school_admin_failure_dropped": (
        VIEWS,
        "                    audit_failure = (account_for_email(email), "
        '"INVALID_CODE")\n',
        "                    pass\n",
    ),
    "A5_google_failure_not_recorded": (
        VIEWS,
        "        except (ParseError, ValidationError, AuthenticationFailed):\n"
        "            reason = self._audit_reason\n",
        "        except ZeroDivisionError:\n            reason = self._audit_reason\n",
    ),
    "A6_google_deactivated_account_not_named": (
        VIEWS,
        "                        self._audit_account = user\n",
        "",
    ),
    "A7_google_exchange_not_a_provider_failure": (
        VIEWS,
        "                    ErrorClass.PROVIDER\n"
        '                    if reason == "GOOGLE_EXCHANGE_FAILED"\n',
        "                    ErrorClass.USER\n"
        '                    if reason == "GOOGLE_EXCHANGE_FAILED"\n',
    ),
    "A8_change_password_wrong_password_not_recorded": (
        VIEWS,
        '            sign_in_failed(request, user, "password_change", "WRONG_PASSWORD")\n',
        "",
    ),
    # --- strict attribution (SM ruling) ---
    "A9_door_failure_names_the_account_holder": (
        "users/auth_audit.py",
        "        actor=failure_actor(request),\n        request=request,\n"
        '        target_type="CustomUser",\n'
        "        target_id=account.id if account is not None else None,\n",
        "        actor=account,\n        request=request,\n"
        '        target_type="CustomUser",\n'
        "        target_id=account.id if account is not None else None,\n",
    ),
    "A10_anonymous_recorded_as_system": (
        EM,
        "        return ActorRole.ANONYMOUS, None, None, None\n",
        "        return ActorRole.SYSTEM, None, None, None\n",
    ),
    "A11_login_failure_names_the_account_holder": (
        "users/serializers.py",
        "                actor=failure_actor(request),\n"
        "                request=request,\n"
        '                target_type="CustomUser",\n'
        "                target_id=user.id if user else None,\n",
        "                actor=user,\n"
        "                request=request,\n"
        '                target_type="CustomUser",\n'
        "                target_id=user.id if user else None,\n",
    ),
    "A12_login_failure_not_scoped_to_the_target_school": (
        "users/serializers.py",
        "                school_id=user.school_id if user else None,\n",
        "",
    ),
    "A13_door_failure_not_scoped_to_the_target_school": (
        "users/auth_audit.py",
        '        school_id=getattr(account, "school_id", None),\n',
        "",
    ),
}

TESTS = [
    "audit.tests_state_change",
    "users.tests_auth_audit_doors",
    "users.tests_auth_audit_events",
    "audit.tests_emitter",
]

originals: dict = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        open(path, "w").write(src.replace(a, b, 1))
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + ["--settings=settings_worktree", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open("docs/evidence/epic-a-s1/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
