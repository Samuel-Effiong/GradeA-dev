# H-273 step 4: BETA_CREDITS probe expectation

## Test

`billing.tests.test_free_plan_activation_security.ConcurrentActivationTests`

## Plan

Run 5 times at f5d2d577, 5 times at tip, same venv and settings, --verbosity 2.

## Expectation

Pass 5/5 at both commits.

If f5d2d577 also fails, it is environmental or flaky and not H-273.
A mismatch from this expectation is a STOP.

## Command

```
python manage.py test billing.tests.test_free_plan_activation_security.ConcurrentActivationTests --settings=AutoGrader.settings --verbosity=2 --noinput
```

## Venv

~/Documents/Virtualenvs/AutoGrader_env_h273 (freeze SHA-256: 52da179f)

## Results

### f5d2d577 (baseline) — 5/5 PASS

```
Run 1: Ran 5 tests in 12.041s OK
Run 2: Ran 5 tests in 12.740s OK
Run 3: Ran 5 tests in 11.925s OK
Run 4: Ran 5 tests in 11.678s OK
Run 5: Ran 5 tests in 11.709s OK
```

### tip (3dc6405b, code same as cba462cf) — 5/5 PASS

```
Run 1: Ran 5 tests in 11.825s OK
Run 2: Ran 5 tests in 12.049s OK
Run 3: Ran 5 tests in 11.791s OK
Run 4: Ran 5 tests in 11.818s OK
Run 5: Ran 5 tests in 12.320s OK
```

### Conclusion

Matches expectation (pass 5/5 at both). The previous failure was caused by the
incomplete venv (missing pytest → test_license_service failed to import → _FailedTest
entry → distorted test counts and environment). With the rebuilt venv, the test passes
reliably at both commits. The BETA_CREDITS assertion (10M) is correct; no trial signal
interference occurs with the proper test environment.
