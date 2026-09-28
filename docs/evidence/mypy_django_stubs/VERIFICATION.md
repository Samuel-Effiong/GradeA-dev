# mypy-django-stubs: Verification

Branch `task/mypy-django-stubs` @ 8cf9268, off beta 4b902fc.

## BLOCKING: `language: system` breaks the CI pre-commit job
`.github/workflows/pre-commit.yml` runs on every push and PR to main, beta and dev. Its only steps are `actions/checkout`, `actions/setup-python` (3.12) and `pre-commit/action@v3.0.1`. It never installs `requirements.txt` and never sets `SECRET_KEY` or any other settings env var. `.env` is gitignored (`.gitignore:19`) and untracked, so it won't exist on the runner. Under the old mirror hook this job worked, because the mirror built its own isolated mypy env. With `language: system`, the runner has no `mypy` on PATH, so the hook fails with "Executable `mypy` not found". I reproduced the same condition locally (venv stripped from PATH, pre-commit launched by absolute path): hook Failed, exit 1. So this branch turns the CI pre-commit job red on the first push to beta after landing. Installing requirements alone wouldn't fix it either: §9.3 shows `django.setup()` raises ImproperlyConfigured without SECRET_KEY, and CI has no `.env`. EVIDENCE.md doesn't mention CI anywhere; §9 only tests local commits.
Suggested fix (your call): in `pre-commit.yml`, add a `pip install -r requirements.txt` step and a job-level `env:` block with SECRET_KEY and the other settings vars, copying the pattern from `tests.yml` (line 72 onwards, line 127). Alternatively, skip mypy in the pre-commit/action step (`SKIP=mypy`) and run it as its own step inside the tests.yml-style env. Either way, the proof should be a real CI run (or an equivalent from-scratch simulation), not just local.

## Verified independently (all hold)
- Whole-repo `mypy .` with the ratchet: "Success: no issues found in 734 source files". This matches §9.1.
- Ratchet list: 159 `module =` overrides, matching the 159 files with errors. `billing.license_service` is present. The three bare-name docs modules (`attack_replay`, `stripe_refusal_real`, `test_refusal_handling_scale`) are each unique in the repo, confirmed with `find`.
- The ratchet doesn't blanket-suppress. `ai_processor.models` is not in the list. I injected a deliberate `-> int` returning a str, and both direct mypy and `pre-commit run mypy` caught it. Reverted; `git diff` is empty.
- Venv-unavailable (§9.3): reproduced. With the venv stripped from PATH, the hook prints "Executable `mypy` not found" and exits 1. Only one `mypy` exists on this box (the venv's): I checked `which -a`, pyenv shims, `~/.local/bin`, `/usr/bin` and `/usr/local/bin`.
- Stronger than claimed: I also tested a WRONG mypy. I built a throwaway venv with only `mypy==1.17.1` (no django-stubs) and put it first on PATH. The hook still fails loudly (`pyproject.toml:1: error: Error importing plugin "mypy_django_plugin.main"`, exit 1), because `plugins = [...]` in pyproject.toml acts as a hard requirement. So "some other mypy silently picked up" can't pass a plugin-less check. This is worth adding to §9.3.
- `waitress` (§9.4): agree it's dead. `types-redis` is also redundant (redis 7.1.0 ships `py.typed`).
- Hook version: pinned `rev: 'v1.17.1'` matches `mypy==1.17.1` in requirements.txt, and `djangorestframework-stubs==3.16.6` and `django-stubs==5.2.8` are both present, so there's no drift today.
- H-46 is present in HARDENING_BACKLOG.md.

## Minor (non-blocking)
The throwaway marker comment from the §9.1 commit test ("# Throwaway marker comment for task/mypy-django-stubs commit-test (step 1).") is still in `billing/license_service.py` in the final diff. It's permanent noise in a production billing file, and it sits in the same file p1b's held commit rewrites, so it's a pointless merge-conflict surface. The test is already documented in §9.1, so please drop the comment in the fix commit.

## Verdict: REJECTED
The only blocker is CI. The local design, the ratchet and the failure modes are all sound. Once the CI workflow is fixed and proven with a real run or an equivalent simulation, a re-check should be quick: I'll look at the workflow diff plus that proof, not redo the whole pass.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
