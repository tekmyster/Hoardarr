from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from hoardarr.core.config import Settings


class IntegrationTargetError(ValueError):
    pass


@dataclass(frozen=True)
class IntegrationTarget:
    base_url: str
    scheme: str
    hostname: str
    port: int
    base_path: str
    resolved_ips: tuple[str, ...]


_DNS_SLOTS = threading.BoundedSemaphore(value=4)


def _resolve(hostname: str, port: int, timeout_seconds: float) -> tuple[str, ...]:
    """Resolve on a bounded daemon thread so libc DNS cannot pin an API worker."""

    if not _DNS_SLOTS.acquire(timeout=timeout_seconds):
        raise IntegrationTargetError("integration hostname resolver is busy")
    completed = threading.Event()
    outcome: dict[str, Any] = {}

    def resolve() -> None:
        try:
            outcome["answers"] = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            outcome["error"] = exc
        finally:
            completed.set()
            _DNS_SLOTS.release()

    threading.Thread(target=resolve, name="hoardarr-dns", daemon=True).start()
    if not completed.wait(timeout_seconds):
        raise IntegrationTargetError("integration hostname resolution timed out")
    if "error" in outcome:
        raise IntegrationTargetError("integration hostname could not be resolved") from outcome[
            "error"
        ]
    answers = outcome.get("answers", [])
    values: set[str] = set()
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        values.add(str(address))
    if not values:
        raise IntegrationTargetError("integration hostname returned no usable addresses")
    return tuple(sorted(values))


def _is_allowed(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    settings: Settings,
    allow_localhost: bool,
) -> bool:
    if address.is_unspecified or address.is_multicast or address.is_link_local:
        return False
    if address.is_loopback:
        return allow_localhost
    return any(address in network for network in settings.allowed_integration_networks)


def normalize_and_resolve_target(
    value: str,
    settings: Settings,
    *,
    allow_localhost: bool,
) -> IntegrationTarget:
    if any(ord(character) < 32 for character in value):
        raise IntegrationTargetError("integration URL contains control characters")
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"}:
        raise IntegrationTargetError("integration URL must use http or https")
    if parts.username is not None or parts.password is not None:
        raise IntegrationTargetError("credentials are forbidden in the integration URL")
    if parts.query or parts.fragment:
        raise IntegrationTargetError("integration URL cannot contain a query or fragment")
    if not parts.hostname:
        raise IntegrationTargetError("integration URL must include a hostname")
    try:
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except (UnicodeError, ValueError) as exc:
        raise IntegrationTargetError("integration URL has an invalid hostname or port") from exc
    decoded_path = unquote(parts.path)
    if decoded_path != parts.path:
        raise IntegrationTargetError("integration URL path must not contain percent encoding")
    if unquote(decoded_path) != decoded_path:
        raise IntegrationTargetError("integration URL path contains nested encoding")
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded_path):
        raise IntegrationTargetError("integration URL path contains control characters")
    if "?" in decoded_path or "#" in decoded_path:
        raise IntegrationTargetError("integration URL path contains a reserved delimiter")
    if "\\" in decoded_path or any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise IntegrationTargetError("integration URL path is not canonical")
    if "//" in decoded_path:
        raise IntegrationTargetError("integration URL path is not canonical")
    base_path = "/" + decoded_path.strip("/") if decoded_path.strip("/") else ""

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        resolved_ips = _resolve(hostname, port, settings.integration_timeout_seconds)
    else:
        mapped = literal.ipv4_mapped if isinstance(literal, ipaddress.IPv6Address) else None
        resolved_ips = (str(mapped or literal),)

    for raw_address in resolved_ips:
        address = ipaddress.ip_address(raw_address)
        if not _is_allowed(address, settings, allow_localhost):
            raise IntegrationTargetError(
                f"integration address {address} is outside the approved Hoardarr networks"
            )
        if address.is_loopback and port == settings.bind_port:
            raise IntegrationTargetError("integration target cannot be the Hoardarr API itself")

    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parts.scheme == "https" else 80
    netloc = display_host if port == default_port else f"{display_host}:{port}"
    normalized = urlunsplit((parts.scheme, netloc, base_path, "", ""))
    return IntegrationTarget(
        base_url=normalized,
        scheme=parts.scheme,
        hostname=hostname,
        port=port,
        base_path=base_path,
        resolved_ips=resolved_ips,
    )


def revalidate_approved_target(
    value: str,
    approved_ips: list[str],
    settings: Settings,
    *,
    allow_localhost: bool,
) -> IntegrationTarget:
    target = normalize_and_resolve_target(value, settings, allow_localhost=allow_localhost)
    approved = {str(ipaddress.ip_address(item)) for item in approved_ips}
    if not approved or not set(target.resolved_ips) <= approved:
        raise IntegrationTargetError("integration DNS now resolves to an unapproved address")
    return target
