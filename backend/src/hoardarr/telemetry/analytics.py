from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from typing import Any


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def nearest_rank(
    values: list[float], quantile: float, *, minimum_samples: int = 20
) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if len(clean) < minimum_samples:
        return None
    index = max(0, min(len(clean) - 1, math.ceil(quantile * len(clean)) - 1))
    return clean[index]


def theil_sen(points: list[tuple[datetime, float]]) -> float | None:
    unique = sorted({_aware(timestamp): float(value) for timestamp, value in points}.items())
    if len(unique) < 2:
        return None
    slopes = []
    for (left_time, left), (right_time, right) in combinations(unique[-120:], 2):
        days = (right_time - left_time).total_seconds() / 86400
        if days > 0:
            slopes.append((right - left) / days)
    return statistics.median(slopes) if slopes else None


def _history_span_days(points: list[tuple[datetime, float]]) -> float:
    if len(points) < 2:
        return 0
    ordered = sorted(points)
    return (_aware(ordered[-1][0]) - _aware(ordered[0][0])).total_seconds() / 86400


@dataclass(frozen=True)
class CapacityForecast:
    status: str
    data_points: int
    history_days: float
    growth_bytes_per_day: float | None
    current_used_bytes: float | None
    total_bytes: float | None
    projected: dict[str, dict[str, Any] | None]
    methodology: str

    def document(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "data_points": self.data_points,
            "history_days": round(self.history_days, 2),
            "growth_bytes_per_day": self.growth_bytes_per_day,
            "current_used_bytes": self.current_used_bytes,
            "total_bytes": self.total_bytes,
            "projected": self.projected,
            "methodology": self.methodology,
        }


def capacity_forecast(
    used_points: list[tuple[datetime, float]],
    *,
    total_bytes: float | None,
    now: datetime | None = None,
    minimum_points: int = 7,
    minimum_days: int = 7,
) -> CapacityForecast:
    current = _aware(now or datetime.now(UTC))
    observations = sorted(
        (timestamp, value)
        for timestamp, value in used_points
        if math.isfinite(value) and value >= 0
    )
    # Capacity is a slow-moving gauge. Keep the last observation from each UTC
    # day so collection cadence cannot make a dense recent window crowd older
    # evidence out of the bounded Theil-Sen input.
    daily: dict[date, tuple[datetime, float]] = {}
    for timestamp, value in observations:
        aware_timestamp = _aware(timestamp)
        daily[aware_timestamp.date()] = (aware_timestamp, float(value))
    valid = [daily[key] for key in sorted(daily)]
    span = _history_span_days(valid)
    method = (
        "Theil-Sen median daily slope from stored capacity observations; dates are rounded "
        "to whole days and are not shown when history or positive growth is insufficient."
    )
    if (
        len(valid) < minimum_points
        or span < minimum_days
        or total_bytes is None
        or total_bytes <= 0
    ):
        return CapacityForecast(
            "insufficient_history",
            len(valid),
            span,
            None,
            valid[-1][1] if valid else None,
            total_bytes,
            {"80": None, "90": None, "95": None, "100": None},
            method,
        )
    slope = theil_sen(valid)
    current_used = valid[-1][1]
    if slope is None or slope <= 0:
        return CapacityForecast(
            "stable_or_declining",
            len(valid),
            span,
            slope,
            current_used,
            total_bytes,
            {"80": None, "90": None, "95": None, "100": None},
            method,
        )
    projections: dict[str, dict[str, Any] | None] = {}
    for label, fraction in (("80", 0.80), ("90", 0.90), ("95", 0.95), ("100", 1.0)):
        remaining = total_bytes * fraction - current_used
        days = max(0, math.ceil(remaining / slope))
        projections[label] = {
            "days": days,
            "date": (current + timedelta(days=days)).date().isoformat(),
        }
    return CapacityForecast(
        "available",
        len(valid),
        span,
        slope,
        current_used,
        total_bytes,
        projections,
        method,
    )


def endurance_forecast(
    percentage_used: list[tuple[datetime, float]],
    *,
    now: datetime | None = None,
    minimum_points: int = 7,
    minimum_days: int = 7,
) -> dict[str, Any]:
    current = _aware(now or datetime.now(UTC))
    points = sorted(
        (timestamp, value)
        for timestamp, value in percentage_used
        if math.isfinite(value) and 0 <= value <= 255
    )
    span = _history_span_days(points)
    method = (
        "Theil-Sen median daily slope of the device-reported percentage-used counter. "
        "This does not estimate remaining TBW or NAND writes."
    )
    if len(points) < minimum_points or span < minimum_days:
        return {
            "status": "insufficient_history",
            "data_points": len(points),
            "history_days": round(span, 2),
            "percentage_used": points[-1][1] if points else None,
            "consumption_percent_per_day": None,
            "projected_exhaustion": None,
            "methodology": method,
        }
    slope = theil_sen(points)
    used = points[-1][1]
    if slope is None or slope <= 0:
        return {
            "status": "stable_or_declining",
            "data_points": len(points),
            "history_days": round(span, 2),
            "percentage_used": used,
            "consumption_percent_per_day": slope,
            "projected_exhaustion": None,
            "methodology": method,
        }
    days = max(0, math.ceil((100 - used) / slope))
    return {
        "status": "available",
        "data_points": len(points),
        "history_days": round(span, 2),
        "percentage_used": used,
        "consumption_percent_per_day": slope,
        "projected_exhaustion": {
            "days": days,
            "date": (current + timedelta(days=days)).date().isoformat(),
        },
        "methodology": method,
    }


def baseline(
    values: list[float], *, minimum_samples: int = 20
) -> dict[str, float | int | str | None]:
    clean = [value for value in values if math.isfinite(value)]
    if len(clean) < minimum_samples:
        return {
            "status": "insufficient_history",
            "samples": len(clean),
            "median": None,
            "lower": None,
            "upper": None,
            "methodology": "Median ± 3.5 median absolute deviations.",
        }
    median = statistics.median(clean)
    mad = statistics.median(abs(value - median) for value in clean)
    scale = 1.4826 * mad
    return {
        "status": "available",
        "samples": len(clean),
        "median": median,
        "lower": median - 3.5 * scale,
        "upper": median + 3.5 * scale,
        "methodology": "Median ± 3.5 median absolute deviations.",
    }


def anomaly(
    *, entity: dict[str, Any], metric_id: str, observed: float, history: list[float], now: datetime
) -> dict[str, Any] | None:
    expected = baseline(history)
    if expected["status"] != "available":
        return None
    lower = float(expected["lower"])
    upper = float(expected["upper"])
    if lower <= observed <= upper:
        return None
    median = float(expected["median"])
    distance = abs(observed - median)
    spread = max(abs(upper - median), 1e-12)
    severity = "critical" if distance > spread * 2 else "warning"
    return {
        "entity": entity,
        "metric_id": metric_id,
        "observed_value": observed,
        "expected_range": {"lower": lower, "upper": upper, "median": median},
        "detected_at": _aware(now),
        "duration_seconds": 0,
        "severity": severity,
        "data_used": {"samples": expected["samples"]},
        "explanation": "The observation is outside this entity's recent robust baseline.",
        "active": True,
        "methodology": expected["methodology"],
    }


def correlate(anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in anomalies:
        topology = item.get("entity", {}).get("topology", {})
        for dimension in ("controller", "port", "expander", "enclosure", "path", "pool"):
            value = topology.get(dimension) if isinstance(topology, dict) else None
            if value:
                groups.setdefault((dimension, str(value)), []).append(item)
    correlations = []
    for (dimension, value), items in groups.items():
        entity_ids = {item.get("entity", {}).get("id") for item in items}
        if len(entity_ids) < 2:
            continue
        correlations.append(
            {
                "dimension": dimension,
                "value": value,
                "entities": sorted(str(item) for item in entity_ids if item),
                "metrics": sorted({str(item.get("metric_id")) for item in items}),
                "explanation": (
                    f"Multiple entities connected through {dimension} {value} were outside "
                    "their recent baselines at the same time."
                ),
                "causation_claimed": False,
            }
        )
    return correlations
