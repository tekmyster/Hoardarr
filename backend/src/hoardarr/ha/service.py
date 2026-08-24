from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import HAConfiguration, HAEvent, utc_now

_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FQDN = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_ROLES = frozenset({"active", "passive"})
_SYNC_STATES = frozenset({"not_configured", "in_sync", "synchronizing", "stale", "unavailable"})
_READINESS = frozenset({"ready", "not_ready", "unknown"})
_STALE_AFTER = timedelta(seconds=45)


class HAError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: object, name: str, maximum: int) -> str:
    result = str(value).strip()
    if not result or len(result) > maximum or any(ord(char) < 32 for char in result):
        raise HAError("ha_configuration_invalid", f"{name} is invalid.")
    return result


def _node_id(value: object, name: str) -> str:
    result = _text(value, name, 128)
    if _NODE_ID.fullmatch(result) is None:
        raise HAError("ha_configuration_invalid", f"{name} is invalid.")
    return result


def _fqdn(value: object, name: str) -> str:
    result = _text(value, name, 253).lower()
    if _FQDN.fullmatch(result) is None:
        raise HAError("ha_configuration_invalid", f"{name} is invalid.")
    return result


def _ip(value: object, name: str, *, optional: bool = False) -> str | None:
    if optional and value in (None, ""):
        return None
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError as exc:
        raise HAError("ha_configuration_invalid", f"{name} is invalid.") from exc


def configuration(session: Session) -> HAConfiguration | None:
    return session.scalar(select(HAConfiguration).order_by(HAConfiguration.created_at).limit(1))


def configure(session: Session, value: Mapping[str, object]) -> HAConfiguration:
    local_id = _node_id(value.get("local_node_id"), "Local node ID")
    peer_id = _node_id(value.get("peer_node_id"), "Peer node ID")
    if local_id == peer_id:
        raise HAError("ha_peer_identity_conflict", "Local and peer node identities must differ.")
    local_role = str(value.get("local_role", "")).strip().lower()
    peer_role = str(value.get("peer_role", "")).strip().lower()
    if {local_role, peer_role} != _ROLES:
        raise HAError(
            "ha_role_conflict", "Exactly one configured node must be active and one passive."
        )
    local_ip = _ip(value.get("local_ip"), "Local IP")
    peer_ip = _ip(value.get("peer_ip"), "Peer IP")
    service_ip = _ip(value.get("service_ip"), "Service IP", optional=True)
    if len({local_ip, peer_ip, service_ip} - {None}) != len(
        [item for item in (local_ip, peer_ip, service_ip) if item is not None]
    ):
        raise HAError(
            "ha_address_conflict", "Local, peer, and service addresses must be different."
        )

    item = configuration(session)
    created = item is None
    if item is None:
        item = HAConfiguration(local_node_id=local_id, peer_node_id=peer_id)
        session.add(item)
    elif item.local_node_id != local_id or item.peer_node_id != peer_id:
        item.peer_reachable = False
        item.peer_last_seen_at = None
        item.peer_report_json = {}
    item.mode = "controlled_single_writer"
    item.local_node_id = local_id
    item.local_name = _text(value.get("local_name"), "Local node name", 128)
    item.local_fqdn = _fqdn(value.get("local_fqdn"), "Local FQDN")
    item.local_ip = str(local_ip)
    item.local_role = local_role
    item.peer_node_id = peer_id
    item.peer_name = _text(value.get("peer_name"), "Peer node name", 128)
    item.peer_fqdn = _fqdn(value.get("peer_fqdn"), "Peer FQDN")
    item.peer_ip = str(peer_ip)
    item.peer_role = peer_role
    item.service_ip = service_ip
    if item.current_owner_node_id not in {local_id, peer_id}:
        item.current_owner_node_id = local_id if local_role == "active" else peer_id
    session.flush()
    session.add(
        HAEvent(
            configuration_id=item.id,
            event_type="ha_configured" if created else "ha_configuration_updated",
            resulting_owner_node_id=item.current_owner_node_id,
            detail_json={"mode": item.mode, "service_ip_configured": service_ip is not None},
        )
    )
    session.flush()
    return item


def record_heartbeat(session: Session, value: Mapping[str, object]) -> HAConfiguration:
    item = configuration(session)
    if item is None:
        raise HAError(
            "ha_not_configured", "Configure both nodes before accepting a peer heartbeat."
        )
    node_id = _node_id(value.get("node_id"), "Peer node ID")
    if node_id != item.peer_node_id:
        raise HAError(
            "ha_peer_identity_mismatch", "The heartbeat does not match the configured peer."
        )
    if (
        _fqdn(value.get("fqdn"), "Peer FQDN") != item.peer_fqdn
        or _ip(value.get("ip"), "Peer IP") != item.peer_ip
    ):
        raise HAError(
            "ha_peer_identity_mismatch", "The peer address does not match the configured identity."
        )
    role = str(value.get("role", "")).strip().lower()
    sync_state = str(value.get("synchronization_state", "")).strip().lower()
    readiness = str(value.get("failover_readiness", "")).strip().lower()
    if role not in _ROLES or sync_state not in _SYNC_STATES or readiness not in _READINESS:
        raise HAError("ha_heartbeat_invalid", "The peer heartbeat contains an invalid state.")
    owner = value.get("current_owner_node_id")
    if owner not in {item.local_node_id, item.peer_node_id, None}:
        raise HAError("ha_owner_identity_invalid", "The reported owner is not a configured node.")
    was_reachable = item.peer_reachable and not _is_stale(item.peer_last_seen_at)
    previous_owner = item.current_owner_node_id
    item.peer_reachable = True
    item.peer_last_seen_at = utc_now()
    item.peer_report_json = {
        "role": role,
        "synchronization_state": sync_state,
        "failover_readiness": readiness,
        "storage_ownership": value.get("storage_ownership", "not_reported"),
    }
    item.current_owner_node_id = str(owner) if owner is not None else item.current_owner_node_id
    if not was_reachable:
        session.add(
            HAEvent(
                configuration_id=item.id,
                event_type="peer_reachable",
                detail_json={"peer_node_id": node_id},
            )
        )
    if previous_owner != item.current_owner_node_id:
        session.add(
            HAEvent(
                configuration_id=item.id,
                event_type="ownership_observed_changed",
                previous_owner_node_id=previous_owner,
                resulting_owner_node_id=item.current_owner_node_id,
                cause="peer heartbeat observation",
            )
        )
    session.flush()
    return item


def status_document(item: HAConfiguration | None, *, now: datetime | None = None) -> dict[str, Any]:
    if item is None:
        return {
            "configured": False,
            "maturity_level": "HA-2",
            "mode": None,
            "peer": None,
            "events": [],
        }
    now = now or utc_now()
    stale = _is_stale(item.peer_last_seen_at, now=now)
    reachable = item.peer_reachable and not stale
    report = dict(item.peer_report_json)
    readiness = str(report.get("failover_readiness", "unknown")) if reachable else "unknown"
    sync_state = (
        str(report.get("synchronization_state", "unavailable")) if reachable else "unavailable"
    )
    return {
        "configured": True,
        "maturity_level": "HA-3",
        "mode": item.mode,
        "local": {
            "node_id": item.local_node_id,
            "name": item.local_name,
            "fqdn": item.local_fqdn,
            "ip": item.local_ip,
            "role": item.local_role,
        },
        "peer": {
            "node_id": item.peer_node_id,
            "name": item.peer_name,
            "fqdn": item.peer_fqdn,
            "ip": item.peer_ip,
            "role": item.peer_role,
            "reachable": reachable,
            "state": "healthy"
            if reachable
            else "stale"
            if item.peer_last_seen_at
            else "unavailable",
            "last_seen_at": item.peer_last_seen_at.isoformat() if item.peer_last_seen_at else None,
        },
        "service_ip": item.service_ip,
        "current_owner_node_id": item.current_owner_node_id,
        "synchronization_state": sync_state,
        "failover_readiness": readiness,
        "storage_ownership": report.get("storage_ownership", "not_reported"),
        "automatic_failover": False,
        "fencing_configured": False,
        "updated_at": item.updated_at.isoformat(),
    }


def event_documents(
    session: Session, item: HAConfiguration | None, *, limit: int = 100
) -> list[dict[str, Any]]:
    if item is None:
        return []
    events = session.scalars(
        select(HAEvent)
        .where(HAEvent.configuration_id == item.id)
        .order_by(HAEvent.occurred_at.desc())
        .limit(min(max(limit, 1), 500))
    )
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "cause": event.cause,
            "previous_owner_node_id": event.previous_owner_node_id,
            "resulting_owner_node_id": event.resulting_owner_node_id,
            "detail": dict(event.detail_json),
            "occurred_at": event.occurred_at.isoformat(),
        }
        for event in events
    ]


def _is_stale(value: datetime | None, *, now: datetime | None = None) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - value > _STALE_AFTER
