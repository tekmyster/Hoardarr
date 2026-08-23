from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from hoardarr.core.config import Settings
from hoardarr.integrations.servarr import PinnedServarrClient, ServarrError, discover_servarr
from hoardarr.integrations.url_policy import (
    IntegrationTargetError,
    normalize_and_resolve_target,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://10.0.0.5:8989",
        "http://user:password@10.0.0.5:8989",
        "http://10.0.0.5:8989/path?apiKey=secret",
        "http://10.0.0.5:8989/path/%252e%252e/admin",
        "http://10.0.0.5:8989/sonarr%3fapi=foo",
        "http://10.0.0.5:8989/sonarr%23fragment",
        "http://10.0.0.5:8989/%00",
        "http://10.0.0.5:8989/path//nested",
    ],
)
def test_target_policy_rejects_ambiguous_or_credentialed_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(IntegrationTargetError):
        normalize_and_resolve_target(url, _settings(tmp_path), allow_localhost=False)


def test_target_policy_rejects_public_and_unapproved_loopback(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(IntegrationTargetError, match="outside"):
        normalize_and_resolve_target("http://8.8.8.8", settings, allow_localhost=False)
    with pytest.raises(IntegrationTargetError, match="outside"):
        normalize_and_resolve_target("http://127.0.0.1:8989", settings, allow_localhost=False)
    target = normalize_and_resolve_target(
        "http://127.0.0.1:8989/sonarr/", settings, allow_localhost=True
    )
    assert target.base_url == "http://127.0.0.1:8989/sonarr"


def test_servarr_uses_pinned_ip_original_host_and_fixed_api_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "hoardarr.integrations.url_policy._resolve",
        lambda _hostname, _port, _timeout: ("10.20.30.40",),
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/system/status"):
            value: object = {
                "appName": "Sonarr",
                "instanceName": "Sonarr",
                "version": "4.0.0",
                "urlBase": "/sonarr",
                "isLinux": True,
            }
        elif path.endswith("/queue"):
            value = {"totalRecords": 0, "records": []}
        else:
            value = []
        return httpx.Response(200, headers={"Content-Type": "application/json"}, json=value)

    result = discover_servarr(
        settings=settings,
        expected_product="sonarr",
        base_url="https://sonarr.internal:8989/sonarr",
        approved_ips=["10.20.30.40"],
        allow_localhost=False,
        api_key="private-key",
        verify_tls=True,
        transport=httpx.MockTransport(handler),
    )
    assert result["api_prefix"] == "/api/v3"
    assert result["product"] == "sonarr"
    assert seen
    for request in seen:
        assert request.url.host == "10.20.30.40"
        assert request.headers["host"] == "sonarr.internal:8989"
        assert request.headers["x-api-key"] == "private-key"
        assert request.url.path.startswith("/sonarr/api/v3/")


@pytest.mark.parametrize(
    ("product", "app_name", "prefix", "support_level"),
    [
        ("sonarr", "Sonarr", "/api/v3", "supported"),
        ("radarr", "Radarr", "/api/v3", "supported"),
        ("lidarr", "Lidarr", "/api/v1", "supported_with_profile_selection"),
        ("readarr", "Readarr", "/api/v1", "legacy_opt_in"),
        ("whisparr", "Whisparr", "/api/v3", "experimental_opt_in"),
        ("prowlarr", "Prowlarr", "/api/v1", "discovery_only"),
    ],
)
def test_each_declared_servarr_product_has_a_verified_discovery_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    product: str,
    app_name: str,
    prefix: str,
    support_level: str,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "hoardarr.integrations.url_policy._resolve",
        lambda _hostname, _port, _timeout: ("10.20.30.40",),
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/system/status"):
            value: object = {"appName": app_name, "version": "fixture-1", "isLinux": True}
        elif request.url.path.endswith("/rootfolder"):
            value = [{"id": 1, "path": "/srv/hoardarr/media", "freeSpace": 42}]
        elif request.url.path.endswith("/remotepathmapping"):
            value = [
                {
                    "id": 2,
                    "host": "downloader",
                    "remotePath": "/downloads",
                    "localPath": "/srv/hoardarr/downloads",
                }
            ]
        elif request.url.path.endswith("/downloadclient/schema"):
            value = [
                {
                    "implementation": "FixtureClient",
                    "configContract": "FixtureSettings",
                    "protocol": "torrent",
                    "fields": [{"name": "category"}],
                }
            ]
        elif request.url.path.endswith("/queue"):
            value = {
                "totalRecords": 4,
                "records": [
                    {"status": "downloading", "title": "must not persist"},
                    {"status": "completed", "trackedDownloadState": "importPending"},
                    {"status": "queued"},
                    {"status": "warning"},
                ],
            }
        else:
            value = [
                {
                    "id": 3,
                    "name": "Downloader",
                    "implementation": "FixtureClient",
                    "configContract": "FixtureSettings",
                    "enable": True,
                }
            ]
        return httpx.Response(200, headers={"Content-Type": "application/json"}, json=value)

    result = discover_servarr(
        settings=settings,
        expected_product=product,
        base_url=f"https://{product}.internal:8989",
        approved_ips=["10.20.30.40"],
        allow_localhost=False,
        api_key="fixture-secret",
        verify_tls=True,
        transport=httpx.MockTransport(handler),
    )

    assert result["product"] == product
    assert result["version"] == "fixture-1"
    assert result["api_prefix"] == prefix
    assert result["support_level"] == support_level
    assert requests[0].endswith(f"{prefix}/system/status")
    if product == "prowlarr":
        assert result["capabilities"] == []
        assert len(requests) == 1
    else:
        assert result["capabilities"] == [
            "activity",
            "download_clients",
            "remote_path_mappings",
            "root_folders",
        ]
        assert result["state"]["active_writes"] == 2
        assert result["state"]["activity"] == {
            "quality": "available",
            "reported_items": 4,
            "total_items": 4,
            "active_writes": 2,
            "downloading": 1,
            "importing": 1,
            "pending": 1,
            "stalled": 1,
        }
        assert "must not persist" not in str(result)
        assert result["state"]["download_client_schemas"][0]["field_names"] == ["category"]


def test_dns_is_revalidated_before_each_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    answers = iter([("10.0.0.20",), ("10.0.0.99",)])

    def resolver(_hostname: str, _port: int, _timeout: float) -> tuple[str, ...]:
        return next(answers)

    monkeypatch.setattr("hoardarr.integrations.url_policy._resolve", resolver)
    with (
        PinnedServarrClient(
            settings=settings,
            base_url="http://sonarr.internal:8989",
            approved_ips=["10.0.0.20"],
            allow_localhost=False,
            api_key="private-key",
            verify_tls=True,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, headers={"Content-Type": "application/json"}, json={}
                )
            ),
        ) as client,
        pytest.raises(IntegrationTargetError, match="unapproved"),
    ):
        client.get_json("/api/v3/system/status")


def test_servarr_errors_do_not_echo_credentials(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    secret = "must-not-appear"
    with (
        PinnedServarrClient(
            settings=settings,
            base_url="http://127.0.0.1:8989",
            approved_ips=["127.0.0.1"],
            allow_localhost=True,
            api_key=secret,
            verify_tls=True,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    401, headers={"Content-Type": "application/json"}, json={"error": secret}
                )
            ),
        ) as client,
        pytest.raises(ServarrError) as caught,
    ):
        client.get_json("/api/v3/system/status")
    assert secret not in str(caught.value)


def test_servarr_rejects_non_standard_non_finite_json(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with (
        PinnedServarrClient(
            settings=settings,
            base_url="http://127.0.0.1:8989",
            approved_ips=["127.0.0.1"],
            allow_localhost=True,
            api_key="private-key",
            verify_tls=True,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b'{"freeSpace":NaN}',
                )
            ),
        ) as client,
        pytest.raises(ServarrError) as caught,
    ):
        client.get_json("/api/v3/rootfolder")
    assert caught.value.code == "invalid_response"
