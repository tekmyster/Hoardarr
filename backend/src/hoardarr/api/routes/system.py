from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import authenticated_principal, database_session
from hoardarr.api.serializers import integration_document, operation_document
from hoardarr.auth.service import Principal
from hoardarr.db.models import HardwareSnapshot, IntegrationConnection, Operation
from hoardarr.storage.inventory import discover_storage_inventory
from hoardarr.storage.telemetry import storage_telemetry
from hoardarr.system.overview import (
    collect_host_metrics,
    collect_neighbor_discovery,
    collect_resource_metrics,
    summarize_storage,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
def status(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    return {
        "application": "Hoardarr",
        "version": request.app.version,
        "api_version": "v1",
        "environment": request.app.state.settings.environment,
        "database_ready": request.app.state.database_ready,
        "frontend_available": request.app.state.frontend_available,
    }


@router.get("/capabilities")
def capabilities(_principal: Principal = Depends(authenticated_principal)) -> dict[str, object]:
    return {
        "first_run_onboarding": {
            "status": "available",
            "network_apply": True,
            "link_modes": ["single", "active_passive", "lacp"],
            "advanced_link_modes": ["bridge"],
            "neighbor_discovery": ["lldp", "cdp"],
        },
        "hardware_inventory": {"status": "available", "storage_mutation": True},
        "storage_setup_wizard": {"status": "available", "storage_mutation": True},
        "servarr": {
            "status": "discovery_and_writes_available",
            "products": ["sonarr", "radarr", "lidarr", "readarr", "whisparr", "prowlarr"],
            "writes_enabled": True,
        },
        "storage_apply": {
            "status": "available_for_complete_plans",
            "blockers": [],
            "layouts": ["individual", "mergerfs", "zfs", "raid", "snapraid", "cache"],
        },
        "updates": {"status": "signed_update_available"},
        "addons": {"status": "signed_local_runtime_available", "marketplace": False},
    }


@router.get("/resources")
def resources(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Return only fast-changing host usage for lightweight dashboard polling."""

    metrics = collect_resource_metrics()
    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    live_storage = discover_storage_inventory()
    performance = storage_telemetry.sample(
        hardware_snapshot=snapshot.payload_json if snapshot is not None else None,
        pools=live_storage["pools"]["items"],
    )
    return {
        "captured_at": metrics["captured_at"],
        "source": "live",
        "cpu": metrics["cpu"],
        "memory": metrics["memory"],
        "network": {"interfaces": metrics["network_interfaces"]},
        "storage": {"system_volume": metrics["boot_volume"], "performance": performance},
    }


@router.get("/overview")
def overview(
    request: Request,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Return one read-only dashboard document made only from live sources."""

    host = collect_host_metrics()
    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    operation_query = select(Operation).order_by(Operation.created_at.desc()).limit(8)
    if not principal.is_admin:
        operation_query = operation_query.where(Operation.actor_id == principal.user_id)
    operations = list(session.scalars(operation_query))
    integrations = list(
        session.scalars(select(IntegrationConnection).order_by(IntegrationConnection.created_at))
    )
    storage = summarize_storage(snapshot.payload_json if snapshot is not None else None)
    live_storage = discover_storage_inventory()
    storage.update(
        {
            "snapshot": (
                {
                    "id": snapshot.id,
                    "captured_at": snapshot.captured_at,
                    "source": snapshot.source,
                }
                if snapshot is not None
                else None
            ),
            "pools": live_storage["pools"],
            "shares": live_storage["shares"],
        }
    )
    failed_operations = [
        item for item in operations if item.status in {"failed", "needs_attention"}
    ]
    critical_drives = (storage.get("health") or {}).get("critical", 0)
    warning_drives = (storage.get("health") or {}).get("warning", 0)
    alerts: list[dict[str, object]] = []
    if critical_drives:
        alerts.append(
            {
                "severity": "critical",
                "message": f"{critical_drives} drive(s) report a critical health state.",
                "source": "latest_hardware_snapshot",
            }
        )
    if warning_drives:
        alerts.append(
            {
                "severity": "warning",
                "message": f"{warning_drives} drive(s) report a warning health state.",
                "source": "latest_hardware_snapshot",
            }
        )
    for operation in failed_operations:
        alerts.append(
            {
                "severity": "warning",
                "message": f"{operation.kind} is {operation.status.replace('_', ' ')}.",
                "source": "operation",
                "operation_id": operation.id,
            }
        )

    network_interfaces = host.pop("network_interfaces")
    return {
        "captured_at": host.pop("captured_at"),
        "source": "live",
        "system": {
            **host,
            "application": "Hoardarr",
            "version": request.app.version,
            "database_ready": request.app.state.database_ready,
        },
        "storage": storage,
        "network": {
            "interfaces": network_interfaces,
            "discovery": collect_neighbor_discovery(),
        },
        "activity": {"operations": [operation_document(item) for item in operations]},
        "applications": {"connections": [integration_document(item) for item in integrations]},
        "alerts": alerts,
    }
