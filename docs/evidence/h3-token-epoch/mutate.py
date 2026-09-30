"""H-3 token_epoch follow-up: apply each mutant to the command, run the H-3
tests, record killers, restore.

E1-E3 target the epoch bump. M1-M7 are the original H-3 mutants
(docs/evidence/h3-student-password-remediation/mutate.py), re-anchored on the
new update() call so the earlier guarantees are re-proven on this code.
Every anchor must occur exactly once, so no mutant lands on the wrong site.
"""

import json
import re
import subprocess
import sys

CMD = "users/management/commands/remediate_student123_passwords.py"
OUT = "docs/evidence/h3-token-epoch/mutation_results.json"
CAS = "updated = User.objects.filter(pk=pk, password=old_hash).update("
BUMP = 'password=unusable, token_epoch=F("token_epoch") + 1'
orig = open(CMD).read()
MUTANTS = {
    "E1_drop_the_epoch_bump": (BUMP, "password=unusable"),
    "E2_bump_outside_the_compare_and_set": (
        CAS,
        'User.objects.filter(pk=pk).update(token_epoch=F("token_epoch") + 1)\n'
        "                    " + CAS,
    ),
    "E3_bump_every_account": (
        '        self.stdout.write(f"Reset: {reset}")\n',
        '        User.objects.update(token_epoch=F("token_epoch") + 1)\n'
        '        self.stdout.write(f"Reset: {reset}")\n',
    ),
    "M1_remove_the_reset": (
        BUMP,
        'password=old_hash, token_epoch=F("token_epoch") + 1',
    ),
    "M2_execute_flag_ignored_dry_run_writes_nothing_or_all": (
        "        if not execute:\n",
        "        if False:\n",
    ),
    "M3_no_compare_and_set": (
        "User.objects.filter(pk=pk, password=old_hash).update",
        "User.objects.filter(pk=pk).update",
    ),
    "M4_model_check_password_rehashes_on_dry_run": (
        "check_password(LITERAL, user.password, setter=None)",
        "user.check_password(LITERAL)",
    ),
    "M5_target_everyone": (
        "if check_password(LITERAL, user.password, setter=None):",
        "if True:",
    ),
    "M6_report_leaks_hash": (
        '"previous_hash_algorithm": _algorithm(old_hash),',
        '"previous_hash_algorithm": old_hash,',
    ),
    "M7_activity_signal_ignored": (
        'seen_ids = active_ids | {d["id"] for d in details if d["last_login"]}',
        "seen_ids = set()",
    ),
}
results = {}
try:
    for name, (a, b) in MUTANTS.items():
        assert orig.count(a) == 1, f"{name}: anchor found {orig.count(a)} times"
        open(CMD, "w").write(orig.replace(a, b, 1))
        p = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                "users.tests_remediate_student123",
                "--settings=settings_worktree",
                "--keepdb",
                "--noinput",
            ],
            capture_output=True,
            text=True,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
finally:
    open(CMD, "w").write(orig)
json.dump(results, open(OUT, "w"), indent=2)
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
