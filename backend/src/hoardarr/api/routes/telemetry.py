from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    require_state_scope,
)
from hoardarr.api.problem import Problem
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.engine import sqlite_database_path
from hoardarr.db.models import (
    MetricAlert,
    MetricAlertRule,
    MetricEntity,
    MetricRollup,
    MetricSample,
    TelemetryState,
)
from hoardarr.telemetry.alerts import alert_document, rule_document
from hoardarr.telemetry.analytics import (
    anomaly,
    capacity_forecast,
    correlate,
    endurance_forecast,
    nearest_rank,
)
from hoardarr.telemetry.catalog import CATALOG_BY_ID, catalog_document
from hoardarr.telemetry.entitlements import EntitlementService
from hoardarr.telemetry.store import aware, current_samples, entity_document, history, period_start

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
METRIC_ID_RE = re.compile(r"[a-z][a-z0-9_.]{2,127}")


class AlertRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    metric_id: str = Field(min_length=3, max_length=128)
    entity_type: str | None = Field(default=None, max_length=64)
    entity_id: str | None = Field(default=None, max_length=36)
    operator: Literal["gt", "lt"]
    warning_value: float
    critical_value: float | None = None
    clear_value: float
    sustained_seconds: int = Field(default=0, ge=0, le=604800)
    enabled: bool = True

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> AlertRuleInput:
        if self.operator == "gt":
            if self.clear_value >= self.warning_value:
                raise ValueError("clear_value must be below warning_value")
            if self.critical_value is not None and self.critical_value < self.warning_value:
                raise ValueError("critical_value must be at or above warning_value")
        else:
            if self.clear_value <= self.warning_value:
                raise ValueError("clear_value must be above warning_value")
            if self.critical_value is not None and self.critical_value > self.warning_value:
                raise ValueError("critical_value must be at or below warning_value")
        return self


def _entitlements(request: Request, session: Session) -> Any:
    return EntitlementService(request.app.state.settings).evaluate(session)


def _require(status: Any, capability: str) -> None:
    if not status.allows(capability):
        raise Problem(
            403,
            "entitlement_required",
            "Capability unavailable",
            f"This request requires the {capability} capability.",
        )


def _metric(metric_id: str) -> Any:
    if not METRIC_ID_RE.fullmatch(metric_id) or metric_id not in CATALOG_BY_ID:
        raise Problem(
            404, "metric_not_found", "Metric not found", "The metric is not in the catalog."
        )
    return CATALOG_BY_ID[metric_id]


def _validate_range(request: Request, start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise Problem(
            422, "timezone_required", "Timezone required", "Time ranges must include a UTC offset."
        )
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if end <= start:
        raise Problem(422, "invalid_time_range", "Invalid time range", "End must be after start.")
    if end - start > timedelta(days=request.app.state.settings.telemetry_max_query_days):
        raise Problem(
            413,
            "time_range_too_large",
            "Time range too large",
            "The requested telemetry range is too large.",
        )
    return start, end


@router.get("/catalog")
def metric_catalog(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    return {
        "items": [
            {**item, "entitled": status.allows(item["capability"])} for item in catalog_document()
        ],
        "quality_states": [
            "available",
            "not_reported",
            "unsupported",
            "temporarily_unavailable",
            "stale",
            "estimated",
            "derived",
        ],
        "entitlements": status.document(),
    }


@router.get("/entitlements")
def entitlement_status(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    return _entitlements(request, session).document()


@router.get("/providers")
def provider_health(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    states = session.scalars(
        select(TelemetryState).where(TelemetryState.id.like("collector:%"))
    ).all()
    return {"items": [state.state_json for state in states]}


@router.get("/settings")
def telemetry_settings(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    settings = request.app.state.settings
    status = _entitlements(request, session)
    database = sqlite_database_path(settings.database_url)
    database_bytes = database.stat().st_size if database is not None and database.exists() else 0
    oldest_raw = session.scalar(select(func.min(MetricSample.observed_at)))
    oldest_rollup = session.scalar(select(func.min(MetricRollup.period_start)))
    entity_counts = {
        str(entity_type): int(count)
        for entity_type, count in session.execute(
            select(MetricEntity.entity_type, func.count()).group_by(MetricEntity.entity_type)
        )
    }
    entity_count = sum(entity_counts.values())
    retention_state = session.get(TelemetryState, "retention_last_run")
    last_cleanup = None
    if retention_state is not None:
        last_cleanup = retention_state.state_json.get("at")
    next_cleanup = None
    if isinstance(last_cleanup, str):
        try:
            next_cleanup = aware(datetime.fromisoformat(last_cleanup)) + timedelta(hours=1)
        except ValueError:
            next_cleanup = None
    # This is explicitly an estimate: a normalized observation averages roughly
    # 240 bytes in SQLite before indexes and WAL overhead.
    estimated_observations = sum(
        86_400 / definition.minimum_interval_seconds * entity_counts.get(entity_type, 0)
        for definition in CATALOG_BY_ID.values()
        for entity_type in definition.entity_types
    )
    return {
        "collection": {
            "fast_interval_seconds": settings.telemetry_fast_interval_seconds,
            "device_interval_seconds": settings.telemetry_device_interval_seconds,
            "hardware_interval_seconds": settings.telemetry_hardware_interval_seconds,
        },
        "history": {
            "recent_resolution_seconds": settings.telemetry_fast_interval_seconds,
            "recent_retention_hours": settings.telemetry_recent_retention_hours,
            "medium_resolution_seconds": 3600,
            "medium_retention_days": settings.telemetry_hourly_retention_days,
            "long_resolution_seconds": 86400,
            "long_retention_days": settings.telemetry_daily_retention_days,
            "maximum_graph_points": settings.telemetry_max_query_points,
            "maximum_series": settings.telemetry_max_graph_series,
            "maximum_observations": settings.telemetry_max_query_observations,
        },
        "storage": {
            "database_bytes": database_bytes,
            "oldest_raw_history": aware(oldest_raw) if oldest_raw else None,
            "oldest_retained_history": (
                aware(oldest_rollup or oldest_raw) if oldest_rollup or oldest_raw else None
            ),
            "entity_count": entity_count,
            "estimated_bytes_per_day": int(estimated_observations * 240),
            "estimate_method": "entity count x catalog cadence x 240 bytes per observation",
            "last_cleanup": last_cleanup,
            "next_cleanup": next_cleanup,
            "cleanup_batch_size": settings.telemetry_cleanup_batch_size,
        },
        "extended_history": {
            "entitled": status.allows("metrics.history.extended"),
            "capability": "metrics.history.extended",
        },
    }


@router.get("/entities")
def entities(
    entity_type: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=1000, ge=1, le=5000),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    statement = (
        select(MetricEntity)
        .order_by(MetricEntity.entity_type, MetricEntity.display_name)
        .limit(limit)
    )
    if entity_type:
        statement = statement.where(MetricEntity.entity_type == entity_type)
    return {"items": [entity_document(entity) for entity in session.scalars(statement)]}


@router.get("/current")
def current(
    request: Request,
    metric_id: list[str] | None = Query(default=None),
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=1000, ge=1, le=5000),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    definitions = [_metric(value) for value in metric_id or []]
    for definition in definitions:
        if definition.capability:
            _require(status, definition.capability)
    items = current_samples(
        session,
        metric_ids=metric_id,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
    )
    visible = [item for item in items if status.allows(item["capability"])]
    restricted = sorted(
        {str(item["capability"]) for item in items if not status.allows(item["capability"])}
    )
    return {
        "captured_at": datetime.now(UTC),
        "items": visible,
        "restricted_capabilities": restricted,
    }


@router.get("/history")
def metric_history(
    request: Request,
    entity_id: str = Query(max_length=36),
    metric_id: str = Query(max_length=128),
    start: datetime = Query(),
    end: datetime = Query(),
    resolution: Literal["auto", "raw", "hour", "day"] = Query(default="auto"),
    limit: int = Query(default=1000, ge=1, le=5000),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    definition = _metric(metric_id)
    start, end = _validate_range(request, start, end)
    status = _entitlements(request, session)
    if definition.capability:
        _require(status, definition.capability)
    if end - start > timedelta(hours=request.app.state.settings.telemetry_recent_retention_hours):
        _require(status, "metrics.history.extended")
    if session.get(MetricEntity, entity_id) is None:
        raise Problem(
            404, "entity_not_found", "Entity not found", "The metric entity does not exist."
        )
    settings = request.app.state.settings
    limit = min(limit, settings.telemetry_max_query_points)
    requested_resolution = resolution
    duration = end - start
    if resolution == "auto":
        expected_raw = duration.total_seconds() / max(1, definition.minimum_interval_seconds)
        if (
            duration <= timedelta(hours=settings.telemetry_recent_retention_hours)
            and expected_raw <= limit
        ):
            resolution = "raw"
        elif (
            duration <= timedelta(days=settings.telemetry_hourly_retention_days)
            and duration.total_seconds() / 3600 <= limit
        ):
            resolution = "hour"
        else:
            resolution = "day"
    model = MetricSample if resolution == "raw" else MetricRollup
    timestamp = MetricSample.observed_at if resolution == "raw" else MetricRollup.period_start
    query_start = start if resolution == "raw" else period_start(start, resolution)
    conditions = [
        model.entity_id == entity_id,
        model.metric_id == metric_id,
        timestamp >= query_start,
        timestamp <= end,
    ]
    if resolution != "raw":
        conditions.append(MetricRollup.resolution == resolution)
    available_points = int(
        session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
    )
    if available_points > limit:
        raise Problem(
            413,
            "point_budget_exceeded",
            "Graph point budget exceeded",
            "Use automatic or a coarser history resolution for this time range.",
        )
    document = history(
        session,
        entity_id=entity_id,
        metric_id=metric_id,
        start=start,
        end=end,
        resolution=resolution,
        limit=limit,
    )
    document.update(
        {
            "requested_resolution": requested_resolution,
            "maximum_points": limit,
            "available_points": available_points,
            "displayed_points": document["points_returned"],
        }
    )
    return document


def _series(
    session: Session, entity_id: str, metric_id: str, start: datetime
) -> list[tuple[datetime, float]]:
    raw = session.execute(
        select(MetricSample.observed_at, MetricSample.value)
        .where(
            MetricSample.entity_id == entity_id,
            MetricSample.metric_id == metric_id,
            MetricSample.observed_at >= start,
            MetricSample.value.is_not(None),
        )
        .order_by(MetricSample.observed_at)
        .limit(5000)
    ).all()
    if raw:
        return [(aware(timestamp), float(value)) for timestamp, value in raw]
    rolled = session.execute(
        select(MetricRollup.period_start, MetricRollup.mean)
        .where(
            MetricRollup.entity_id == entity_id,
            MetricRollup.metric_id == metric_id,
            MetricRollup.resolution == "day",
            MetricRollup.period_start >= start,
            MetricRollup.mean.is_not(None),
        )
        .order_by(MetricRollup.period_start)
        .limit(1000)
    ).all()
    return [(aware(timestamp), float(value)) for timestamp, value in rolled]


def _latest_value(session: Session, entity_id: str, metric_id: str) -> float | None:
    return session.scalar(
        select(MetricSample.value)
        .where(
            MetricSample.entity_id == entity_id,
            MetricSample.metric_id == metric_id,
            MetricSample.value.is_not(None),
        )
        .order_by(MetricSample.observed_at.desc())
        .limit(1)
    )


@router.get("/analytics/capacity/{entity_id}")
def capacity_projection(
    entity_id: str,
    request: Request,
    days: int = Query(default=30, ge=7, le=365),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    _require(status, "metrics.analytics.capacity")
    entity = session.get(MetricEntity, entity_id)
    if entity is None:
        raise Problem(
            404, "entity_not_found", "Entity not found", "The metric entity does not exist."
        )
    now = datetime.now(UTC)
    forecast = capacity_forecast(
        _series(session, entity_id, "capacity.used", now - timedelta(days=days)),
        total_bytes=_latest_value(session, entity_id, "capacity.total"),
        now=now,
    )
    return {"entity": entity_document(entity), "forecast": forecast.document()}


@router.get("/analytics/endurance/{entity_id}")
def endurance_projection(
    entity_id: str,
    request: Request,
    days: int = Query(default=30, ge=7, le=365),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    _require(status, "metrics.analytics.endurance")
    entity = session.get(MetricEntity, entity_id)
    if entity is None:
        raise Problem(
            404, "entity_not_found", "Entity not found", "The metric entity does not exist."
        )
    now = datetime.now(UTC)
    forecast = endurance_forecast(
        _series(session, entity_id, "drive.percentage_used", now - timedelta(days=days)),
        now=now,
    )
    return {"entity": entity_document(entity), "forecast": forecast}


@router.get("/analytics/latency/{entity_id}")
def latency_percentiles(
    entity_id: str,
    request: Request,
    metric_id: Literal["io.read.latency", "io.write.latency"],
    hours: int = Query(default=24, ge=1, le=720),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    _require(status, "metrics.analytics.performance")
    entity = session.get(MetricEntity, entity_id)
    if entity is None:
        raise Problem(
            404, "entity_not_found", "Entity not found", "The metric entity does not exist."
        )
    values = [
        value
        for _, value in _series(
            session, entity_id, metric_id, datetime.now(UTC) - timedelta(hours=hours)
        )
    ]
    return {
        "entity": entity_document(entity),
        "metric_id": metric_id,
        "samples": len(values),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "mean": sum(values) / len(values) if values else None,
        "median": nearest_rank(values, 0.50),
        "p50": nearest_rank(values, 0.50),
        "p95": nearest_rank(values, 0.95),
        "p99": nearest_rank(values, 0.99),
        "status": "available" if len(values) >= 20 else "insufficient_history",
        "methodology": (
            "Nearest-rank percentiles from stored latency observations; "
            "at least 20 samples are required."
        ),
    }


def _active_anomalies(session: Session, *, hours: int = 24) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    latest = current_samples(session, limit=5000)
    output = []
    for item in latest:
        if item["value"] is None or not isinstance(item["value"], (int, float)):
            continue
        definition = CATALOG_BY_ID[item["metric_id"]]
        if definition.capability is None and item["metric_id"] not in {
            "io.read.latency",
            "io.write.latency",
            "drive.temperature",
            "io.utilization",
        }:
            continue
        points = _series(
            session,
            item["entity"]["id"],
            item["metric_id"],
            now - timedelta(hours=hours),
        )
        result = anomaly(
            entity=item["entity"],
            metric_id=item["metric_id"],
            observed=float(item["value"]),
            history=[value for _, value in points[:-1]],
            now=now,
        )
        if result:
            output.append(result)
    return output


@router.get("/analytics/anomalies")
def anomalies(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    _require(status, "metrics.analytics.anomaly")
    return {"items": _active_anomalies(session)}


@router.get("/analytics/correlations")
def correlations(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    _require(status, "metrics.analytics.anomaly")
    return {"items": correlate(_active_anomalies(session))}


@router.get("/top")
def top_metrics(
    request: Request,
    metric_id: str,
    direction: Literal["highest", "lowest"] = "highest",
    limit: int = Query(default=10, ge=1, le=100),
    entity_type: str | None = Query(default=None, max_length=64),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    _metric(metric_id)
    status = _entitlements(request, session)
    _require(status, "metrics.analytics.performance")
    items = [
        item
        for item in current_samples(
            session, metric_ids=[metric_id], entity_type=entity_type, limit=5000
        )
        if isinstance(item["value"], (int, float))
        and item["quality"] in {"available", "derived", "estimated"}
    ]
    items.sort(key=lambda item: float(item["value"]), reverse=direction == "highest")
    return {"metric_id": metric_id, "direction": direction, "items": items[:limit]}


@router.get("/alerts")
def alerts(
    state: Literal["active", "resolved", "all"] = "active",
    limit: int = Query(default=100, ge=1, le=1000),
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    statement = (
        select(MetricAlert, MetricEntity)
        .join(MetricEntity, MetricEntity.id == MetricAlert.entity_id)
        .order_by(MetricAlert.started_at.desc())
        .limit(limit)
    )
    if state != "all":
        statement = statement.where(MetricAlert.state == state)
    return {
        "items": [alert_document(alert, entity) for alert, entity in session.execute(statement)]
    }


@router.get("/alert-rules")
def alert_rules(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    _require(_entitlements(request, session), "metrics.alerting.advanced")
    rules = session.scalars(select(MetricAlertRule).order_by(MetricAlertRule.name)).all()
    return {"items": [rule_document(rule) for rule in rules]}


@router.post("/alert-rules", status_code=201)
def create_alert_rule(
    body: AlertRuleInput,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    _require(_entitlements(request, session), "metrics.alerting.advanced")
    definition = _metric(body.metric_id)
    if definition.unit == "state":
        raise Problem(
            422,
            "numeric_metric_required",
            "Numeric metric required",
            "Custom threshold rules require a numeric metric.",
        )
    if body.entity_type and body.entity_type not in definition.entity_types:
        raise Problem(
            422,
            "invalid_entity_type",
            "Invalid entity type",
            "The metric is not available for that entity type.",
        )
    if body.entity_id and session.get(MetricEntity, body.entity_id) is None:
        raise Problem(404, "entity_not_found", "Entity not found", "The entity does not exist.")
    values = body.model_dump()
    values["name"] = body.name.strip()
    rule = MetricAlertRule(**values, created_by=principal.user_id)
    session.add(rule)
    session.flush()
    record_audit(
        session,
        principal=principal,
        action="telemetry.alert_rule.create",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="metric_alert_rule",
        target_id=rule.id,
        details={"metric_id": rule.metric_id, "entity_type": rule.entity_type},
    )
    return rule_document(rule)


@router.put("/alert-rules/{rule_id}")
def update_alert_rule(
    rule_id: str,
    body: AlertRuleInput,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    _require(_entitlements(request, session), "metrics.alerting.advanced")
    rule = session.get(MetricAlertRule, rule_id)
    if rule is None:
        raise Problem(
            404, "alert_rule_not_found", "Alert rule not found", "The rule does not exist."
        )
    definition = _metric(body.metric_id)
    if definition.unit == "state":
        raise Problem(
            422,
            "numeric_metric_required",
            "Numeric metric required",
            "Custom threshold rules require a numeric metric.",
        )
    if body.entity_type and body.entity_type not in definition.entity_types:
        raise Problem(
            422,
            "invalid_entity_type",
            "Invalid entity type",
            "The metric is not available for that entity type.",
        )
    for name, value in body.model_dump().items():
        setattr(rule, name, body.name.strip() if name == "name" else value)
    record_audit(
        session,
        principal=principal,
        action="telemetry.alert_rule.update",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="metric_alert_rule",
        target_id=rule.id,
        details={"metric_id": rule.metric_id, "enabled": rule.enabled},
    )
    return rule_document(rule)


@router.delete("/alert-rules/{rule_id}", status_code=204)
def delete_alert_rule(
    rule_id: str,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> Response:
    _require(_entitlements(request, session), "metrics.alerting.advanced")
    rule = session.get(MetricAlertRule, rule_id)
    if rule is None:
        raise Problem(
            404, "alert_rule_not_found", "Alert rule not found", "The rule does not exist."
        )
    record_audit(
        session,
        principal=principal,
        action="telemetry.alert_rule.delete",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="metric_alert_rule",
        target_id=rule.id,
        details={"metric_id": rule.metric_id},
    )
    session.delete(rule)
    return Response(status_code=204)


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    alert = session.get(MetricAlert, alert_id)
    if alert is None:
        raise Problem(404, "alert_not_found", "Alert not found", "The alert does not exist.")
    if alert.acknowledged_at is None:
        alert.acknowledged_at = datetime.now(UTC)
        alert.acknowledged_by = principal.user_id
    entity = session.get(MetricEntity, alert.entity_id)
    if entity is None:
        raise Problem(
            409, "alert_entity_missing", "Alert unavailable", "The alert entity no longer exists."
        )
    return alert_document(alert, entity)


def _prometheus_name(metric_id: str) -> str:
    return "hoardarr_" + metric_id.replace(".", "_")


@router.get("/export/prometheus")
def prometheus_export(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> Response:
    status = _entitlements(request, session)
    _require(status, "metrics.export")
    lines = []
    for item in current_samples(session, limit=5000):
        if not isinstance(item["value"], (int, float)) or not status.allows(item["capability"]):
            continue
        definition = CATALOG_BY_ID[item["metric_id"]]
        name = _prometheus_name(item["metric_id"])
        entity = item["entity"]
        lines.append(f"# HELP {name} {definition.name}")
        lines.append(f"# TYPE {name} gauge")
        labels = f'entity_id="{entity["id"]}",entity_type="{entity["entity_type"]}"'
        lines.append(f"{name}{{{labels}}} {item['value']}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/reports/{report_type}")
def report(
    report_type: Literal["health", "capacity", "performance", "endurance", "inventory", "alerts"],
    request: Request,
    start: datetime,
    end: datetime,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    status = _entitlements(request, session)
    _require(status, "metrics.reporting")
    start, end = _validate_range(request, start, end)
    prefixes = {
        "health": ("health.", "drive.temperature", "drive.media_errors"),
        "capacity": ("capacity.", "storage."),
        "performance": ("io.",),
        "endurance": ("drive.percentage_used", "drive.endurance", "drive.lifetime"),
        "inventory": tuple(),
        "alerts": tuple(),
    }[report_type]
    if report_type == "alerts":
        rows = session.execute(
            select(MetricAlert, MetricEntity)
            .join(MetricEntity, MetricEntity.id == MetricAlert.entity_id)
            .where(MetricAlert.started_at >= start, MetricAlert.started_at <= end)
            .order_by(MetricAlert.started_at)
            .limit(5000)
        )
        items = [alert_document(alert, entity) for alert, entity in rows]
    elif report_type == "inventory":
        items = [
            entity_document(entity) for entity in session.scalars(select(MetricEntity).limit(5000))
        ]
    else:
        items = [
            item
            for item in current_samples(session, limit=5000)
            if any(item["metric_id"].startswith(prefix) for prefix in prefixes)
            and status.allows(item["capability"])
        ]
    return {
        "report_type": report_type,
        "period": {"start": start, "end": end},
        "generated_at": datetime.now(UTC),
        "items": items,
        "source": "stored Hoardarr telemetry",
    }
