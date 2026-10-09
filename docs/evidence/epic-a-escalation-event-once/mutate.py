"""Intent audit events (the resolve-intent emit plus H-101, the escalation
event written once): apply each mutant, run the test modules, record
the killers, restore. Every anchor must occur exactly once and every mutant
must parse.

Rule 17: the test subprocess runs with PYTHONDONTWRITEBYTECODE=1, and the
__pycache__ of every mutated module's directory is deleted before each
mutant and after each restore.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

FLOW = "billing/license_stripe_mutation.py"
COMMAND = "billing/management/commands/resolve_licence_stripe_intent.py"
METADATA = "audit/metadata.py"
TESTS = [
    "billing.tests.test_h28_intent_audit",
    "billing.tests.test_h28_seat_phases",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = os.environ.get(
    "MUT_RESULTS", "docs/evidence/epic-a-escalation-event-once/mutation_results.json"
)

IN_FLOW = (
    "            if escalating and stored != LicenseStripeMutationStatus.ESCALATED:\n"
    "                audit_intent_status(intent, stored, status)\n"
)
BEAT = (
    "            if claimed:\n"
    "                audit_intent_status(intent, was, "
    "LicenseStripeMutationStatus.ESCALATED)\n"
)
CLOSE = (
    "            if closed:\n"
    "                audit_intent_status(intent, intent.status, OUTCOMES[outcome])\n"
)

MUTANTS = {
    "I1_an_in_flow_escalation_writes_no_event": (FLOW, IN_FLOW, ""),
    "I2_every_status_change_writes_an_event": (
        FLOW,
        "            if escalating and stored != LicenseStripeMutationStatus.ESCALATED:\n",
        "            if stored != LicenseStripeMutationStatus.ESCALATED:\n",
    ),
    "I17_a_stale_copy_records_the_escalation_again": (
        FLOW,
        "            if escalating and stored != LicenseStripeMutationStatus.ESCALATED:\n",
        "            if escalating:\n",
    ),
    "I18_before_is_not_the_stored_status": (
        FLOW,
        "                audit_intent_status(intent, stored, status)\n",
        '                audit_intent_status(intent, "PENDING", status)\n',
    ),
    "I3_the_beat_escalation_writes_no_event": (FLOW, BEAT, ""),
    "I4_the_beat_check_records_an_intent_it_did_not_claim": (
        FLOW,
        BEAT,
        BEAT.replace("            if claimed:\n", "            if True:\n"),
    ),
    "I5_a_manual_close_writes_no_event": (COMMAND, CLOSE, ""),
    "I6_a_lost_race_still_writes_an_event": (
        COMMAND,
        CLOSE,
        CLOSE.replace("            if closed:\n", "            if True:\n"),
    ),
    "I7_the_close_is_not_under_command_actor": (
        COMMAND,
        "        with transaction.atomic(), command_actor(resolver, command=COMMAND):\n",
        "        with transaction.atomic():\n",
    ),
    "I8_the_close_and_its_event_are_not_one_transaction": (
        COMMAND,
        "        with transaction.atomic(), command_actor(resolver, command=COMMAND):\n",
        "        with command_actor(resolver, command=COMMAND):\n",
    ),
    "I9_the_request_user_is_not_the_actor": (
        FLOW,
        "                actor=current_request_actor(),\n",
        "                actor=None,\n",
    ),
    "I10_the_request_is_not_passed": (
        FLOW,
        "                request=current_request(),\n",
        "",
    ),
    "I11_a_raising_emitter_stops_the_escalation": (
        FLOW,
        "    except Exception:  # noqa: BLE001 - the audit event must not block",
        "    except KeyError:  # noqa: BLE001 - the audit event must not block",
    ),
    "I12_before_and_after_are_swapped": (
        FLOW,
        '                before={"intent_status": str(before)},\n'
        '                after={"intent_status": str(after)},\n',
        '                before={"intent_status": str(after)},\n'
        '                after={"intent_status": str(before)},\n',
    ),
    "I13_the_licence_id_is_left_out": (
        FLOW,
        '                metadata={"license_id": str(intent.license_subscription_id)},\n',
        "                metadata={},\n",
    ),
    "I14_the_reason_goes_into_the_event": (
        FLOW,
        '                metadata={"license_id": str(intent.license_subscription_id)},\n',
        "                metadata={\n"
        '                    "license_id": str(intent.license_subscription_id),\n'
        '                    "source": intent.failure_reason or "",\n'
        "                },\n",
    ),
    "I15_intent_status_is_not_allowed_in_before_after": (
        METADATA,
        '            "license_id",\n            "intent_status",\n',
        '            "license_id",\n',
    ),
    "I16_license_id_is_not_allowed_in_the_metadata": (
        METADATA,
        '        {"changed_fields", "source", "license_id"}\n',
        '        {"changed_fields", "source"}\n',
    ),
}


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


originals: dict = {}
results = {}
env = {**os.environ, "EXEMPT_EMAIL_DOMAINS": "", "PYTHONDONTWRITEBYTECODE": "1"}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        mutated = src.replace(a, b, 1)
        ast.parse(mutated)
        clear_pycache(path)
        open(path, "w").write(mutated)
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env=env,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
        clear_pycache(path)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)
        clear_pycache(path)

with open(OUT, "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
