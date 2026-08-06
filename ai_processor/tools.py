import base64
import ipaddress
import logging
import socket
from io import BytesIO
from typing import List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

logger = logging.getLogger(__name__)

IMAGE_COMPRESSION_TARGET_BYTES = int(1.5 * 1024 * 1024)
IMAGE_COMPRESSION_HARD_CAP_BYTES = 4 * 1024 * 1024
IMAGE_COMPRESSION_QUALITY_STEPS = [85, 75, 65, 55, 45]
IMAGE_COMPRESSION_SCALE_STEPS = [1.0, 0.85, 0.7, 0.55]
IMAGE_COMPRESSION_MIN_DIMENSION = 1000

# The fetch_url_content tool lets the model request the app server fetch a
# URL the teacher wrote in free text. Without these limits it's a
# server-side-request-forgery primitive: any authenticated teacher could
# steer the model into fetching http://169.254.169.254/... (cloud instance
# metadata) or an internal-only hostname, and the response would flow back
# into the model (and potentially into the generated assignment).
FETCH_URL_ALLOWED_SCHEMES = {"http", "https"}
FETCH_URL_TIMEOUT_SECONDS = 10
FETCH_URL_MAX_REDIRECTS = 5
FETCH_URL_MAX_BYTES = 2 * 1024 * 1024  # 2MB cap on fetched page content
FETCH_URL_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
}


class ImageCompressionError(Exception):
    """Raised when an image cannot be compressed under the hard size cap."""


class BlockedURLError(Exception):
    """Raised when a URL is not safe to fetch server-side (SSRF guard)."""


def _assert_url_is_publicly_fetchable(url: str) -> None:
    """
    Reject URLs that aren't safe for the app server to make an outbound
    request to: non-http(s) schemes, and hosts that resolve to
    private/loopback/link-local/reserved/multicast addresses (this covers
    RFC1918 ranges, 127.0.0.1, and the 169.254.169.254 cloud metadata
    address, which is link-local). Called before every request AND before
    following every redirect hop, since redirects are how one otherwise-safe
    URL can be turned into a fetch of an internal address.
    """
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in FETCH_URL_ALLOWED_SCHEMES:
        raise BlockedURLError(
            f"Only http/https URLs can be fetched (got scheme '{scheme or 'none'}')."
        )

    hostname = parsed.hostname
    if not hostname:
        raise BlockedURLError("URL is missing a host and cannot be fetched.")

    if hostname.lower() in FETCH_URL_BLOCKED_HOSTNAMES:
        raise BlockedURLError(
            f"'{hostname}' is a restricted host and cannot be fetched."
        )

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise BlockedURLError(f"Could not resolve host '{hostname}': {e}") from e

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise BlockedURLError(
                f"'{hostname}' resolves to a restricted address and cannot be fetched."
            )


def _fetch_validated(url: str) -> requests.Response:
    """
    GET a URL that has passed `_assert_url_is_publicly_fetchable`, following
    redirects manually (capped, and re-validated on every hop) instead of
    letting `requests` follow them automatically - otherwise a safe URL
    could 302 the server into fetching an internal address before we ever
    get a chance to check it.
    """
    current_url = url
    for _ in range(FETCH_URL_MAX_REDIRECTS + 1):
        _assert_url_is_publicly_fetchable(current_url)

        res = requests.get(
            current_url,
            timeout=FETCH_URL_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )

        if res.is_redirect or res.is_permanent_redirect:
            location = res.headers.get("Location")
            res.close()
            if not location:
                raise BlockedURLError("Redirect response is missing a Location header.")
            current_url = urljoin(current_url, location)
            continue

        return res

    raise BlockedURLError(f"Too many redirects while fetching {url}.")


def perform_search(urls: List[str]):
    results = {}

    for url in urls:
        try:
            res = _fetch_validated(url)
            try:
                res.raise_for_status()

                content = res.raw.read(FETCH_URL_MAX_BYTES + 1, decode_content=True)
                if len(content) > FETCH_URL_MAX_BYTES:
                    results[url] = (
                        f"Error fetching {url}: page exceeds the "
                        f"{FETCH_URL_MAX_BYTES} byte fetch limit."
                    )
                    continue

                soup = BeautifulSoup(content, "lxml")

                for script in soup(["script", "style"]):
                    script.decompose()

                text = soup.get_text(separator="\n", strip=True)
                results[url] = text
            finally:
                res.close()
        except BlockedURLError as e:
            logger.warning(
                "Blocked unsafe fetch_url_content request for %s: %s", url, e
            )
            results[url] = f"Error fetching {url}: {e}"
        except requests.RequestException as e:
            results[url] = f"Error fetching {url}: {e}"
        except Exception as e:
            results[url] = f"Error fetching {url}: {e}"
    return results


def encode_image(uploaded_file=None, image_byte=None):
    if uploaded_file is not None:
        byte = uploaded_file.read()
    elif image_byte is not None:
        byte = image_byte
    return base64.b64encode(byte).decode()


def safe_sort_key(x):
    """
    Sort key for question_number values of unknown type. Returns a tuple so
    a collection mixing ints and non-numeric strings (1, "2a", "3") sorts
    deterministically - numeric values first in numeric order, then the
    rest lexically - instead of raising TypeError the way a bare
    int-or-str key does when Python compares int to str.
    """
    s = str(x).strip()
    if s.isdigit():
        return (0, int(s), "")
    return (1, 0, s)


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def compress_image_for_upload(
    image: Image.Image,
    target_bytes: int = IMAGE_COMPRESSION_TARGET_BYTES,
    hard_cap_bytes: int = IMAGE_COMPRESSION_HARD_CAP_BYTES,
) -> bytes:
    """Re-encode an image as JPEG, iteratively reducing quality and then
    dimensions until it fits under target_bytes. Raises ImageCompressionError
    if it cannot get under hard_cap_bytes even at the quality/dimension floor.
    """
    rgb_image = _to_rgb(image)
    original_size = rgb_image.size

    attempts = 0
    best_bytes = None

    for scale in IMAGE_COMPRESSION_SCALE_STEPS:
        if scale == 1.0:
            candidate = rgb_image
        else:
            width, height = original_size
            new_width = max(int(width * scale), IMAGE_COMPRESSION_MIN_DIMENSION)
            new_height = max(int(height * scale), IMAGE_COMPRESSION_MIN_DIMENSION)
            if max(new_width, new_height) >= max(width, height):
                continue
            candidate = rgb_image.resize((new_width, new_height), Image.LANCZOS)

        for quality in IMAGE_COMPRESSION_QUALITY_STEPS:
            attempts += 1
            buffered = BytesIO()
            candidate.save(buffered, format="JPEG", quality=quality, optimize=True)
            data = buffered.getvalue()

            if best_bytes is None or len(data) < len(best_bytes):
                best_bytes = data

            if len(data) <= target_bytes:
                if attempts > 1:
                    logger.warning(
                        "Image compression required %d attempt(s) "
                        "(scale=%.2f, quality=%d) to reach %d bytes",
                        attempts,
                        scale,
                        quality,
                        len(data),
                    )
                return data

    if best_bytes is not None and len(best_bytes) <= hard_cap_bytes:
        logger.warning(
            "Image compression could not reach target of %d bytes; "
            "falling back to smallest achieved size of %d bytes",
            target_bytes,
            len(best_bytes),
        )
        return best_bytes

    raise ImageCompressionError(
        "Unable to compress image under the maximum allowed size "
        f"({hard_cap_bytes} bytes) even at minimum quality/dimensions."
    )
