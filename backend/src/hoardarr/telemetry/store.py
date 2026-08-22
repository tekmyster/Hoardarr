from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import Session

from hoardarr.db.models import MetricEntity, MetricRollup, MetricSample, TelemetryState, utc_now
from hoardarr.telemetry.catalog import CATALOG_BY_ID
from hoardarr.telemetry.samples import MetricReading


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def ingest(session: Session, readings: list[MetricReading]) -> dict[str, int]:
    if not readings:
        return {"inserted": 0, "duplicates": 0}
    inserted = 0
    duplicates = 0
    entity_keys = {(item.entity.entity_type, item.entity.stable_id) for item in readings}
    entity_cache: dict[tuple[str, str], MetricEntity] = {}
    stable_ids = sorted({key[1] for key in entity_keys})
    for offset in range(0, len(stable_ids), 500):
        candidates = session.scalars(
            select(MetricEntity).where(
                MetricEntity.stable_id.in_(stable_ids[offset : offset + 500])
            )
        )
        for entity in candidates:
            key = (entity.entity_type, entity.stable_id)
            if key in entity_keys:
                entity_cache[key] = entity

    for reading in readings:
        key = (reading.entity.entity_type, reading.entity.stable_id)
        entity = entity_cache.get(key)
        if entity is None:
            entity = MetricEntity(
                entity_type=reading.entity.entity_type,
                stable_id=reading.entity.stable_id,
                display_name=reading.entity.display_name,
                labels_json=dict(reading.entity.labels),
                topology_json=dict(reading.entity.topology),
                first_seen_at=reading.normalized_time,
                last_seen_at=reading.normalized_time,
            )
            session.add(entity)
            entity_cache[key] = entity
        else:
            entity.display_name = reading.entity.display_name
            entity.labels_json = dict(reading.entity.labels)
            entity.topology_json = dict(reading.entity.topology)
            entity.last_seen_at = max(aware(entity.last_seen_at), reading.normalized_time)
    session.flush()

    for offset in range(0, len(readings), 500):
        batch = readings[offset : offset + 500]
        requested = [
            (
                entity_cache[(item.entity.entity_type, item.entity.stable_id)].id,
                item.metric_id,
                item.normalized_time,
            )
            for item in batch
        ]
        rows = session.execute(
            select(MetricSample.entity_id, MetricSample.metric_id, MetricSample.observed_at).where(
                tuple_(
                    MetricSample.entity_id,
                    MetricSample.metric_id,
                    MetricSample.observed_at,
                ).in_(requested)
            )
        )
        existing_keys = {
            (entity_id, metric_id, aware(observed_at))
            for entity_id, metric_id, observed_at in rows
        }
        seen_in_batch: set[tuple[str, str, datetime]] = set()
        pending: list[MetricSample] = []
        for reading, key in zip(batch, requested, strict=True):
            normalized_key = (key[0], key[1], aware(key[2]))
            if normalized_key in existing_keys or normalized_key in seen_in_batch:
                duplicates += 1
                continue
            seen_in_batch.add(normalized_key)
            entity = entity_cache[(reading.entity.entity_type, reading.entity.stable_id)]
            pending.append(
                MetricSample(
                    entity_id=entity.id,
                    metric_id=reading.metric_id,
                    value=(
                        float(reading.value)
                        if reading.value is not None and not isinstance(reading.value, str)
                        else None
                    ),
                    value_text=reading.value if isinstance(reading.value, str) else None,
                    quality=reading.quality,
                    source=reading.source,
                    collection_interval_seconds=reading.collection_interval_seconds,
                    raw=reading.raw,
                    labels_json=dict(reading.labels),
                    error_code=reading.error_code,
                    observed_at=reading.normalized_time,
                )
            )
            inserted += 1
        session.add_all(pending)
        session.flush()
    return {"inserted": inserted, "duplicates": duplicates}


def entity_document(entity: MetricEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "entity_type": entity.entity_type,
        "stable_id": entity.stable_id,
        "display_name": entity.display_name,
        "labels": entity.labels_json,
        "topology": entity.topology_json,
        "first_seen_at": aware(entity.first_seen_at),
        "last_seen_at": aware(entity.last_seen_at),
    }


def sample_document(sample: MetricSample, entity: MetricEntity) -> dict[str, Any]:
    definition = CATALOG_BY_ID[sample.metric_id]
    return {
        "metric_id": sample.metric_id,
        "name": definition.name,
        "entity": entity_document(entity),
        "timestamp": aware(sample.observed_at),
        "value": sample.value_text if sample.value_text is not None else sample.value,
        "unit": definition.unit,
        "source": sample.source,
        "collection_interval_seconds": sample.collection_interval_seconds,
        "quality": sample.quality,
        "raw": sample.raw,
        "labels": sample.labels_json,
        "capability": definition.capability,
        "error_code": sample.error_code,
    }


def current_samples(
    session: Session,
    *,
    metric_ids: list[str] | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    latest = (
        select(
            MetricSample.entity_id,
            MetricSample.metric_id,
            func.max(MetricSample.observed_at).label("latest_at"),
        )
        .group_by(MetricSample.entity_id, MetricSample.metric_id)
        .subquery()
    )
    statement = (
        select(MetricSample, MetricEntity)
        .join(MetricEntity, MetricEntity.id == MetricSample.entity_id)
        .join(
            latest,
            (latest.c.entity_id == MetricSample.entity_id)
            & (latest.c.metric_id == MetricSample.metric_id)
            & (latest.c.latest_at == MetricSample.observed_at),
        )
        .order_by(MetricEntity.entity_type, MetricEntity.display_name, MetricSample.metric_id)
        .limit(limit)
    )
    if metric_ids:
        statement = statement.where(MetricSample.metric_id.in_(metric_ids))
    if entity_type:
        statement = statement.where(MetricEntity.entity_type == entity_type)
    if entity_id:
        statement = statement.where(MetricEntity.id == entity_id)
    now = datetime.now(UTC)
    documents = []
    for sample, entity in session.execute(statement):
        document = sample_document(sample, entity)
        stale_after = max(30, sample.collection_interval_seconds * 3)
        if document["quality"] in {"available", "derived", "estimated"} and now - aware(
            sample.observed_at
        ) > timedelta(seconds=stale_after):
            document["quality"] = "stale"
        documents.append(document)
    return documents


def history(
    session: Session,
    *,
    entity_id: str,
    metric_id: str,
    start: datetime,
    end: datetime,
    resolution: str,
    limit: int,
) -> dict[str, Any]:
    entity = session.get(MetricEntity, entity_id)
    if entity is None:
        return {"entity": None, "metric_id": metric_id, "resolution": resolution, "points": []}
    if resolution == "raw":
        rows = session.scalars(
            select(MetricSample)
            .where(
                MetricSample.entity_id == entity_id,
                MetricSample.metric_id == metric_id,
                MetricSample.observed_at >= start,
                MetricSample.observed_at <= end,
            )
            .order_by(MetricSample.observed_at)
            .limit(limit)
        ).all()
        points = [
            {
                "timestamp": aware(row.observed_at),
                "value": row.value_text if row.value_text is not None else row.value,
                "quality": row.quality,
                "source": row.source,
                "raw": True,
                "interval_seconds": row.collection_interval_seconds,
            }
            for row in rows
        ]
    else:
        query_start = period_start(start, resolution)
        rows = session.scalars(
            select(MetricRollup)
            .where(
                MetricRollup.entity_id == entity_id,
                MetricRollup.metric_id == metric_id,
                MetricRollup.resolution == resolution,
                MetricRollup.period_start >= query_start,
                MetricRollup.period_start <= end,
            )
            .order_by(MetricRollup.period_start)
            .limit(limit)
        ).all()
        points = [
            {
                "timestamp": aware(row.period_start),
                "value": row.last_text if row.last_text is not None else row.mean,
                "first": row.first_text if row.first_text is not None else row.first,
                "minimum": row.minimum,
                "maximum": row.maximum,
                "last": row.last_text if row.last_text is not None else row.last,
                "p50": row.p50,
                "p95": row.p95,
                "p99": row.p99,
                "sample_count": row.sample_count,
                "quality": row.quality,
                "raw": False,
                "interval_seconds": 3600 if resolution == "hour" else 86400,
                "transition_count": row.transition_count,
                "states": row.states_json,
            }
            for row in rows
        ]
    return {
        "entity": entity_document(entity),
        "metric_id": metric_id,
        "unit": CATALOG_BY_ID[metric_id].unit,
        "resolution": resolution,
        "source_resolution": resolution,
        "aggregation_method": (
            "raw samples" if resolution == "raw" else "first/last/minimum/maximum/mean/count"
        ),
        "raw": resolution == "raw",
        "start": start,
        "end": end,
        "points_returned": len(points),
        "points": points,
    }


def period_start(value: datetime, resolution: str) -> datetime:
    value = aware(value)
    if resolution == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def build_rollups(
    session: Session,
    *,
    now: datetime | None = None,
    percentile_sample_limit: int = 20_000,
) -> dict[str, int]:
    """Roll up completed buckets while retaining only one bucket in memory."""
    current = aware(now or utc_now())
    created = 0
    for resolution, age in (("hour", timedelta(hours=1)), ("day", timedelta(days=1))):
        cutoff = current - age
        rows = session.execute(
            select(MetricSample)
            .where(MetricSample.observed_at < cutoff)
            .order_by(MetricSample.entity_id, MetricSample.metric_id, MetricSample.observed_at)
            .execution_options(yield_per=1000)
        ).scalars()
        key: tuple[str, str, datetime] | None = None
        samples: list[MetricSample] = []

        def flush(
            group_key: tuple[str, str, datetime],
            group: list[MetricSample],
            bucket_resolution: str = resolution,
        ) -> None:
            nonlocal created
            entity_id, metric_id, period = group_key
            values = [float(item.value) for item in group if item.value is not None]
            percentile_values = values if len(values) <= percentile_sample_limit else []
            text_values = [item.value_text for item in group if item.value_text is not None]
            states: list[str] = []
            for value in text_values:
                if not states or states[-1] != value:
                    states.append(value)
            existing = session.scalar(
                select(MetricRollup).where(
                    MetricRollup.entity_id == entity_id,
                    MetricRollup.metric_id == metric_id,
                    MetricRollup.resolution == bucket_resolution,
                    MetricRollup.period_start == period,
                )
            )
            document = {
                "sample_count": len(group),
                "minimum": min(values) if values else None,
                "maximum": max(values) if values else None,
                "mean": sum(values) / len(values) if values else None,
                "first": next((item.value for item in group if item.value is not None), None),
                "last": next(
                    (item.value for item in reversed(group) if item.value is not None), None
                ),
                "first_text": text_values[0] if text_values else None,
                "last_text": text_values[-1] if text_values else None,
                "transition_count": max(0, len(states) - 1),
                "states_json": states,
                "p50": percentile(percentile_values, 0.50),
                "p95": percentile(percentile_values, 0.95),
                "p99": percentile(percentile_values, 0.99),
                "quality": "derived" if values else group[-1].quality,
            }
            if existing is None:
                session.add(
                    MetricRollup(
                        entity_id=entity_id,
                        metric_id=metric_id,
                        resolution=bucket_resolution,
                        period_start=period,
                        **document,
                    )
                )
                created += 1
            elif existing.sample_count > len(group):
                # A prior cleanup already rolled up the complete bucket and only
                # a bounded remainder of its raw samples is still present.
                return
            else:
                for name, value in document.items():
                    setattr(existing, name, value)

        for row in rows:
            row_key = (row.entity_id, row.metric_id, period_start(row.observed_at, resolution))
            if key is not None and row_key != key:
                flush(key, samples)
                samples = []
            key = row_key
            samples.append(row)
        if key is not None:
            flush(key, samples)
    session.flush()
    return {"rollups_created": created}


def _delete_batch(session: Session, model: Any, condition: Any, batch_size: int) -> int:
    identifiers = select(model.id).where(condition).order_by(model.id).limit(batch_size)
    return int(session.execute(delete(model).where(model.id.in_(identifiers))).rowcount or 0)


def apply_retention(
    session: Session,
    *,
    now: datetime | None = None,
    recent_hours: int,
    hourly_days: int,
    daily_days: int,
    batch_size: int = 10_000,
    percentile_sample_limit: int = 20_000,
) -> dict[str, int]:
    current = aware(now or utc_now())
    state = session.get(TelemetryState, "retention")
    requested = {
        "recent_hours": recent_hours,
        "hourly_days": hourly_days,
        "daily_days": daily_days,
    }
    if state is None:
        state = TelemetryState(id="retention", state_json=requested)
        session.add(state)
    else:
        # License changes never shorten retention. A deliberate settings update may
        # change these values in a future settings endpoint.
        effective = state.state_json
        recent_hours = int(effective.get("recent_hours", recent_hours))
        hourly_days = int(effective.get("hourly_days", hourly_days))
        daily_days = int(effective.get("daily_days", daily_days))
    build_rollups(session, now=current, percentile_sample_limit=percentile_sample_limit)
    raw_deleted = _delete_batch(
        session,
        MetricSample,
        MetricSample.observed_at < current - timedelta(hours=recent_hours),
        batch_size,
    )
    hourly_deleted = _delete_batch(
        session,
        MetricRollup,
        (MetricRollup.resolution == "hour")
        & (MetricRollup.period_start < current - timedelta(days=hourly_days)),
        batch_size,
    )
    daily_deleted = _delete_batch(
        session,
        MetricRollup,
        (MetricRollup.resolution == "day")
        & (MetricRollup.period_start < current - timedelta(days=daily_days)),
        batch_size,
    )
    state.state_json = {
        "recent_hours": recent_hours,
        "hourly_days": hourly_days,
        "daily_days": daily_days,
        "last_run": current.isoformat(),
        "batch_size": batch_size,
    }
    state.updated_at = current
    return {
        "raw_deleted": raw_deleted,
        "hourly_deleted": hourly_deleted,
        "daily_deleted": daily_deleted,
    }
