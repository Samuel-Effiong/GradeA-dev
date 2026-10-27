"""H-268 re-gate seam: between the original gate tip (3cc85098) and the re-gate
tip, `AutoGrader/reason_codes.py` differs only in the named function
`_overage_cap_variant` (new, replaces the lambda) and `REASON_CODES` (the spec
now references the named function instead of a lambda).

Usage: python -I seam_check_regate.py <base dir> <tip dir>; exit 0 = as packed.
"""

import ast
import copy
import pathlib
import sys

PATH = "AutoGrader/reason_codes.py"

ALLOWED = {
    "_overage_cap_variant",  # new named function
    "REASON_CODES",  # lambda → _overage_cap_variant in the spec
}


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

    if changed != ALLOWED:
        print(f"SEAM: {PATH} changed {sorted(changed)}; allowed {sorted(ALLOWED)}")
        return 2

    print(f"{PATH}: {sorted(changed)}")
    print("SEAM OK: only the lambda-to-function units changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
