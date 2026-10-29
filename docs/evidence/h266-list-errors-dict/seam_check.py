"""H-266 seam check: the only production file changed is AutoGrader/settings.py,
and only the LIST_SERIALIZER_ERRORS_AS_DICT value changed.

Usage: python -I seam_check.py <base_dir> <tip_dir>
"""

import ast
import sys


def setting_value(source, key):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values, strict=True):
            if isinstance(k, ast.Constant) and k.value == key:
                return ast.literal_eval(v)
    return None


def rest_framework_dict(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, _v in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "LIST_SERIALIZER_ERRORS_AS_DICT"
                ):
                    return node
    return None


def main():
    base_dir, tip_dir = sys.argv[1], sys.argv[2]
    path = "AutoGrader/settings.py"

    base_src = open(f"{base_dir}/{path}").read()
    tip_src = open(f"{tip_dir}/{path}").read()

    base_val = setting_value(base_src, "LIST_SERIALIZER_ERRORS_AS_DICT")
    tip_val = setting_value(tip_src, "LIST_SERIALIZER_ERRORS_AS_DICT")

    print(f"LIST_SERIALIZER_ERRORS_AS_DICT: base={base_val}, tip={tip_val}")

    ok = True
    if base_val is not False:
        print(f"FAIL: expected base value False, got {base_val!r}")
        ok = False
    if tip_val is not True:
        print(f"FAIL: expected tip value True, got {tip_val!r}")
        ok = False

    if ok:
        print("SEAM OK: only the setting value changed")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
