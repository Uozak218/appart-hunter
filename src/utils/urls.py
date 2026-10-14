"""URL safety helpers for fetching photos from untrusted listing sources.

Scrapers feed us URLs that came from the public internet. We must not blindly
GET them: a malicious annotator could try to coerce us into fetching
169.254.169.254 (cloud metadata), 10.0.0.1 (private network), or localhost.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Known-good image CDNs for the sources we scrape. Additions welcome as new
# sources are added. Subdomain prefix match ("*.leboncoin.fr") is implicit.
ALLOWED_PHOTO_DOMAINS: tuple[str, ...] = (
    # Big aggregators
    "leboncoin.fr",
    "seloger.com",
    "pap.fr",
    "bienici.com",
    "logic-immo.com",
    "superimmo.com",
    # Agency networks
    "foncia.com",
    "orpi.com",
    "century21.fr",
    "laforet.com",
    "nestenn.com",
    # Niche / boutique
    "espaces-atypiques.com",
    "junot.fr",
    "barnes-paris.com",
    "barnes-international.com",
    "engelvoelkers.com",
    "paris-seine-immobilier.com",
    "district-immo.com",
    "breteuilhomes.com",
    "cabinet-roche.fr",
    "locservice.fr",
    "paris-gestion-immobilier.com",
    # Per-agency / theme CDNs encountered in scraped HTML
    "apimo.pro",
    "laforet-intranet.com",
    "uploadcare.engelvoelkers.com",
    "cdn.bre.im",
    "staticlbi.com",
    "cloudimg.io",
    "immo-facile.com",
    # Netty.immo (GLF, GTF, small agencies)
    "netty.immo",
    # GTF Locations
    "gtf.fr",
    # Generic CDNs / hosting
    "cloudinary.com",
    "akamaized.net",
    "akamaihd.net",
    "cloudfront.net",
    "wp.com",
    "wpenginepowered.com",
)


def _host_matches(host: str, suffix: str) -> bool:
    host = host.lower().rstrip(".")
    suffix = suffix.lower().rstrip(".")
    return host == suffix or host.endswith("." + suffix)


def _is_private_host(host: str) -> bool:
    # Literal IP → check directly
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    # DNS lookup: any resolved address in a private range → block.
    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # unresolvable: reject
    for info in addrs:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def is_safe_photo_url(url: str, *, resolve_dns: bool = True) -> bool:
    """True iff the URL is https, hits a whitelisted domain, and isn't a private host."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    if not any(_host_matches(host, suffix) for suffix in ALLOWED_PHOTO_DOMAINS):
        return False
    return not (resolve_dns and _is_private_host(host))
