from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from hoardarr.api.onboarding_schemas import NetworkPlanRequest
from hoardarr.api.schemas import StrictModel

HOST_RE = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.:-]*[A-Za-z0-9])?")


class SyslogSettings(StrictModel):
    enabled: bool = False
    server: str | None = Field(default=None, max_length=253)
    transport: Literal["udp", "tcp"] = "udp"
    port: int = Field(default=514, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_destination(self) -> SyslogSettings:
        if self.enabled and (not self.server or not HOST_RE.fullmatch(self.server)):
            raise ValueError("a valid syslog server is required")
        return self


class SnmpSettings(StrictModel):
    enabled: bool = False
    community: str | None = Field(default=None, min_length=1, max_length=255)
    allowed_managers: list[str] = Field(default_factory=list, max_length=32)
    location: str = Field(default="", max_length=255)
    contact: str = Field(default="", max_length=255)

    @field_validator("allowed_managers")
    @classmethod
    def validate_managers(cls, values: list[str]) -> list[str]:
        normalized = [str(ipaddress.ip_network(value, strict=False)) for value in values]
        return sorted(set(normalized))

    @field_validator("community")
    @classmethod
    def validate_community(cls, value: str | None) -> str | None:
        if value is not None and (any(character.isspace() for character in value) or "\0" in value):
            raise ValueError("SNMP community cannot contain whitespace")
        return value

    @field_validator("location", "contact")
    @classmethod
    def validate_single_line(cls, value: str) -> str:
        if any(character in value for character in "\r\n\0"):
            raise ValueError("SNMP identity values must be one line")
        return value

    @model_validator(mode="after")
    def validate_credentials(self) -> SnmpSettings:
        if self.enabled and (not self.community or not self.allowed_managers):
            raise ValueError("SNMP requires a community and at least one allowed manager")
        return self


class TrapDestination(StrictModel):
    server: str = Field(max_length=253)
    port: int = Field(default=162, ge=1, le=65535)
    community: str = Field(min_length=1, max_length=255)

    @field_validator("server")
    @classmethod
    def validate_server(cls, value: str) -> str:
        if not HOST_RE.fullmatch(value):
            raise ValueError("trap server must be a valid host name or address")
        return value.lower()

    @field_validator("community")
    @classmethod
    def validate_community(cls, value: str) -> str:
        if any(character.isspace() for character in value) or "\0" in value:
            raise ValueError("trap community cannot contain whitespace")
        return value


class TrapSettings(StrictModel):
    enabled: bool = False
    destinations: list[TrapDestination] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_destinations(self) -> TrapSettings:
        if self.enabled and not self.destinations:
            raise ValueError("at least one trap destination is required")
        return self


class AccessRule(StrictModel):
    source: str = Field(max_length=64)
    destination: str = Field(default="this_server", max_length=64)
    protocol: Literal["ssh", "http", "https", "smb", "nfs", "iscsi", "fcoe"]
    action: Literal["allow", "deny"]

    @field_validator("source", "destination")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if normalized in {"any", "this_server"}:
            return normalized
        return str(ipaddress.ip_network(value, strict=False))

    @model_validator(mode="after")
    def validate_fcoe_source(self) -> AccessRule:
        if self.source == "this_server" and self.destination == "this_server":
            raise ValueError("source and destination cannot both be this server")
        if self.protocol == "fcoe" and (self.source != "any" or self.destination != "this_server"):
            raise ValueError(
                "FCoE host rules use any to this server; use Nexus zoning for WWPN access"
            )
        return self


class ManagedNetworkRequest(StrictModel):
    host: NetworkPlanRequest
    syslog: SyslogSettings = Field(default_factory=SyslogSettings)
    snmp: SnmpSettings = Field(default_factory=SnmpSettings)
    traps: TrapSettings = Field(default_factory=TrapSettings)
    access_rules: list[AccessRule] = Field(default_factory=list, max_length=128)


NetworkComponent = Literal[
    "server",
    "network",
    "ntp",
    "discovery",
    "syslog",
    "snmp",
    "traps",
    "access_rules",
]


class ManagedNetworkPlanRequest(StrictModel):
    configuration: ManagedNetworkRequest
    changed_components: list[NetworkComponent] = Field(default_factory=list, max_length=8)


class ManagedNetworkApplyRequest(StrictModel):
    configuration: ManagedNetworkRequest
    changed_components: list[NetworkComponent] = Field(min_length=1, max_length=8)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["APPLY"]


class ManagedNetworkConfirmRequest(StrictModel):
    token: str = Field(pattern=r"^[0-9a-f]{32}$")
