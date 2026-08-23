from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from hoardarr.core.config import Settings
from hoardarr.integrations.media import discover_media_server


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'media.db').as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
    )


def test_plex_libraries_use_pinned_json_api_and_preserve_missing_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hoardarr.integrations.url_policy._resolve",
        lambda _hostname, _port, _timeout: ("10.20.30.50",),
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/library/sections"):
            value = {
                "MediaContainer": {
                    "version": "1.42.0",
                    "Directory": [
                        {
                            "key": "1",
                            "title": "Movies",
                            "type": "movie",
                            "Location": [{"path": "/data/media/Movies"}],
                        }
                    ],
                }
            }
        else:
            value = {"MediaContainer": {"totalSize": 4020}}
        return httpx.Response(200, headers={"content-type": "application/json"}, json=value)

    result = discover_media_server(
        settings=_settings(tmp_path),
        expected_product="plex",
        base_url="https://plex.internal:32400",
        approved_ips=["10.20.30.50"],
        allow_localhost=False,
        api_key="plex-secret",
        verify_tls=True,
        transport=httpx.MockTransport(handler),
    )
    library = result["state"]["libraries"][0]
    assert result["product"] == "plex"
    assert library == {
        "id": "1",
        "name": "Movies",
        "media_type": "movie",
        "paths": ["/data/media/Movies"],
        "item_count": 4020,
        "capacity_bytes": None,
        "quality": "available",
    }
    assert all(request.url.host == "10.20.30.50" for request in seen)
    assert all(request.headers["host"] == "plex.internal:32400" for request in seen)
    assert all(request.headers["x-plex-token"] == "plex-secret" for request in seen)


@pytest.mark.parametrize("product", ["jellyfin", "emby"])
def test_jellyfin_and_emby_collect_read_only_libraries(
    tmp_path: Path, product: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hoardarr.integrations.url_policy._resolve",
        lambda _hostname, _port, _timeout: ("10.20.30.60",),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/System/Info"):
            value: object = {"ProductName": product.title(), "Version": "10.10.0"}
        elif request.url.path.endswith("/Library/VirtualFolders"):
            value = [{
                "Name": "TV",
                "ItemId": "library-tv",
                "Locations": ["/data/media/TV"],
                "LibraryOptions": {"ContentType": "tvshows"},
            }]
        else:
            value = {"TotalRecordCount": 1234, "Items": []}
        return httpx.Response(200, headers={"content-type": "application/json"}, json=value)

    result = discover_media_server(
        settings=_settings(tmp_path),
        expected_product=product,
        base_url=f"http://{product}.internal:8096",
        approved_ips=["10.20.30.60"],
        allow_localhost=False,
        api_key="media-secret",
        verify_tls=True,
        transport=httpx.MockTransport(handler),
    )
    assert result["state"]["libraries"][0]["item_count"] == 1234
    assert result["state"]["libraries"][0]["capacity_bytes"] is None


def test_malformed_media_provider_output_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hoardarr.integrations.url_policy._resolve",
        lambda _hostname, _port, _timeout: ("10.20.30.70",),
    )
    with pytest.raises(Exception, match="invalid library response"):
        discover_media_server(
            settings=_settings(tmp_path),
            expected_product="plex",
            base_url="http://plex.internal:32400",
            approved_ips=["10.20.30.70"],
            allow_localhost=False,
            api_key="media-secret",
            verify_tls=True,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, headers={"content-type": "application/json"}, json={"unexpected": []}
                )
            ),
        )
