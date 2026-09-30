"""Epic A S8: apply each mutant, run the S8 tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

CMD = "audit/management/commands/audit_volume_report.py"
VOL = "audit/volume.py"

MUTANTS = {
    "V1_not_read_only": (
        CMD,
        '                cursor.execute("SET TRANSACTION READ ONLY")\n',
        '                cursor.execute("SELECT 1")\n',
    ),
    "V2_no_tablesample": (
        CMD,
        '                f"{quoted} TABLESAMPLE SYSTEM (1)",  # nosec B608\n',
        "",
    ),
    "V3_unbounded_sample": (
        CMD,
        '                    f"FROM (SELECT * FROM {source} LIMIT %s) s",\n',
        '                    f"FROM (SELECT * FROM {source}) s WHERE %s > 0",\n',
    ),
    "V4_an_email_reaches_the_output": (
        CMD,
        "                out(f\"day {row['day']} {action} {row['n']}\")\n",
        "                out(f\"day {row['day']} {action} {row['n']} \"\n"
        '                    f"{AuditEvent.objects.exclude(actor_email=None)'
        ".values_list('actor_email', flat=True).first()}\")\n",
    ),
    "V5_window_ignored": (
        CMD,
        "                AuditEvent.objects.filter(action=action, occurred_at__gte=since)\n",
        "                AuditEvent.objects.filter(action=action)\n",
    ),
    "V6_student_rate_dropped": (
        CMD,
        "            daily[action] = daily.get(action, 0) + n * students\n",
        "            daily[action] = daily.get(action, 0) + n\n",
    ),
    "V7_three_years_kept_as_one": (
        VOL,
        "    RetentionClass.STUDENT_RECORD.value: 365 * 3,\n",
        "    RetentionClass.STUDENT_RECORD.value: 365,\n",
    ),
    # v2's N1: the per-class count is unwindowed again (a full scan).
    "V8_class_count_scans_the_table": (
        CMD,
        "                retention_class=retention_class, occurred_at__gte=since\n",
        "                occurred_at__gte=since\n",
    ),
}

TESTS = ["audit.tests_volume_report"]
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

with open("docs/evidence/epic-a-s8/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
