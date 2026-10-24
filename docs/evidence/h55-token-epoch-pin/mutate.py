"""H-55: apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

C = "users/management/commands/remediate_student123_passwords.py"
BUMP = 'token_epoch=F("token_epoch") + 1'

MUTANTS = {
    # The Verification Engineer's surviving mutant (H-3 record, logged as H-55).
    "M1_epoch_set_to_1": (C, BUMP, "token_epoch=1"),
    "M2_epoch_bumped_by_2": (C, BUMP, 'token_epoch=F("token_epoch") + 2'),
    "M3_epoch_not_bumped": (C, BUMP, 'token_epoch=F("token_epoch")'),
}

TESTS = [
    "users.tests_remediate_student123",
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

with open("docs/evidence/h55-token-epoch-pin/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
