"""Apply each mutant, run the AUTHZ-T1/T2 suite, record which tests kill it, restore."""

import json
import re
import subprocess
import sys

M, V, A, T = (
    "users/models.py",
    "users/views.py",
    "users/authentication.py",
    "users/tokens.py",
)
S = "users/serializers.py"
MUTANTS = {
    "M1_logout_does_not_bump": (V, "        request.user.revoke_all_sessions()\n", ""),
    "M2_auth_class_ignores_epoch": (
        A,
        "if token_epoch_of(token) != user.token_epoch:",
        "if False:",
    ),
    "M3_refresh_ignores_epoch": (
        S,
        "        assert_epoch_current(refresh, user)\n",
        "",
    ),
    "M4_missing_claim_is_invalid": (
        T,
        "int(token.get(EPOCH_CLAIM, 0))",
        "int(token.get(EPOCH_CLAIM, -1))",
    ),
    "M5_set_password_does_not_bump": (
        M,
        "        super().set_password(raw_password)\n        self._queue_epoch_bump()\n",
        "        super().set_password(raw_password)\n",
    ),
    "M6_set_unusable_password_does_not_bump": (
        M,
        "        super().set_unusable_password()\n        self._queue_epoch_bump()\n",
        "        super().set_unusable_password()\n",
    ),
    "M7_update_fields_skips_epoch": (
        M,
        'kwargs["update_fields"] = set(update_fields) | {"token_epoch"}',
        'kwargs["update_fields"] = set(update_fields)',
    ),
    "M8_rehash_bumps_epoch": (
        M,
        "        self._rehashing = True\n        try:",
        "        self._rehashing = False\n        try:",
    ),
    "M9_tokens_not_stamped_with_current_epoch": (
        T,
        "        token[EPOCH_CLAIM] = (",
        "        token[EPOCH_CLAIM] = 0 and (",
    ),
    "M10_bump_is_read_modify_write": (
        M,
        'self.token_epoch = F("token_epoch") + 1',
        "self.token_epoch = self.token_epoch + 1",
    ),
    "M11_stale_epoch_accepted_if_lower": (
        A,
        "if token_epoch_of(token) != user.token_epoch:",
        "if token_epoch_of(token) > user.token_epoch:",
    ),
}
sources = {p: open(p).read() for p in {m[0] for m in MUTANTS.values()}}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        assert a in sources[path], f"{name}: anchor not found"
        open(path, "w").write(sources[path].replace(a, b, 1))
        p = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                "users.tests_token_revocation",
                "--settings=settings_worktree",
                "--keepdb",
            ],
            capture_output=True,
            text=True,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(sources[path])
finally:
    for path, src in sources.items():
        open(path, "w").write(src)
with open("docs/evidence/authz-token-epoch/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
