# H-284 expectations (committed before the runs)

Goal: CI "Dependency audit" (scripts/pip_audit_gate.py, pip-audit 2.9.0 --strict --no-deps --disable-pip) goes green on beta.

Disclosed: E1 was observed before this file was written, on 2026-11-03 at e71e710b (it came from CI, and was then reproduced locally). It is recorded here as a baseline, not as a prediction.

- E1 (baseline, red): at origin/beta, the gate exits 1 with "Found 20 known vulnerabilities" (authlib, filelock, oauthlib, pyasn1, social-auth-core, tornado).
- E2 (green): at this branch, the gate exits 0, prod "No known vulnerabilities found", dev likewise.
- E3: `uv pip check` in the fresh venv h284_deps_env reports no conflicts.
- E4: targeted run in h284_deps_env, AutoGrader.tests_pip_audit_gate + ai_processor.tests_h262_prod_image_dev_tools + AutoGrader.tests_strict_gate (if present): all OK, 0 failures.
- E5, planted bugs. Each is restored by sha256, and each must be KILLED:
  - P1: re-add "safety" to DEV_ONLY_PACKAGES makes test_all_dev_packages_in_dev_requirements fail.
  - P2: delete the social-auth-core entry from .pip-audit-ignore.toml makes the gate exit 1 (1 vulnerability, GHSA-h2r5-x5m7-3935).
- E6: full run (strict_bundle copy b35 pointed at h284_deps_env): Ran 8906, OK (skipped=30). No test count change, since only two set entries were removed and no test methods.

## Amendment, 2026-11-03: H-285 folded in (owner: "remove djoser"), committed before these runs

The owner also ruled that main gets nothing, not even a hotfix. This is beta only.

H-285 removes djoser, social-auth-app-django, social-auth-core, python3-openid, requests-oauthlib and oauthlib. Nothing else depends on them, and our code doesn't import them. It also removes INSTALLED_APPS "djoser", the DJOSER settings block, and every ignore entry for those packages. P2 above is withdrawn because that entry is removed, and it is replaced by P3 and P4.

- E7 (red): AutoGrader.tests_h285_djoser_removed at this commit gives Ran 4, FAILED (failures=4).
- E8 (green): after the change, Ran 4, OK. The gate still exits 0, now with no social-auth or oauthlib entries.
- E5 planted bugs, revised. Each must be KILLED:
  - P1: as above.
  - P3: re-add "djoser" to INSTALLED_APPS makes test_djoser_not_in_installed_apps fail. In h284_deps_env, where djoser is uninstalled, Django setup also fails.
  - P4: re-add `social-auth-core==4.7.0` to requirements.txt makes the gate exit 1 AND test_removed_packages_not_pinned fail.
- E6 revised: full run gives Ran 8910, OK (skipped=30), which is 8906 + 4 new tests.
