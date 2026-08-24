from __future__ import annotations

from typing import Any

from hoardarr.db.models import MetricAlert, MetricEntity


def alert_runbook(alert: MetricAlert, entity: MetricEntity) -> dict[str, Any] | None:
    """Return evidence-aware operator guidance without diagnosing from one counter."""

    if alert.metric_id == "drive.interface_crc_errors":
        return {
            "id": "connection-crc-errors",
            "title": "Check the connection path",
            "summary": (
                "CRC errors often indicate a cable, connector, expander, or controller path "
                "problem before they indicate disk media failure. This counter alone does not "
                "prove the drive is failing."
            ),
            "actions": [
                "Confirm whether the counter continues increasing under normal activity.",
                "Inspect and reseat the relevant cable or connection when it is safe to do so.",
                "Check other devices on the same controller or expander for matching errors.",
            ],
            "evidence": ["interface CRC counter", "stored topology context"],
        }
    if alert.metric_id in {"drive.pending_sectors", "drive.uncorrectable_sectors"}:
        return {
            "id": "unreadable-sector-risk",
            "title": "Protect the data before testing the drive",
            "summary": (
                "The drive reported sectors it could not read reliably. Preserve or evacuate "
                "important data before running an extended health test. A single attribute does "
                "not establish the exact failure cause."
            ),
            "actions": [
                "Confirm current backups or drain important data to healthy storage.",
                "Run the supported SMART extended test after active writes have stopped.",
                "Review the test result and whether pending or uncorrectable counts increase.",
            ],
            "evidence": [alert.metric_id, "SMART/provider observation"],
        }
    if alert.metric_id == "storage.path.state" or entity.entity_type == "storage_path":
        return {
            "id": "storage-path-reduced",
            "title": "Storage path needs attention",
            "summary": (
                "Storage may remain online through another path, but controller/path redundancy "
                "is reduced. Hoardarr has not inferred that the underlying storage failed."
            ),
            "actions": [
                "Confirm at least one healthy path still serves the logical storage.",
                "Inspect the reported controller, HBA, port, cable, and target path.",
                "After recovery, confirm full redundancy and that path flapping has stopped.",
            ],
            "evidence": ["durable path-state transition", "logical-storage topology"],
        }
    if alert.metric_id == "health.overall" and entity.entity_type == "snapraid_configuration":
        return {
            "id": "snapraid-protection-reduced",
            "title": "Refresh or repair SnapRAID protection",
            "summary": (
                "Recent files may not have current parity protection, or a configured member is "
                "unavailable. The reported provider state determines which condition applies."
            ),
            "actions": [
                "Review the SnapRAID member and parity freshness details.",
                "Restore missing members before starting a sync when possible.",
                "Run sync or scrub only after active ARR/download writes are clear.",
            ],
            "evidence": ["SnapRAID provider health state"],
        }
    if alert.metric_id == "health.overall" and entity.entity_type in {"pool", "vdev"}:
        provider = str(entity.labels_json.get("provider", "pool provider"))
        return {
            "id": "pool-protection-reduced",
            "title": "Pool protection is reduced",
            "summary": (
                "The pool provider reports a degraded or faulted state. Hoardarr preserves the "
                "provider's meaning and does not identify a failed member without member evidence."
            ),
            "actions": [
                "Review the pool member and provider error details.",
                "Confirm backups before replacement or repair work.",
                "Use the provider-supported replacement or recovery workflow.",
            ],
            "evidence": [f"health.overall from {provider}"],
        }
    return None
