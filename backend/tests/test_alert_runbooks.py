from __future__ import annotations

from hoardarr.db.models import MetricAlert, MetricEntity, new_id, utc_now
from hoardarr.telemetry.runbooks import alert_runbook


def alert(metric_id: str) -> MetricAlert:
    now = utc_now()
    return MetricAlert(
        id=new_id(),
        rule_id="test-rule",
        entity_id="entity",
        metric_id=metric_id,
        severity="warning",
        state="active",
        trigger_value=1,
        threshold_json={},
        topology_json={},
        details_json={},
        started_at=now,
        last_seen_at=now,
    )


def entity(entity_type: str, *, provider: str | None = None) -> MetricEntity:
    return MetricEntity(
        id="entity",
        entity_type=entity_type,
        stable_id=f"test:{entity_type}",
        display_name="Test entity",
        labels_json={"provider": provider} if provider else {},
        topology_json={},
    )


def test_crc_runbook_does_not_diagnose_disk_failure() -> None:
    runbook = alert_runbook(alert("drive.interface_crc_errors"), entity("drive"))
    assert runbook is not None
    assert "cable" in runbook["summary"]
    assert "does not prove" in runbook["summary"]


def test_pending_sector_runbook_prioritizes_data_before_testing() -> None:
    runbook = alert_runbook(alert("drive.pending_sectors"), entity("drive"))
    assert runbook is not None
    assert runbook["actions"][0].startswith("Confirm current backups")
    assert "extended health test" in runbook["summary"]


def test_path_runbook_preserves_online_but_reduced_distinction() -> None:
    runbook = alert_runbook(alert("storage.path.state"), entity("storage_path"))
    assert runbook is not None
    assert "remain online" in runbook["summary"]
    assert "underlying storage failed" in runbook["summary"]


def test_pool_and_snapraid_guidance_preserve_provider_meaning() -> None:
    pool = alert_runbook(alert("health.overall"), entity("pool", provider="zfs"))
    snapraid = alert_runbook(alert("health.overall"), entity("snapraid_configuration"))
    assert pool is not None and "provider reports" in pool["summary"]
    assert pool["evidence"] == ["health.overall from zfs"]
    assert snapraid is not None and "parity protection" in snapraid["summary"]


def test_unknown_metric_has_no_invented_runbook() -> None:
    assert alert_runbook(alert("io.read.iops"), entity("drive")) is None
