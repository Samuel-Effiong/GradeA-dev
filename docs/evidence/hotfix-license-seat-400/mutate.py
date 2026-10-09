"""Hotfix license-seat-400: apply each mutant, run the seat tests, record
killers, restore. Every anchor must occur exactly once in its file:
replace(..., 1) on a non-unique anchor silently mutates the wrong site.
"""

import json
import re
import subprocess
import sys

SERVICE = "billing/license_service.py"
SERIALIZER = "billing/serializers.py"
STRIPE = "billing/stripe_service.py"

MUTANTS = {
    "H1_catch_removed": (
        SERIALIZER,
        "        except LicenseRequestError as exc:\n",
        "        except ZeroDivisionError as exc:\n",
    ),
    "H2_catch_widened_to_bare_value_error": (
        SERIALIZER,
        "        except LicenseRequestError as exc:\n",
        "        except ValueError as exc:\n",
    ),
    "H3_seat_refusal_raised_untyped": (
        SERVICE,
        "            raise LicenseRequestError(\n"
        '                f"This licence has {seats}, but {added}. "\n',
        "            raise ValueError(\n"
        '                f"This licence has {seats}, but {added}. "\n',
    ),
    "H4_stripe_preflight_removed": (
        STRIPE,
        "        LicenseSubscriptionService.check_seat_capacity(\n",
        "        (lambda **kw: None)(\n",
    ),
    "H5_cap_off_by_one": (
        SERVICE,
        "        if max_seats > 0 and total_requested > max_seats:\n",
        "        if max_seats > 0 and total_requested >= max_seats:\n",
    ),
    "H6_carried_over_teachers_not_counted": (
        SERVICE,
        "        total_requested = len(carry_forward_emails) + len(genuinely_new_emails)\n",
        "        total_requested = len(genuinely_new_emails)\n",
    ),
    "H7_message_loses_the_remedy": (
        SERVICE,
        '                "Remove a teacher or increase Max seats."\n',
        '                ""\n',
    ),
    "H8_singular_wording_broken": (
        SERVICE,
        "            seats = f\"{max_seats} seat{'' if max_seats == 1 else 's'}\"\n",
        '            seats = f"{max_seats} seats"\n',
    ),
    # The Verification Engineer's surviving mutants (VERIFICATION.md, N1),
    # killed by the tests added for N1.
    "V1_stripe_precheck_ignores_carry_forward": (
        STRIPE,
        "            existing_license=LicenseSubscription.objects.filter(\n"
        "                school=school, is_active=True\n"
        "            ).first(),\n",
        "            existing_license=None,\n",
    ),
    "V2_zero_credit_plan_refusal_untyped": (
        SERVICE,
        "            raise LicenseRequestError(\n"
        '                f"License plan {plan.name} must define monthly_credits. "\n',
        "            raise ValueError(\n"
        '                f"License plan {plan.name} must define monthly_credits. "\n',
    ),
    "V3_standard_tier_refusal_untyped": (
        SERVICE,
        "            raise LicenseRequestError(\n"
        '                "Standard Grader tier is not available under License subscription"\n',
        "            raise ValueError(\n"
        '                "Standard Grader tier is not available under License subscription"\n',
    ),
    "V4_max_seats_guard_untyped": (
        SERVICE,
        '            raise LicenseRequestError("max_seats must be a positive integer")\n',
        '            raise ValueError("max_seats must be a positive integer")\n',
    ),
    # Widening (founder report): the school admin's add/remove teachers.
    "W1_no_seats_refusal_untyped": (
        SERVICE,
        "            raise LicenseRequestError(\n"
        "                LicenseSubscriptionService._no_seats_message(\n",
        "            raise ValueError(\n"
        "                LicenseSubscriptionService._no_seats_message(\n",
    ),
    "W2_inactive_licence_refusal_untyped": (
        SERVICE,
        "            raise LicenseRequestError(\n"
        "                \"This licence isn't active, so teachers can't be added to it.\"\n",
        "            raise ValueError(\n"
        "                \"This licence isn't active, so teachers can't be added to it.\"\n",
    ),
    "W3_not_listed_as_user_facing": (
        "AutoGrader/error_messages.py",
        "        # Written to be shown: a licence request the caller can fix.\n"
        "        LicenseRequestError,\n",
        "",
    ),
    "W4_remove_refusal_untyped": (
        SERVICE,
        "            raise LicenseRequestError(\n"
        '                "This teacher isn\'t an active teacher on this licence."\n',
        "            raise ValueError(\n"
        '                "This teacher isn\'t an active teacher on this licence."\n',
    ),
    "W5_no_seats_message_loses_the_counts": (
        SERVICE,
        '        in_use = f"{license_sub.teacher_count} of {license_sub.max_seats} in use"\n',
        '        in_use = "some in use"\n',
    ),
    "W6_full_licence_message_branch_lost": (
        SERVICE,
        "        if remaining == 0:\n",
        "        if False:\n",
    ),
}

TESTS = [
    "billing.tests.test_license_seat_400",
    "billing.tests.test_license_service",
    "billing.tests.test_license_teacher_changes_400",
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
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open("docs/evidence/hotfix-license-seat-400/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
