# flake8: noqa
"""H-270 gate (SM). Run from the worktree root inside the private venv:
    python -I docs/evidence/h270-profile-webp/gate.py <tip>
Steps: red run (call-site revert), makemigrations, seam, own + near, mutants M1-M8.
Every check is against NOTES.md; any difference prints STOP and exits non-zero."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

E = pathlib.Path("docs/evidence/h270-profile-webp")
BASE = "94a52815"
OWN = "users.tests_h270_profile_webp"
NEAR = ["users", "classrooms", "assignments.tests_h267_pillow_formats"]
SEAM = sorted(["AutoGrader/image_formats.py", "users/models.py"])
GUARDS = {
    "test_a_save_that_keeps_the_picture_does_not_re_encode_it",
    "test_an_old_jpeg_picture_still_loads_unchanged",
}
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

print("== 0. red run: users/models.py at", BASE)
subprocess.run(["git", "checkout", BASE, "--", "users/models.py"], check=True)
rc, t = test([OWN], E / "red_run.txt")
b = blocks(t)
restore()
webp = "test_a_webp_upload_loses_its_exif_too"
ok = (
    rc != 0
    and line(t, r"^Ran 15 tests")
    and line(t, r"^FAILED \(failures=13\)$")
    and "_FailedTest" not in t
    and len(b) == 13
    and not (set(b) & GUARDS)
    and all(k == "FAIL" for v in b.values() for k, _ in v)
    and all(
        "b'EXIF' unexpectedly found in" in blk for _, blk in b.get(webp, [("", "")])
    )
    and all(
        "False is not true : profile_pics/" in blk
        for n, v in b.items()
        if n != webp
        for _, blk in v
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
if rc or not line(t, r"^Ran 15 tests") or not line(t, r"^OK$"):
    stop("own not Ran 15 OK")
rc, t = test(NEAR, E / "near.txt")
print(line(t, r"^Ran .*"), line(t, r"^(OK|FAILED).*"))
if rc or not line(t, r"^Ran 1553 tests") or not line(t, r"^OK \(skipped=4\)$"):
    stop("near not Ran 1553 OK (skipped=4)")
skips = re.findall(r"\.\.\. skipped '([^']*)'", t)
bad = [s for s in skips if "CI_REQUIRE" not in s]
print("skips", len(skips), "non-opt-in", bad)
if bad or len(skips) != 4:
    stop("skips differ")

print("== 2. mutants")
IF = "AutoGrader/image_formats.py"
SAVE = 'clean.save(out, format="WEBP", quality=PROFILE_IMAGE_WEBP_QUALITY, method=6)'
HOOK = "if self.profile_image and not self.profile_image._committed:"
MUT = {
    "M1": (
        "users/models.py",
        re.compile(re.escape(HOOK)),
        "if False:",
        {"test_the_model_stores_a_new_picture_as_webp": "False is not true"},
    ),
    "M2": (
        IF,
        re.compile(re.escape(SAVE)),
        SAVE[:-1] + ', exif=opened.info.get("exif", b""))',
        {
            "test_gps_and_orientation_are_gone_and_the_picture_is_upright": "b'EXIF' unexpectedly found in"
        },
    ),
    "M3": (
        IF,
        re.compile(r"ImageOps\.exif_transpose\(opened\)"),
        "opened",
        {
            "test_gps_and_orientation_are_gone_and_the_picture_is_upright": "(40, 20) != (20, 40)"
        },
    ),
    "M4": (
        IF,
        re.compile(r"    img\.thumbnail\(\n.*?\n    \)\n", re.S),
        "",
        {
            "test_a_large_picture_is_shrunk_to_1024_on_its_longest_side": "(3000, 1500) != (1024, 512)"
        },
    ),
    "M5": (
        IF,
        re.compile(r'mode = "RGBA" if _has_transparency\(img\) else "RGB"'),
        'mode = "RGB"',
        {"test_transparency_is_kept": "'RGB' != 'RGBA'"},
    ),
    "M6": (
        "users/models.py",
        re.compile(re.escape(HOOK)),
        "if self.profile_image:",
        {
            "test_a_save_that_keeps_the_picture_does_not_re_encode_it": "!=",
            "test_an_old_jpeg_picture_still_loads_unchanged": "!=",
        },
    ),
    "M7": (
        IF,
        re.compile(re.escape('name=f"{stem}.webp"')),
        "name=os.path.basename(upload.name)",
        {"test_the_model_stores_a_new_picture_as_webp": "False is not true"},
    ),
    "M8": (
        IF,
        re.compile(re.escape(SAVE)),
        SAVE[:-1] + ', icc_profile=icc or b"")',
        {"test_an_icc_profile_is_not_stored": "b'ICCP' unexpectedly found in"},
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
