"""Mutants for the H-1 Stage 3 payload-viewer fix (gaps #3, #4, #5).

One mutant per guard the fix adds. Each entry: (name, path, old, new, the
tests expected to catch it). Applied by payload_viewer_mutants.sh inside a
disposable worktree; every restore is checked against the commit's blob.
"""

SETTINGS_TEST = (
    "users.tests_cache_matrix_payload_viewers.PayloadViewerFreshnessTests."
    "test_gap3_student_settings_save_refreshes_teacher_and_school_admin"
)
FANOUT = "AutoGrader.tests_cache_user_fanout"
PV = "users.tests_cache_matrix_payload_viewers"

MUTANTS = [
    (
        "m1_admins_of_teacher_schools_dropped",
        "users/signals.py",
        "    admin_schools = set(filter(None, school_ids)) | set(teacher_school_ids)",
        "    admin_schools = set(filter(None, school_ids))  # MUTANT m1",
        f"{PV} {FANOUT}",
    ),
    (
        "m2_payload_only_change_bumps_nobody",
        "users/signals.py",
        "            return user_payload_viewer_scopes([instance.pk], [instance.school_id])",
        "            return []  # MUTANT m2",
        f"{PV} {FANOUT}",
    ),
    (
        "m3_payload_only_change_also_bumps_schools",
        "users/signals.py",
        "            return user_payload_viewer_scopes([instance.pk], [instance.school_id])",
        "            return viewer_scopes_for_users([instance.pk], [instance.school_id])  # MUTANT m3",
        FANOUT,
    ),
    (
        "m4_settings_bumps_no_viewers",
        "users/signals.py",
        "        scopes.extend(user_payload_viewer_scopes([user_id], [school_id]))",
        "        pass  # MUTANT m4",
        f"{PV} {FANOUT}",
    ),
    (
        "m5_settings_also_bumps_schools",
        "users/signals.py",
        "        scopes.extend(user_payload_viewer_scopes([user_id], [school_id]))",
        "        scopes.extend(viewer_scopes_for_users([user_id], [school_id]))  # MUTANT m5",
        FANOUT,
    ),
    (
        "m6_bucket_skips_payload_viewers",
        "billing/signals.py",
        "    scopes.extend(user_payload_viewer_scopes([user_id], [school_id]))",
        "    pass  # MUTANT m6",
        PV,
    ),
    (
        "m7_bucket_skips_superadmins",
        "billing/signals.py",
        "    scopes.extend((SCOPE_USER, admin_id) for admin_id in superadmin_user_ids())",
        "    pass  # MUTANT m7",
        PV,
    ),
    (
        "m8_bio_not_payload_visible",
        "users/signals.py",
        'PAYLOAD_ONLY_USER_FIELDS = ("bio",)',
        "PAYLOAD_ONLY_USER_FIELDS = ()  # MUTANT m8",
        f"{PV} {FANOUT}",
    ),
]

if __name__ == "__main__":
    import sys

    action, name = sys.argv[1], sys.argv[2]
    entry = next(m for m in MUTANTS if m[0] == name)
    _, path, old, new, tests = entry
    if action == "tests":
        print(tests)
    elif action == "path":
        print(path)
    elif action == "apply":
        source = open(path).read()
        assert (
            source.count(old) == 1
        ), f"{name}: pattern found {source.count(old)} times"
        open(path, "w").write(source.replace(old, new))
        print("applied")
    elif action == "names":
        print(" ".join(m[0] for m in MUTANTS))
