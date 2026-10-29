"""Apply each landing-(A) mutant, run the suites that cover it, record
killers, restore. Every anchor must occur exactly once in its file:
replace(..., 1) on a non-unique anchor silently mutates the wrong site.
"""

import json
import re
import subprocess
import sys

ENROLL = "classrooms/services/enrollment.py"
ROSTER = "classrooms/services/roster_import.py"
BACKFILL = "classrooms/management/commands/backfill_pending_student_invites.py"

MUTANTS = {
    "R1_roster_names_not_passed": (
        ROSTER,
        "            first_name=row.first_name,\n",
        '            first_name="",\n',
    ),
    "R2_already_enrolled_not_skipped": (
        ROSTER,
        "    if (\n        existing is not None\n",
        "    if (\n        False\n        and existing is not None\n",
    ),
    "R3_new_student_ignores_names": (
        ENROLL,
        "        email=email,\n        first_name=first_name,\n",
        '        email=email,\n        first_name="",\n',
    ),
    "R4_promotion_keeps_the_old_code": (
        ENROLL,
        "        # no student row carries a code after onboarding.\n"
        "        student.activation_token = None\n",
        "        # no student row carries a code after onboarding.\n" "        pass\n",
    ),
    "B1_convert_keeps_the_old_code": (
        BACKFILL,
        "        student.must_change_password = True\n"
        "        student.activation_token = None\n",
        "        student.must_change_password = True\n",
    ),
    # The orphan and placeholder branches both call _clear_code; each anchor
    # includes the branch's own counter line to stay unique.
    "B2_orphan_code_not_cleared": (
        BACKFILL,
        "                    self._clear_code(student)\n                cleared_only += 1\n",
        "                    pass\n                cleared_only += 1\n",
    ),
    "B3_dry_run_clears_codes": (
        BACKFILL,
        "                if not dry_run:\n                    self._clear_code(student)\n"
        "                cleared_only += 1\n",
        "                if True:\n                    self._clear_code(student)\n"
        "                cleared_only += 1\n",
    ),
    "B6_placeholder_code_not_cleared": (
        BACKFILL,
        "                    self._clear_code(student)\n                placeholder += 1\n",
        "                    pass\n                placeholder += 1\n",
    ),
    "B4_output_names_the_email": (
        BACKFILL,
        '                f"{prefix}convert: student {student.pk} (pending course "\n',
        '                f"{prefix}convert: student {student.email} (pending course "\n',
    ),
    "B5_placeholder_converted_like_a_real_address": (
        BACKFILL,
        "            if student.email.endswith(PLACEHOLDER_DOMAIN):\n",
        "            if False:\n",
    ),
    # The "has ever signed in" rule (SM ruling after the first verification).
    "S1_must_change_password_back_as_the_onboarding_signal": (
        ENROLL,
        "        if student.is_active and has_signed_in(student):\n",
        "        if student.is_active and not student.must_change_password:\n",
    ),
    "S2_last_login_ignored": (
        ENROLL,
        "    return student.last_login is not None or (\n",
        "    return False or (\n",
    ),
    "S3_user_activity_ignored": (
        ENROLL,
        "        UserActivity.objects.filter(user=student).exists()\n",
        "        False\n",
    ),
    "S4_roster_reports_every_row_as_invited": (
        ROSTER,
        "    if invited:\n",
        "    if True:\n",
    ),
    # Re-verification blocker 1: stamp last_login without the cache fan-out.
    "S5_login_does_not_stamp_last_login": (
        "users/serializers.py",
        "        stamp_last_login(self.user)\n",
        "",
    ),
    "S6_google_sign_in_does_not_stamp_last_login": (
        "users/views.py",
        "            stamp_last_login(user)\n"
        "            refresh = EpochRefreshToken.for_user(user)\n",
        "            refresh = EpochRefreshToken.for_user(user)\n",
    ),
    "S7_stamp_through_save_fires_the_fanout": (
        "users/services.py",
        "    type(user).objects.filter(pk=user.pk).update(last_login=now)\n"
        "    user.last_login = now\n",
        "    user.last_login = now\n" '    user.save(update_fields=["last_login"])\n',
    ),
    "S8_update_last_login_back_on": (
        "AutoGrader/settings.py",
        "    # users.services.stamp_last_login, a queryset update that sends no "
        "signal.\n}",
        "    # users.services.stamp_last_login, a queryset update that sends no "
        'signal.\n    "UPDATE_LAST_LOGIN": True,\n}',
    ),
    # Re-verification blocker 2: a deactivated account is never re-enabled.
    "D1_deactivated_refusal_removed": (
        ENROLL,
        "        if not student.is_active and not was_never_activated(student):\n",
        "        if False:\n",
    ),
    "D2_email_verified_at_ignored": (
        ENROLL,
        "        and student.email_verified_at is None\n",
        "        and True\n",
    ),
    "D3_sign_in_ignored_by_never_activated": (
        ENROLL,
        "        and not has_signed_in(student)\n    )",
        "        and True\n    )",
    ),
    "D4_roster_reports_a_disabled_account_as_failed": (
        ROSTER,
        "    except AccountDisabledError as exc:\n",
        "    except ZeroDivisionError as exc:\n",
    ),
    "D5_backfill_converts_a_deactivated_account": (
        BACKFILL,
        "            if not was_never_activated(student):\n",
        "            if False:\n",
    ),
}

TESTS = [
    "classrooms.tests_roster_ready_to_use",
    "classrooms.tests_backfill_pending_student_invites",
    "classrooms.test_bulk_enrollment",
    "users.tests_last_login_stamp",
]

originals = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        open(path, "w").write(src.replace(a, b, 1))
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + ["--settings=settings_worktree", "--keepdb"],
            capture_output=True,
            text=True,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open("docs/evidence/retire-student-token-signup/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
