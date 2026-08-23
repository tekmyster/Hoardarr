from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_INTEGRATION_NETWORKS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)
DEFAULT_TRUSTED_PROXIES = ("127.0.0.1", "::1")


class Settings(BaseSettings):
    """Process configuration loaded exclusively from HOARDARR_* variables."""

    model_config = SettingsConfigDict(
        env_prefix="HOARDARR_",
        env_file=None,
        case_sensitive=False,
        extra="forbid",
    )

    environment: str = "production"
    database_url: str = "sqlite:////var/lib/hoardarr/hoardarr.db"
    secret_key_file: Path = Path("/var/lib/hoardarr/secret.key")
    setup_token: SecretStr | None = None
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=7877, ge=1, le=65535)
    frontend_dir: Path = Path("/usr/lib/hoardarr/current/frontend")
    session_ttl_seconds: int = Field(default=43_200, ge=300, le=2_592_000)
    remembered_session_ttl_seconds: int = Field(default=2_592_000, ge=300, le=31_536_000)
    secure_cookies: bool = True
    allowed_origins: tuple[str, ...] = ()
    trusted_proxy_addresses: tuple[str, ...] = DEFAULT_TRUSTED_PROXIES
    max_request_body_bytes: int = Field(default=1024 * 1024, ge=16 * 1024, le=16 * 1024 * 1024)
    authentication_concurrency: int = Field(default=2, ge=1, le=8)
    hardware_detector: Path = Path("/usr/lib/hoardarr/scripts/detect-hardware.py")
    account_executor_socket: Path = Path("/run/hoardarr/account-executor.sock")
    account_executor_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    storage_executor_socket: Path = Path("/run/hoardarr/storage-executor.sock")
    storage_status_socket: Path = Path("/run/hoardarr/storage-status.sock")
    storage_executor_timeout_seconds: float = Field(default=1_209_600.0, ge=60.0, le=2_592_000.0)
    snapraid_config_root: Path = Path("/etc/snapraid")
    connectivity_executor_timeout_seconds: float = Field(default=120.0, ge=5.0, le=600.0)
    network_sysfs_root: Path = Path("/sys")
    hardware_scan_timeout_seconds: int = Field(default=30, ge=5, le=300)
    hardware_scan_output_limit_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=64 * 1024,
        le=64 * 1024 * 1024,
    )
    integration_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    integration_activity_interval_seconds: int = Field(default=30, ge=10, le=300)
    integration_allowed_networks: tuple[str, ...] = DEFAULT_INTEGRATION_NETWORKS
    worker_poll_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    update_channel: str = "stable"
    update_metadata_url: str = (
        "https://github.com/tekmyster/Hoardarr/releases/latest/download/update.json"
    )
    update_signature_url: str = (
        "https://github.com/tekmyster/Hoardarr/releases/latest/download/update.json.sig"
    )
    update_trust_file: Path = Path("/etc/hoardarr/update-trust.json")
    update_artifact_root: Path = Path("/var/lib/hoardarr/update-artifacts")
    addon_inbox: Path = Path("/var/lib/hoardarr/addon-inbox")
    addon_root: Path = Path("/usr/lib/hoardarr/addons")
    addon_trust_file: Path = Path("/etc/hoardarr/addon-trust.json")
    addon_unit_root: Path = Path("/etc/systemd/system")
    telemetry_license_file: Path = Path("/etc/hoardarr/telemetry-license.json")
    telemetry_license_trust_file: Path = Path("/etc/hoardarr/telemetry-license-trust.json")
    installation_identity_file: Path = Path("/etc/machine-id")
    telemetry_fast_interval_seconds: int = Field(default=5, ge=2, le=60)
    telemetry_device_interval_seconds: int = Field(default=300, ge=60, le=3600)
    telemetry_hardware_interval_seconds: int = Field(default=900, ge=300, le=86400)
    telemetry_recent_retention_hours: int = Field(default=48, ge=1, le=168)
    telemetry_hourly_retention_days: int = Field(default=90, ge=7, le=730)
    telemetry_daily_retention_days: int = Field(default=730, ge=30, le=3650)
    telemetry_max_query_points: int = Field(default=1200, ge=100, le=50000)
    telemetry_max_graph_series: int = Field(default=16, ge=1, le=128)
    telemetry_max_query_observations: int = Field(default=20000, ge=1000, le=500000)
    telemetry_max_query_days: int = Field(default=730, ge=1, le=3650)
    telemetry_cleanup_batch_size: int = Field(default=10000, ge=1000, le=100000)
    telemetry_rollup_percentile_samples: int = Field(default=20000, ge=100, le=100000)
    telemetry_collector_workers: int = Field(default=4, ge=1, le=16)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"production", "development", "test"}:
            raise ValueError("environment must be production, development, or test")
        return normalized

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith(("sqlite:///", "sqlite+pysqlite:///")):
            raise ValueError("this Hoardarr release supports SQLite database URLs only")
        return value

    @field_validator("integration_allowed_networks")
    @classmethod
    def validate_networks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("at least one integration network must be allowed")
        for value in values:
            ipaddress.ip_network(value, strict=False)
        return values

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError("allowed origins must be absolute http or https origins")
            if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
                raise ValueError("allowed origins cannot include paths, credentials, or queries")
            normalized.append(f"{parts.scheme}://{parts.netloc}".lower())
        return tuple(normalized)

    @field_validator("trusted_proxy_addresses")
    @classmethod
    def validate_trusted_proxies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(str(ipaddress.ip_address(value)) for value in values)

    @field_validator("update_channel")
    @classmethod
    def validate_update_channel(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"stable", "beta"}:
            raise ValueError("update channel must be stable or beta")
        return normalized

    @field_validator("update_metadata_url", "update_signature_url")
    @classmethod
    def validate_update_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.hostname != "github.com" or parts.username:
            raise ValueError("update metadata must use the configured GitHub HTTPS origin")
        return value

    @property
    def session_cookie_name(self) -> str:
        return "__Host-hoardarr_session" if self.secure_cookies else "hoardarr_session"

    @property
    def csrf_cookie_name(self) -> str:
        return "__Host-hoardarr_csrf" if self.secure_cookies else "hoardarr_csrf"

    @property
    def allowed_integration_networks(
        self,
    ) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(value, strict=False) for value in self.integration_allowed_networks
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
