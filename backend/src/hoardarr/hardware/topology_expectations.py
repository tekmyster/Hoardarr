from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    HardwareSnapshot,
    TopologyDriftEvent,
    TopologyExpectation,
    utc_now,
)
from hoardarr.storage.topology import build_storage_topology

_TRACKED_KINDS = {"controller", "enclosure", "path", "drive"}


def _declaration(node: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: node.get(key)
        for key in (
            "id",
            "kind",
            "label",
            "address",
            "stable_identity",
            "controller_id",
            "enclosure_id",
            "slot",
            "negotiated_speed_gbps",
            "capable_speed_gbps",
        )
    }


def expected_topology_document(snapshot: HardwareSnapshot) -> dict[str, Any]:
    topology = build_storage_topology(snapshot.payload_json, include_live_state=False)
    nodes = [
        _declaration(node)
        for node in topology.get("nodes", [])[:8192]
        if isinstance(node, Mapping) and node.get("kind") in _TRACKED_KINDS
    ]
    return {
        "schema_version": 1,
        "source_snapshot_id": snapshot.id,
        "source_snapshot_sha256": snapshot.sha256,
        "nodes": nodes,
    }


def create_topology_expectation(
    session: Session, *, snapshot: HardwareSnapshot, name: str, created_by: str
) -> TopologyExpectation:
    now = utc_now()
    for current in session.scalars(
        select(TopologyExpectation).where(TopologyExpectation.active.is_(True))
    ):
        current.active = False
        current.updated_at = now
        for drift in session.scalars(
            select(TopologyDriftEvent).where(
                TopologyDriftEvent.expectation_id == current.id,
                TopologyDriftEvent.state == "active",
            )
        ):
            drift.state = "resolved"
            drift.resolved_at = now
            drift.last_seen_at = now
    expectation = TopologyExpectation(
        name=name.strip(),
        source_snapshot_id=snapshot.id,
        expected_json=expected_topology_document(snapshot),
        active=True,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(expectation)
    session.flush()
    return expectation


def _fingerprint(kind: str, entity_id: str) -> str:
    return hashlib.sha256(f"{kind}\0{entity_id}".encode()).hexdigest()


def _drift(
    kind: str,
    severity: str,
    expected: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
    message: str,
) -> dict[str, Any]:
    entity_id = str(expected.get("id") or (observed or {}).get("id") or "unknown")
    return {
        "fingerprint": _fingerprint(kind, entity_id),
        "kind": kind,
        "severity": severity,
        "entity_type": str(expected.get("kind") or (observed or {}).get("kind") or "unknown"),
        "entity_id": entity_id,
        "message": message,
        "expected": dict(expected),
        "observed": dict(observed or {}),
    }


def compare_topology(
    expected_document: Mapping[str, Any], observed_hardware: Mapping[str, Any]
) -> list[dict[str, Any]]:
    topology = build_storage_topology(dict(observed_hardware), include_live_state=False)
    observed_nodes = {
        str(node["id"]): _declaration(node)
        for node in topology.get("nodes", [])[:8192]
        if isinstance(node, Mapping) and node.get("kind") in _TRACKED_KINDS and node.get("id")
    }
    expected_nodes = {
        str(node["id"]): dict(node)
        for node in expected_document.get("nodes", [])[:8192]
        if isinstance(node, Mapping) and node.get("kind") in _TRACKED_KINDS and node.get("id")
    }
    drifts: list[dict[str, Any]] = []
    for entity_id, expected in expected_nodes.items():
        observed = observed_nodes.get(entity_id)
        label = str(expected.get("label") or entity_id)
        kind = str(expected.get("kind"))
        if observed is None:
            drifts.append(
                _drift(
                    f"missing_{kind}",
                    "critical" if kind == "drive" else "warning",
                    expected,
                    None,
                    f"Expected {kind} {label} is not present in the latest scan.",
                )
            )
            continue
        if kind == "drive" and expected.get("enclosure_id") and expected.get("slot"):
            old_location = (expected.get("enclosure_id"), str(expected.get("slot")))
            new_location = (observed.get("enclosure_id"), str(observed.get("slot")))
            if old_location != new_location:
                drifts.append(
                    _drift(
                        "drive_moved",
                        "warning",
                        expected,
                        observed,
                        f"{label} moved from bay {old_location[1]} to {new_location[1]}.",
                    )
                )
        expected_rate = expected.get("negotiated_speed_gbps")
        observed_rate = observed.get("negotiated_speed_gbps")
        if (
            isinstance(expected_rate, (int, float))
            and isinstance(observed_rate, (int, float))
            and observed_rate < expected_rate
        ):
            drifts.append(
                _drift(
                    "link_rate_degraded",
                    "warning",
                    expected,
                    observed,
                    f"{label} negotiated {observed_rate:g} Gb/s; expected {expected_rate:g} Gb/s.",
                )
            )
    for entity_id, observed in observed_nodes.items():
        if entity_id in expected_nodes:
            continue
        kind = str(observed.get("kind"))
        if kind in {"controller", "enclosure", "path"}:
            drifts.append(
                _drift(
                    f"new_{kind}",
                    "info",
                    observed,
                    observed,
                    f"A new {kind} is visible: {observed.get('label') or entity_id}.",
                )
            )
    return sorted(drifts, key=lambda item: (item["severity"], item["kind"], item["entity_id"]))


def reconcile_topology_snapshot(session: Session, snapshot: HardwareSnapshot) -> dict[str, int]:
    expectation = session.scalar(
        select(TopologyExpectation)
        .where(TopologyExpectation.active.is_(True))
        .order_by(TopologyExpectation.updated_at.desc())
        .limit(1)
    )
    if expectation is None:
        return {"active": 0, "opened": 0, "resolved": 0}
    now = snapshot.captured_at or utc_now()
    observed = compare_topology(expectation.expected_json, snapshot.payload_json)
    active = {
        event.fingerprint: event
        for event in session.scalars(
            select(TopologyDriftEvent).where(
                TopologyDriftEvent.expectation_id == expectation.id,
                TopologyDriftEvent.state == "active",
            )
        )
    }
    seen: set[str] = set()
    opened = 0
    for item in observed:
        fingerprint = str(item["fingerprint"])
        seen.add(fingerprint)
        event = active.get(fingerprint)
        if event is None:
            event = TopologyDriftEvent(
                expectation_id=expectation.id,
                snapshot_id=snapshot.id,
                first_seen_at=now,
                fingerprint=fingerprint,
            )
            session.add(event)
            opened += 1
        event.snapshot_id = snapshot.id
        event.kind = str(item["kind"])
        event.severity = str(item["severity"])
        event.entity_type = str(item["entity_type"])
        event.entity_id = str(item["entity_id"])
        event.message = str(item["message"])
        event.expected_json = dict(item["expected"])
        event.observed_json = dict(item["observed"])
        event.state = "active"
        event.last_seen_at = now
        event.resolved_at = None
    resolved = 0
    for fingerprint, event in active.items():
        if fingerprint not in seen:
            event.state = "resolved"
            event.resolved_at = now
            event.last_seen_at = now
            resolved += 1
    session.flush()
    return {"active": len(observed), "opened": opened, "resolved": resolved}


def expectation_document(expectation: TopologyExpectation) -> dict[str, Any]:
    return {
        "id": expectation.id,
        "name": expectation.name,
        "source_snapshot_id": expectation.source_snapshot_id,
        "expected": expectation.expected_json,
        "active": expectation.active,
        "created_at": expectation.created_at,
        "updated_at": expectation.updated_at,
    }


def drift_document(event: TopologyDriftEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "expectation_id": event.expectation_id,
        "snapshot_id": event.snapshot_id,
        "kind": event.kind,
        "severity": event.severity,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "message": event.message,
        "expected": event.expected_json,
        "observed": event.observed_json,
        "state": event.state,
        "first_seen_at": event.first_seen_at,
        "last_seen_at": event.last_seen_at,
        "resolved_at": event.resolved_at,
    }
