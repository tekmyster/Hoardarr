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
    product: Literal["sonarr", "radarr", "lidarr", "readarr", "whisparr", "prowlarr"]
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
    method: Literal["quick", "hdd_overwrite", "ata_secure_erase", "nvme_sanitize"] | None = None
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


class SnapraidReplacementPreviewRequest(StrictModel):
    pool_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,62}$")
    data_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    replacement_device_id: str = Field(min_length=1, max_length=512)
    filesystem: Literal["ext4", "xfs", "btrfs"] = "ext4"


class SnapraidReplacementApplyRequest(StrictModel):
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
    start_at: datetime | None = None
    maintenance_window_minutes: int | None = Field(default=None, ge=15, le=7 * 24 * 60)


class StorageDrainApplyRequest(StrictModel):
    plan: dict[str, Any]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["I AGREE"]
