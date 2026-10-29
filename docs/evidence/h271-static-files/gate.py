# flake8: noqa
"""H-271 gate (SM). Run from the worktree root inside the private venv:
    python -I docs/evidence/h271-static-files/gate.py <tip>
Steps: red run (call-site revert), makemigrations, seam, own + near, pip-audit, mutants M1-M6.
Every check is against NOTES.md; any difference prints STOP and exits non-zero."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

E = pathlib.Path("docs/evidence/h271-static-files")
BASE = "f870fb0b"
OWN = "AutoGrader.tests_h271_static_files"
NEAR = ["AutoGrader", "audit", "users", "ai_processor.tests_h262_prod_image_dev_tools"]
SEAM = sorted(
    [".gitignore", "AutoGrader/settings.py", "Dockerfile", "requirements.txt"]
)
REVERT = ["Dockerfile", "AutoGrader/settings.py"]
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
    subprocess.run(["git", "checkout", "HEAD", "--", *SEAM], check=False)
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

print("== 0. red run:", REVERT, "at", BASE)
subprocess.run(["git", "checkout", BASE, "--", *REVERT], check=True)
rc, t = test([OWN], E / "red_run.txt")
b = blocks(t)
restore()
RED = {
    "setUpClass": ("ERROR", "has no attribute 'STATICFILES_STORAGE'"),
    "test_the_container_collects_static_files_before_gunicorn_starts": (
        "FAIL",
        "-1 == -1",
    ),
    "test_collectstatic_is_the_staticfiles_command": (
        "FAIL",
        "'cloudinary_storage' != 'django.contrib.staticfiles'",
    ),
    "test_production_static_storage_is_whitenoise_manifest": (
        "FAIL",
        "'django.contrib.staticfiles.storage.ManifestStaticFilesStorage' != "
        "'whitenoise.storage.CompressedManifestStaticFilesStorage'",
    ),
    "test_whitenoise_comes_directly_after_the_security_middleware": (
        "FAIL",
        "'whitenoise.middleware.WhiteNoiseMiddleware' not found in [",
    ),
    "test_collected_files_go_to_their_own_folder": ("FAIL", "static') != PosixPath("),
}
ok = (
    rc != 0
    and line(t, r"^Ran 6 tests")
    and line(t, r"^FAILED \(failures=5, errors=1\)$")
    and "_FailedTest" not in t
    and set(b) == set(RED)
    and all(
        len(b[n]) == 1 and b[n][0][0] == kind and frag in b[n][0][1]
        for n, (kind, frag) in RED.items()
    )
)
print(line(t, r"^Ran .*"), line(t, r"^FAILED.*"), "blocks", sorted(b))
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
if rc or not line(t, r"^Ran 7 tests") or not line(t, r"^OK$"):
    stop("own not Ran 7 OK")
rc, t = test(NEAR, E / "near.txt")
print(line(t, r"^Ran .*"), line(t, r"^(OK|FAILED).*"))
if rc or not line(t, r"^Ran 2064 tests") or not line(t, r"^OK( \(skipped=\d+\))?$"):
    stop("near not Ran 2064 OK")
skips = re.findall(r"\.\.\. skipped '([^']*)'", t)
bad = [s for s in skips if "CI_REQUIRE" not in s]
print("skips", len(skips), "non-opt-in", bad)
if bad:
    stop("a skip without CI_REQUIRE")

print("== 1c. pip-audit")
with open(E / "pip_audit.txt", "w") as fh:
    r = subprocess.run(
        [sys.executable, "scripts/pip_audit_gate.py"],
        stdout=fh,
        stderr=subprocess.STDOUT,
        env=ENV,
    )
print("pip-audit exit", r.returncode)
if r.returncode:
    stop("pip-audit gate not 0")

print("== 2. mutants")
ST = "AutoGrader/settings.py"
WN = '    "whitenoise.middleware.WhiteNoiseMiddleware",\n'
MUT = {
    "M1": (
        "Dockerfile",
        re.compile(re.escape("python manage.py collectstatic --noinput; ")),
        "",
        {"test_the_container_collects_static_files_before_gunicorn_starts": "-1 == -1"},
    ),
    "M2": (
        ST,
        re.compile(re.escape(WN)),
        "",
        {
            "test_whitenoise_comes_directly_after_the_security_middleware": "not found in [",
            "test_the_admin_login_page_and_its_stylesheet_are_served": "404 != 200",
        },
    ),
    "M3": (
        ST,
        re.compile(
            re.escape('"whitenoise.storage.CompressedManifestStaticFilesStorage"')
        ),
        '"django.contrib.staticfiles.storage.ManifestStaticFilesStorage"',
        {
            "test_production_static_storage_is_whitenoise_manifest": "!= 'whitenoise.storage.CompressedManifestStaticFilesStorage'"
        },
    ),
    "M4": (
        ST,
        re.compile(re.escape('STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")')),
        'STATIC_ROOT = os.path.join(BASE_DIR, "static")',
        {"test_collected_files_go_to_their_own_folder": "static') != PosixPath("},
    ),
    "M5": (
        ST,
        re.compile(r'(INSTALLED_APPS = \[\n)(.*?)(    "cloudinary_storage",\n)', re.S),
        r"\1\3\2",
        {
            "test_collectstatic_is_the_staticfiles_command": "'cloudinary_storage' != 'django.contrib.staticfiles'",
            "setUpClass": "has no attribute 'STATICFILES_STORAGE'",
        },
    ),
    "M6": (
        ST,
        re.compile(
            re.escape(WN) + r'(.*?    "audit\.middleware\.AuditMiddleware",\n)', re.S
        ),
        r"\1" + WN.replace("\\", "\\\\"),
        {"test_whitenoise_comes_directly_after_the_security_middleware": "!="},
    ),
}
MUT = {k: (f, p, r, 1, [OWN], e) for k, (f, p, r, e) in MUT.items()}
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
