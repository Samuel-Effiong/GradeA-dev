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
