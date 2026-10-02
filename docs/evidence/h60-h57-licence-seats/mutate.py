"""H-60 + H-57: apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import ast
import json
import os
import re
import subprocess
import sys

L = "billing/license_service.py"
S = "billing/stripe_service.py"
M = "billing/license_stripe_mutation.py"
Z = "billing/serializers.py"
V = "billing/license_views.py"
# 0b (2026-09-30): mutation runs on its own test DB, never the regression's.
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree")
LOG = "license_stripe_mutation.log_provider_error(intent, exc)\n"
TA = " + license_stripe_mutation.TRY_AGAIN"
SEATS_FIXED = (
    "            raise ValueError(\n"
    '                "Stripe error while updating seats."' + TA + "\n"
)
SEATS_RAW = (
    "            raise ValueError(\n"
    '                f"Stripe error while updating seats: {exc}"\n'
)

MUTANTS = {
    # H-60: each site shows Stripe's text again.
    "A1_seats_read_shows_stripe_text": (
        L,
        "            )\n            " + LOG + SEATS_FIXED,
        "            )\n            " + LOG + SEATS_RAW,
    ),
    "A2_seats_payment_failed_shows_why": (
        L,
        '                    "Seat increase payment failed. Seats have not been increased."\n',
        '                    f"Seat increase payment failed ({why}). "\n',
    ),
    "A3_seats_modify_shows_stripe_text": (
        L,
        "        except stripe.error.StripeError as exc:\n            "
        + LOG
        + SEATS_FIXED,
        "        except stripe.error.StripeError as exc:\n            "
        + LOG
        + SEATS_RAW,
    ),
    "A4_plan_read_shows_stripe_text": (
        S,
        '                "Could not retrieve Stripe subscription."\n',
        '                f"Could not retrieve Stripe subscription: {exc}"\n',
    ),
    "A5_plan_price_shows_stripe_text": (
        S,
        '                    "Custom price creation failed."' + TA + "\n",
        '                    f"Custom price creation failed: {exc}"\n',
    ),
    "A6_plan_card_shows_stripe_text": (
        S,
        '                "Card declined. The plan has not been changed; update the "\n'
        '                "payment method and try again.",\n',
        '                f"Card declined: {exc}",\n',
    ),
    "A7_plan_modify_shows_stripe_text": (
        S,
        '                "Stripe error while changing the plan."\n',
        '                f"Stripe error while changing the plan: {exc}"\n',
    ),
    "A8_cancel_shows_stripe_text": (
        L,
        '                "Failed to schedule Stripe cancellation."\n',
        '                f"Failed to schedule Stripe cancellation: {exc}"\n',
    ),
    "A9_convert_shows_stripe_text": (
        L,
        '                "Failed to cancel Stripe subscription."\n',
        '                f"Failed to cancel Stripe subscription: {exc}"\n',
    ),
    "A10_log_carries_stripe_text": (
        M,
        '        "Licence %s, intent %s: Stripe failed (%s, code=%s, request=%s)",\n'
        "        intent.license_subscription_id,\n"
        "        intent.id,\n"
        "        type(exc).__name__,\n",
        '        "Licence %s, intent %s: Stripe failed (%s: %s)",\n'
        "        intent.license_subscription_id,\n"
        "        intent.id,\n"
        "        type(exc).__name__,\n"
        "        str(exc),\n"
        "    )\n"
        "    (\n",
    ),
    "A11_log_drops_the_intent_id": (
        M,
        "        intent.license_subscription_id,\n        intent.id,\n        type(exc).__name__,\n",
        "        intent.license_subscription_id,\n        None,\n        type(exc).__name__,\n",
    ),
    # H-57.
    "B1_max_seats_patchable_again": (
        Z,
        '    "max_seats": "Seats can\'t be changed here. Use the update_seats action.",\n',
        "",
    ),
    "B2_unchanged_echo_refused": (
        Z,
        '    """True when a PATCH `value` for `field` is not what `instance` holds."""\n',
        '    """True when a PATCH `value` for `field` is not what `instance` holds."""\n'
        "    return True\n",
    ),
    "B3_blank_and_null_differ": (
        Z,
        "    return (value or None) != (stored or None)\n",
        "    return value != stored\n",
    ),
    "B4_fk_compared_by_identity": (
        Z,
        '        return getattr(value, "pk", value) != getattr(stored, "pk", stored)\n',
        "        return value is not stored\n",
    ),
    "B5_teacher_emails_ignored": (
        Z,
        '            if attrs.get("teacher_emails"):\n',
        "            if False:\n",
    ),
    "B6_refusal_applies_the_rest": (
        Z,
        "                raise serializers.ValidationError(refused)\n",
        "                pass\n",
    ),
    # The not-recorded outcome on the licence routes (v2's finding).
    "N1_seats_not_recorded_falls_to_500": (
        V,
        "        except license_stripe_mutation.LicenceStripeChangeNotRecorded as e:\n"
        "            return _not_recorded_response(e)\n"
        "        except Exception as e:\n"
        '            logger.exception("Unexpected error updating seats: %s", e)\n',
        "        except Exception as e:\n"
        '            logger.exception("Unexpected error updating seats: %s", e)\n',
    ),
    "N2_escalated_answers_503": (
        V,
        '        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)\n',
        '        return Response({"detail": str(exc)}, status=503)\n',
    ),
    "N3_no_retry_after": (V, '    response["Retry-After"] = "30"\n', ""),
    "N4_compensated_marked_escalated": (
        M,
        "                    escalated=False,\n",
        "                    escalated=True,\n",
    ),
    "N5_handler_logs_the_chain": (
        V,
        "        license_stripe_mutation.log_provider_error(exc.intent, exc)\n",
        '        logger.exception("Licence change not recorded: %s", exc)\n',
    ),
    # Round 3 (v2's notes 2 and 3).
    "C1_stripe_price_patchable_again": (
        Z,
        "                and self.instance.billing_method == LicenseBillingMethod.STRIPE\n",
        "                and False\n",
    ),
    "C2_offline_price_refused_too": (
        Z,
        "                and self.instance.billing_method == LicenseBillingMethod.STRIPE\n",
        "                and True\n",
    ),
    "W1_plan_message_framed_twice": (
        L,
        "        except ValueError:\n"
        "            # The inner messages are already the client's fixed text (H-60);\n",
        "        except ValueError as exc:\n"
        '            raise ValueError(f"Stripe price change failed: {exc}") from exc\n'
        "            # The inner messages are already the client's fixed text (H-60);\n",
    ),
}

TESTS = [
    "billing.tests.test_h60_licence_stripe_text",
    "billing.tests.test_h57_licence_patch",
    "billing.tests.test_h28_plan_phases",
]
originals: dict = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        mutated = src.replace(a, b, 1)
        # A mutant that doesn't parse is "killed" by an import error, not by
        # a test (run 2's A1/A3). Refuse it instead.
        ast.parse(mutated)
        open(path, "w").write(mutated)
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open("docs/evidence/h60-h57-licence-seats/r3_mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
