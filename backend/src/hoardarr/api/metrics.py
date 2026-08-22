from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector


@dataclass(frozen=True)
class ApiMetrics:
    registry: CollectorRegistry
    requests: Counter
    duration: Histogram


def create_api_metrics() -> ApiMetrics:
    registry = CollectorRegistry(auto_describe=True)
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    return ApiMetrics(
        registry=registry,
        requests=Counter(
            "hoardarr_api_requests_total",
            "Completed Hoardarr API requests",
            ("method", "route", "status"),
            registry=registry,
        ),
        duration=Histogram(
            "hoardarr_api_request_duration_seconds",
            "Hoardarr API request duration",
            ("method", "route"),
            registry=registry,
        ),
    )
