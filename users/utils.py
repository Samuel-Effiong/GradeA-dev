import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings

# --- Personal vs business email classification ------------------------------
#
# The product deliberately keeps a person's individual account and their
# school account separate, and the email domain is the thing that tells them
# apart:
#
#   * individual (TEACHER) accounts must use a PERSONAL mailbox
#   * school admin accounts, and teachers enrolled under a school's license,
#     must use a BUSINESS mailbox
#
# So this is a two-way classification, not a blocklist. It used to be the
# latter -- "business" meant "domain not in a list of 22 consumer providers"
# -- which made the business-only rule one character wide: gmail.com was
# blocked but googlemail.com, yahoo.co.uk, proton.me, gmx.net and every
# throwaway-mail provider all counted as "business" and could mint school
# admin accounts. Both helpers below now fail CLOSED: an address that is
# malformed, or that comes from a disposable provider, is neither personal
# nor business, so it is refused on both tracks rather than silently
# passing one of them.
PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        # Google
        "gmail.com",
        "googlemail.com",
        # Microsoft
        "hotmail.com",
        "hotmail.co.uk",
        "hotmail.fr",
        "hotmail.de",
        "hotmail.it",
        "hotmail.es",
        "hotmail.ca",
        "hotmail.com.br",
        "outlook.com",
        "outlook.co.uk",
        "outlook.fr",
        "outlook.de",
        "outlook.es",
        "outlook.it",
        "outlook.com.au",
        "live.com",
        "live.co.uk",
        "live.ca",
        "live.com.au",
        "live.fr",
        "live.de",
        "live.nl",
        "live.it",
        "msn.com",
        "passport.com",
        "windowslive.com",
        # Yahoo
        "yahoo.com",
        "yahoo.co.uk",
        "yahoo.co.in",
        "yahoo.co.jp",
        "yahoo.ca",
        "yahoo.com.au",
        "yahoo.com.br",
        "yahoo.com.mx",
        "yahoo.com.ph",
        "yahoo.com.sg",
        "yahoo.de",
        "yahoo.fr",
        "yahoo.es",
        "yahoo.it",
        "yahoo.gr",
        "yahoo.ie",
        "yahoo.in",
        "ymail.com",
        "rocketmail.com",
        # Apple
        "icloud.com",
        "me.com",
        "mac.com",
        # AOL
        "aol.com",
        "aol.co.uk",
        "aim.com",
        # Privacy-focused providers
        "protonmail.com",
        "protonmail.ch",
        "proton.me",
        "pm.me",
        "tutanota.com",
        "tutanota.de",
        "tutamail.com",
        "tuta.io",
        "hushmail.com",
        "hush.com",
        "mailfence.com",
        "posteo.de",
        "riseup.net",
        "startmail.com",
        # Other global consumer providers
        "mail.com",
        "email.com",
        "usa.com",
        "consultant.com",
        "gmx.com",
        "gmx.net",
        "gmx.de",
        "gmx.at",
        "gmx.ch",
        "gmx.co.uk",
        "web.de",
        "fastmail.com",
        "fastmail.fm",
        "zoho.com",
        "zohomail.com",
        "lycos.com",
        "inbox.com",
        "inbox.lv",
        "hotmail.be",
        "rediffmail.com",
        "sify.com",
        "indiatimes.com",
        # Russia / CIS
        "mail.ru",
        "bk.ru",
        "list.ru",
        "inbox.ru",
        "internet.ru",
        "yandex.com",
        "yandex.ru",
        "ya.ru",
        "rambler.ru",
        # China
        "qq.com",
        "foxmail.com",
        "163.com",
        "126.com",
        "yeah.net",
        "sina.com",
        "sina.cn",
        "sohu.com",
        "aliyun.com",
        # Korea / Japan
        "naver.com",
        "daum.net",
        "hanmail.net",
        "nate.com",
        "docomo.ne.jp",
        "ezweb.ne.jp",
        "softbank.ne.jp",
        # US ISPs
        "comcast.net",
        "verizon.net",
        "att.net",
        "sbcglobal.net",
        "bellsouth.net",
        "cox.net",
        "charter.net",
        "earthlink.net",
        "juno.com",
        "netzero.net",
        "optonline.net",
        "roadrunner.com",
        "rr.com",
        "windstream.net",
        "frontier.com",
        "centurylink.net",
        # Canada
        "shaw.ca",
        "sympatico.ca",
        "rogers.com",
        "telus.net",
        "videotron.ca",
        # UK / Ireland
        "btinternet.com",
        "sky.com",
        "virginmedia.com",
        "talktalk.net",
        "ntlworld.com",
        "blueyonder.co.uk",
        "eircom.net",
        # France
        "orange.fr",
        "wanadoo.fr",
        "free.fr",
        "laposte.net",
        "sfr.fr",
        "bbox.fr",
        "neuf.fr",
        # Germany / Austria / Switzerland
        "t-online.de",
        "freenet.de",
        "arcor.de",
        "aon.at",
        "bluewin.ch",
        # Italy / Spain / Portugal
        "libero.it",
        "virgilio.it",
        "alice.it",
        "tiscali.it",
        "tin.it",
        "terra.es",
        "telefonica.net",
        "sapo.pt",
        # Latin America
        "uol.com.br",
        "bol.com.br",
        "ig.com.br",
        "globo.com",
        "terra.com.br",
        "prodigy.net.mx",
        # Africa / other
        "vodamail.co.za",
        "webmail.co.za",
        "mweb.co.za",
        "telkomsa.net",
        "yahoo.com.ng",
    }
)

# Throwaway / disposable mailbox providers. These are neither personal nor
# business: an address here is not a durable identity for either track, so
# both helpers refuse it.
DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {
        "mailinator.com",
        "10minutemail.com",
        "10minutemail.net",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "sharklasers.com",
        "grr.la",
        "temp-mail.org",
        "tempmail.com",
        "tempmailo.com",
        "tempr.email",
        "throwawaymail.com",
        "trashmail.com",
        "trashmail.de",
        "getnada.com",
        "nada.email",
        "maildrop.cc",
        "dispostable.com",
        "fakeinbox.com",
        "mailnesia.com",
        "spamgourmet.com",
        "mintemail.com",
        "moakt.com",
        "emailondeck.com",
        "discard.email",
        "mytemp.email",
        "burnermail.io",
        "yopmail.com",
        "yopmail.net",
        "yopmail.fr",
        "mailcatch.com",
        "spam4.me",
        "einrot.com",
        "getairmail.com",
        "tmpmail.org",
    }
)

# Nothing is exempt by default. The exemption list is a QA lever (see
# EXEMPT_EMAIL_DOMAINS in settings) and it bypasses BOTH rules, so a
# permanently-on entry for a public throwaway provider is a standing hole in
# the school-admin gate. Configure it per-environment instead.
DEFAULT_EXEMPT_DOMAINS: tuple[str, ...] = ()


def get_cipher():
    """Derives a valid Fernet key from the Django SECRET_KEY."""
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key)
    return Fernet(fernet_key)


def encrypt_token(token: str) -> str:
    """Encrypts a string token using Fernet."""
    if not token:
        return token
    return get_cipher().encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypts a Fernet encrypted string token."""
    if not encrypted_token:
        return encrypted_token
    return get_cipher().decrypt(encrypted_token.encode()).decode()


def email_domain(email: str) -> str:
    """Return the normalised domain of `email`, or "" if it isn't a usable
    address.

    Callers used to do `email.split("@")[-1].lower()` inline, which returned
    the whole string for an address with no "@" (so "gmail.com" typed into
    the wrong box classified as a *business* domain), blew up with
    AttributeError on None, and left a trailing space on an unstripped value
    -- enough to make "gmail.com " miss the consumer list entirely. Anything
    this function can't confidently reduce to a domain comes back as "", and
    every rule below treats "" as failing.
    """

    if not isinstance(email, str):
        return ""

    candidate = email.strip()
    if candidate.count("@") != 1:
        return ""

    local, _, domain = candidate.partition("@")
    if not local.strip():
        return ""

    # A trailing dot is a legal fully-qualified form ("gmail.com.") that must
    # not be allowed to dodge a set lookup.
    domain = domain.strip().rstrip(".").lower()

    if not domain or "." not in domain or any(c.isspace() for c in domain):
        return ""
    if ".." in domain or domain.startswith((".", "-")) or domain.endswith("-"):
        return ""

    # Unicode domains are compared in their punycode form so that a school on
    # an IDN domain classifies consistently wherever it's typed.
    try:
        domain = domain.encode("idna").decode("ascii")
    # UnicodeDecodeError is a subclass of UnicodeError, so the one clause
    # already covers both.
    except UnicodeError:
        return ""

    return domain


def _matches_domain_set(domain: str, domains) -> bool:
    """True if `domain` is in `domains`, or is a subdomain of one of them.

    The suffix walk is what stops mail.gmail.com or students.yahoo.co.uk from
    reading as a business domain purely because the exact string isn't listed.
    """

    if not domain:
        return False

    labels = domain.split(".")
    for i in range(len(labels) - 1):
        if ".".join(labels[i:]) in domains:
            return True

    return False


def _configured(setting_name: str, default=()) -> frozenset:
    """Read a domain list from settings, normalised for comparison."""

    values = getattr(settings, setting_name, None)
    if not values:
        values = default

    return frozenset(str(v).strip().lower().rstrip(".") for v in values if v)


def is_disposable_email(email: str) -> bool:
    """True if the address comes from a throwaway-mailbox provider."""

    domain = email_domain(email)
    disposable = DISPOSABLE_EMAIL_DOMAINS | _configured("DISPOSABLE_EMAIL_DOMAINS")

    return _matches_domain_set(domain, disposable)


def is_personal_email(email: str) -> bool:
    """True if the address belongs to a known consumer mailbox provider.

    Individual (TEACHER) accounts require this. Note that it is a positive
    test against a known list -- an unrecognised domain is *not* personal --
    so an unknown domain is refused on the individual track rather than
    waved through.
    """

    domain = email_domain(email)
    if not domain or is_disposable_email(email):
        return False

    personal = PERSONAL_EMAIL_DOMAINS | _configured("DISALLOWED_EMAIL_DOMAINS")

    return _matches_domain_set(domain, personal)


def is_business_email(email: str) -> bool:
    """True if the address belongs to an organisation rather than a person.

    School admin accounts and license-enrolled teachers require this. A
    malformed address, a consumer provider, or a disposable provider all
    return False.

    If ALLOWED_BUSINESS_EMAIL_DOMAINS is configured, it becomes an explicit
    allowlist and nothing outside it is accepted -- useful for a locked-down
    deployment that only ever serves known schools.
    """

    domain = email_domain(email)
    if not domain:
        return False

    allowlist = _configured("ALLOWED_BUSINESS_EMAIL_DOMAINS")
    if allowlist:
        return _matches_domain_set(domain, allowlist)

    if is_disposable_email(email) or is_personal_email(email):
        return False

    return True


def is_exempt_email_domain(email: str) -> bool:
    """Return True if the email domain is exempt from the personal/business
    email restriction (e.g. QA addresses), and should be accepted regardless
    of account type.

    This bypasses BOTH rules, so it stays empty unless an environment
    explicitly sets EXEMPT_EMAIL_DOMAINS.
    """

    domain = email_domain(email)
    exempt = _configured("EXEMPT_EMAIL_DOMAINS", DEFAULT_EXEMPT_DOMAINS)

    return _matches_domain_set(domain, exempt)
