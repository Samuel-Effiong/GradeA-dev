"""Epic A S3: apply each mutant, run the S3 tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

MODELS = "billing/models.py"
CTX = "audit/context.py"
TASKS = "audit/tasks.py"
LIC = "billing/license_service.py"
SVC = "billing/services.py"

MUTANTS = {
    # --- the actor rule (G5) ---
    "A1_owner_is_the_actor_again": (
        MODELS,
        "        actor=current_request_actor(),\n",
        "        actor=owner,\n",
    ),
    "A2_ledger_row_is_the_target_again": (
        MODELS,
        '        target_type="CustomUser",\n        target_id=owner_id,\n',
        '        target_type="CreditLedger",\n        target_id=row.id,\n',
    ),
    "A3_request_actor_ignored": (
        CTX,
        '    if user is not None and getattr(user, "is_authenticated", False):\n'
        "        return user\n",
        "    if False:\n        return user\n",
    ),
    "A4_not_scoped_to_the_owners_school": (
        MODELS,
        '        school_id=getattr(owner, "school_id", None),\n',
        "",
    ),
    # --- sweeps record themselves (G6) ---
    "S1_retention_sweep_records_nothing": (
        TASKS,
        "    emit(\n        AuditAction.AUDIT_RETENTION_SWEEP,\n"
        '        target_type="AuditEvent",\n        metadata={\n'
        '            "deleted_general": deleted_general,\n',
        "    (lambda *a, **k: None)(\n        AuditAction.AUDIT_RETENTION_SWEEP,\n"
        '        target_type="AuditEvent",\n        metadata={\n'
        '            "deleted_general": deleted_general,\n',
    ),
    "S2_zero_count_run_not_recorded": (
        TASKS,
        "    emit(\n        AuditAction.AUDIT_RETENTION_SWEEP,\n"
        '        target_type="AuditEvent",\n        metadata={\n'
        '            "deleted_general": deleted_general,\n',
        "    (emit if deleted_general or deleted_student else (lambda *a, **k: None))(\n"
        "        AuditAction.AUDIT_RETENTION_SWEEP,\n"
        '        target_type="AuditEvent",\n        metadata={\n'
        '            "deleted_general": deleted_general,\n',
    ),
    "S3_pii_sweep_records_nothing": (
        TASKS,
        "    emit(\n        AuditAction.AUDIT_RETENTION_SWEEP,\n"
        '        target_type="AuditEvent",\n        metadata={"scrubbed": updated},\n',
        "    (lambda *a, **k: None)(\n        AuditAction.AUDIT_RETENTION_SWEEP,\n"
        '        target_type="AuditEvent",\n        metadata={"scrubbed": updated},\n',
    ),
    # --- the clawback (G7, D4) ---
    "C1_clawback_bypasses_the_ledger": (
        LIC,
        "            SubscriptionService.expire_bucket(\n                bucket,\n",
        "            (lambda *a, **k: None)(\n                bucket,\n",
    ),
    "C2_clawback_logs_the_email": (
        LIC,
        "            teacher.id,\n            license_sub.id,\n            len(buckets),\n",
        "            teacher.email,\n            license_sub.id,\n            len(buckets),\n",
    ),
    "C3_rollover_logs_the_email": (
        LIC,
        '                        "teacher %s: requested %d (%s).",\n'
        "                        teacher.id,\n",
        '                        "teacher %s: requested %d (%s).",\n'
        "                        teacher.email,\n",
    ),
    "R1_expire_recheck_removed": (
        SVC,
        "        if bucket.is_processed:\n            return 0\n",
        "",
    ),
}

TESTS = [
    "audit.tests_background_attribution",
    "billing.tests.test_credit_transaction_audit",
    "audit.tests_license_admin_attribution",
    "audit.tests_state_change",
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

with open("docs/evidence/epic-a-s3/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
