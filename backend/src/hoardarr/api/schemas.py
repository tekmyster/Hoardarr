from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupClaimRequest(StrictModel):
    token: SecretStr = Field(min_length=16, max_length=256)
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=1)


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1)
    remember_me: bool = False


class TokenCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[Literal["read", "operate", "admin"]] = Field(min_length=1, max_length=3)
    expires_at: datetime | None = None


class MediaAccountProvisionRequest(StrictModel):
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    credential_mode: Literal["generate", "provide"]
    password: SecretStr | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def validate_credential(self) -> MediaAccountProvisionRequest:
        password = self.password.get_secret_value() if self.password is not None else None
        if self.credential_mode == "generate" and password is not None:
            raise ValueError("password must be omitted when Hoardarr generates it")
        if self.credential_mode == "provide" and not password:
            raise ValueError("password is required when setting it yourself")
        if password is not None and any(character in password for character in "\r\n\0"):
            raise ValueError("password cannot contain line breaks or null characters")
        return self


class IntegrationResolveRequest(StrictModel):
    base_url: str = Field(min_length=8, max_length=2048)
    allow_localhost: bool = False


class IntegrationCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    product: Literal[
        "sonarr",
        "radarr",
        "lidarr",
        "readarr",
        "whisparr",
        "prowlarr",
        "plex",
        "jellyfin",
        "emby",
    ]
    base_url: str = Field(min_length=8, max_length=2048)
    api_key: SecretStr = Field(min_length=8, max_length=1024)
    verify_tls: bool = True
    allow_localhost: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class WizardCreateRequest(StrictModel):
    workflow: Literal["storage_setup"] = "storage_setup"
    mode: Literal["guided", "simple", "advanced"] = "guided"
    hardware_snapshot_id: str | None = Field(default=None, max_length=36)


class TopologyExpectationCreateRequest(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=128)
    confirmation: Literal["SAVE"]

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class TopologyExpectationRemoveRequest(StrictModel):
    confirmation: Literal["REMOVE"]


class HardwareLocateRequest(StrictModel):
    device_id: str = Field(min_length=1, max_length=512)
    enabled: bool = True
    duration_seconds: int = Field(default=300, ge=10, le=300)


class TopologyPlanCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    template_id: Literal[
        "generic-8-bay",
        "generic-12-bay",
        "generic-24-bay-shelf",
        "generic-dual-path-shelf",
    ]

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class TopologyPlanController(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=128)
    state: Literal["existing", "planned"]


class TopologyPlanEnclosure(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=128)
    bay_count: int = Field(ge=1, le=1024)
    controller_ids: list[str] = Field(min_length=1, max_length=16)


class TopologyPlanChassis(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=128)


class TopologyPlanChange(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    kind: Literal["disk_addition", "disk_retirement"]
    label: str = Field(min_length=1, max_length=128)
    enclosure_id: str | None = Field(default=None, max_length=64)
    slot: int | None = Field(default=None, ge=1, le=1024)
    capacity_bytes: int | None = Field(default=None, ge=1, le=8 * 1024**5)
    stable_device_id: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_target(self) -> TopologyPlanChange:
        if self.kind == "disk_addition" and (self.enclosure_id is None or self.slot is None):
            raise ValueError("a planned disk addition requires an enclosure and bay")
        if self.kind == "disk_retirement" and not self.stable_device_id:
            raise ValueError("a planned retirement requires the existing stable drive identity")
        return self


class TopologyPlanDocumentRequest(StrictModel):
    schema_version: Literal[1]
    chassis: TopologyPlanChassis
    controllers: list[TopologyPlanController] = Field(min_length=1, max_length=16)
    enclosures: list[TopologyPlanEnclosure] = Field(min_length=1, max_length=64)
    changes: list[TopologyPlanChange] = Field(default_factory=list, max_length=4096)
    notes: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def validate_graph(self) -> TopologyPlanDocumentRequest:
        controller_ids = [item.id for item in self.controllers]
        enclosure_ids = [item.id for item in self.enclosures]
        change_ids = [item.id for item in self.changes]
        if len(set(controller_ids)) != len(controller_ids):
            raise ValueError("controller IDs must be unique")
        if len(set(enclosure_ids)) != len(enclosure_ids):
            raise ValueError("enclosure IDs must be unique")
        if len(set(change_ids)) != len(change_ids):
            raise ValueError("change IDs must be unique")
        controller_set = set(controller_ids)
        enclosure_set = set(enclosure_ids)
        for enclosure in self.enclosures:
            if not set(enclosure.controller_ids) <= controller_set:
                raise ValueError("every enclosure controller must exist in this plan")
        planned_slots: set[tuple[str, int]] = set()
        for change in self.changes:
            if change.enclosure_id is not None and change.enclosure_id not in enclosure_set:
                raise ValueError("every planned bay must belong to an enclosure in this plan")
            if change.kind == "disk_addition":
                enclosure = next(item for item in self.enclosures if item.id == change.enclosure_id)
                if change.slot is None or change.slot > enclosure.bay_count:
                    raise ValueError("planned disk bay exceeds the enclosure bay count")
                key = (enclosure.id, change.slot)
                if key in planned_slots:
                    raise ValueError("only one planned disk may occupy an enclosure bay")
                planned_slots.add(key)
        return self


class TopologyPlanUpdateRequest(StrictModel):
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=128)
    plan: TopologyPlanDocumentRequest

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class TopologyPlanRemoveRequest(StrictModel):
    confirmation: Literal["REMOVE"]


class WizardStepRequest(StrictModel):
    revision: int = Field(ge=0)
    answers: dict[str, Any]


class WizardPlanRequest(StrictModel):
    revision: int = Field(ge=0)


class WizardPlanApprovalRequest(StrictModel):
    revision: int = Field(ge=0)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hardware_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_device_ids: list[str] = Field(min_length=1, max_length=1024)
    confirmation: str = Field(min_length=1, max_length=32)


class ShareAclEntryRequest(StrictModel):
    kind: Literal["user", "group"]
    name: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    role: Literal["administrator", "media_application", "media_user"]


class ConnectivityServiceRequest(StrictModel):
    protocol: Literal["smb", "nfs", "iscsi", "fcoe"]
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
    path: str | None = Field(default=None, max_length=4096)
    read_only: bool = False
    browseable: bool = True
    valid_users: list[str] = Field(default_factory=list, max_length=64)
    write_users: list[str] = Field(default_factory=list, max_length=64)
    read_users: list[str] = Field(default_factory=list, max_length=64)
    acl_entries: list[ShareAclEntryRequest] = Field(default_factory=list, max_length=128)
    inherit_acl: bool = True
    clients: list[str] = Field(default_factory=list, max_length=64)
    backing_path: str | None = Field(default=None, max_length=4096)
    size_bytes: int | None = Field(default=None, ge=1024**3, le=8 * 1024**5)
    target_iqn: str | None = Field(default=None, max_length=223)
    portal_ips: list[str] = Field(default_factory=list, max_length=16)
    initiator_iqns: list[str] = Field(default_factory=list, max_length=64)
    chap_enabled: bool = True
    chap_username: str | None = Field(default=None, max_length=63)
    chap_password: SecretStr | None = Field(default=None, min_length=12, max_length=255)
    generate_chap_password: bool = False
    interfaces: list[str] = Field(default_factory=list, max_length=8)
    fcoe_mode: Literal["fabric", "vn2vn"] = "fabric"
    dcb_mode: Literal["auto", "host", "firmware", "none"] = "auto"
    auto_vlan: bool = True
    fip_responder: bool = False
    initiator_wwpns: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_protocol_fields(self) -> ConnectivityServiceRequest:
        from hoardarr.connectivity.service import normalize_connectivity_request

        normalize_connectivity_request(self, require_secret=False)
        return self


class ConnectivityDeleteRequest(StrictModel):
    confirmation: Literal["I AGREE"]
    delete_backing_data: bool = False


class ServarrApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["APPLY"]


class UpdateApplyRequest(StrictModel):
    metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["APPLY"]


class AddonInstallRequest(StrictModel):
    package_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    manifest: dict[str, Any]
    signature: str = Field(min_length=40, max_length=1024)
    key_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")
    approved_privileges: list[str] = Field(max_length=32)


class AddonLifecycleRequest(StrictModel):
    action: Literal["enable", "disable", "remove"]


class TierTransferPreviewRequest(StrictModel):
    workload: Literal["torrent", "usenet"]
    source: str = Field(min_length=2, max_length=4096)
    destination: str = Field(min_length=2, max_length=4096)
    method: Literal["auto", "copy", "move", "hardlink"] = "auto"
    retain_until: Literal["seeding_complete", "manual", "never", "import_complete"] | None = None
    cleanup: bool = True
    completed_steps: list[
        Literal["download_complete", "download", "repair", "unpack", "verify"]
    ] = Field(default_factory=list, max_length=4)


class TierTransferApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["APPLY"]


class TierTransferCleanupRequest(StrictModel):
    confirmation: Literal["APPLY"]


class DeviceMaintenancePreviewRequest(StrictModel):
    device_id: str = Field(min_length=1, max_length=512)
    action: Literal["wipe", "sector_conversion"]
    method: (
        Literal[
            "quick",
            "metadata_clear",
            "hdd_overwrite",
            "ata_secure_erase",
            "nvme_sanitize",
            "nvme_crypto_erase",
            "scsi_sanitize",
            "scsi_crypto_erase",
        ]
        | None
    ) = None
    passes: int = Field(default=1, ge=1, le=7)
    target_logical_bytes: Literal[512, 4096] | None = None

    @model_validator(mode="after")
    def validate_action_options(self) -> DeviceMaintenancePreviewRequest:
        if self.action == "wipe" and self.method is None:
            raise ValueError("method is required for a wipe")
        if self.action == "sector_conversion" and self.target_logical_bytes is None:
            raise ValueError("target_logical_bytes is required for sector conversion")
        if self.action == "wipe" and self.target_logical_bytes is not None:
            raise ValueError("target_logical_bytes applies only to sector conversion")
        if self.action == "sector_conversion" and self.method is not None:
            raise ValueError("method applies only to a wipe")
        return self


class DeviceMaintenanceApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["I AGREE"]


class ForeignInspectionPreviewRequest(StrictModel):
    candidate_id: str = Field(pattern=r"^foreign:[0-9a-f]{24}$")


class ForeignStackPreviewRequest(StrictModel):
    candidate_id: str = Field(pattern=r"^foreign:[0-9a-f]{24}$")


class UnraidAssignmentEvidence(StrictModel):
    slot: str = Field(pattern=r"^(?:parity2?|disk(?:[1-9]|1[0-9]|2[0-8]))$")
    role: Literal["data", "parity"]
    serial: str = Field(min_length=1, max_length=256)
    wwn: str | None = Field(default=None, min_length=1, max_length=256)
    eui64: str | None = Field(default=None, min_length=1, max_length=256)
    nguid: str | None = Field(default=None, min_length=1, max_length=256)
    capacity_bytes: int | None = Field(default=None, ge=1)
    filesystem_type: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_slot_role(self) -> UnraidAssignmentEvidence:
        expected = "parity" if self.slot.startswith("parity") else "data"
        if self.role != expected:
            raise ValueError(f"{self.slot} must have role {expected}")
        if self.role == "parity" and self.filesystem_type is not None:
            raise ValueError("parity evidence must not claim a filesystem")
        return self


class UnraidEvidenceRequest(StrictModel):
    schema_version: Literal[1]
    source: Literal["unraid_runtime_state"]
    captured_at: datetime
    unraid_version: str | None = Field(default=None, min_length=1, max_length=64)
    assignments: list[UnraidAssignmentEvidence] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_assignments(self) -> UnraidEvidenceRequest:
        slots = [item.slot for item in self.assignments]
        identities = [
            (item.wwn or "").strip().casefold()
            or (item.eui64 or "").strip().casefold()
            or (item.nguid or "").strip().casefold()
            or item.serial.strip().casefold()
            for item in self.assignments
        ]
        if len(slots) != len(set(slots)):
            raise ValueError("assignment slots must be unique")
        if len(identities) != len(set(identities)):
            raise ValueError("assignment identities must be unique")
        if sum(item.role == "parity" for item in self.assignments) > 2:
            raise ValueError("Unraid supports at most two parity assignments")
        return self


class ForeignInspectionApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["INSPECT READ ONLY"]


class ForeignMigrationPreviewRequest(StrictModel):
    candidate_id: str = Field(pattern=r"^foreign:[0-9a-f]{24}$")
    destination_backend_id: str = Field(min_length=36, max_length=36)
    verification_mode: Literal["fast", "accurate"] = "accurate"
    collision_policy: Literal["stop", "reuse_identical"] = "stop"
    reserve_bytes: int = Field(default=1_073_741_824, ge=0, le=10**15)
    selection: dict[str, Any] | None = None


class ForeignMigrationApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["COPY AND VERIFY"]


class SnapraidReplacementPreviewRequest(StrictModel):
    pool_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,62}$")
    data_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    replacement_device_id: str = Field(min_length=1, max_length=512)
    filesystem: Literal["ext4", "xfs", "btrfs"] = "ext4"


class SnapraidReplacementApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["I AGREE"]


class ArrayReplacementPreviewRequest(StrictModel):
    target_id: str = Field(pattern=r"^(?:zfs:[A-Za-z][A-Za-z0-9_.:-]{0,254}|md:md[0-9]+)$")
    old_member_path: str | None = Field(default=None, min_length=5, max_length=4096)
    replacement_device_id: str = Field(min_length=1, max_length=512)


class ArrayReplacementApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["I AGREE"]


class StorageRedundancyPreviewRequest(StrictModel):
    storage_entity_id: str = Field(min_length=36, max_length=36)
    action: Literal["add", "remove", "replace", "configure"]
    path_identity: str | None = Field(default=None, min_length=3, max_length=512)
    remove_path_identity: str | None = Field(default=None, min_length=3, max_length=512)
    policy: Literal["recommended", "failover", "multibus", "group_by_prio"] = "recommended"
    settings: dict[str, Any] | None = None


class StorageRedundancyApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["APPLY"]


class StorageGroupCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    namespace_path: str = Field(min_length=2, max_length=4096)
    purpose: Literal["media", "downloads", "archive", "backup", "general"] = "media"


class PhysicalDiskObservationRequest(StrictModel):
    stable_identity: str = Field(min_length=3, max_length=512)
    kernel_path: str | None = Field(default=None, max_length=4096)
    serial: str | None = Field(default=None, max_length=256)
    wwn: str | None = Field(default=None, max_length=256)
    vendor: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)
    capacity_bytes: int | None = Field(default=None, ge=0)
    logical_sector_bytes: int | None = Field(default=None, ge=128, le=65536)
    physical_sector_bytes: int | None = Field(default=None, ge=128, le=65536)
    media_type: Literal["hdd", "ssd", "nvme", "removable", "unknown"] | None = None
    health_state: Literal["healthy", "warning", "critical", "not_reported", "unsupported"] = (
        "not_reported"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class PhysicalDiskReconcileRequest(StrictModel):
    items: list[PhysicalDiskObservationRequest] = Field(min_length=1, max_length=1024)


class PhysicalDiskReservationRequest(StrictModel):
    action: Literal["reserve", "release"]


class StorageBackendAssignRequest(StrictModel):
    physical_disk_id: str | None = Field(default=None, min_length=36, max_length=36)
    storage_entity_id: str | None = Field(default=None, min_length=36, max_length=36)
    namespace_path: str | None = Field(default=None, min_length=2, max_length=4096)
    role: Literal["data", "parity", "cache", "archive", "landing"] = "data"


class StorageBackendTransitionRequest(StrictModel):
    target_state: Literal[
        "active",
        "preferred_write",
        "draining",
        "verifying",
        "read_only",
        "retired",
        "reuse_ready",
        "wipe_pending",
    ]
    reason: str | None = Field(default=None, max_length=512)


class StorageBackendActivationRequest(StrictModel):
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=512)


class StorageBackendRetirementRequest(StrictModel):
    action: Literal["release_for_reuse"]
    confirmation: Literal["RELEASE"]
    reason: str | None = Field(default=None, max_length=512)


class StorageDrainPreviewRequest(StrictModel):
    source_backend_id: str = Field(min_length=36, max_length=36)
    destination_backend_ids: list[str] = Field(min_length=1, max_length=64)
    verification_mode: Literal["fast", "accurate", "paranoid"] = "accurate"
    reserve_bytes: int = Field(default=1_073_741_824, ge=0, le=10**15)
    enforce_source_read_only: bool = False
    bandwidth_limit_mib_per_second: int | None = Field(default=None, ge=1, le=10_240)
    io_priority: Literal["normal", "background", "idle"] = "normal"
    start_at: datetime | None = None
    maintenance_window_minutes: int | None = Field(default=None, ge=15, le=7 * 24 * 60)


class StorageDrainApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["I AGREE"]
