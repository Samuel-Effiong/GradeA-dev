"""Dedicated tests for the fetch_url_content SSRF boundary.

Before this file the guard had ZERO direct tests: `tools.py` sat at 53.2%
coverage with lines 55-158 - `_assert_url_is_publicly_fetchable`,
`_fetch_validated` and `perform_search`, i.e. the entire defence -
uncovered. The percentage was never the point; a security control with no
tests is one nobody can change safely and nobody would notice breaking.

WHAT THE BOUNDARY DEFENDS

`fetch_url_content` is a model-callable tool: the assignment-generation
prompt tells the model to pull any URL it finds in the teacher's free-text
instruction. So an authenticated teacher chooses, indirectly, an address
the APPLICATION SERVER will connect to, and the response flows back into
the model and can end up inside the generated assignment. Unguarded that
is a straightforward SSRF primitive - cloud instance metadata at
169.254.169.254, internal-only hostnames, services on localhost.

THE TWO-STAGE CHECK, AND WHY BOTH STAGES EXIST

  1. Pre-flight: scheme allow-list, blocked hostnames, and resolve the
     name and reject restricted addresses.
  2. Post-connect: re-check the address ACTUALLY connected to.

Stage 1 alone is not enough, and that is not a theoretical quibble: it
resolves a name, and the TCP connection happens afterwards as a separate
act. A hostile resolver can answer with a public address for the check and
a private one for the connection (DNS rebinding) and stage 1 cannot see
the difference. Stage 2 is the only check that sees the truth, which is
why it FAILS CLOSED when the peer cannot be determined - a silently
skipped SSRF check is indistinguishable from no check at all.

`test_peer_introspection_works_against_a_real_connection` exists
specifically so that a urllib3 upgrade which changes the response's
internal layout breaks CI loudly, instead of quietly turning stage 2 into
a permanent "cannot determine" error (or, if someone then "fixes" it by
failing open, into nothing at all).
"""

import os
import socket
import unittest
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from ai_processor import tools
from ai_processor.tools import BlockedURLError, perform_search

RUN_NETWORK = os.environ.get("CI_REQUIRE_NETWORK") == "1"


def fake_addrinfo(*addresses):
    """socket.getaddrinfo's shape, for the addresses we want it to claim."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
        for address in addresses
    ]


class SchemeAndHostAllowListTest(SimpleTestCase):
    """Stage 1, the parts that need no DNS at all."""

    def test_non_http_schemes_are_refused(self):
        for url in (
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/x",
            "javascript:alert(1)",
            "data:text/plain,hello",
        ):
            with self.subTest(url=url):
                with self.assertRaises(BlockedURLError) as caught:
                    tools._assert_url_is_publicly_fetchable(url)
                self.assertIn("http", str(caught.exception).lower())

    def test_a_url_with_no_host_is_refused(self):
        with self.assertRaises(BlockedURLError):
            tools._assert_url_is_publicly_fetchable("http:///nohost")

    def test_the_named_metadata_hostnames_are_refused_without_resolving(self):
        """
        Blocked by name, so the check holds even where those names resolve
        to something that looks public.
        """
        with patch.object(socket, "getaddrinfo") as resolver:
            for host in ("metadata.google.internal", "METADATA.GOOG"):
                with self.subTest(host=host):
                    with self.assertRaises(BlockedURLError):
                        tools._assert_url_is_publicly_fetchable(f"http://{host}/x")
            resolver.assert_not_called()

    def test_an_unresolvable_host_is_refused_not_crashed(self):
        with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("nope")):
            with self.assertRaises(BlockedURLError) as caught:
                tools._assert_url_is_publicly_fetchable("http://nx.example/x")
        self.assertIn("resolve", str(caught.exception).lower())


class RestrictedAddressRangesTest(SimpleTestCase):
    """Stage 1's address classification, range by range."""

    RESTRICTED = {
        "loopback": "127.0.0.1",
        "loopback-alt": "127.99.4.2",
        "rfc1918-10": "10.0.0.7",
        "rfc1918-172": "172.16.30.9",
        "rfc1918-192": "192.168.1.1",
        "cloud-metadata": "169.254.169.254",
        "link-local": "169.254.10.10",
        "multicast": "224.0.0.1",
        "unspecified": "0.0.0.0",  # noqa: S104 - asserted as BLOCKED, not bound
        "carrier-nat": "100.64.0.1",
    }

    def test_every_restricted_range_is_refused(self):
        for label, address in self.RESTRICTED.items():
            with self.subTest(range=label, address=address):
                with patch.object(
                    socket, "getaddrinfo", return_value=fake_addrinfo(address)
                ):
                    with self.assertRaises(BlockedURLError) as caught:
                        tools._assert_url_is_publicly_fetchable("http://evil.test/x")
                self.assertIn("restricted", str(caught.exception).lower())

    def test_ipv6_loopback_and_link_local_are_refused(self):
        for address in ("::1", "fe80::1", "fc00::1"):
            with self.subTest(address=address):
                with patch.object(
                    socket,
                    "getaddrinfo",
                    return_value=[
                        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 0, 0, 0))
                    ],
                ):
                    with self.assertRaises(BlockedURLError):
                        tools._assert_url_is_publicly_fetchable("http://evil.test/x")

    def test_one_bad_address_among_several_is_enough_to_refuse(self):
        """
        A name can resolve to many addresses. Allowing the fetch because
        the FIRST one looked fine would let an attacker hide an internal
        address behind a public one.
        """
        with patch.object(
            socket,
            "getaddrinfo",
            return_value=fake_addrinfo("93.184.216.34", "169.254.169.254"),
        ):
            with self.assertRaises(BlockedURLError):
                tools._assert_url_is_publicly_fetchable("http://evil.test/x")

    def test_a_genuinely_public_address_is_allowed(self):
        """The guard must not block the feature it is guarding."""
        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ):
            tools._assert_url_is_publicly_fetchable("https://example.com/page")


class DnsRebindingIsCaughtAfterConnectingTest(SimpleTestCase):
    """
    Stage 2: the window stage 1 structurally cannot close.

    DNS says "public"; the socket lands somewhere private. Simulated by
    letting the pre-check see a public address while the connected socket
    reports an internal peer - which is exactly what a rebinding attack
    produces.
    """

    def _response_from_peer(self, peer):
        response = MagicMock()
        response.is_redirect = False
        response.is_permanent_redirect = False
        response.raw._connection.sock.getpeername.return_value = (peer, 443)
        return response

    def test_a_connection_that_lands_on_metadata_is_refused(self):
        response = self._response_from_peer("169.254.169.254")

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=response):
            with self.assertRaises(BlockedURLError) as caught:
                tools._fetch_validated("https://rebind.test/x")

        self.assertIn("restricted", str(caught.exception).lower())
        response.close.assert_called_once()

    def test_a_connection_that_lands_on_localhost_is_refused(self):
        response = self._response_from_peer("127.0.0.1")

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=response):
            with self.assertRaises(BlockedURLError):
                tools._fetch_validated("https://rebind.test/x")

    def test_an_undeterminable_peer_fails_CLOSED(self):
        """
        If we cannot tell who we connected to, we do not read the body.
        The alternative - assume it was fine - turns an unnoticed library
        change into a silently disabled security control.
        """
        response = MagicMock()
        response.is_redirect = False
        response.is_permanent_redirect = False
        response.raw._connection = None
        response.raw.connection = None

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=response):
            with self.assertRaises(BlockedURLError) as caught:
                tools._fetch_validated("https://unknown.test/x")

        self.assertIn("could not determine", str(caught.exception).lower())

    def test_a_public_peer_is_allowed_through(self):
        response = self._response_from_peer("93.184.216.34")

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=response):
            self.assertIs(tools._fetch_validated("https://ok.test/x"), response)


class RedirectsAreRevalidatedTest(SimpleTestCase):
    """A redirect is a second URL, and gets the whole check again."""

    def _redirect_to(self, location):
        response = MagicMock()
        response.is_redirect = True
        response.is_permanent_redirect = False
        response.headers = {"Location": location}
        response.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 443)
        return response

    def test_a_redirect_into_an_internal_address_is_refused(self):
        redirect = self._redirect_to("http://169.254.169.254/latest/meta-data/")

        def resolver(host, *args, **kwargs):
            if host == "169.254.169.254":
                return fake_addrinfo("169.254.169.254")
            return fake_addrinfo("93.184.216.34")

        with patch.object(socket, "getaddrinfo", side_effect=resolver), patch.object(
            tools.requests, "get", return_value=redirect
        ):
            with self.assertRaises(BlockedURLError):
                tools._fetch_validated("https://innocent.test/start")

    def test_a_redirect_to_a_non_http_scheme_is_refused(self):
        redirect = self._redirect_to("file:///etc/passwd")

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=redirect):
            with self.assertRaises(BlockedURLError):
                tools._fetch_validated("https://innocent.test/start")

    def test_a_redirect_with_no_location_is_refused(self):
        response = MagicMock()
        response.is_redirect = True
        response.is_permanent_redirect = False
        response.headers = {}
        response.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 443)

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=response):
            with self.assertRaises(BlockedURLError):
                tools._fetch_validated("https://innocent.test/start")

    def test_an_endless_redirect_chain_terminates(self):
        redirect = self._redirect_to("https://innocent.test/again")

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=redirect):
            with self.assertRaises(BlockedURLError) as caught:
                tools._fetch_validated("https://innocent.test/start")

        self.assertIn("too many redirects", str(caught.exception).lower())


class PerformSearchReportsRatherThanRaisesTest(SimpleTestCase):
    """
    The model needs an answer for every URL it asked about, so a blocked
    fetch becomes an error STRING for that URL - it must never take down
    the whole generation, and must never return the page body.
    """

    def test_a_blocked_url_yields_an_error_string_not_an_exception(self):
        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("127.0.0.1")
        ):
            results = perform_search(["http://internal.test/secret"])

        self.assertEqual(list(results), ["http://internal.test/secret"])
        self.assertIn("Error fetching", results["http://internal.test/secret"])
        self.assertIn("restricted", results["http://internal.test/secret"].lower())

    def test_one_blocked_url_does_not_stop_the_others(self):
        def resolver(host, *args, **kwargs):
            return fake_addrinfo(
                "127.0.0.1" if host == "internal.test" else "93.184.216.34"
            )

        good = MagicMock()
        good.is_redirect = False
        good.is_permanent_redirect = False
        good.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 443)
        good.raw.read.return_value = b"<html><body>Hello</body></html>"

        with patch.object(socket, "getaddrinfo", side_effect=resolver), patch.object(
            tools.requests, "get", return_value=good
        ):
            results = perform_search(
                ["http://internal.test/secret", "http://public.test/page"]
            )

        self.assertIn("Error fetching", results["http://internal.test/secret"])
        self.assertIn("Hello", results["http://public.test/page"])

    def test_an_oversized_page_is_truncated_to_an_error_not_returned(self):
        response = MagicMock()
        response.is_redirect = False
        response.is_permanent_redirect = False
        response.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 443)
        response.raw.read.return_value = b"x" * (tools.FETCH_URL_MAX_BYTES + 1)

        with patch.object(
            socket, "getaddrinfo", return_value=fake_addrinfo("93.184.216.34")
        ), patch.object(tools.requests, "get", return_value=response):
            results = perform_search(["http://public.test/huge"])

        self.assertIn("exceeds", results["http://public.test/huge"])


@unittest.skipUnless(
    RUN_NETWORK, "Live network check is opt-in: set CI_REQUIRE_NETWORK=1"
)
class PeerIntrospectionAgainstARealConnectionTest(SimpleTestCase):
    """
    The load-bearing assumption of stage 2, checked against a real socket.

    `_peer_address` reaches into urllib3's response internals. If an
    upgrade moves them, stage 2 starts failing closed on every fetch -
    which is safe but breaks the feature, and invites someone to "fix" it
    by failing open. This test makes that a loud CI failure instead.
    """

    def test_peer_introspection_works_against_a_real_connection(self):
        response = tools._fetch_validated("https://example.com/")
        try:
            peer = tools._peer_address(response)
            self.assertIsNotNone(
                peer,
                "could not read the peer address off a real urllib3 response - "
                "stage 2 of the SSRF guard would now fail closed on every fetch",
            )
            assert peer is not None  # narrows Optional[str] for mypy
            import ipaddress

            self.assertFalse(
                tools._is_restricted_address(ipaddress.ip_address(peer)),
                f"example.com resolved to a restricted address: {peer}",
            )
        finally:
            response.close()
