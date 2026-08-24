from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from hoardarr.fleet.central import FleetCentralSettings, create_central_app
from hoardarr.fleet.migrate import central_database_is_current, upgrade_central_database
from sqlalchemy import create_engine, inspect


def main() -> None:
    database_url = os.environ["HOARDARR_TEST_FLEET_POSTGRES_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    assert not central_database_is_current(engine, database_url)
    upgrade_central_database(database_url)
    assert central_database_is_current(engine, database_url)
    tables = set(inspect(engine).get_table_names())
    assert {"fleet_installations", "fleet_ingested_batches", "fleet_drives"} <= tables

    with tempfile.TemporaryDirectory(prefix="hoardarr-fleet-postgres-") as directory:
        settings = FleetCentralSettings(
            database_url=database_url,
            secret_key_file=Path(directory) / "central.key",
            admin_token="postgres-integration-admin-token-0001",
        )
        with TestClient(create_central_app(settings)) as client:
            assert client.get("/healthz").status_code == 200
            response = client.post(
                "/api/telemetry/v1/register",
                json={
                    "installation_id": str(uuid.uuid4()),
                    "hoardarr_version": "0.3.11",
                    "build_commit": "f" * 40,
                    "schema_version": 1,
                    "platform_family": "linux",
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                },
            )
            assert response.status_code == 201, response.text
            summary = client.get(
                "/api/admin/v1/fleet/summary",
                headers={"X-Hoardarr-Admin-Token": settings.admin_token},
            )
            assert summary.status_code == 200, summary.text
            assert summary.json()["active_installations"] == 1
    engine.dispose()


if __name__ == "__main__":
    main()
