from __future__ import annotations

import re
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator

from hoardarr.api.schemas import StrictModel

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class BackupTargetCreateRequest(StrictModel):
    name: str
    provider: Literal[
        "aws_s3",
        "minio",
        "cloudflare_r2",
        "wasabi",
        "backblaze_b2",
        "generic_s3",
    ]
    endpoint_url: str | None = None
    region: str = "us-east-1"
    bucket: str
    prefix: str = "hoardarr"
    access_key_id: SecretStr
    secret_access_key: SecretStr
    session_token: SecretStr | None = None
    force_path_style: bool = False
    verify_tls: bool = True
    allow_private_network: bool = False
    allow_insecure_http: bool = False
    bandwidth_limit_mib: int | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not 1 <= len(cleaned) <= 128 or any(ord(char) < 32 for char in cleaned):
            raise ValueError("name must contain 1-128 printable characters")
        return cleaned

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        cleaned = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", cleaned):
            raise ValueError("region is invalid")
        return cleaned

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        cleaned = value.strip()
        if (
            not BUCKET_RE.fullmatch(cleaned)
            or ".." in cleaned
            or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", cleaned)
        ):
            raise ValueError("bucket must be a valid S3 bucket name")
        return cleaned

    @field_validator("access_key_id", "secret_access_key", "session_token")
    @classmethod
    def validate_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value()
        if not 8 <= len(raw) <= 4096 or any(ord(char) < 32 for char in raw):
            raise ValueError("backup credentials must contain 8-4096 printable characters")
        return value

    @field_validator("bandwidth_limit_mib")
    @classmethod
    def validate_bandwidth(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 10_240:
            raise ValueError("bandwidth limit must be 1-10240 MiB/s")
        return value

    @model_validator(mode="after")
    def validate_network_options(self) -> BackupTargetCreateRequest:
        if self.allow_insecure_http and not self.allow_private_network:
            raise ValueError(
                "insecure HTTP is allowed only for explicitly approved private endpoints"
            )
        if not self.verify_tls and not self.allow_private_network:
            raise ValueError(
                "disabled TLS verification is allowed only for explicitly approved "
                "private endpoints"
            )
        return self


class BackupConfirmationRequest(StrictModel):
    confirmation: Literal["BACK UP HOARDARR"]


class BackupRestoreValidationRequest(StrictModel):
    confirmation: Literal["VALIDATE RESTORE"]


class BackupScheduleRequest(StrictModel):
    enabled: bool
    interval_hours: int = 24

    @field_validator("interval_hours")
    @classmethod
    def validate_interval(cls, value: int) -> int:
        if not 1 <= value <= 720:
            raise ValueError("backup interval must be 1-720 hours")
        return value


class BackupCredentialRotationRequest(StrictModel):
    access_key_id: SecretStr
    secret_access_key: SecretStr
    session_token: SecretStr | None = None

    @field_validator("access_key_id", "secret_access_key", "session_token")
    @classmethod
    def validate_secret(cls, value: SecretStr | None) -> SecretStr | None:
        return BackupTargetCreateRequest.validate_secret(value)
