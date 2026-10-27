"""H-268 seam, by program: between the base and the tip, `AutoGrader/reason_codes.py`
differs only in the named units, and `CodedError.__init__` only in the
documented way.

Usage: python -I seam_check.py <base dir> <tip dir>; exit 0 = as packed.
"""

import ast
import copy
import pathlib
import sys

PATH = "AutoGrader/reason_codes.py"

# The top-level units that may change (the key function's output for each).
ALLOWED_TOPS = {
    "from collections.abc import Callable",  # new import
    "ReasonSpec",  # the variant_from_params field
}

# The class-level method that may change.
ALLOWED_METHODS = {"CodedError.__init__"}


def _key(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign):
        return ",".join(ast.unparse(t) for t in node.targets)
    if isinstance(node, ast.AnnAssign) and node.target:
        return ast.unparse(node.target)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return ast.unparse(node)
    return "stmt:" + ast.dump(node)


def _units(tree):
    units = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            shell = copy.deepcopy(node)
            shell.body = [
                n
                for n in shell.body
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            units[node.name] = ast.dump(shell)
            for n in node.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units[f"{node.name}.{n.name}"] = n
        else:
            units[_key(node)] = node
    return units


def _dump(x):
    return x if isinstance(x, str) else ast.dump(x)


def main():
    base_dir, tip_dir = map(pathlib.Path, sys.argv[1:3])
    base = _units(ast.parse((base_dir / PATH).read_text(encoding="utf-8")))
    tip = _units(ast.parse((tip_dir / PATH).read_text(encoding="utf-8")))
    changed = {
        k
        for k in set(base) | set(tip)
        if k not in base or k not in tip or _dump(base[k]) != _dump(tip[k])
    }

    # REASON_CODES is an annotated assignment keyed as "REASON_CODES".
    # Allow it to change (the OVERAGE_CAP_EXCEEDED entry changed).
    allowed_all = ALLOWED_TOPS | ALLOWED_METHODS | {"REASON_CODES"}
    if changed != allowed_all:
        print(f"SEAM: {PATH} changed {sorted(changed)}; allowed {sorted(allowed_all)}")
        return 2

    print(f"{PATH}: {sorted(changed)}")
    ok = True

    # Check that CodedError.__init__ changed only in the documented way:
    init_tip = tip["CodedError.__init__"]
    body = ast.unparse(init_tip)

    if "chosen = variant" not in body:
        print("SEAM: CodedError.__init__ is missing 'chosen = variant'")
        ok = False
    if "spec.variant_from_params" not in body:
        print("SEAM: CodedError.__init__ is missing variant_from_params consultation")
        ok = False
    if "self._variant = chosen" in body:
        print("SEAM: self._variant should be set from variant, not chosen")
        ok = False

    if not ok:
        return 2
    print("SEAM OK: only the named units changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
