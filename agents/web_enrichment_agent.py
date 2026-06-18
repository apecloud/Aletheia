import html
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse


BLOCKED_URL_PATTERNS = [
    re.compile(r"(^|[?&])(api[_-]?key|token|secret|password|credential)=", re.I),
]


def _clean_text(value: str | None, limit: int = 1200) -> str:
    if not value:
        return ""
    text = html.unescape(re.sub(r"\s+", " ", value)).strip()
    return text[:limit].rstrip()


def _is_public_web_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if any(pattern.search(url) for pattern in BLOCKED_URL_PATTERNS):
        return False
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return not (host in {"localhost"} or host.endswith(".local"))
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str = ""
    rank: int = 0
    provider: str = "gpt_researcher"
