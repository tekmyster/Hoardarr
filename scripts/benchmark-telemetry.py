#!/usr/bin/env python3
"""Deterministic single-host telemetry ingestion/query benchmark."""

from __future__ import annotations

import json
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.telemetry.samples import EntityReading, MetricReading
from hoardarr.telemetry.store import current_samples, ingest


def run(count: int, samples_per_entity: int = 20) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory(prefix="hoardarr-telemetry-") as folder:
        path = Path(folder) / "benchmark.db"
        database_url = f"sqlite:///{path.as_posix()}"
        upgrade_database(database_url)
        engine = create_database_engine(database_url)
        factory = create_session_factory(engine)
        now = datetime.now(UTC).replace(microsecond=0)
        readings = [
            MetricReading(
                entity=EntityReading("drive", f"wwn:benchmark:{entity:04d}", f"Drive {entity}"),
                metric_id=metric,
                observed_at=now + timedelta(seconds=sample),
                value=float(entity + sample),
                quality="available",
                source="benchmark fixture",
                collection_interval_seconds=5,
            )
            for entity in range(count)
            for sample in range(samples_per_entity)
            for metric in ("io.read.iops", "io.write.iops", "io.read.latency", "io.write.latency")
        ]
        tracemalloc.start()
        cpu_start = time.process_time()
        wall_start = time.perf_counter()
        with factory() as session, session.begin():
            result = ingest(session, readings)
        ingest_wall = time.perf_counter() - wall_start
        ingest_cpu = time.process_time() - cpu_start
        query_start = time.perf_counter()
        with factory() as session:
            returned = len(current_samples(session, limit=5000))
        query_wall = time.perf_counter() - query_start
        _, peak_memory = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        engine.dispose()
        return {
            "entities": count,
            "samples_inserted": result["inserted"],
            "current_values_returned": returned,
            "ingest_wall_ms": round(ingest_wall * 1000, 2),
            "ingest_cpu_ms": round(ingest_cpu * 1000, 2),
            "query_wall_ms": round(query_wall * 1000, 2),
            "peak_python_bytes": peak_memory,
            "database_bytes": path.stat().st_size,
        }


def main() -> int:
    print(json.dumps({"results": [run(count) for count in (1, 24, 60, 120, 240)]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
