"""H-246: the environment name Sentry files events under.

Beta and production both run with ENVIRONMENT=prod, because ENVIRONMENT
also chooses how the app behaves (hosts, CORS, cookies) and beta must
behave like production. Sentry was given ENVIRONMENT as its environment,
so every beta event arrived as "prod": the Epic A dashboard mixed the two,
and an alert would page for a spike caused by testing on beta.

SENTRY_ENVIRONMENT names the deployment for Sentry alone ("beta" on the
beta server). When it is not set, Sentry keeps ENVIRONMENT, as before.
"""


def sentry_environment(app_environment, override=None):
    """The name Sentry files events under: `override` when it holds a name,
    otherwise the app's own ENVIRONMENT."""
    name = override.strip() if isinstance(override, str) else ""
    return name or app_environment
