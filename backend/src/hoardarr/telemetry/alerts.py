from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    MetricAlert,
    MetricAlertRule,
    MetricEntity,
    MetricSample,
    StorageEntity,
    StoragePath,
    StorageRedundancyEvent,
    TelemetryState,
)
from hoardarr.telemetry.store import aware, entity_document

BASIC_RULES = {
    "capacity.utilization": {"warning": 90.0, "critical": 95.0, "clear_below": 88.0},
    "drive.temperature": {"warning": 55.0, "critical": 65.0, "clear_below": 52.0},
    "drive.nvme.critical_warning": {"warning": 1.0, "critical": 1.0, "clear_below": 1.0},
}
FAULT_STATES = {"faulted", "failed", "critical", "degraded", "missing", "read_only"}
PATH_FLAP_WINDOW = timedelta(minutes=10)
PATH_FLAP_EVENT_THRESHOLD = 4


def _redundancy_health_alert_enabled(
    session: Session, entity: MetricEntity, observed_state: str | None
) -> bool:
    if entity.entity_type not in {"logical_storage", "storage_path"}:
        return True
    storage_id = entity.topology_json.get("storage_entity_id")
    if not isinstance(storage_id, str):
        return True
    storage = session.get(StorageEntity, storage_id)
    if storage is None:
        return True
    settings = storage.config_json.get("redundancy_settings")
    if not isinstance(settings, dict):
        return True
    topology_state = entity.labels_json.get("topology_state")
    if topology_state == "no_path":
        return settings.get("alert_on_total_loss") is not False
    if topology_state == "failed_over":
        return settings.get("alert_on_failover") is not False
    if entity.entity_type == "logical_storage" and observed_state in {"faulted", "failed"}:
        return settings.get("alert_on_total_loss") is not False
    return settings.get("alert_on_reduced") is not False


def _rule_id(metric_id: str, entity_id: str) -> str:
    digest = hashlib.sha256(f"{metric_id}\0{entity_id}".encode()).hexdigest()
    return f"basic:{digest}"


def evaluate_basic_alerts(session: Session, samples: list[MetricSample]) -> dict[str, int]:
    now = max((aware(sample.observed_at) for sample in samples), default=datetime.now(UTC))
    opened = 0
    resolved = 0
    for sample in samples:
        entity = session.get(MetricEntity, sample.entity_id)
        if entity is None:
            continue
        rule_id = _rule_id(sample.metric_id, entity.id)
        active = session.scalar(
            select(MetricAlert).where(
                MetricAlert.rule_id == rule_id,
                MetricAlert.state == "active",
            )
        )
        triggered = False
        severity = "warning"
        threshold: dict[str, Any] = {}
        if (
            sample.metric_id == "health.overall"
            and sample.value_text in FAULT_STATES
            and _redundancy_health_alert_enabled(session, entity, sample.value_text)
        ):
            triggered = True
            severity = (
                "critical" if sample.value_text in {"faulted", "failed", "critical"} else "warning"
            )
            threshold = {"states": sorted(FAULT_STATES)}
        elif sample.metric_id in BASIC_RULES and sample.value is not None:
            rule = BASIC_RULES[sample.metric_id]
            triggered = sample.value >= rule["warning"]
            severity = "critical" if sample.value >= rule["critical"] else "warning"
            threshold = dict(rule)
        if triggered:
            if active is None:
                session.add(
                    MetricAlert(
                        rule_id=rule_id,
                        entity_id=entity.id,
                        metric_id=sample.metric_id,
                        severity=severity,
                        state="active",
                        trigger_value=sample.value,
                        threshold_json=threshold,
                        topology_json=dict(entity.topology_json),
                        details_json={
                            "source": sample.source,
                            "observed_state": sample.value_text,
                        },
                        started_at=aware(sample.observed_at),
                        last_seen_at=aware(sample.observed_at),
                    )
                )
                opened += 1
            else:
                active.last_seen_at = aware(sample.observed_at)
                active.severity = severity
                active.trigger_value = sample.value
        elif active is not None:
            clear = True
            if sample.metric_id in BASIC_RULES and sample.value is not None:
                clear = sample.value < BASIC_RULES[sample.metric_id]["clear_below"]
            if clear:
                active.state = "resolved"
                active.resolved_at = now
                resolved += 1
    flapping = _evaluate_path_flapping(session, now)
    custom = evaluate_custom_alerts(session, samples)
    return {
        "opened": opened + flapping["opened"] + custom["opened"],
        "resolved": resolved + flapping["resolved"] + custom["resolved"],
    }


def _evaluate_path_flapping(session: Session, now: datetime) -> dict[str, int]:
    """Alert once for repeated durable state transitions, never for repeated polls."""

    since = now - PATH_FLAP_WINDOW
    counts = Counter(
        event.path_id
        for event in session.scalars(
            select(StorageRedundancyEvent).where(
                StorageRedundancyEvent.path_id.is_not(None),
                StorageRedundancyEvent.event_type.in_(("path_failed", "path_recovered")),
                StorageRedundancyEvent.occurred_at >= since,
                StorageRedundancyEvent.occurred_at <= now,
            )
        )
        if event.path_id is not None
    )
    opened = 0
    resolved = 0
    for path in session.scalars(select(StoragePath)):
        storage = session.get(StorageEntity, path.storage_entity_id)
        if storage is None:
            continue
        settings = storage.config_json.get("redundancy_settings")
        enabled = (
            not isinstance(settings, dict)
            or settings.get("alert_on_path_flapping") is not False
        )
        entity = session.scalar(
            select(MetricEntity).where(
                MetricEntity.entity_type == "storage_path",
                MetricEntity.stable_id == f"storage-path:{path.stable_path_identity}"[:512],
            )
        )
        if entity is None:
            continue
        rule_id = _rule_id("storage.path.flapping", entity.id)
        active = session.scalar(
            select(MetricAlert).where(
                MetricAlert.rule_id == rule_id,
                MetricAlert.state == "active",
            )
        )
        transition_count = counts[path.id]
        if enabled and transition_count >= PATH_FLAP_EVENT_THRESHOLD:
            if active is None:
                session.add(
                    MetricAlert(
                        rule_id=rule_id,
                        entity_id=entity.id,
                        metric_id="storage.path.state",
                        severity="warning",
                        state="active",
                        trigger_value=float(transition_count),
                        threshold_json={
                            "event_count": PATH_FLAP_EVENT_THRESHOLD,
                            "window_seconds": int(PATH_FLAP_WINDOW.total_seconds()),
                        },
                        topology_json=dict(entity.topology_json),
                        details_json={
                            "condition": "path_flapping",
                            "path_identity": path.stable_path_identity,
                            "observed_transitions": transition_count,
                        },
                        started_at=now,
                        last_seen_at=now,
                    )
                )
                opened += 1
            else:
                active.last_seen_at = now
                active.trigger_value = float(transition_count)
        elif active is not None:
            active.state = "resolved"
            active.resolved_at = now
            resolved += 1
    return {"opened": opened, "resolved": resolved}


def _custom_triggered(rule: MetricAlertRule, value: float) -> tuple[bool, str]:
    if rule.operator == "gt":
        triggered = value >= rule.warning_value
        critical = rule.critical_value is not None and value >= rule.critical_value
    else:
        triggered = value <= rule.warning_value
        critical = rule.critical_value is not None and value <= rule.critical_value
    return triggered, "critical" if critical else "warning"


def _custom_cleared(rule: MetricAlertRule, value: float) -> bool:
    return value <= rule.clear_value if rule.operator == "gt" else value >= rule.clear_value


def evaluate_custom_alerts(session: Session, samples: list[MetricSample]) -> dict[str, int]:
    """Evaluate user rules with a durable sustained-condition window and hysteresis."""
    opened = 0
    resolved = 0
    rules = session.scalars(select(MetricAlertRule).where(MetricAlertRule.enabled.is_(True))).all()
    for sample in samples:
        if sample.value is None or sample.quality not in {"available", "derived", "estimated"}:
            continue
        entity = session.get(MetricEntity, sample.entity_id)
        if entity is None:
            continue
        for rule in rules:
            if rule.metric_id != sample.metric_id:
                continue
            if rule.entity_id and rule.entity_id != entity.id:
                continue
            if rule.entity_type and rule.entity_type != entity.entity_type:
                continue
            rule_id = f"custom:{rule.id}:{entity.id}"[:128]
            active = session.scalar(
                select(MetricAlert).where(
                    MetricAlert.rule_id == rule_id,
                    MetricAlert.state == "active",
                )
            )
            triggered, severity = _custom_triggered(rule, float(sample.value))
            candidate_digest = hashlib.sha256(f"{rule.id}\0{entity.id}".encode()).hexdigest()
            candidate_id = f"alert-candidate:{candidate_digest[:48]}"
            candidate = session.get(TelemetryState, candidate_id)
            observed_at = aware(sample.observed_at)
            if triggered:
                if candidate is None:
                    candidate = TelemetryState(
                        id=candidate_id,
                        state_json={"since": observed_at.isoformat()},
                        updated_at=observed_at,
                    )
                    session.add(candidate)
                    sustained = rule.sustained_seconds == 0
                else:
                    try:
                        since = datetime.fromisoformat(str(candidate.state_json["since"]))
                    except (KeyError, TypeError, ValueError):
                        candidate.state_json = {"since": observed_at.isoformat()}
                        since = observed_at
                    sustained = (
                        observed_at - aware(since)
                    ).total_seconds() >= rule.sustained_seconds
                    candidate.updated_at = observed_at
                if sustained and active is None:
                    session.add(
                        MetricAlert(
                            rule_id=rule_id,
                            entity_id=entity.id,
                            metric_id=sample.metric_id,
                            severity=severity,
                            state="active",
                            trigger_value=sample.value,
                            threshold_json={
                                "operator": rule.operator,
                                "warning": rule.warning_value,
                                "critical": rule.critical_value,
                                "clear": rule.clear_value,
                                "sustained_seconds": rule.sustained_seconds,
                            },
                            topology_json=dict(entity.topology_json),
                            details_json={"rule_name": rule.name, "source": sample.source},
                            started_at=observed_at,
                            last_seen_at=observed_at,
                        )
                    )
                    opened += 1
                elif active is not None:
                    active.last_seen_at = observed_at
                    active.trigger_value = sample.value
                    active.severity = severity
            elif _custom_cleared(rule, float(sample.value)):
                if candidate is not None:
                    session.delete(candidate)
                if active is not None:
                    active.state = "resolved"
                    active.resolved_at = observed_at
                    resolved += 1
    return {"opened": opened, "resolved": resolved}


def rule_document(rule: MetricAlertRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "metric_id": rule.metric_id,
        "entity_type": rule.entity_type,
        "entity_id": rule.entity_id,
        "operator": rule.operator,
        "warning_value": rule.warning_value,
        "critical_value": rule.critical_value,
        "clear_value": rule.clear_value,
        "sustained_seconds": rule.sustained_seconds,
        "enabled": rule.enabled,
        "created_at": aware(rule.created_at),
        "updated_at": aware(rule.updated_at),
    }


def alert_document(alert: MetricAlert, entity: MetricEntity) -> dict[str, Any]:
    return {
        "id": alert.id,
        "rule_id": alert.rule_id,
        "entity": entity_document(entity),
        "metric_id": alert.metric_id,
        "severity": alert.severity,
        "state": alert.state,
        "trigger_value": alert.trigger_value,
        "threshold": alert.threshold_json,
        "topology": alert.topology_json,
        "details": alert.details_json,
        "started_at": aware(alert.started_at),
        "last_seen_at": aware(alert.last_seen_at),
        "resolved_at": aware(alert.resolved_at) if alert.resolved_at else None,
        "acknowledged_at": aware(alert.acknowledged_at) if alert.acknowledged_at else None,
        "acknowledged_by": alert.acknowledged_by,
    }
