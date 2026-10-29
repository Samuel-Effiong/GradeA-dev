# flake8: noqa
"""H-265 gate (SM). Run from the worktree root inside the private venv:
    python -I docs/evidence/h265-user-facing-errors/gate.py <tip>
Steps: red run (call-site revert), makemigrations, seam, own + near, mutants M1-M6.
Every check is against NOTES.md; any difference prints STOP and exits non-zero."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

E = pathlib.Path("docs/evidence/h265-user-facing-errors")
BASE = "a3f5a6a1"
OWN = "billing.tests_h265_user_facing_errors"
NEAR = ["billing", "dashboard", "AutoGrader.tests_error_messages"]
SITES = [
    "billing/license_views.py",
    "billing/views.py",
    "billing/license_overage_offline_views.py",
    "dashboard/views.py",
    "billing/serializers.py",
    "billing/qa_time_travel.py",
    "billing/views_admin_credits.py",
]
SEAM = sorted(
    [
        "AutoGrader/error_messages.py",
        "billing/errors.py",
        "billing/license_overage_offline_views.py",
        "billing/license_service.py",
        "billing/license_views.py",
        "billing/qa_time_travel.py",
        "billing/serializers.py",
        "billing/services.py",
        "billing/stripe_service.py",
        "billing/views.py",
        "billing/views_admin_credits.py",
        "dashboard/query_params.py",
        "dashboard/views.py",
    ]
)
ENV = dict(
    os.environ,
    PYTHONDONTWRITEBYTECODE="1",
    PYTHONFAULTHANDLER="1",
    EXEMPT_EMAIL_DOMAINS="",
)
CAP = [
    "systemd-run",
    "--user",
    "--scope",
    "-q",
    "-p",
    "MemoryMax=7G",
    "-p",
    "MemorySwapMax=0",
    "nice",
    "-n",
    "10",
]


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def stop(msg):
    print("STOP:", msg)
    restore()
    sys.exit(2)


def restore():
    subprocess.run(
        [
            "git",
            "checkout",
            "HEAD",
            "--",
            *SITES,
            "AutoGrader/error_messages.py",
            "billing/license_service.py",
        ],
        check=False,
    )
    for p in pathlib.Path(".").glob("*/__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


def test(labels, out, settings="settings_worktree"):
    with open(out, "w") as fh:
        rc = subprocess.run(
            CAP
            + [
                "timeout",
                "-k",
                "60",
                "3000",
                sys.executable,
                "manage.py",
                "test",
                "--noinput",
                "--verbosity",
                "2",
                f"--settings={settings}",
                *labels,
            ],
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=ENV,
        ).returncode
    return rc, pathlib.Path(out).read_text()


def blocks(text):
    out = {}
    for b in re.split(r"^={70}$", text, flags=re.M):
        m = re.match(r"\s*(FAIL|ERROR): (\w+) \(", b)
        if m:
            out.setdefault(m.group(2), []).append((m.group(1), b))
    return out


def line(text, pat):
    m = re.search(pat, text, re.M)
    return m.group(0) if m else None


tip = sys.argv[1]
if sh("git", "rev-parse", "HEAD").strip() != sh("git", "rev-parse", tip).strip():
    stop("not at tip")
if sh("git", "status", "--porcelain", "--", ".", f":!{E}"):
    stop("tree not clean")

print("== 0. red run: sites at", BASE)
subprocess.run(["git", "checkout", BASE, "--", *SITES], check=True)
rc, t = test([OWN], E / "red_run.txt")
b = blocks(t)
restore()
ok = (
    rc != 0
    and line(t, r"^Ran 23 tests")
    and line(t, r"^FAILED \(failures=20\)$")
    and "_FailedTest" not in t
    and len(b) == 20
    and all(n.endswith("_hides_library_error_text") for n in b)
    and all(
        k == "FAIL" and "sk_live_LEAK internal detail' !=" in blk
        for v in b.values()
        for k, blk in v
    )
)
print(line(t, r"^Ran .*"), line(t, r"^FAILED.*"), "blocks", len(b))
if not ok:
    stop("red run is not as written")
print("red run as written")

print("== 1a. makemigrations")
r = subprocess.run(
    [
        sys.executable,
        "manage.py",
        "makemigrations",
        "--check",
        "--dry-run",
        "--settings=settings_worktree",
    ],
    capture_output=True,
    text=True,
    env=ENV,
)
if r.returncode:
    stop("makemigrations reports changes")
print("== 1b. seam")
changed = sorted(
    f
    for f in sh("git", "diff", "--name-only", BASE, "HEAD").split()
    if not f.startswith("docs/")
    and "/tests" not in f
    and not f.split("/")[-1].startswith("test")
)
(E / "seam.txt").write_text("\n".join(changed) + "\n")
print(changed)
if changed != SEAM:
    stop("seam differs")

print("== 1. own + near")
rc, t = test([OWN], E / "own.txt")
if rc or not line(t, r"^Ran 23 tests") or not line(t, r"^OK$"):
    stop("own not Ran 23 OK")
rc, t = test(NEAR, E / "near.txt")
print(line(t, r"^Ran .*"), line(t, r"^(OK|FAILED).*"))
if rc or not line(t, r"^Ran 2730 tests") or not line(t, r"^OK( \(skipped=\d+\))?$"):
    stop("near not Ran 2730 OK")
skips = re.findall(r"\.\.\. skipped '([^']*)'", t)
bad = [s for s in skips if "RUN_" not in s]
print("skips", len(skips), "non-opt-in", bad)
if bad:
    stop("non-opt-in skip")

print("== 2. mutants")
FB = lambda v, s: re.compile(
    r"describe_user_error\(\s*"
    + v
    + r",\s*fallback_message=\""
    + re.escape(s)
    + r"\",?\s*\)"
)
MUT = {
    "M1": (
        "billing/license_views.py",
        FB("e", "We couldn't change the license plan. Please try again."),
        "str(e)",
        1,
        [OWN],
        {"test_change_plan_hides_library_error_text": "!="},
    ),
    "M2": (
        "AutoGrader/error_messages.py",
        re.compile(r"^        BillingRequestError,\n", re.M),
        "",
        1,
        [OWN],
        {
            "test_change_plan_shows_a_written_refusal": "!=",
            "test_update_seats_shows_a_written_refusal": "!=",
            "test_select_plan_shows_a_written_refusal": "!=",
        },
    ),
    "M3": (
        "AutoGrader/error_messages.py",
        re.compile(r"^        QueryParamError,\n", re.M),
        "",
        1,
        ["dashboard.tests_dashboard_audit_fixes"],
        {"test_non_numeric_limit_is_a_400_not_a_500": "not found in"},
    ),
    "M4": (
        "billing/license_service.py",
        re.compile(
            r'raise BillingRequestError\("License already has this many seats\."\)'
        ),
        'raise ValueError("License already has this many seats.")',
        1,
        [OWN],
        {"test_update_seats_shows_a_written_refusal": "!="},
    ),
    "M5": (
        "dashboard/views.py",
        FB("exc", "One of the query parameters is not valid."),
        "str(exc)",
        1,
        [OWN],
        {"test_unit_performance_hides_library_error_text": "!="},
    ),
    "M6": (
        "billing/views_admin_credits.py",
        FB("e", "We couldn't top up the credits. Please try again."),
        "str(e)",
        1,
        [OWN],
        {"test_top_up_credits_hides_library_error_text": "!="},
    ),
}
results = {}
(E / "mutant_logs").mkdir(exist_ok=True)
for mid, (f, pat, rep, count, labels, expected) in MUT.items():
    src = pathlib.Path(f).read_text()
    new, n = pat.subn(rep, src, count=count)
    if n != 1:
        results[mid] = {"status": "BROKEN", "why": "anchor not found"}
        continue
    try:
        pathlib.Path(f).write_text(new)
        rc, t = test(labels, E / "mutant_logs" / f"{mid}.txt")
    finally:
        pathlib.Path(f).write_text(src)
        restore()
    b = blocks(t)
    missing = [
        k
        for k, frag in expected.items()
        if k not in b or not all(frag in blk for _, blk in b[k])
    ]
    status = (
        "SURVIVED"
        if rc == 0
        else (
            "KILLED"
            if not missing and "_FailedTest" not in t and line(t, r"^Ran ")
            else "BROKEN"
        )
    )
    results[mid] = {
        "status": status,
        "ran": line(t, r"^Ran .*"),
        "missing": missing,
        "also_failed": sorted(set(b) - set(expected)),
    }
    print(mid, results[mid])
(E / "mutation_results.json").write_text(json.dumps(results, indent=1))
if sh("git", "status", "--porcelain", "--", ".", f":!{E}"):
    stop("source not clean after mutants")
if any(v["status"] != "KILLED" for v in results.values()):
    print("STOP: not every mutant KILLED")
    sys.exit(6)
print("GATE GREEN")
