from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from hoardarr.core.config import Settings
from hoardarr.integrations.servarr import PinnedServarrClient, ServarrError

MEDIA_PRODUCTS = frozenset({"plex", "jellyfin", "emby"})
MAX_LIBRARIES = 64
MAX_LIBRARY_PATHS = 16


def correlate_library_storage(
    libraries: list[dict[str, Any]],
    storage_groups: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Attach only locally provable Storage Group mappings.

    Media servers commonly report container- or remote-host paths. String
    similarity is not sufficient evidence that such a path belongs to local
    storage, so a mapping is high-confidence only when both paths resolve on
    this host, the library is inside the group namespace, and both have the
    same filesystem device identity.
    """

    groups: list[tuple[dict[str, str], Path, int]] = []
    for group in storage_groups[:256]:
        group_id = group.get("id")
        group_name = group.get("name")
        namespace = group.get("namespace_path")
        if (
            not isinstance(group_id, str)
            or not isinstance(group_name, str)
            or not isinstance(namespace, str)
            or not Path(namespace).is_absolute()
        ):
            continue
        try:
            resolved = Path(namespace).resolve(strict=True)
            groups.append((group, resolved, resolved.stat().st_dev))
        except OSError:
            continue
    result: list[dict[str, Any]] = []
    for library in libraries[:MAX_LIBRARIES]:
        matches: dict[str, tuple[dict[str, str], Path]] = {}
        raw_paths = library.get("paths")
        if not isinstance(raw_paths, list):
            raw_paths = []
        for raw_path in raw_paths[:MAX_LIBRARY_PATHS]:
            if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
                continue
            try:
                candidate = Path(raw_path).resolve(strict=True)
                candidate_device = candidate.stat().st_dev
            except OSError:
                continue
            for group, namespace, namespace_device in groups:
                if candidate_device != namespace_device:
                    continue
                if candidate != namespace and namespace not in candidate.parents:
                    continue
                matches[group["id"]] = (group, namespace)
        mapping: dict[str, Any] = {
            "quality": "not_reported",
            "confidence": "unknown",
            "source": "local_path_not_proven",
            "storage_group_id": None,
            "storage_group_name": None,
            "storage_group_namespace": None,
            "storage_capacity_bytes": None,
            "storage_free_bytes": None,
        }
        if len(matches) == 1:
            group, namespace = next(iter(matches.values()))
            try:
                usage = shutil.disk_usage(namespace)
                capacity = usage.total
                free = usage.free
            except OSError:
                capacity = None
                free = None
            mapping = {
                "quality": "available",
                "confidence": "high",
                "source": "local_path_device_and_namespace",
                "storage_group_id": group["id"],
                "storage_group_name": group["name"],
                "storage_group_namespace": group["namespace_path"],
                "storage_capacity_bytes": capacity,
                "storage_free_bytes": free,
            }
        elif len(matches) > 1:
            mapping["source"] = "ambiguous_local_paths"
        result.append({**library, "storage_mapping": mapping})
    return result


def _text(value: object, *, maximum: int = 512) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:maximum] if cleaned else None


def _paths(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:MAX_LIBRARY_PATHS]:
        if isinstance(item, Mapping):
            path = _text(item.get("path"), maximum=4096)
        else:
            path = _text(item, maximum=4096)
        if path is not None and path not in result:
            result.append(path)
    return result


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000_000:
        return None
    return value


def _plex_libraries(client: PinnedServarrClient) -> tuple[str | None, list[dict[str, Any]]]:
    response = client.get_json("/library/sections")
    container = response.get("MediaContainer") if isinstance(response, Mapping) else None
    if not isinstance(container, Mapping):
        raise ServarrError("invalid_response", "Plex returned an invalid library response")
    version = _text(container.get("version"), maximum=64)
    rows = container.get("Directory")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ServarrError("invalid_response", "Plex returned an invalid library list")
    libraries: list[dict[str, Any]] = []
    for row in rows[:MAX_LIBRARIES]:
        if not isinstance(row, Mapping):
            continue
        key = _text(row.get("key"), maximum=128)
        name = _text(row.get("title"), maximum=256)
        if key is None or name is None:
            continue
        count: int | None = None
        try:
            path = f"/library/sections/{quote(key, safe='')}/all"
            items = client.get_json(
                f"{path}?X-Plex-Container-Start=0&X-Plex-Container-Size=0"
            )
            item_container = items.get("MediaContainer") if isinstance(items, Mapping) else None
            if isinstance(item_container, Mapping):
                count = _count(item_container.get("totalSize"))
                if count is None:
                    count = _count(item_container.get("size"))
        except ServarrError:
            # One unavailable library must not hide the other reported libraries.
            count = None
        libraries.append(
            {
                "id": key,
                "name": name,
                "media_type": _text(row.get("type"), maximum=64) or "Not reported",
                "paths": _paths(row.get("Location")),
                "item_count": count,
                "capacity_bytes": None,
                "quality": "available",
            }
        )
    return version, libraries


def _emby_libraries(
    client: PinnedServarrClient, *, expected_product: str
) -> tuple[str | None, list[dict[str, Any]]]:
    system = client.get_json("/System/Info")
    if not isinstance(system, Mapping):
        raise ServarrError(
            "invalid_response", f"{expected_product.title()} returned invalid system data"
        )
    product_name = str(system.get("ProductName") or system.get("ServerName") or "").casefold()
    if expected_product not in product_name and not (
        expected_product == "jellyfin" and "jellyfin" in product_name
    ):
        raise ServarrError("product_mismatch", "The media endpoint is not the configured product")
    version = _text(system.get("Version"), maximum=64)
    rows = client.get_json("/Library/VirtualFolders")
    if not isinstance(rows, list):
        raise ServarrError(
            "invalid_response",
            f"{expected_product.title()} returned an invalid library list",
        )
    libraries: list[dict[str, Any]] = []
    for row in rows[:MAX_LIBRARIES]:
        if not isinstance(row, Mapping):
            continue
        name = _text(row.get("Name"), maximum=256)
        item_id = _text(row.get("ItemId"), maximum=128)
        if name is None or item_id is None:
            continue
        count: int | None = None
        try:
            query = f"ParentId={quote(item_id, safe='')}&Recursive=true&Limit=0"
            items = client.get_json(f"/Items?{query}&EnableTotalRecordCount=true")
            if isinstance(items, Mapping):
                count = _count(items.get("TotalRecordCount"))
        except ServarrError:
            count = None
        library_options = row.get("LibraryOptions")
        content_type = (
            _text(library_options.get("ContentType"), maximum=64)
            if isinstance(library_options, Mapping)
            else None
        )
        libraries.append(
            {
                "id": item_id,
                "name": name,
                "media_type": content_type or "Not reported",
                "paths": _paths(row.get("Locations")),
                "item_count": count,
                "capacity_bytes": None,
                "quality": "available",
            }
        )
    return version, libraries


def discover_media_server(
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
    if expected_product not in MEDIA_PRODUCTS:
        raise ServarrError("unsupported_product", "The configured media product is unsupported")
    header = "X-Plex-Token" if expected_product == "plex" else "X-Emby-Token"
    with PinnedServarrClient(
        settings=settings,
        base_url=base_url,
        approved_ips=approved_ips,
        allow_localhost=allow_localhost,
        api_key=api_key,
        verify_tls=verify_tls,
        transport=transport,
        api_key_header=header,
        product_label=expected_product.title(),
    ) as client:
        if expected_product == "plex":
            version, libraries = _plex_libraries(client)
        else:
            version, libraries = _emby_libraries(client, expected_product=expected_product)
    return {
        "product": expected_product,
        "version": version,
        "capabilities": ["media_libraries"],
        "state": {
            "status": {"app_name": expected_product.title(), "version": version},
            "libraries": libraries,
        },
    }
