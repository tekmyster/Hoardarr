from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from hoardarr.fleet.central import FleetCentralSettings, create_central_app


def central_main() -> None:
    database_url = os.environ.get("HOARDARR_FLEET_DATABASE_URL")
    key_file = os.environ.get("HOARDARR_FLEET_SECRET_KEY_FILE")
    admin_token = os.environ.get("HOARDARR_FLEET_ADMIN_TOKEN")
    if not database_url or not key_file:
        raise SystemExit(
            "HOARDARR_FLEET_DATABASE_URL and HOARDARR_FLEET_SECRET_KEY_FILE are required"
        )
    if not admin_token or len(admin_token) < 32:
        raise SystemExit("HOARDARR_FLEET_ADMIN_TOKEN must contain at least 32 characters")
    app = create_central_app(
        FleetCentralSettings(
            database_url=database_url,
            secret_key_file=Path(key_file),
            admin_token=admin_token,
        )
    )
    uvicorn.run(
        app,
        host=os.environ.get("HOARDARR_FLEET_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("HOARDARR_FLEET_BIND_PORT", "8091")),
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    central_main()
