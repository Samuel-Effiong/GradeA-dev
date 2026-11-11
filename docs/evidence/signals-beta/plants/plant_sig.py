"""User list plant harness: copy, mutate, sha check, run, restore, sha check.
Brief 4 (signals, beta): plants S1-S6.

The saved copy is deleted once the restore sha matches (the sha lines in
the output are the proof); it is kept only on a mismatch.

Usage (from the worktree root): python plant_uls.py <PLANT_ID> <log_path>
"""

import hashlib
import os
import shutil
import subprocess
import sys

PLANTS = {
    "S1-drop-flag": (
        "billing/intent_signals.py",
        [
            (
                "    for q in conditions.values():\n",
                '    for n, q in conditions.items():\n        if n == "is_power_grader":\n            continue\n',
            ),
        ],
    ),
    "S2-tiebreak": (
        "billing/intent_signals.py",
        [
            (
                'F("signals_score").desc(nulls_last=True),',
                'F("signals_score").asc(nulls_last=True),',
            ),
        ],
    ),
    "S3-cache": (
        "users/mixins.py",
        [
            (
                "query_params = json.dumps(self.request.query_params.dict(), sort_keys=True)",
                'query_params = "{}"',
            ),
        ],
    ),
    "S4-default-order": (
        "billing/views.py",
        [
            (
                "        if ordering in signal_rules.ORDERING_VALUES:\n",
                "        if True:\n",
            ),
            (
                'ordering.startswith("-"), id_field="user_id"',
                '(ordering or "-").startswith("-"), id_field="user_id"',
            ),
        ],
    ),
    "S5-gate": (
        "users/filters.py",
        [
            (
                '        """The same super admin gate as filter_list_value."""\n'
                '        user = getattr(self.request, "user", None)\n'
                "        if not (user and user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN):\n"
                "            return queryset\n",
                '        """The same super admin gate as filter_list_value."""\n',
            ),
        ],
    ),
    "S6-boundary": (
        "billing/intent_signals.py",
        [
            (
                '"active_last_week": Q(**{f"{prefix}last_active_at__gte": now - ACTIVE_WINDOW}),',
                '"active_last_week": Q(**{f"{prefix}last_active_at__gt": now - ACTIVE_WINDOW}),',
            ),
        ],
    ),
}

LABELS = [
    "billing.tests.test_intent_signals",
    "users.tests_user_list_signals",
    "billing.tests.test_intent_signals_pins",
    "users.tests_user_list_sorting",
    "users.tests_user_list_payload_pin",
]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main():
    plant, log = sys.argv[1], sys.argv[2]
    path, edits = PLANTS[plant]
    saved = f"{log}.{path.replace('/', '_')}.orig"
    shutil.copyfile(path, saved)
    original = sha(path)
    committed = subprocess.run(
        ["git", "show", f"HEAD:{path}"], capture_output=True, check=True
    ).stdout
    assert hashlib.sha256(committed).hexdigest() == original, "file differs from HEAD"
    text = open(path).read()
    for old, new in edits:
        assert text.count(old) == 1, (plant, old[:60])
        text = text.replace(old, new)
    open(path, "w").write(text)
    planted = sha(path)
    print(f"{plant} {path} original {original}")
    print(f"{plant} {path} planted  {planted}")
    assert planted != original
    try:
        with open(log, "w") as out:
            subprocess.run(
                [
                    "flock",
                    "/home/bond-servant-in-training/.machine-fullsuite.lock",
                    sys.executable,
                    "manage.py",
                    "test",
                    *LABELS,
                    "--settings=settings_worktree",
                    "--keepdb",
                    "--noinput",
                    "-v",
                    "2",
                ],
                stdout=out,
                stderr=subprocess.STDOUT,
            )
    finally:
        shutil.copyfile(saved, path)
        restored = sha(path)
        print(
            f"{plant} {path} restored {restored} {'OK' if restored == original else 'MISMATCH'}"
        )
        assert restored == original
        os.remove(saved)


if __name__ == "__main__":
    main()
