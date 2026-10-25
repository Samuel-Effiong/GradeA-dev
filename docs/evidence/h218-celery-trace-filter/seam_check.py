"""H-218 seam, by program: AutoGrader/safe_logging.py and AutoGrader/celery.py at
the tip differ from the base only by the statements added for the filter.

Comments do not reach the syntax tree. Every top-level statement of each base
file must appear, unchanged and in the same order, at the tip; the statements
the tip adds must be exactly the ones named in ADDED, by kind and name. In
safe_logging.py, CeleryTraceFilter.filter must return only True, raise
nothing, and call describe_error_for_log only inside a try with an
`except Exception` (the never-raise rule). In celery.py, the added `if` must
install the filter on the logger "celery.app.trace".
Usage: python -I seam_check.py <safe_logging base> <safe_logging tip>
<celery base> <celery tip>; exit 0 = as packed.
"""

import ast
import sys

ADDED = {
    "safe_logging": [
        ("Import", "logging"),
        ("Assign", "_CELERY_TRACE_KEPT"),
        ("Assign", "RETRY_TEXT_OMITTED"),
        ("ClassDef", "CeleryTraceFilter"),
    ],
    "celery": [
        ("Import", "logging"),
        ("ImportFrom", "AutoGrader.safe_logging.CeleryTraceFilter"),
        ("Assign", "_trace_logger"),
        ("If", "_trace_logger"),
    ],
}


def _label(node):
    kind = type(node).__name__
    if isinstance(node, ast.Import):
        return kind, ",".join(a.name for a in node.names)
    if isinstance(node, ast.ImportFrom):
        return kind, ",".join(f"{node.module}.{a.name}" for a in node.names)
    if isinstance(node, ast.Assign):
        return kind, ",".join(ast.unparse(t) for t in node.targets)
    if isinstance(node, ast.ClassDef):
        return kind, node.name
    if isinstance(node, ast.If):
        return kind, "_trace_logger" if "_trace_logger" in ast.unparse(node) else "?"
    return kind, ast.unparse(node)[:60]


def _added(base, tip):
    """The tip's statements not matched, in order, by the base's; None if a
    base statement is missing or changed."""
    want = [ast.dump(n) for n in base.body]
    extra, i = [], 0
    for node in tip.body:
        if i < len(want) and ast.dump(node) == want[i]:
            i += 1
        else:
            extra.append(node)
    return extra if i == len(want) else None


def _filter_faults(cls):
    faults = []
    [method] = [
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "filter"
    ]
    for node in ast.walk(method):
        if isinstance(node, ast.Return) and not (
            isinstance(node.value, ast.Constant) and node.value.value is True
        ):
            faults.append(f"a return other than True at line {node.lineno}")
        if isinstance(node, ast.Raise):
            faults.append(f"a raise at line {node.lineno}")
    guarded = set()
    for node in ast.walk(method):
        if isinstance(node, ast.Try) and any(
            h.type is not None and ast.unparse(h.type) == "Exception"
            for h in node.handlers
        ):
            for inner in node.body:
                guarded.update(id(n) for n in ast.walk(inner))
    for node in ast.walk(method):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "describe_error_for_log"
            and id(node) not in guarded
        ):
            faults.append(
                f"describe_error_for_log outside the try at line {node.lineno}"
            )
    return faults


def check(name, base_path, tip_path):
    base = ast.parse(open(base_path, encoding="utf-8").read())
    tip = ast.parse(open(tip_path, encoding="utf-8").read())
    extra = _added(base, tip)
    if extra is None:
        return [f"{name}: a base statement is missing or changed"]
    labels = [_label(n) for n in extra]
    if labels != ADDED[name]:
        return [f"{name}: added {labels}, expected {ADDED[name]}"]
    faults = []
    if name == "safe_logging":
        [cls] = [n for n in extra if isinstance(n, ast.ClassDef)]
        faults += [f"{name}: {f}" for f in _filter_faults(cls)]
    else:
        [install] = [n for n in extra if isinstance(n, ast.If)]
        text = ast.unparse(install)
        assign = ast.unparse([n for n in extra if isinstance(n, ast.Assign)][0])
        if "addFilter(CeleryTraceFilter())" not in text or (
            "getLogger('celery.app.trace')" not in assign
        ):
            faults.append(f"{name}: the filter is not installed on celery.app.trace")
    return faults


def main():
    faults = check("safe_logging", sys.argv[1], sys.argv[2]) + check(
        "celery", sys.argv[3], sys.argv[4]
    )
    if faults:
        print("SEAM:", "; ".join(faults))
        return 2
    print(
        "SEAM OK: only the filter, its two constants, the imports and its install "
        "on celery.app.trace were added; the filter returns only True and guards describe"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
