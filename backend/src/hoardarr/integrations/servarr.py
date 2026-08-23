from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from hoardarr.core.config import Settings
from hoardarr.integrations.url_policy import IntegrationTarget, revalidate_approved_target


@dataclass(frozen=True)
class ProductDefinition:
    api_prefix: str
    app_names: frozenset[str]
    support_level: str
    declared_capabilities: frozenset[str]


PRODUCTS: dict[str, ProductDefinition] = {
    "sonarr": ProductDefinition(
        "/api/v3",
        frozenset({"sonarr"}),
        "supported",
        frozenset({"root_folders", "remote_path_mappings", "download_clients", "activity"}),
    ),
    "radarr": ProductDefinition(
        "/api/v3",
        frozenset({"radarr"}),
        "supported",
        frozenset({"root_folders", "remote_path_mappings", "download_clients", "activity"}),
    ),
    "lidarr": ProductDefinition(
        "/api/v1",
        frozenset({"lidarr"}),
        "supported_with_profile_selection",
        frozenset({"root_folders", "remote_path_mappings", "download_clients", "activity"}),
    ),
    "readarr": ProductDefinition(
        "/api/v1",
        frozenset({"readarr"}),
        "legacy_opt_in",
        frozenset({"root_folders", "remote_path_mappings", "download_clients", "activity"}),
    ),
    "whisparr": ProductDefinition(
        "/api/v3",
        frozenset({"whisparr"}),
        "experimental_opt_in",
        frozenset({"root_folders", "remote_path_mappings", "download_clients", "activity"}),
    ),
    "prowlarr": ProductDefinition(
        "/api/v1",
        frozenset({"prowlarr"}),
        "discovery_only",
        frozenset(),
    ),
}


class ServarrError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


class PinnedServarrClient:
    def __init__(
        self,
        *,
        settings: Settings,
        base_url: str,
        approved_ips: list[str],
        allow_localhost: bool,
        api_key: str,
        verify_tls: bool,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = base_url
        self.approved_ips = list(approved_ips)
        self.allow_localhost = allow_localhost
        self.target = revalidate_approved_target(
            base_url,
            approved_ips,
            settings,
            allow_localhost=allow_localhost,
        )
        self.api_key = api_key
        self._client = httpx.Client(
            verify=verify_tls,
            timeout=httpx.Timeout(settings.integration_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            http1=True,
            http2=False,
            transport=transport,
        )

    def __enter__(self) -> PinnedServarrClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    @staticmethod
    def _host_header(target: IntegrationTarget) -> str:
        host = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
        default_port = 443 if target.scheme == "https" else 80
        return host if target.port == default_port else f"{host}:{target.port}"

    def _network_url(self, path: str) -> str:
        address = ipaddress.ip_address(self.target.resolved_ips[0])
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"{self.target.scheme}://{host}:{self.target.port}{self.target.base_path}{path}"

    def get_json(self, path: str) -> Any:
        return self.request_json("GET", path)

    def request_json(self, method: str, path: str, payload: Any | None = None) -> Any:
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ServarrError("invalid_request", "Unsupported Servarr request method")
        # Re-resolve before every request. The network connection still uses the
        # approved literal address, preventing DNS rebinding between validation
        # and connection establishment.
        self.target = revalidate_approved_target(
            self.base_url,
            self.approved_ips,
            self.settings,
            allow_localhost=self.allow_localhost,
        )
        deadline = time.monotonic() + self.settings.integration_timeout_seconds
        request = self._client.build_request(
            method,
            self._network_url(path),
            headers={
                "Host": self._host_header(self.target),
                "X-Api-Key": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            extensions={"sni_hostname": self.target.hostname},
        )
        try:
            response = self._client.send(request, stream=True)
            try:
                if 300 <= response.status_code < 400:
                    raise ServarrError("redirect_refused", "Servarr returned an untrusted redirect")
                if response.status_code in {401, 403}:
                    raise ServarrError(
                        "authentication_failed", "Servarr rejected the API credential"
                    )
                if response.status_code == 404:
                    raise ServarrError(
                        "capability_missing", "Servarr does not expose this API capability"
                    )
                if response.status_code >= 400:
                    raise ServarrError(
                        "remote_error", f"Servarr returned HTTP {response.status_code}"
                    )
                if response.status_code == 204 or method == "DELETE":
                    return None
                content_type = response.headers.get("content-type", "").lower()
                if "json" not in content_type:
                    raise ServarrError("invalid_response", "Servarr returned a non-JSON response")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise ServarrError(
                            "connection_failed", "Servarr exceeded the total request deadline"
                        )
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ServarrError("response_too_large", "Servarr response exceeded 2 MiB")
                    chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise ServarrError(
                        "connection_failed", "Servarr exceeded the total request deadline"
                    )
            finally:
                response.close()
        except ServarrError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
            raise ServarrError("connection_failed", "Servarr could not be reached safely") from exc
        try:
            return json.loads(b"".join(chunks), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ServarrError("invalid_response", "Servarr returned invalid JSON") from exc


def _minimal_roots(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ServarrError("invalid_response", "Servarr root folders were not a list")
    return [
        {"id": item.get("id"), "path": item.get("path"), "free_space": item.get("freeSpace")}
        for item in value
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]


def _minimal_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ServarrError("invalid_response", "Servarr remote path mappings were not a list")
    return [
        {
            "id": item.get("id"),
            "host": item.get("host"),
            "remote_path": item.get("remotePath"),
            "local_path": item.get("localPath"),
        }
        for item in value
        if isinstance(item, dict)
    ]


def _minimal_clients(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ServarrError("invalid_response", "Servarr download clients were not a list")
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "implementation": item.get("implementation"),
            "config_contract": item.get("configContract"),
            "enabled": item.get("enable"),
        }
        for item in value
        if isinstance(item, dict)
    ]


def _minimal_schemas(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ServarrError("invalid_response", "Servarr client schemas were not a list")
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), list) else []
        result.append(
            {
                "implementation": item.get("implementation"),
                "config_contract": item.get("configContract"),
                "protocol": item.get("protocol"),
                "field_names": sorted(
                    field["name"]
                    for field in fields
                    if isinstance(field, dict) and isinstance(field.get("name"), str)
                ),
            }
        )
    return result


_DOWNLOADING_STATES = frozenset({"downloading"})
_IMPORTING_STATES = frozenset({"importpending", "importing"})
_PENDING_STATES = frozenset({"queued", "delay", "paused"})
_STALLED_STATES = frozenset({"warning", "error", "failed"})


def _minimal_activity(value: Any) -> dict[str, Any]:
    """Return bounded, title-free write activity from a Servarr queue response."""

    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ServarrError("invalid_response", "Servarr queue response is incomplete")
    records = value["records"]
    total = value.get("totalRecords")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        total = len(records)
    if total > len(records):
        return {
            "quality": "temporarily_unavailable",
            "reason": "The bounded queue response did not include every active item.",
            "reported_items": len(records),
            "total_items": total,
            "active_writes": 0,
            "downloading": 0,
            "importing": 0,
            "pending": 0,
            "stalled": 0,
        }
    counts = {"downloading": 0, "importing": 0, "pending": 0, "stalled": 0}
    for item in records[:1000]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").casefold()
        tracked = str(item.get("trackedDownloadState") or "").casefold()
        if tracked in _IMPORTING_STATES:
            counts["importing"] += 1
        elif status in _DOWNLOADING_STATES:
            counts["downloading"] += 1
        elif status in _PENDING_STATES:
            counts["pending"] += 1
        elif status in _STALLED_STATES:
            counts["stalled"] += 1
    return {
        "quality": "available",
        "reported_items": len(records),
        "total_items": total,
        "active_writes": counts["downloading"] + counts["importing"],
        **counts,
    }


def discover_servarr(
    *,
    settings: Settings,
    expected_product: str,
    base_url: str,
    approved_ips: list[str],
    allow_localhost: bool,
    api_key: str,
    verify_tls: bool,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    definition = PRODUCTS.get(expected_product)
    if definition is None:
        raise ServarrError("unsupported_product", "Unsupported Servarr product")
    with PinnedServarrClient(
        settings=settings,
        base_url=base_url,
        approved_ips=approved_ips,
        allow_localhost=allow_localhost,
        api_key=api_key,
        verify_tls=verify_tls,
        transport=transport,
    ) as client:
        status = client.get_json(f"{definition.api_prefix}/system/status")
        if not isinstance(status, dict) or not isinstance(status.get("appName"), str):
            raise ServarrError("invalid_response", "Servarr status response is incomplete")
        product = status["appName"].strip().casefold()
        if product not in definition.app_names:
            raise ServarrError(
                "product_mismatch",
                f"Expected {expected_product}, but the endpoint identified itself as {product}",
            )
        capabilities: list[str] = []
        state: dict[str, Any] = {
            "status": {
                "app_name": status.get("appName"),
                "instance_name": status.get("instanceName"),
                "version": status.get("version"),
                "url_base": status.get("urlBase"),
                "is_docker": status.get("isDocker"),
                "is_linux": status.get("isLinux"),
                "is_windows": status.get("isWindows"),
            }
        }
        reads = (
            ("root_folders", "/rootfolder", _minimal_roots),
            ("remote_path_mappings", "/remotepathmapping", _minimal_mappings),
            ("download_clients", "/downloadclient", _minimal_clients),
            ("download_clients", "/downloadclient/schema", _minimal_schemas),
        )
        for capability, suffix, sanitizer in reads:
            if capability not in definition.declared_capabilities:
                continue
            try:
                remote = client.get_json(f"{definition.api_prefix}{suffix}")
            except ServarrError as exc:
                if exc.code == "capability_missing":
                    continue
                raise
            key = "download_client_schemas" if suffix.endswith("schema") else capability
            state[key] = sanitizer(remote)
            if capability not in capabilities:
                capabilities.append(capability)
        if "activity" in definition.declared_capabilities:
            try:
                queue = client.get_json(
                    f"{definition.api_prefix}/queue?page=1&pageSize=1000&sortDirection=ascending"
                )
                activity = _minimal_activity(queue)
            except ServarrError as exc:
                if exc.code == "capability_missing":
                    activity = {"quality": "unsupported", "active_writes": 0}
                else:
                    raise
            state["activity"] = activity
            if activity["quality"] == "available":
                state["active_writes"] = activity["active_writes"]
            capabilities.append("activity")
        return {
            "product": expected_product,
            "version": status.get("version"),
            "api_prefix": definition.api_prefix,
            "support_level": definition.support_level,
            "capabilities": sorted(capabilities),
            "state": state,
        }


def discover_servarr_activity(
    *,
    settings: Settings,
    expected_product: str,
    base_url: str,
    approved_ips: list[str],
    allow_localhost: bool,
    api_key: str,
    verify_tls: bool,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Probe only bounded write-sensitive activity for the durable worker cadence."""

    definition = PRODUCTS.get(expected_product)
    if definition is None:
        raise ServarrError("unsupported_product", "Unsupported Servarr product")
    if "activity" not in definition.declared_capabilities:
        return {"product": expected_product, "activity": {"quality": "unsupported"}}
    with PinnedServarrClient(
        settings=settings,
        base_url=base_url,
        approved_ips=approved_ips,
        allow_localhost=allow_localhost,
        api_key=api_key,
        verify_tls=verify_tls,
        transport=transport,
    ) as client:
        status = client.get_json(f"{definition.api_prefix}/system/status")
        if not isinstance(status, dict) or not isinstance(status.get("appName"), str):
            raise ServarrError("invalid_response", "Servarr status response is incomplete")
        product = status["appName"].strip().casefold()
        if product not in definition.app_names:
            raise ServarrError(
                "product_mismatch",
                f"Expected {expected_product}, but the endpoint identified itself as {product}",
            )
        queue = client.get_json(
            f"{definition.api_prefix}/queue?page=1&pageSize=1000&sortDirection=ascending"
        )
        return {"product": expected_product, "activity": _minimal_activity(queue)}


def normalize_mutation_plan(product: str, value: Any) -> dict[str, Any]:
    definition = PRODUCTS.get(product)
    if definition is None:
        raise ServarrError("unsupported_product", "Unsupported Servarr product")
    if not isinstance(value, dict):
        raise ServarrError("invalid_plan", "Servarr change plan must be an object")
    allowed = {
        "product",
        "api_prefix",
        "root_folders",
        "remote_path_mappings",
        "download_clients",
    }
    if set(value) - allowed:
        raise ServarrError("invalid_plan", "Servarr change plan contains unknown fields")
    if (
        value.get("product", product) != product
        or value.get("api_prefix", definition.api_prefix) != definition.api_prefix
    ):
        raise ServarrError("invalid_plan", "Servarr change plan targets a different product")
    roots: list[dict[str, Any]] = []
    for item in value.get("root_folders", []):
        if not isinstance(item, dict) or set(item) - {"path", "profile_ids"}:
            raise ServarrError("invalid_plan", "Root-folder change is invalid")
        path = item.get("path")
        if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
            raise ServarrError("invalid_plan", "Root-folder path must be an absolute path")
        profiles = item.get("profile_ids", {})
        if not isinstance(profiles, dict) or not all(
            key in {"qualityProfileId", "metadataProfileId"}
            and isinstance(identifier, int)
            and identifier > 0
            for key, identifier in profiles.items()
        ):
            raise ServarrError("invalid_plan", "Root-folder profiles are invalid")
        if product in {"lidarr", "readarr"} and not {
            "qualityProfileId",
            "metadataProfileId",
        }.issubset(profiles):
            raise ServarrError(
                "profile_required",
                f"{product} requires explicit quality and metadata profiles",
            )
        roots.append({"path": path.rstrip("/") or "/", "profile_ids": dict(profiles)})
    mappings: list[dict[str, str]] = []
    for item in value.get("remote_path_mappings", []):
        if not isinstance(item, dict) or set(item) != {"host", "remote_path", "local_path"}:
            raise ServarrError("invalid_plan", "Remote-path change is invalid")
        if not all(isinstance(item[key], str) and item[key] for key in item):
            raise ServarrError("invalid_plan", "Remote-path values are required")
        mappings.append(
            {
                "host": item["host"].strip().casefold(),
                "remote_path": item["remote_path"],
                "local_path": item["local_path"],
            }
        )
    allowed_download_fields = {
        "sonarr": frozenset({"category", "tvCategory"}),
        "radarr": frozenset({"category", "movieCategory"}),
        "lidarr": frozenset({"category", "musicCategory"}),
        "readarr": frozenset({"category", "bookCategory"}),
        "whisparr": frozenset({"category"}),
        "prowlarr": frozenset({"category"}),
    }[product]
    clients: list[dict[str, Any]] = []
    for item in value.get("download_clients", []):
        if not isinstance(item, dict) or set(item) != {"id", "fields"}:
            raise ServarrError("invalid_plan", "Download-client change is invalid")
        if (
            not isinstance(item["id"], int)
            or item["id"] <= 0
            or not isinstance(item["fields"], dict)
        ):
            raise ServarrError("invalid_plan", "Download-client change is invalid")
        fields: dict[str, str] = {}
        for name, field_value in item["fields"].items():
            if not isinstance(name, str) or not name or not isinstance(field_value, str):
                raise ServarrError("invalid_plan", "Download-client field is invalid")
            if name not in allowed_download_fields:
                raise ServarrError(
                    "download_field_refused",
                    "This download-client field is outside storage onboarding",
                )
            if (
                not field_value
                or len(field_value) > 256
                or any(ord(character) < 32 for character in field_value)
            ):
                raise ServarrError("invalid_plan", "Download-client field value is invalid")
            fields[name] = field_value
        clients.append({"id": item["id"], "fields": fields})
    if product == "prowlarr" and (roots or mappings):
        raise ServarrError(
            "capability_missing", "Prowlarr does not manage media root folders or remote paths"
        )
    return {
        "product": product,
        "api_prefix": definition.api_prefix,
        "root_folders": roots,
        "remote_path_mappings": mappings,
        "download_clients": clients,
    }


def apply_servarr_plan(
    *,
    settings: Settings,
    expected_product: str,
    base_url: str,
    approved_ips: list[str],
    allow_localhost: bool,
    api_key: str,
    verify_tls: bool,
    plan: dict[str, Any],
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Apply an exact, normalized plan and compensate creations after partial failure."""
    normalized = normalize_mutation_plan(expected_product, plan)
    prefix = normalized["api_prefix"]
    applied: list[dict[str, Any]] = []
    compensation: list[tuple[str, str, Any | None]] = []
    with PinnedServarrClient(
        settings=settings,
        base_url=base_url,
        approved_ips=approved_ips,
        allow_localhost=allow_localhost,
        api_key=api_key,
        verify_tls=verify_tls,
        transport=transport,
    ) as client:
        try:
            existing_roots = (
                _minimal_roots(client.get_json(f"{prefix}/rootfolder"))
                if normalized["root_folders"]
                else []
            )
            for root in normalized["root_folders"]:
                if any(item["path"].rstrip("/") == root["path"] for item in existing_roots):
                    applied.append(
                        {"type": "root_folder", "path": root["path"], "state": "unchanged"}
                    )
                    continue
                created = client.request_json(
                    "POST",
                    f"{prefix}/rootfolder",
                    {"path": root["path"], **root["profile_ids"]},
                )
                if not isinstance(created, dict) or not isinstance(created.get("id"), int):
                    raise ServarrError(
                        "invalid_response", "Servarr did not return a created root folder"
                    )
                compensation.append(("DELETE", f"{prefix}/rootfolder/{created['id']}", None))
                applied.append({"type": "root_folder", "path": root["path"], "state": "created"})
            existing_mappings = (
                _minimal_mappings(client.get_json(f"{prefix}/remotepathmapping"))
                if normalized["remote_path_mappings"]
                else []
            )
            for mapping in normalized["remote_path_mappings"]:
                if any(
                    item.get("host", "").casefold() == mapping["host"]
                    and item.get("remote_path") == mapping["remote_path"]
                    and item.get("local_path") == mapping["local_path"]
                    for item in existing_mappings
                ):
                    applied.append({"type": "remote_path_mapping", **mapping, "state": "unchanged"})
                    continue
                created = client.request_json(
                    "POST",
                    f"{prefix}/remotepathmapping",
                    {
                        "host": mapping["host"],
                        "remotePath": mapping["remote_path"],
                        "localPath": mapping["local_path"],
                    },
                )
                if not isinstance(created, dict) or not isinstance(created.get("id"), int):
                    raise ServarrError(
                        "invalid_response", "Servarr did not return a created mapping"
                    )
                compensation.append(("DELETE", f"{prefix}/remotepathmapping/{created['id']}", None))
                applied.append({"type": "remote_path_mapping", **mapping, "state": "created"})
            for change in normalized["download_clients"]:
                path = f"{prefix}/downloadclient/{change['id']}"
                current = client.get_json(path)
                if not isinstance(current, dict) or not isinstance(current.get("fields"), list):
                    raise ServarrError("invalid_response", "Download-client response is incomplete")
                original = json.loads(json.dumps(current))
                by_name = {
                    field.get("name"): field
                    for field in current["fields"]
                    if isinstance(field, dict) and isinstance(field.get("name"), str)
                }
                missing = set(change["fields"]) - set(by_name)
                if missing:
                    raise ServarrError(
                        "schema_changed", "Download-client schema changed before apply"
                    )
                for name, field_value in change["fields"].items():
                    by_name[name]["value"] = field_value
                client.request_json("PUT", path, current)
                compensation.append(("PUT", path, original))
                applied.append(
                    {
                        "type": "download_client",
                        "id": change["id"],
                        "fields": sorted(change["fields"]),
                        "state": "updated",
                    }
                )
        except Exception as apply_error:
            rollback_failed = False
            for method, path, payload in reversed(compensation):
                try:
                    client.request_json(method, path, payload)
                except Exception:
                    rollback_failed = True
            if rollback_failed:
                raise ServarrError(
                    "partial_failure_needs_attention",
                    "Servarr changes failed and compensation did not fully complete",
                ) from apply_error
            raise
    return {"product": expected_product, "applied": applied, "state": "completed"}
