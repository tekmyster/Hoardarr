from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from hoardarr.api.dependencies import authenticated_principal, require_state_scope
from hoardarr.api.networking_schemas import ManagedNetworkRequest
from hoardarr.api.onboarding_schemas import NetworkPlanRequest
from hoardarr.api.problem import Problem
from hoardarr.auth.service import Principal
from hoardarr.networking.executor import NetworkFailure, build_plan
from hoardarr.system.network import discover_network_interfaces, normalized_hash

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("")
def onboarding_definition(
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    return {
        "version": 1,
        "steps": [
            "administrator",
            "server",
            "network_discovery",
            "network_redundancy",
            "network_addressing",
            "time",
            "network_test",
            "review",
            "storage_discovery",
        ],
        "defaults": {
            "experience": "guided",
            "server": {
                "hostname": "hoardarr",
                "timezone": "UTC",
                "dst_mode": "automatic",
            },
            "network": {
                "mode": "single",
                "interface_ids": [],
                "addressing": "dhcp",
                "addresses": [],
                "gateway": None,
                "dns_servers": [],
                "vlan_id": None,
                "mtu": 1500,
                "bridge": {"enabled": False, "stp": True, "prefer_rstp": True},
            },
            "ntp": {"servers": ["pool.ntp.org"]},
            "discovery": {
                "lldp": {"enabled": True, "mode": "rx_tx"},
                "cdp": {"receive": True, "smart_transmit": True},
            },
        },
        "apply_available": True,
    }


@router.get("/network/interfaces")
def network_interfaces(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    interfaces = discover_network_interfaces(request.app.state.settings.network_sysfs_root)
    return {
        "items": interfaces,
        "defaults": {
            "lldp": {"enabled": True, "mode": "rx_tx"},
            "cdp": {"receive": True, "smart_transmit": True},
        },
        "captured_from": "sysfs",
        "field_semantics": {
            "speed_mbps": "Current negotiated link speed; null means sysfs did not report it.",
            "model": (
                "Controller model from sysfs or the local udev database; null means unknown."
            ),
        },
    }


@router.post("/network/plan")
def network_plan(
    payload: NetworkPlanRequest,
    request: Request,
    _principal: Principal = Depends(require_state_scope("operate")),
) -> dict[str, object]:
    interfaces = discover_network_interfaces(request.app.state.settings.network_sysfs_root)
    available = {str(item["id"]): item for item in interfaces}
    missing = [item for item in payload.network.interface_ids if item not in available]
    if missing:
        raise Problem(
            409,
            "network_interface_changed",
            "Network hardware changed",
            f"Selected interface(s) are no longer present: {', '.join(missing)}.",
        )

    selected = [available[item] for item in payload.network.interface_ids]
    if payload.experience == "guided" and any(not item.get("is_physical") for item in selected):
        raise Problem(
            422,
            "physical_interface_required",
            "Choose a physical network port",
            "Virtual interfaces are available only in Advanced setup.",
        )
    configuration = ManagedNetworkRequest(host=payload)
    try:
        result = build_plan(
            configuration,
            ["server", "network", "ntp", "discovery"],
            network_sysfs_root=request.app.state.settings.network_sysfs_root,
        )
    except NetworkFailure as exc:
        raise Problem(409, exc.code, "Networking request failed", exc.message) from exc
    result["plan"]["kind"] = "first_run_network"
    result["plan"]["derived"] = {
        "link_strategy": payload.network.mode,
        "lldp_daemon": "lldpd" if payload.discovery.lldp.enabled else None,
        "cdp_policy": (
            "receive_and_transmit_after_neighbor_detection"
            if payload.discovery.cdp.receive and payload.discovery.cdp.smart_transmit
            else "receive_only"
            if payload.discovery.cdp.receive
            else "disabled"
        ),
        "bridge_stp": payload.network.bridge.stp if payload.network.mode == "bridge" else None,
        "prefer_rstp": (
            payload.network.bridge.prefer_rstp if payload.network.mode == "bridge" else None
        ),
        "rollback_timeout_seconds": 120,
        "pre_apply_tests": ["link", "address", "gateway", "dns", "ntp"],
    }
    if any(
        item.get("lldp", {}).get("firmware_ownership") == "verify_before_transmit"
        for item in selected
    ):
        result["plan"]["warnings"].append(
            {
                "code": "lldp_firmware_ownership_check_required",
                "message": (
                    "LLDP firmware ownership will be checked before host transmission so "
                    "DCB/FCoE configuration is not disturbed."
                ),
            }
        )
    result["sha256"] = normalized_hash(result["plan"])
    return result
