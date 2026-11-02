# flake8: noqa
"""H-276 mutation battery (SM, 2026-11-02). Each mutant is one exact
string replacement in one file; the file is restored from a saved copy and
its sha256 checked after every mutant. Run from the worktree root:
    python docs/evidence/h276-per-device-logout/mutants.py
"""

import hashlib
import pathlib
import subprocess
import sys

TESTS = [
    "users.tests_h276_per_device_logout",
    "users.tests_token_revocation",
]
M = [
    ("1", "users/tokens.py", "        token[SESSION_CLAIM] = str(uuid.uuid4())\n", ""),
    (
        "2",
        "users/authentication.py",
        "        if is_session_revoked(token):",
        "        if False:",
    ),
    ("3", "users/serializers.py", "        assert_session_live(refresh)\n", ""),
    (
        "4",
        "users/views.py",
        "                revoke_session(request.user, own_sid)\n",
        "                pass\n",
    ),
    (
        "5",
        "users/views.py",
        "            if own_sid is None:\n                request.user.revoke_all_sessions()\n            else:",
        "            request.user.revoke_all_sessions()\n            if True:",
    ),
    (
        "6",
        "users/views.py",
        "                if refresh_sid is not None and refresh_sid != own_sid and same_user:",
        "                if refresh_sid is not None and refresh_sid != own_sid:",
    ),
    (
        "7",
        "users/views.py",
        "            if own_sid is None:\n                request.user.revoke_all_sessions()",
        "            if own_sid is None:\n                pass",
    ),
    (
        "8",
        "users/tokens.py",
        "    RevokedSession.objects.filter(revoked_at__lt=cutoff).delete()",
        "    pass",
    ),
    (
        "9",
        "users/tokens.py",
        "    return sid is not None and RevokedSession.objects.filter(sid=sid).exists()",
        "    return sid is not None and RevokedSession.objects.filter(sid=sid, user__isnull=True).exists()",
    ),
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


ONLY = set(sys.argv[1:])
results, bad = [], False
for mid, path, old, new in M:
    if ONLY and mid not in ONLY:
        continue
    p = pathlib.Path(path)
    orig = p.read_bytes()
    h = sha(p)
    text = orig.decode()
    if text.count(old) != 1:
        results.append((mid, "NOT-APPLIED (anchor count %d)" % text.count(old)))
        bad = True
        continue
    p.write_text(text.replace(old, new))
    try:
        r = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                *TESTS,
                "--settings=settings_worktree",
                "--noinput",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        tail = [
            l for l in r.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))
        ]
        verdict = "KILLED" if r.returncode != 0 else "SURVIVED"
    finally:
        p.write_bytes(orig)
    ok = sha(p) == h
    if not ok:
        bad = True
    results.append(
        (
            mid,
            f"{verdict} | {' '.join(tail)} | restore sha {'OK' if ok else 'MISMATCH'}",
        )
    )
    print(mid, results[-1][1], flush=True)
print("\nSUMMARY")
for mid, v in results:
    print(f"M-H276-{mid}: {v}")
sys.exit(1 if bad or any("SURVIVED" in v for _, v in results) else 0)
