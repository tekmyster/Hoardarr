from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from hoardarr.api.schemas import StrictModel

HOSTNAME_RE = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
NTP_RE = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.:-]*[A-Za-z0-9])?")


class ServerAnswers(StrictModel):
    hostname: str = Field(min_length=1, max_length=253)
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    dst_mode: Literal["automatic", "standard_time"] = "automatic"

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not HOSTNAME_RE.fullmatch(normalized) or ".." in normalized:
            raise ValueError("hostname must be a valid DNS host name")
        return normalized


class BridgeAnswers(StrictModel):
    enabled: bool = False
    stp: bool = True
    prefer_rstp: bool = True


class NetworkAnswers(StrictModel):
    mode: Literal["single", "active_passive", "lacp", "bridge"] = "single"
    interface_ids: list[str] = Field(min_length=1, max_length=16)
    addressing: Literal["dhcp", "static"] = "dhcp"
    addresses: list[str] = Field(default_factory=list, max_length=16)
    gateway: str | None = Field(default=None, max_length=64)
    dns_servers: list[str] = Field(default_factory=list, max_length=8)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    mtu: int = Field(default=1500, ge=1280, le=9216)
    bridge: BridgeAnswers = Field(default_factory=BridgeAnswers)

    @field_validator("interface_ids")
    @classmethod
    def clean_interfaces(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 64 for value in cleaned):
            raise ValueError("interface identifiers must be 1 to 64 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("interface identifiers must be unique")
        return cleaned

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, values: list[str]) -> list[str]:
        for value in values:
            ipaddress.ip_interface(value)
        return values

    @field_validator("gateway")
    @classmethod
    def validate_gateway(cls, value: str | None) -> str | None:
        if value is not None:
            ipaddress.ip_address(value)
        return value

    @field_validator("dns_servers")
    @classmethod
    def validate_dns(cls, values: list[str]) -> list[str]:
        for value in values:
            ipaddress.ip_address(value)
        return values

    @model_validator(mode="after")
    def validate_addressing(self) -> NetworkAnswers:
        if self.addressing == "static" and not self.addresses:
            raise ValueError("static addressing requires at least one address with a prefix")
        if self.addressing == "dhcp" and (self.addresses or self.gateway):
            raise ValueError("DHCP addressing cannot include static addresses or a gateway")
        return self


class NtpAnswers(StrictModel):
    servers: list[str] = Field(default_factory=lambda: ["pool.ntp.org"], min_length=1, max_length=8)

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        if any(not NTP_RE.fullmatch(value) for value in normalized):
            raise ValueError("NTP servers must be valid host names or addresses")
        return normalized


class LldpAnswers(StrictModel):
    enabled: bool = True
    mode: Literal["rx_tx", "receive_only"] = "rx_tx"


class CdpAnswers(StrictModel):
    receive: bool = True
    smart_transmit: bool = True


class DiscoveryAnswers(StrictModel):
    lldp: LldpAnswers = Field(default_factory=LldpAnswers)
    cdp: CdpAnswers = Field(default_factory=CdpAnswers)


class NetworkPlanRequest(StrictModel):
    experience: Literal["guided", "advanced"] = "guided"
    server: ServerAnswers
    network: NetworkAnswers
    ntp: NtpAnswers = Field(default_factory=NtpAnswers)
    discovery: DiscoveryAnswers = Field(default_factory=DiscoveryAnswers)

    @model_validator(mode="after")
    def guided_policy(self) -> NetworkPlanRequest:
        selected = len(self.network.interface_ids)
        required = 2 if self.network.mode in {"active_passive", "lacp"} else 1
        if selected < required:
            raise ValueError(f"{self.network.mode} requires at least {required} interface(s)")
        if self.network.mode == "single" and selected != 1:
            raise ValueError("single-interface mode requires exactly one interface")
        if self.experience == "guided" and self.network.mode == "bridge":
            raise ValueError("bridge mode is available only in Advanced setup")
        if self.network.mode == "bridge" and not self.network.bridge.enabled:
            raise ValueError("bridge settings must be enabled when bridge mode is selected")
        if self.network.mode != "bridge" and self.network.bridge.enabled:
            raise ValueError("bridge settings require bridge mode")
        if self.network.mode == "bridge" and not self.network.bridge.stp:
            raise ValueError("host spanning tree protection must remain enabled")
        if self.network.mode == "bridge" and selected > 1:
            raise ValueError("bond physical uplinks first, then place the bond in one bridge")
        return self
