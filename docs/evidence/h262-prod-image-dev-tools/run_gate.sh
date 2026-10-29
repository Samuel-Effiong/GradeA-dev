#!/usr/bin/env bash
# H-262 gate runner — saves raw output for every step.
# Usage: bash docs/evidence/h262-prod-image-dev-tools/run_gate.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
LOGDIR="$REPO/docs/evidence/h262-prod-image-dev-tools/logs"
VENV="$HOME/Documents/Virtualenvs/AutoGrader_env_h262"
BASE=a3f5a6a1
TEST_MODULE=ai_processor.tests_h262_prod_image_dev_tools

source "$VENV/bin/activate"
cd "$REPO"

mkdir -p "$LOGDIR"

echo "=== RED RUN ==="
git checkout "$BASE" -- \
  requirements.txt Dockerfile scripts/pip_audit_gate.py \
  .pip-audit-ignore.toml \
  .github/workflows/tests.yml .github/workflows/migration-safety.yml \
  .github/workflows/audit.yml
rm -f requirements-dev.txt .pip-audit-ignore-dev.toml

python manage.py test "$TEST_MODULE" --noinput -v 2 2>&1 | tee "$LOGDIR/red-run.log" || true

git checkout HEAD -- \
  requirements.txt requirements-dev.txt Dockerfile scripts/pip_audit_gate.py \
  .pip-audit-ignore.toml .pip-audit-ignore-dev.toml \
  .github/workflows/tests.yml .github/workflows/migration-safety.yml \
  .github/workflows/audit.yml

echo ""
echo "=== GREEN RUN ==="
python manage.py test "$TEST_MODULE" --noinput -v 2 2>&1 | tee "$LOGDIR/green-run.log"

echo ""
echo "=== NEAR SUITE (ai_processor) ==="
python manage.py test ai_processor --noinput -v 2 2>&1 | tee "$LOGDIR/near-suite.log"

echo ""
echo "=== SEAM CHECK ==="
git diff --name-only "$BASE"..HEAD -- ':(exclude)docs/*' 2>&1 | tee "$LOGDIR/seam-check.log"

echo ""
echo "=== MUTANTS ==="

assert_clean() {
  if [ -n "$(git diff --name-only)" ]; then
    echo "FATAL: tree is dirty after M${1} restore:" >&2
    git diff --name-only >&2
    exit 1
  fi
}

run_mutant() {
  local n="$1" test="$2" fragment="$3"
  shift 3
  local log="$LOGDIR/mutant-m${n}.log"
  echo "--- M${n} ---"
  "$@"
  {
    echo "### git diff of mutated file(s)"
    git diff
    echo ""
    echo "### test output"
    python manage.py test "${TEST_MODULE}.${test}" --noinput -v 2 2>&1
  } > "$log" 2>&1
  local rc=$?
  cat "$log"
  if ! grep -qF "FAILED" "$log"; then
    echo "FATAL: M${n} was NOT killed (expected FAILED)" >&2
    exit 1
  fi
  if ! grep -qF "$fragment" "$log"; then
    echo "FATAL: M${n} fragment mismatch — expected '$fragment' in log" >&2
    exit 1
  fi
  echo "M${n}: KILLED, fragment verified"
}

# M1
run_mutant 1 "RequirementsSplitTest.test_dev_packages_not_in_production_requirements" \
  "dev packages found in production requirements: ['flower']" \
  bash -c 'echo "flower==2.0.1" >> requirements.txt'
git checkout -- requirements.txt
assert_clean 1

# M2
run_mutant 2 "RequirementsSplitTest.test_requirements_dev_includes_production" \
  "!= '-r requirements.txt'" \
  sed -i 's/^-r requirements.txt$/# removed/' requirements-dev.txt
git checkout -- requirements-dev.txt
assert_clean 2

# M3
run_mutant 3 "DockerfileMultiStageTest.test_dockerfile_has_build_and_runtime_stages" \
  "expected at least 2 FROM stages, found 1" \
  sed -i '/^FROM.*AS build$/d' Dockerfile
git checkout -- Dockerfile
assert_clean 3

# M4
run_mutant 4 "DockerfileMultiStageTest.test_build_essential_only_in_build_stage" \
  "build-essential must not appear in the runtime stage" \
  python3 -c "
t = open('Dockerfile').read()
p = t.split('FROM python:3.12-slim-bookworm')
p[2] = p[2].replace('libgl1', 'build-essential libgl1', 1)
open('Dockerfile','w').write('FROM python:3.12-slim-bookworm'.join(p))
"
git checkout -- Dockerfile
assert_clean 4

# M5
run_mutant 5 "DockerfileMultiStageTest.test_no_libmariadb_dev_anywhere" \
  "libmariadb-dev must be removed entirely" \
  python3 -c "
t = open('Dockerfile').read()
open('Dockerfile','w').write(t.replace('libgl1', 'libmariadb-dev libgl1', 1))
"
git checkout -- Dockerfile
assert_clean 5

# M6
run_mutant 6 "DockerfileMultiStageTest.test_dev_headers_only_in_build_stage" \
  "libpq-dev must not appear in the runtime stage" \
  python3 -c "
t = open('Dockerfile').read()
p = t.split('FROM python:3.12-slim-bookworm')
p[2] = p[2].replace('libgl1', 'libpq-dev libgl1', 1)
open('Dockerfile','w').write('FROM python:3.12-slim-bookworm'.join(p))
"
git checkout -- Dockerfile
assert_clean 6

# M7
run_mutant 7 "AuditGateTest.test_audit_gate_audits_dev_requirements_too" \
  "not found in ['requirements.txt', 'requirements.txt']" \
  sed -i 's/^REQUIREMENTS_DEV = "requirements-dev.txt"/REQUIREMENTS_DEV = "requirements.txt"/' scripts/pip_audit_gate.py
git checkout -- scripts/pip_audit_gate.py
assert_clean 7

# M8
run_mutant 8 "AuditGateTest.test_dev_only_ignores_in_separate_file" \
  ".pip-audit-ignore-dev.toml not found" \
  bash -c 'mv .pip-audit-ignore-dev.toml /tmp/_m8_backup.toml'
cp /tmp/_m8_backup.toml .pip-audit-ignore-dev.toml
assert_clean 8

# M9
run_mutant 9 "CIWorkflowTest.test_tests_workflow_installs_dev_requirements" \
  "tests.yml must install requirements-dev.txt" \
  sed -i 's/requirements-dev.txt/requirements.txt/' .github/workflows/tests.yml
git checkout -- .github/workflows/tests.yml
assert_clean 9

# M10
run_mutant 10 "CIWorkflowTest.test_audit_workflow_audits_both_files" \
  "audit.yml must reference requirements-dev.txt" \
  sed -i 's/requirements-dev.txt/requirements.txt/' .github/workflows/audit.yml
git checkout -- .github/workflows/audit.yml
assert_clean 10

# M11
run_mutant 11 "AuditGateTest.test_audit_gate_audits_dev_requirements_too" \
  "not found in ['requirements.txt']" \
  python3 -c "
import re
t = open('scripts/pip_audit_gate.py').read()
t = re.sub(
    r'\n    all_ids = sorted.*?\n    rc = subprocess\.run.*?\n    \)\.returncode\n    return rc',
    '\n    return rc',
    t, flags=re.DOTALL)
open('scripts/pip_audit_gate.py','w').write(t)
"
git checkout -- scripts/pip_audit_gate.py
assert_clean 11

echo ""
echo "=== DONE ==="
