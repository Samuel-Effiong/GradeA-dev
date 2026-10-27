# flake8: noqa: E501
"""H-257 seam, by syntax tree and by text (Hardening Engineer, 4d).

    python -I seam_check.py <base commit>

Run at the worktree's root. Every Python file the row changes (the new test
module and docs/ aside) must have the base's syntax tree, so its change is
comments only, except users/mixins.py, whose one change is the keyword
usedforsecurity=False on the hashlib.md5 call. The two config files may change
only as written: pyproject.toml loses `tests = ['B101', 'B102']` and gains
`skips = ['B404']` (plus comments); .pre-commit-config.yaml's bandit exclude
gains the test-module pattern (plus comments).
"""

import ast
import subprocess
import sys

BASE = sys.argv[1]
NEW_TEST = "users/tests_h257_cache_key_digest.py"


def show(path):
    return subprocess.run(
        ["git", "show", f"{BASE}:{path}"], capture_output=True, text=True, check=True
    ).stdout


def code_lines(text):
    """Non-blank lines with full-line comments removed."""
    return [
        l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")
    ]


changed = subprocess.run(
    ["git", "diff", "--name-only", BASE, "HEAD"],
    capture_output=True,
    text=True,
    check=True,
).stdout.split()
ok = True
for path in changed:
    if path.startswith("docs/") or path == NEW_TEST:
        continue
    if path.endswith(".py"):
        base, tip = ast.parse(show(path)), ast.parse(open(path).read())
        if path == "users/mixins.py":
            calls = [
                n
                for n in ast.walk(tip)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == "hashlib.md5"
            ]
            kws = [
                k
                for c in calls
                for k in c.keywords
                if k.arg == "usedforsecurity" and ast.unparse(k.value) == "False"
            ]
            if len(calls) != 1 or len(kws) != 1:
                print(f"SEAM STOP {path}: not one md5 call with usedforsecurity=False")
                ok = False
                continue
            calls[0].keywords.remove(kws[0])
        same = ast.dump(base) == ast.dump(tip)
        print(
            f"{'seam ok' if same else 'SEAM STOP'} {path}: syntax tree as at the base"
            + (
                " apart from usedforsecurity=False"
                if path == "users/mixins.py"
                else " (comments only)"
            )
        )
        ok = ok and same
    elif path == "pyproject.toml":
        b, t = code_lines(show(path)), code_lines(open(path).read())
        want = [l for l in b if l != "tests = ['B101', 'B102']"]
        want.insert(
            want.index("exclude_dirs = ['tests', 'venv', 'docs']") + 1,
            "skips = ['B404']",
        )
        same = t == want
        print(
            f"{'seam ok' if same else 'SEAM STOP'} {path}: the tests limit removed, skips = ['B404'] added"
        )
        ok = ok and same
    elif path == ".pre-commit-config.yaml":
        b, t = code_lines(show(path)), code_lines(open(path).read())
        old = '        exclude: "tests/|examples/|docs/|setup.py"'
        new = '        exclude: "tests/|examples/|docs/|setup.py|(^|/)tests?_[^/]*\\\\.py$"'
        same = b.count(old) == 1 and t == [new if l == old else l for l in b]
        print(
            f"{'seam ok' if same else 'SEAM STOP'} {path}: the bandit exclude gains the test-module pattern"
        )
        ok = ok and same
    else:
        print(f"SEAM STOP {path}: not a file this row may change")
        ok = False
print("SEAM OK" if ok else "SEAM STOP")
sys.exit(0 if ok else 1)
