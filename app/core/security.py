"""Outbound network allow-listing and SSRF protection.

There is no generic URL-fetching tool anywhere in this application. Every
tool's HTTP calls funnel through `safe_get`, which always validates the target
URL before any request is made.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from app.core.exceptions import PlaceMatchError

# Domains allowed by exact hostname match.
ALLOWED_EXACT_DOMAINS: set[str] = {
    "nominatim.openstreetmap.org",
    "overpass-api.de",
    "overpass.kumi.systems",
    "api.open-meteo.com",
    "archive-api.open-meteo.com",
    "geocoding-api.open-meteo.com",
    "api.worldbank.org",
    "www.gov.uk",
    "getwherenext.com",
    "api.frankfurter.dev",
}

# Domains allowed by exact match OR any subdomain (e.g. en.wikivoyage.org).
ALLOWED_SUFFIX_DOMAINS: set[str] = {
    "wikivoyage.org",
    "wikipedia.org",
}


class OutboundRequestBlocked(PlaceMatchError):
    """Raised when a URL fails the outbound allow-list / SSRF checks."""


def _hostname_allowed(hostname: str, extra_allowed_domains: set[str] | None) -> bool:
    hostname = hostname.lower()
    all_exact = ALLOWED_EXACT_DOMAINS | (extra_allowed_domains or set())
    if hostname in all_exact:
        return True
    for suffix in ALLOWED_SUFFIX_DOMAINS:
        if hostname == suffix or hostname.endswith("." + suffix):
            return True
    return False


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _resolves_to_private_address(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Cannot resolve -- treat as unsafe rather than silently proceeding.
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def validate_outbound_url(url: str, *, extra_allowed_domains: set[str] | None = None) -> str:
    """Validate a URL against the outbound allow-list and SSRF protections.

    Raises OutboundRequestBlocked if the URL is not safe to fetch. Returns the
    url unchanged on success.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise OutboundRequestBlocked(f"Blocked outbound request: only https is allowed ({url!r}).")
    hostname = parsed.hostname
    if not hostname:
        raise OutboundRequestBlocked(f"Blocked outbound request: no hostname ({url!r}).")
    if _is_ip_literal(hostname):
        raise OutboundRequestBlocked("Blocked outbound request: raw IP-literal hosts are not allowed.")
    if not _hostname_allowed(hostname, extra_allowed_domains):
        raise OutboundRequestBlocked(f"Blocked outbound request: host '{hostname}' is not allow-listed.")
    if _resolves_to_private_address(hostname):
        raise OutboundRequestBlocked(
            f"Blocked outbound request: host '{hostname}' resolves to a private/reserved address."
        )
    return url


DEFAULT_USER_AGENT = "PlaceMatch/0.1 (academic course project; contact: edanperetz2@gmail.com)"


async def safe_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 10.0,
    extra_allowed_domains: set[str] | None = None,
) -> httpx.Response:
    """SSRF-safe GET request. Validates the URL, disables redirects, sets a UA."""
    validate_outbound_url(url, extra_allowed_domains=extra_allowed_domains)
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url, params=params, headers=request_headers)
        return response
