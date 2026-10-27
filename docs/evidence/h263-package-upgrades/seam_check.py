# flake8: noqa: E501
"""H-263 seam, by syntax tree and by text (Hardening Engineer, 4d).

    python -I seam_check.py <base commit>

Run at the worktree's root. The row changes pins and one setting, nothing else:

* `requirements.txt` keeps its length and differs by exactly the six pins;
* `Dockerfile` differs by exactly gunicorn's pin;
* `.pip-audit-ignore.toml` loses exactly the six packages' blocks (87 ids), and
  every other entry is unchanged;
* `AutoGrader/settings.py`'s syntax tree equals the base's apart from the one
  key `LIST_SERIALIZER_ERRORS_AS_DICT: False` in `REST_FRAMEWORK`;
* the new test module and `docs/` are this row's own.

Anything else changed is a stop.
"""

import ast
import re
import subprocess
import sys

import tomllib

BASE = sys.argv[1]
NEW_TEST = "assignments/tests_h263_list_error_shape.py"
PINS = {
    "gunicorn": ("20.0.4", "22.0.0"),
    "pillow": ("12.0.0", "13.0.0"),
    "PyJWT": ("2.10.1", "2.15.0"),
    "lxml": ("6.0.2", "6.1.0"),
    "urllib3": ("2.5.0", "2.8.0"),
    "djangorestframework": ("3.16.1", "3.18.2"),
}
SETTING = "LIST_SERIALIZER_ERRORS_AS_DICT"


def show(path):
    return subprocess.run(
        ["git", "show", f"{BASE}:{path}"], capture_output=True, text=True, check=True
    ).stdout


def report(ok, path, what):
    print(f"{'seam ok' if ok else 'SEAM STOP'} {path}: {what}")
    return ok


def rest_framework_dict(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "REST_FRAMEWORK" for t in node.targets
        ):
            return node.value
    return None


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
    if path == "requirements.txt":
        b, t = show(path).splitlines(), open(path).read().splitlines()
        want = list(b)
        for p, (old, new) in PINS.items():
            assert want.count(f"{p}=={old}") == 1, f"{p}=={old} not once at the base"
            want[want.index(f"{p}=={old}")] = f"{p}=={new}"
        ok = (
            report(
                t == want, path, "the same length, differing by exactly the six pins"
            )
            and ok
        )
    elif path == "Dockerfile":
        old, new = PINS["gunicorn"]
        b, t = show(path), open(path).read()
        want = b.replace(
            f'RUN pip install "gunicorn=={old}"',
            f'RUN pip install "gunicorn=={new}"',
            1,
        )
        ok = (
            report(
                b.count(f'RUN pip install "gunicorn=={old}"') == 1 and t == want,
                path,
                f"gunicorn's pin alone, {old} to {new}",
            )
            and ok
        )
    elif path == ".pip-audit-ignore.toml":
        base_doc = tomllib.loads(show(path))
        tip_doc = tomllib.loads(open(path).read())
        gone = {p.lower() for p in PINS}
        kept = [e for e in base_doc["ignore"] if e["package"].lower() not in gone]
        removed = [e for e in base_doc["ignore"] if e["package"].lower() in gone]
        ids_out = sum(len(e["ids"]) for e in removed)
        same = tip_doc["ignore"] == kept and len(removed) == len(gone) and ids_out == 87
        ok = (
            report(
                same,
                path,
                f"the six packages' blocks out ({ids_out} ids), the other {len(kept)} entries unchanged",
            )
            and ok
        )
        # the header's comments are prose, but the ids must not have moved
        b_ids = re.findall(r'^\s+"([A-Z]+-[0-9A-Za-z-]+)",$', show(path), re.M)
        t_ids = re.findall(r'^\s+"([A-Z]+-[0-9A-Za-z-]+)",$', open(path).read(), re.M)
        ok = (
            report(
                len(b_ids) - len(t_ids) == 87 and set(t_ids) < set(b_ids),
                path,
                "every id left is one the base already listed",
            )
            and ok
        )
    elif path == "AutoGrader/settings.py":
        base, tip = ast.parse(show(path)), ast.parse(open(path).read())
        b_rf, t_rf = rest_framework_dict(base), rest_framework_dict(tip)
        if b_rf is None or t_rf is None:
            ok = report(False, path, "REST_FRAMEWORK not found in both")
            continue
        added = [
            (k, v)
            for k, v in zip(t_rf.keys, t_rf.values)
            if isinstance(k, ast.Constant) and k.value == SETTING
        ]
        if len(added) != 1 or not (
            isinstance(added[0][1], ast.Constant) and added[0][1].value is False
        ):
            ok = report(False, path, f"not one {SETTING}: False")
            continue
        i = t_rf.keys.index(added[0][0])
        del t_rf.keys[i], t_rf.values[i]
        ok = (
            report(
                ast.dump(base) == ast.dump(tip),
                path,
                f"syntax tree as at the base apart from {SETTING}: False",
            )
            and ok
        )
    else:
        ok = report(False, path, "not a file this row may change")
print("SEAM OK" if ok else "SEAM STOP")
sys.exit(0 if ok else 1)
