import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY
# H-256: set here, not left to the SDK's default (2 in stripe 14.4.1, 0 in
# older lines). The SDK's own timeout (80 s per request) is kept.
stripe.max_network_retries = 2
