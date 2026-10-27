"""H-269 seam check: only the documented units changed in the two production files.

AutoGrader/reason_codes.py: only REASON_CODES changed (the MID_BATCH entry
gained variant_from_params).

students/task_tracking.py: only _mid_batch_error changed (the variant= arg
was removed).

Usage: python -I seam_check.py <base_dir> <tip_dir>
"""

import ast
import sys


def top_level_names(source):
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and isinstance(
            next(
                n
                for n in ast.walk(tree)
                if hasattr(n, "body") and node in ast.iter_child_nodes(n)
            ),
            ast.Module,
        )
    }


def functions_in(source):
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def changed_top_level(base_src, tip_src):
    base_tree = ast.parse(base_src)
    tip_tree = ast.parse(tip_src)
    base_nodes = {}
    tip_nodes = {}
    for tree, store in ((base_tree, base_nodes), (tip_tree, tip_nodes)):
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        store[target.id] = ast.dump(node)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                store[node.target.id] = ast.dump(node)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                store[node.name] = ast.dump(node)
    changed = set()
    for name in set(base_nodes) | set(tip_nodes):
        if base_nodes.get(name) != tip_nodes.get(name):
            changed.add(name)
    return changed


def main():
    base_dir, tip_dir = sys.argv[1], sys.argv[2]

    ok = True

    # reason_codes.py: only REASON_CODES changed
    rc_base = open(f"{base_dir}/AutoGrader/reason_codes.py").read()
    rc_tip = open(f"{tip_dir}/AutoGrader/reason_codes.py").read()
    rc_changed = changed_top_level(rc_base, rc_tip)
    print(f"reason_codes.py changed top-level names: {sorted(rc_changed)}")
    if rc_changed != {"REASON_CODES"}:
        print("FAIL: expected only REASON_CODES to change in reason_codes.py")
        ok = False
    else:
        print("reason_codes.py: SEAM OK (only REASON_CODES changed)")

    # task_tracking.py: only _mid_batch_error changed
    tt_base = open(f"{base_dir}/students/task_tracking.py").read()
    tt_tip = open(f"{tip_dir}/students/task_tracking.py").read()
    tt_changed = changed_top_level(tt_base, tt_tip)
    print(f"task_tracking.py changed top-level names: {sorted(tt_changed)}")
    if tt_changed != {"_mid_batch_error"}:
        print("FAIL: expected only _mid_batch_error to change in task_tracking.py")
        ok = False
    else:
        print("task_tracking.py: SEAM OK (only _mid_batch_error changed)")

    if ok:
        print("SEAM OK")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
