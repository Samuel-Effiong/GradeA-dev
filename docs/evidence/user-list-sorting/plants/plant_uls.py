"""User list plant harness: copy, mutate, sha check, run, restore, sha check.

Usage (from the worktree root): python plant_uls.py <PLANT_ID> <log_path>
"""

import hashlib
import shutil
import subprocess
import sys

PLANTS = {
    "Q-dates": (
        "users/user_list.py",
        [
            (
                '"activated_at": (lambda: F("email_verified_at"), ()),',
                '"activated_at": (lambda: F("date_joined"), ()),',
            ),
        ],
    ),
    "Q-plan": (
        "users/user_list.py",
        [
            (
                '                _one(_allocations(False), "license_subscription__plan_id"),\n'
                '                _one(_allocations(True), "license_subscription__plan_id"),\n'
                '                _one(_active_subscription(), "plan_id"),\n',
                '                _one(_active_subscription(), "plan_id"),\n'
                '                _one(_allocations(False), "license_subscription__plan_id"),\n'
                '                _one(_allocations(True), "license_subscription__plan_id"),\n',
            ),
        ],
    ),
    "Q-counts": (
        "users/user_list.py",
        [
            ("                        ai_graded_at__isnull=False,\n", ""),
        ],
    ),
    "Q-credits": (
        "users/user_list.py",
        [
            ('                    - F("credit_wallet__reserved_credits"),\n', ""),
        ],
    ),
    "Q-cache": (
        "users/mixins.py",
        [
            (
                "query_params = json.dumps(self.request.query_params.dict(), sort_keys=True)",
                'query_params = "{}"',
            ),
        ],
    ),
    "Q-listonly": (
        "users/views.py",
        [
            (
                '        if self.action == "list":\n            return UserListSerializer',
                "        if True:\n            return UserListSerializer",
            ),
        ],
    ),
    "Q-prefetch": (
        "users/user_list.py",
        [
            ('        "credit_wallet__buckets",\n', ""),
        ],
    ),
    "Q-gate": (
        "users/filters.py",
        [
            (
                "        if not (user and user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN):\n"
                "            return queryset\n",
                "",
            ),
        ],
    ),
}

LABELS = [
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


if __name__ == "__main__":
    main()
