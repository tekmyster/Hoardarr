from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from hoardarr.telemetry.catalog import CATALOG_BY_ID

MetricQuality = Literal[
    "available",
    "not_reported",
    "unsupported",
    "temporarily_unavailable",
    "stale",
    "estimated",
    "derived",
]
QUALITY_VALUES = frozenset(
    {
        "available",
        "not_reported",
        "unsupported",
        "temporarily_unavailable",
        "stale",
        "estimated",
        "derived",
    }
)


@dataclass(frozen=True)
class EntityReading:
    entity_type: str
    stable_id: str
    display_name: str
    labels: dict[str, str] = field(default_factory=dict)
    topology: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.entity_type or len(self.entity_type) > 64:
            raise ValueError("entity_type is invalid")
        if not self.stable_id or len(self.stable_id) > 512:
            raise ValueError("stable_id is invalid")
        if not self.display_name or len(self.display_name) > 256:
            raise ValueError("display_name is invalid")
        if len(self.labels) > 32 or len(self.topology) > 32:
            raise ValueError("entity metadata is too large")
        for collection in (self.labels, self.topology):
            if any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key
                or len(key) > 64
                or len(value) > 512
                for key, value in collection.items()
            ):
                raise ValueError("entity metadata contains an invalid value")


@dataclass(frozen=True)
class MetricReading:
    entity: EntityReading
    metric_id: str
    observed_at: datetime
    value: float | int | str | None
    quality: MetricQuality
    source: str
    collection_interval_seconds: int
    labels: dict[str, str] = field(default_factory=dict)
    error_code: str | None = None

    def __post_init__(self) -> None:
        definition = CATALOG_BY_ID.get(self.metric_id)
        if definition is None:
            raise ValueError(f"metric is not in the catalog: {self.metric_id}")
        if self.entity.entity_type not in definition.entity_types:
            raise ValueError("metric does not support this entity type")
        if self.quality not in QUALITY_VALUES:
            raise ValueError("invalid metric quality")
        if isinstance(self.value, str):
            if definition.unit != "state" or not self.value.strip() or len(self.value) > 128:
                raise ValueError("text metric value is invalid")
        elif self.value is not None:
            numeric = float(self.value)
            if not math.isfinite(numeric):
                raise ValueError("metric values must be finite")
            nonnegative_units = {
                "bitmask",
                "bits_per_second",
                "boolean",
                "bytes",
                "bytes_per_day",
                "bytes_per_second",
                "count",
                "hours",
                "milliseconds",
                "minutes",
                "operations_per_second",
                "packets_per_second",
                "percent",
                "ratio",
                "rpm",
                "seconds",
            }
            if numeric < 0 and definition.unit in nonnegative_units:
                raise ValueError("metric value cannot be negative")
        if self.quality in {"not_reported", "unsupported", "temporarily_unavailable"}:
            if self.value is not None:
                raise ValueError("unavailable metric quality cannot include a value")
        elif self.value is None:
            raise ValueError("available metric quality requires a value")
        if self.observed_at.tzinfo is None:
            raise ValueError("metric timestamps must include a timezone")
        if not self.source or len(self.source) > 128:
            raise ValueError("metric source is invalid")
        if not 1 <= self.collection_interval_seconds <= 86400:
            raise ValueError("collection interval is invalid")
        if len(self.labels) > 16:
            raise ValueError("too many metric labels")
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or len(key) > 64
            or len(value) > 256
            for key, value in self.labels.items()
        ):
            raise ValueError("metric labels contain an invalid value")
        if self.error_code is not None and (
            not self.error_code
            or len(self.error_code) > 96
            or re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", self.error_code) is None
        ):
            raise ValueError("metric error code is invalid")

    @property
    def normalized_time(self) -> datetime:
        return self.observed_at.astimezone(UTC)

    @property
    def raw(self) -> bool:
        return CATALOG_BY_ID[self.metric_id].kind == "raw"
