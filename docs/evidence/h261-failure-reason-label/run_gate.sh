#!/usr/bin/env bash
# H-261 gate runner — saves raw output for every step.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
LOGDIR="$REPO/docs/evidence/h261-failure-reason-label/logs"
VENV="$HOME/Documents/Virtualenvs/h266_list_errors_dict_env"
BASE=a3f5a6a1
TEST_MODULE=billing.tests.test_h261_failure_reason_label

source "$VENV/bin/activate"
cd "$REPO"

mkdir -p "$LOGDIR"

assert_clean() {
  local dirty
  dirty="$(git diff --name-only -- ':(exclude)docs/evidence/*')"
  if [ -n "$dirty" ]; then
    echo "FATAL: tree is dirty after M${1} restore:" >&2
    echo "$dirty" >&2
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
    git diff -- ':(exclude)docs/evidence/*'
    echo ""
    echo "### test output"
    python manage.py test "${TEST_MODULE}.${test}" --noinput -v 2 2>&1
  } > "$log" 2>&1 || true
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

echo "=== SEAM CHECK ==="
git diff --name-only "$BASE"..HEAD -- ':(exclude)docs/*' 2>&1 | tee "$LOGDIR/seam-check.log"

echo ""
echo "=== MUTANTS ==="

# M1 (Site 1): revert "Stripe refused: {stripe_error_label(exc)}" → "{exc}"
run_mutant 1 "Site1RefusedTests.test_failure_reason_has_no_stripe_text" \
  "sk_live_FAKE_key_1234" \
  sed -i 's/failure_reason=f"Stripe refused: {stripe_error_label(exc)}"/failure_reason=f"Stripe refused: {exc}"/' billing/license_stripe_mutation.py
git checkout -- billing/license_stripe_mutation.py
assert_clean 1

# M2 (Site 2): revert "Outcome unknown ({stripe_error_label(exc)}); …" → "{exc}"
run_mutant 2 "Site2OutcomeUnknownTests.test_failure_reason_has_no_stripe_text" \
  "sk_live_FAKE_key_1234" \
  sed -i 's/failure_reason=f"Outcome unknown ({stripe_error_label(exc)})/failure_reason=f"Outcome unknown ({exc})/' billing/license_stripe_mutation.py
git checkout -- billing/license_stripe_mutation.py
assert_clean 2

# M3 (Site 3): revert "Checkout … failed: {stripe_error_label(exc)}" → "{exc}"
run_mutant 3 "Site3CheckoutTests.test_failure_reason_has_no_stripe_text" \
  "sk_live_FAKE_key_1234" \
  sed -i 's/f"Checkout session creation failed: {stripe_error_label(exc)}"/f"Checkout session creation failed: {exc}"/' billing/license_service.py
git checkout -- billing/license_service.py
assert_clean 3

# M4 (Site 4): revert "… also failed: {stripe_error_label(revert_exc)}" → "{revert_exc}"
run_mutant 4 "Site4RevertFailureTests.test_failure_reason_has_no_stripe_text" \
  "sk_live_FAKE_key_1234" \
  sed -i 's/{stripe_error_label(revert_exc)}/{revert_exc}/' billing/license_stripe_mutation.py
git checkout -- billing/license_stripe_mutation.py
assert_clean 4

echo ""
echo "=== ALL 4 MUTANTS KILLED, FRAGMENTS VERIFIED ==="
