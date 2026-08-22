#!/usr/bin/env python3
"""Accelerated bounded-memory telemetry ingestion/retention soak profile."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import MetricSample
from hoardarr.telemetry.samples import EntityReading, MetricReading
from hoardarr.telemetry.store import apply_retention, ingest
from sqlalchemy import func, select


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drives", type=int, default=60, choices=(1, 24, 60, 120, 240))
    parser.add_argument("--cycles", type=int, default=100)
    args = parser.parse_args()
    if not 10 <= args.cycles <= 10_000:
        parser.error("--cycles must be between 10 and 10000")

    with tempfile.TemporaryDirectory(prefix="hoardarr-telemetry-soak-") as folder:
        path = Path(folder) / "soak.db"
        url = f"sqlite:///{path.as_posix()}"
        upgrade_database(url)
        engine = create_database_engine(url)
        factory = create_session_factory(engine)
        start = datetime.now(UTC).replace(microsecond=0) - timedelta(days=10)
        metrics = ("io.read.iops", "io.write.iops", "io.read.latency", "io.write.latency")
        memory_samples: list[int] = []
        wall_start = time.perf_counter()
        tracemalloc.start()
        for cycle in range(args.cycles):
            observed = start + timedelta(seconds=cycle * 5)
            readings = [
                MetricReading(
                    entity=EntityReading("drive", f"wwn:soak:{drive:04d}", f"Drive {drive}"),
                    metric_id=metric,
                    observed_at=observed,
                    value=float(drive + cycle),
                    quality="available",
                    source="soak fixture",
                    collection_interval_seconds=5,
                )
                for drive in range(args.drives)
                for metric in metrics
            ]
            with factory() as session, session.begin():
                ingest(session, readings)
            if cycle % 10 == 0 or cycle == args.cycles - 1:
                memory_samples.append(tracemalloc.get_traced_memory()[0])
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        raw_before = args.drives * args.cycles * len(metrics)
        deleted = 0
        while True:
            with factory() as session, session.begin():
                result = apply_retention(
                    session,
                    now=datetime.now(UTC),
                    recent_hours=48,
                    hourly_days=90,
                    daily_days=730,
                    batch_size=10_000,
                )
            deleted += result["raw_deleted"]
            if result["raw_deleted"] < 10_000:
                break
        with factory() as session:
            raw_after = int(session.scalar(select(func.count()).select_from(MetricSample)) or 0)
        engine.dispose()
        warm = memory_samples[len(memory_samples) // 2 :]
        result = {
            "drives": args.drives,
            "cycles": args.cycles,
            "observations": raw_before,
            "wall_seconds": round(time.perf_counter() - wall_start, 3),
            "peak_python_bytes": peak,
            "warm_memory_min_bytes": min(warm),
            "warm_memory_max_bytes": max(warm),
            "warm_memory_growth_bytes": warm[-1] - warm[0],
            "database_bytes_before_cleanup": path.stat().st_size,
            "raw_deleted": deleted,
            "raw_after_cleanup": raw_after,
        }
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
