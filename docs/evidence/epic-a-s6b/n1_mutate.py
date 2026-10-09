"""S6b N1 author mutants (FILE_TOO_LARGE params as numbers).

Run from the S6b worktree at its gated commit: `python mutate.py [NAME ...]`,
or `python mutate.py check` to confirm every anchor matches exactly once.
"""

import hashlib
import os
import re
import subprocess
import sys

NEW = "assignments.tests_file_reason_codes"
SVC = "assignments/services.py"
MUTANTS = {
    # Bytes: actual as a display string again.
    "N1a_bytes_actual_text": (
        "AutoGrader/uploads.py",
        '                "actual": int(uploaded_file.size),\n',
        '                "actual": human_size(uploaded_file.size),\n',
        [NEW],
    ),
    # Pages: actual as "3 pages" again.
    "N1b_pages_actual_text": (
        SVC,
        '                        "actual": int(exc.page_count),\n',
        '                        "actual": f"{exc.page_count} pages",\n',
        [NEW],
    ),
    # Pixels: actual as "WxH px" again.
    "N1c_pixels_actual_text": (
        SVC,
        '        params["actual"] = width * height\n',
        '        params["actual"] = f"{width}x{height} px"\n',
        [NEW, "assignments.tests_security"],
    ),
    # Compression: actual as text again.
    "N1d_compression_actual_text": (
        SVC,
        '        params["actual"] = int(smallest)\n',
        '        params["actual"] = human_size(smallest)\n',
        [NEW],
    ),
    # display ignored: the message shows raw numbers.
    "N1e_display_ignored": (
        "AutoGrader/reason_codes.py",
        "**params, **(display or {})",
        "**params",
        [NEW, "AutoGrader.tests_reason_codes"],
    ),
    # An unknown bomb size gets an invented number.
    "N1f_unknown_size_invented": (
        SVC,
        "        bomb = Image.MAX_IMAGE_PIXELS\n",
        (
            "        bomb = Image.MAX_IMAGE_PIXELS\n"
            '        params["actual"] = 2 * (bomb or 0)\n'
        ),
        ["assignments.tests_security"],
    ),
}


def sha(b):
    return hashlib.sha256(b).hexdigest()


if sys.argv[1:] == ["check"]:
    for n, (p, o, _, _) in MUTANTS.items():
        print(n, open(p).read().count(o))
    sys.exit()

for name in sys.argv[1:] or MUTANTS:
    path, old, new, mods = MUTANTS[name]
    pristine = subprocess.check_output(["git", "show", f"HEAD:{path}"])
    src = open(path).read()
    if src.count(old) != 1:
        print(name, "APPLY_FAILED", src.count(old), flush=True)
        continue
    open(path, "w").write(src.replace(old, new))
    try:
        out = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--scope",
                "-q",
                "-p",
                "MemoryMax=6G",
                "-p",
                "MemorySwapMax=0",
                "nice",
                "-n",
                "10",
                "timeout",
                "-k",
                "60",
                "1800",
                sys.executable,
                "manage.py",
                "test",
                *mods,
                "--settings=settings_worktree",
                "--noinput",
                "--keepdb",
                "-v1",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        o = out.stdout + out.stderr
        failing = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", o, re.M)))
        summary = next(
            (line for line in o.splitlines() if line.startswith(("OK", "FAILED"))),
            f"? rc={out.returncode}",
        )
        print(name, "KILLED" if failing else "SURVIVED", summary, failing, flush=True)
    finally:
        subprocess.run(["git", "checkout", "--", path], check=True)
        print(
            "  restore",
            "ok" if sha(open(path, "rb").read()) == sha(pristine) else "MISMATCH",
            flush=True,
        )
