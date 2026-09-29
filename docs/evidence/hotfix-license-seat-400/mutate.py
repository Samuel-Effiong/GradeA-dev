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
}

TESTS = [
    "billing.tests.test_license_seat_400",
    "billing.tests.test_license_service",
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
