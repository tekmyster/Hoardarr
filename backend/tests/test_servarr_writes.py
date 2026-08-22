from __future__ import annotations

import json

import httpx
import pytest

from hoardarr.core.config import Settings
from hoardarr.integrations.servarr import ServarrError, apply_servarr_plan, normalize_mutation_plan


def _settings(tmp_path):  # type: ignore[no-untyped-def]
    return Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        secret_key_file=tmp_path / "secret.key",
        frontend_dir=tmp_path,
    )


def _transport(*, fail_mapping: bool = False) -> httpx.MockTransport:
    state = {
        "roots": [],
        "mappings": [],
        "client": {"id": 4, "fields": [{"name": "category", "value": "old"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if path.endswith("/rootfolder") and method == "GET":
            return httpx.Response(200, json=state["roots"])
        if path.endswith("/rootfolder") and method == "POST":
            item = {"id": 11, **json.loads(request.content)}
            state["roots"].append(item)
            return httpx.Response(201, json=item)
        if path.endswith("/rootfolder/11") and method == "DELETE":
            state["roots"].clear()
            return httpx.Response(204)
        if path.endswith("/remotepathmapping") and method == "GET":
            return httpx.Response(200, json=state["mappings"])
        if path.endswith("/remotepathmapping") and method == "POST":
            if fail_mapping:
                return httpx.Response(500, json={"error": "secret remote detail"})
            item = {"id": 12, **json.loads(request.content)}
            state["mappings"].append(item)
            return httpx.Response(201, json=item)
        if path.endswith("/downloadclient/4") and method == "GET":
            return httpx.Response(200, json=state["client"])
        if path.endswith("/downloadclient/4") and method == "PUT":
            state["client"] = json.loads(request.content)
            return httpx.Response(202, json=state["client"])
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def test_product_aware_plan_rejects_missing_profiles_and_prowlarr_roots() -> None:
    with pytest.raises(ServarrError) as exc:
        normalize_mutation_plan("lidarr", {"root_folders": [{"path": "/media/Music"}]})
    assert exc.value.code == "profile_required"
    with pytest.raises(ServarrError) as exc:
        normalize_mutation_plan("prowlarr", {"root_folders": [{"path": "/media"}]})
    assert exc.value.code == "capability_missing"


def test_servarr_write_applies_roots_mappings_and_download_fields(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = apply_servarr_plan(
        settings=_settings(tmp_path),
        expected_product="radarr",
        base_url="http://127.0.0.1:7878",
        approved_ips=["127.0.0.1"],
        allow_localhost=True,
        api_key="never-return-this-key",
        verify_tls=False,
        transport=_transport(),
        plan={
            "root_folders": [{"path": "/mnt/hoardarr/media/Movies"}],
            "remote_path_mappings": [
                {
                    "host": "Downloader",
                    "remote_path": "/downloads",
                    "local_path": "/mnt/hoardarr/downloads",
                }
            ],
            "download_clients": [{"id": 4, "fields": {"category": "movies"}}],
        },
    )
    assert result["state"] == "completed"
    assert [item["type"] for item in result["applied"]] == [
        "root_folder",
        "remote_path_mapping",
        "download_client",
    ]
    assert "never-return-this-key" not in json.dumps(result)


def test_servarr_write_compensates_created_root_on_partial_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ServarrError) as exc:
        apply_servarr_plan(
            settings=_settings(tmp_path),
            expected_product="sonarr",
            base_url="http://127.0.0.1:8989",
            approved_ips=["127.0.0.1"],
            allow_localhost=True,
            api_key="never-log-this-key",
            verify_tls=False,
            transport=_transport(fail_mapping=True),
            plan={
                "root_folders": [{"path": "/mnt/hoardarr/media/TV"}],
                "remote_path_mappings": [
                    {
                        "host": "downloader",
                        "remote_path": "/downloads",
                        "local_path": "/mnt/hoardarr/downloads",
                    }
                ],
            },
        )
    assert exc.value.code == "remote_error"
    assert "secret remote detail" not in str(exc.value)


@pytest.mark.parametrize(
    "field",
    ["password", "apiKey", "token", "host", "port", "useSsl", "clientSecret", "enable"],
)
def test_servarr_write_allows_only_product_storage_fields(field: str) -> None:
    with pytest.raises(ServarrError) as exc:
        normalize_mutation_plan(
            "radarr", {"download_clients": [{"id": 1, "fields": {field: "bad"}}]}
        )
    assert exc.value.code == "download_field_refused"


def test_servarr_write_uses_product_aware_category_names() -> None:
    assert normalize_mutation_plan(
        "sonarr", {"download_clients": [{"id": 1, "fields": {"tvCategory": "tv"}}]}
    )["download_clients"][0]["fields"] == {"tvCategory": "tv"}
    with pytest.raises(ServarrError) as exc:
        normalize_mutation_plan(
            "radarr", {"download_clients": [{"id": 1, "fields": {"tvCategory": "tv"}}]}
        )
    assert exc.value.code == "download_field_refused"
