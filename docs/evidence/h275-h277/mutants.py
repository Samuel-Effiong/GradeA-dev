# flake8: noqa
"""H-275/H-277 mutation battery (SM, 2026-11-02). Each mutant is one exact
string replacement in one file; the file is restored from a saved copy and
its sha256 checked after every mutant. Run from the worktree root:
    python docs/evidence/h275-h277/mutants.py [ids...]
"""

import hashlib
import pathlib
import subprocess
import sys

TESTS = [
    "billing.tests.test_h275_invoice_wording",
    "users.tests_h277_logout_owner",
    "users.tests_h276_per_device_logout",
]
W = "billing/invoice_wording.py"
S = "billing/stripe_service.py"
V = "users/views.py"
M = [
    ("1", W, "    if license:\n", "    if False:\n"),
    (
        "2",
        W,
        "    text = received if paid else failed\n",
        "    text = failed if paid else received\n",
    ),
    (
        "3",
        W,
        '    received, failed = _PHRASES.get(billing_reason or "", _FALLBACK)\n',
        '    received, failed = _PHRASES.get(billing_reason or "", (f"Subscription payment received ({billing_reason})", f"Subscription payment failed ({billing_reason})"))\n',
    ),
    (
        "4",
        S,
        "billing_reason=billing_reason, paid=True, license=True\n",
        "billing_reason=billing_reason, paid=True, license=False\n",
    ),
    (
        "5",
        S,
        "billing_reason=billing_reason, paid=True, license=False\n",
        "billing_reason=billing_reason, paid=True, license=True\n",
    ),
    (
        "6",
        S,
        "billing_reason=billing_reason, paid=False, license=False\n",
        "billing_reason=billing_reason, paid=False, license=True\n",
    ),
    (
        "7",
        S,
        "billing_reason=billing_reason, paid=False, license=True\n",
        "billing_reason=billing_reason, paid=False, license=False\n",
    ),
    (
        "8",
        V,
        "            if str(token.get(api_settings.USER_ID_CLAIM)) != str(request.user.pk):\n",
        "            if False:\n",
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
    print(f"M-H275-{mid}: {v}")
sys.exit(1 if bad or any("SURVIVED" in v for _, v in results) else 0)
