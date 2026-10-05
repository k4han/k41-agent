"""Safe base_url validation to mitigate SSRF via user-supplied endpoints."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


BLOCKED_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.google.com",
        "metadata.goog",
        "instance-data",
        "instance-data-compute",
        "metadata.azure.com",
        "metadata.aws",
        "169.254.169.254",
        "100.100.100.200",
        "192.0.0.192",
        "169.254.169.123",
        "fd00:ec2::254",
    }
)

_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")
_LINK_LOCAL_V6 = ipaddress.ip_network("fe80::/10")
_UNSPECIFIED_V4 = ipaddress.ip_network("0.0.0.0/8")


def _parse_numeric_part(part: str) -> int | None:
    text = part.strip()
    if not text:
        return None
    try:
        # Base-0 handles 0x hex, 0o octal, and decimal.
        return int(text, 0)
    except ValueError:
        pass
    # Python int(x, 0) rejects legacy leading-zero octal like "0177".
    # Handle it explicitly to catch 0177.0.0.1 style bypasses.
    if len(text) > 1 and text[0] == "0" and all(c in "01234567" for c in text):
        try:
            return int(text, 8)
        except ValueError:
            return None
    return None


def _decode_possible_ipv4(host: str) -> ipaddress.IPv4Address | None:
    cleaned = host.strip().lower().rstrip(".")
    if not cleaned or ":" in cleaned:
        return None
    # Fast path: only digits, hex markers, and dots can be numeric IPs.
    # Hostnames containing other letters or hyphens are DNS names.
    allowed = set("0123456789abcdefxob.")
    if any(c not in allowed for c in cleaned):
        return None
    # Must contain at least one digit.
    if not any(c.isdigit() for c in cleaned):
        return None
    parts = cleaned.split(".")
    if len(parts) < 1 or len(parts) > 4:
        return None
    values: list[int] = []
    for part in parts:
        parsed = _parse_numeric_part(part)
        if parsed is None or parsed < 0 or parsed >= 2**32:
            return None
        values.append(parsed)
    try:
        if len(values) == 1:
            n = values[0]
        elif len(values) == 2:
            if values[0] > 0xFF or values[1] > 0xFFFFFF:
                return None
            n = (values[0] << 24) | values[1]
        elif len(values) == 3:
            if values[0] > 0xFF or values[1] > 0xFF or values[2] > 0xFFFF:
                return None
            n = (values[0] << 24) | (values[1] << 16) | values[2]
        else:
            if any(v > 0xFF for v in values):
                return None
            n = (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
        return ipaddress.IPv4Address(n)
    except (ValueError, OverflowError):
        return None


def _blocked_ip_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    # Unwrap embedded IPv4 (IPv4-mapped, 6to4, Teredo) so
    # ::ffff:169.254.169.254 cannot bypass metadata blocking.
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if ip.version == 6:
        try:
            mapped = getattr(ip, "ipv4_mapped", None)
            if mapped is not None:
                candidates.append(mapped)
            sixtofour = getattr(ip, "sixtofour", None)
            if sixtofour is not None:
                candidates.append(sixtofour)
            teredo = getattr(ip, "teredo", None)
            if teredo is not None:
                # Teredo returns (server, client) IPv4 tuple.
                for part in teredo:
                    if isinstance(part, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
                        candidates.append(part)
        except (ValueError, AttributeError):
            pass
    for candidate in candidates:
        if str(candidate).lower() in BLOCKED_METADATA_HOSTS:
            return "Base URL host is blocked (cloud metadata endpoint)."
        if candidate.is_unspecified:
            return "Base URL host is blocked (unspecified address)."
        if candidate.is_multicast:
            return "Base URL host is blocked (multicast address)."
        try:
            if candidate.version == 4:
                if candidate in _LINK_LOCAL_V4:
                    return "Base URL host is blocked (link-local metadata range)."
                if candidate in _UNSPECIFIED_V4:
                    return "Base URL host is blocked (unspecified address)."
            else:
                if candidate in _LINK_LOCAL_V6:
                    return "Base URL host is blocked (link-local range)."
        except (ValueError, TypeError):
            continue
    return None


def _check_resolved_ips(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        # DNS failure: allow validation to pass here; the later
        # verification fetch will surface a connection error.
        return
    except Exception:
        return
    for info in infos:
        try:
            sockaddr = info[4]
            if not sockaddr:
                continue
            ip = ipaddress.ip_address(str(sockaddr[0]))
        except ValueError:
            continue
        reason = _blocked_ip_reason(ip)
        if reason:
            raise ValueError(reason)


def validate_http_base_url(raw_url: str) -> str:
    """Validate scheme and host for a user-supplied base URL.

    Returns normalized URL (without trailing slash).
    Raises ValueError with a user-facing message when invalid or blocked.

    Security model: private and loopback addresses are allowed because
    local Ollama and Firecrawl instances commonly run on localhost or LAN.
    Dashboard is admin-trusted and defaults to localhost-only binding.
    Do not expose the dashboard on 0.0.0.0 without strong auth, otherwise
    a user-supplied base_url could be used to probe the internal network.
    DNS names are resolved and each resolved IP is checked against
    unspecified, multicast, and cloud link-local/metadata ranges.
    Hex, octal, and integer IPv4 encodings are normalized before checking.
    """

    cleaned = (raw_url or "").strip()
    if not cleaned:
        raise ValueError("Base URL is required.")
    try:
        parsed = urlsplit(cleaned)
    except ValueError as exc:
        raise ValueError("Base URL must be a valid HTTP or HTTPS URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL must be an HTTP or HTTPS URL.")
    # Reject embedded credentials to avoid credential leaks via SSRF logs.
    if parsed.username or parsed.password:
        raise ValueError("Base URL must not contain credentials.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("Base URL must be an HTTP or HTTPS URL.")
    host = host.rstrip(".")
    if host in BLOCKED_METADATA_HOSTS:
        raise ValueError("Base URL host is blocked (cloud metadata endpoint).")
    if host.endswith(".internal") and host.startswith("metadata."):
        raise ValueError("Base URL host is blocked (cloud metadata endpoint).")
    # Block unspecified and multicast addresses. Private and loopback
    # addresses are allowed because local Ollama and Firecrawl instances
    # commonly run on localhost or LAN. Dashboard is admin-trusted and
    # defaults to localhost-only binding.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP: try hex/octal/integer encoded IPv4 forms
        # like 0x7f.0x0.0x0.0x1, 0177.0.0.1, or 2130706433.
        decoded = _decode_possible_ipv4(host)
        if decoded is not None:
            reason = _blocked_ip_reason(decoded)
            if reason:
                raise ValueError(reason)
            return cleaned.rstrip("/")
        # Hostname: resolve DNS and check each IP against blocked ranges.
        # This catches attacker.com -> 169.254.169.254 style bypasses.
        _check_resolved_ips(host)
        return cleaned.rstrip("/")
    reason = _blocked_ip_reason(ip)
    if reason:
        raise ValueError(reason)
    return cleaned.rstrip("/")


def base_url_error(raw_url: str) -> str | None:
    """Return error message when base_url is unsafe, else None."""
    try:
        validate_http_base_url(raw_url)
    except ValueError as exc:
        return str(exc)
    return None
