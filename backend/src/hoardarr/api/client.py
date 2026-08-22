from __future__ import annotations

import ipaddress

from fastapi import Request


def client_identity(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    trusted = {
        ipaddress.ip_address(value) for value in request.app.state.settings.trusted_proxy_addresses
    }
    if peer_address not in trusted:
        return str(peer_address)
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded or len(forwarded) > 2048:
        return str(peer_address)
    # A supported reverse proxy must append or replace X-Forwarded-For. Walk
    # right-to-left, discard configured proxy hops, and take the first external
    # address. This prevents a caller-supplied leftmost value from winning.
    for raw_value in reversed(forwarded.split(",")[-20:]):
        try:
            address = ipaddress.ip_address(raw_value.strip())
        except ValueError:
            continue
        if address not in trusted:
            return str(address)
    return str(peer_address)
