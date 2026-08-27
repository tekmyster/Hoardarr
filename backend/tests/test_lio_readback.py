from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import hoardarr.connectivity.executor as executor
import hoardarr.connectivity.lio_readback as readback
from hoardarr.connectivity.service import config_hash

SERVICE_ID = "44444444-4444-4444-8444-444444444444"
VOLUME_ID = "55555555-5555-4555-8555-555555555555"
TARGET_IQN = "iqn.2026-08.com.hoardarr:readback"
INITIATORS = [
    "iqn.2026-08.com.hoardarr:client-a",
    "iqn.2026-08.com.hoardarr:client-b",
]
PORTALS = ["192.0.2.10", "192.0.2.11"]
DEVICE_FIXTURE = "/dev/zvol/tank/readback-fixture"
CHAP_FIXTURE = "fixture-value-456"


def _config(*, chap: bool = True, wildcard: bool = False) -> dict[str, Any]:
    digest_fields = {
        "storage_volume_id": VOLUME_ID,
        "stable_identity": "zfs:zvol:tank/readback-fixture",
        "provider": "zfs",
        "resource_type": "zvol",
        "provider_resource_id": "tank/readback-fixture",
        "device_path": DEVICE_FIXTURE,
        "size_bytes": 8 * 1024**3,
    }
    binding = {"kind": "managed_zvol", **digest_fields}
    binding["binding_sha256"] = config_hash(digest_fields)
    return {
        "protocol": "iscsi",
        "name": "readback-target",
        "managed_zvol_binding": binding,
        "target_iqn": TARGET_IQN,
        "portal_ips": ["0.0.0.0"] if wildcard else PORTALS,
        "initiator_iqns": INITIATORS,
        "chap_username": "hoardarr_readback" if chap else None,
        "chap_enabled": chap,
    }


def _document(*, chap: bool = True, wildcard: bool = False) -> dict[str, Any]:
    backstore = readback.managed_backstore_name(SERVICE_ID)
    acls = []
    for initiator in INITIATORS:
        acl: dict[str, Any] = {"node_wwn": initiator}
        if chap:
            acl.update(
                {
                    "chap_userid": "hoardarr_readback",
                    "chap_password": CHAP_FIXTURE,
                }
            )
        acls.append(acl)
    return {
        "storage_objects": [
            {"name": "unrelated-file", "plugin": "fileio", "dev": "/srv/unrelated"},
            {"name": backstore, "plugin": "block", "dev": DEVICE_FIXTURE},
        ],
        "targets": [
            {"fabric": "loopback", "wwn": "naa.6001405unrelated", "tpgs": []},
            {
                "fabric": "iscsi",
                "wwn": TARGET_IQN,
                "tpgs": [
                    {
                        "tag": 1,
                        "attributes": {
                            "generate_node_acls": 0,
                            "demo_mode_write_protect": 1,
                        },
                        "luns": [
                            {
                                "index": 0,
                                "storage_object": f"/backstores/block/{backstore}",
                            }
                        ],
                        "portals": [
                            {"ip_address": address, "port": 3260}
                            for address in (["0.0.0.0"] if wildcard else PORTALS)
                        ],
                        "node_acls": acls,
                    }
                ],
            },
        ],
        "fabric_modules": [{"name": "iscsi"}],
    }


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _verify(document: dict[str, Any], *, chap: bool = True, wildcard: bool = False):
    return readback.verify_managed_apply(
        document,
        service_id=SERVICE_ID,
        config=_config(chap=chap, wildcard=wildcard),
        secret=CHAP_FIXTURE if chap else None,
    )


def test_exact_apply_readback_is_versioned_sanitized_and_deterministic() -> None:
    document = _document()
    evidence = _verify(document)
    assert evidence["schema_version"] == 1
    assert evidence["state"] == "active"
    assert evidence["backstore_plugin"] == "block"
    assert evidence["device_matches_binding"] is True
    assert evidence["chap_configured"] is True
    assert evidence["chap_user_matches"] is True
    assert evidence["chap_secret_matches"] is True
    assert len(evidence["evidence_sha256"]) == 64
    digest = evidence["evidence_sha256"]
    unsigned = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    assert (
        digest
        == hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert evidence == _verify(copy.deepcopy(document))
    serialized = json.dumps(evidence, sort_keys=True)
    assert DEVICE_FIXTURE not in serialized
    assert "tank/readback-fixture" not in serialized
    assert CHAP_FIXTURE not in serialized
    assert "unrelated" not in serialized


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "directory",
        "oversize",
        "malformed",
        "duplicate_key",
        "wrong_top_level",
        "overflow",
    ],
)
def test_saveconfig_reader_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    path = tmp_path / "saveconfig.json"
    if case == "missing":
        pass
    elif case == "directory":
        path.mkdir()
    elif case == "oversize":
        monkeypatch.setattr(readback, "MAX_SAVECONFIG_BYTES", 16)
        path.write_bytes(b"{" + b"x" * 16 + b"}")
    elif case == "malformed":
        path.write_text("{not-json", encoding="utf-8")
    elif case == "duplicate_key":
        path.write_text(
            '{"storage_objects":[],"storage_objects":[],"targets":[]}', encoding="utf-8"
        )
    elif case == "wrong_top_level":
        _write(path, [])
    else:
        monkeypatch.setattr(readback, "MAX_COLLECTION_ENTRIES", 2)
        _write(path, {"storage_objects": [{}, {}, {}], "targets": []})
    with pytest.raises(readback.LioReadbackError) as caught:
        readback.read_saveconfig(path)
    assert caught.value.code.startswith("connectivity_lio_readback_")
    assert str(caught.value) == "The current iSCSI target state could not be verified."


def test_saveconfig_reader_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    link = tmp_path / "saveconfig.json"
    _write(source, _document())
    link.symlink_to(source)
    with pytest.raises(readback.LioReadbackError):
        readback.read_saveconfig(link)


def test_saveconfig_reader_rejects_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "saveconfig.json"
    _write(path, _document())
    monkeypatch.setattr(
        readback.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError()),
    )
    with pytest.raises(readback.LioReadbackError) as caught:
        readback.read_saveconfig(path)
    assert caught.value.code == "connectivity_lio_readback_unavailable"


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "symlink",
        "directory",
        "oversize",
        "malformed",
        "duplicate_key",
        "wrong_top_level",
        "overflow",
    ],
)
def test_reader_failure_after_targetcli_never_saves_executor_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    path = tmp_path / "saveconfig.json"
    if case == "symlink":
        source = tmp_path / "source.json"
        _write(source, _document())
        path.symlink_to(source)
    elif case == "directory":
        path.mkdir()
    elif case == "oversize":
        monkeypatch.setattr(readback, "MAX_SAVECONFIG_BYTES", 16)
        path.write_bytes(b"{" + b"x" * 16 + b"}")
    elif case == "malformed":
        path.write_text("{not-json", encoding="utf-8")
    elif case == "duplicate_key":
        path.write_text(
            '{"storage_objects":[],"storage_objects":[],"targets":[]}',
            encoding="utf-8",
        )
    elif case == "wrong_top_level":
        _write(path, [])
    elif case == "overflow":
        monkeypatch.setattr(readback, "MAX_COLLECTION_ENTRIES", 2)
        _write(path, {"storage_objects": [{}, {}, {}], "targets": []})
    monkeypatch.setattr(executor, "RTSLIB_SAVECONFIG_PATH", path)
    monkeypatch.setattr(executor, "_load_state", lambda: {})
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"iscsi": {"available": True}}},
    )
    targetcli_calls: list[list[str]] = []
    monkeypatch.setattr(executor, "_targetcli", lambda values: targetcli_calls.append(list(values)))
    state_writes: list[object] = []
    monkeypatch.setattr(executor, "_save_state", lambda value: state_writes.append(value))
    config = _config()
    with pytest.raises(executor.ExecutorFailure) as caught:
        executor.apply(SERVICE_ID, config_hash(config), config, CHAP_FIXTURE)
    assert caught.value.needs_attention is True
    assert len(targetcli_calls) == 2
    assert state_writes == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_backstore",
        "duplicate_backstore",
        "wrong_plugin",
        "wrong_device",
        "missing_target",
        "duplicate_target",
        "wrong_fabric",
        "duplicate_tpg",
        "tpg_tag",
        "lun_number",
        "lun_mapping",
        "extra_lun",
        "missing_portal",
        "extra_portal",
        "duplicate_portal",
        "wrong_port",
        "missing_acl",
        "extra_acl",
        "duplicate_acl",
        "missing_safety",
        "wrong_safety",
        "type_confusion",
    ],
)
def test_selected_graph_negative_matrix_is_fail_closed(mutation: str) -> None:
    document = _document()
    storage = document["storage_objects"]
    targets = document["targets"]
    target = targets[1]
    tpg = target["tpgs"][0]
    if mutation == "missing_backstore":
        storage.pop()
    elif mutation == "duplicate_backstore":
        storage.append(copy.deepcopy(storage[-1]))
    elif mutation == "wrong_plugin":
        storage[-1]["plugin"] = "fileio"
    elif mutation == "wrong_device":
        storage[-1]["dev"] = "/dev/zvol/tank/other-fixture"
    elif mutation == "missing_target":
        targets.pop()
    elif mutation == "duplicate_target":
        targets.append(copy.deepcopy(target))
    elif mutation == "wrong_fabric":
        target["fabric"] = "loopback"
    elif mutation == "duplicate_tpg":
        target["tpgs"].append(copy.deepcopy(tpg))
    elif mutation == "tpg_tag":
        tpg["tag"] = 2
    elif mutation == "lun_number":
        tpg["luns"][0]["index"] = 1
    elif mutation == "lun_mapping":
        tpg["luns"][0]["storage_object"] = "/backstores/block/other"
    elif mutation == "extra_lun":
        tpg["luns"].append(copy.deepcopy(tpg["luns"][0]))
    elif mutation == "missing_portal":
        tpg["portals"].pop()
    elif mutation == "extra_portal":
        tpg["portals"].append({"ip_address": "192.0.2.12", "port": 3260})
    elif mutation == "duplicate_portal":
        tpg["portals"].append(copy.deepcopy(tpg["portals"][0]))
    elif mutation == "wrong_port":
        tpg["portals"][0]["port"] = 3261
    elif mutation == "missing_acl":
        tpg["node_acls"].pop()
    elif mutation == "extra_acl":
        extra = copy.deepcopy(tpg["node_acls"][0])
        extra["node_wwn"] = "iqn.2026-08.com.hoardarr:unexpected"
        tpg["node_acls"].append(extra)
    elif mutation == "duplicate_acl":
        tpg["node_acls"].append(copy.deepcopy(tpg["node_acls"][0]))
    elif mutation == "missing_safety":
        tpg["attributes"].pop("generate_node_acls")
    elif mutation == "type_confusion":
        tpg["portals"][0] = ["192.0.2.10", 3260]
    else:
        tpg["attributes"]["demo_mode_write_protect"] = 0
    with pytest.raises(readback.LioReadbackError) as caught:
        _verify(document)
    assert DEVICE_FIXTURE not in str(caught.value)
    assert CHAP_FIXTURE not in str(caught.value)


def test_wildcard_and_explicit_portals_are_not_interchangeable() -> None:
    assert _verify(_document(wildcard=True), wildcard=True)["portals"] == [
        {"ip_address": "0.0.0.0", "port": 3260}
    ]
    with pytest.raises(readback.LioReadbackError):
        _verify(_document(wildcard=True), wildcard=False)
    with pytest.raises(readback.LioReadbackError):
        _verify(_document(wildcard=False), wildcard=True)


@pytest.mark.parametrize(
    "mutation",
    ["disabled_has_chap", "wrong_user", "wrong_password", "mutual_chap"],
)
def test_chap_disagreement_is_secret_safe(mutation: str) -> None:
    disabled = mutation == "disabled_has_chap"
    document = _document(chap=not disabled)
    tpg = document["targets"][1]["tpgs"][0]
    if disabled:
        tpg["node_acls"][0]["chap_userid"] = "unexpected-user"
        tpg["node_acls"][0]["chap_password"] = CHAP_FIXTURE
    elif mutation == "wrong_user":
        tpg["node_acls"][0]["chap_userid"] = "unexpected-user"
    elif mutation == "wrong_password":
        tpg["node_acls"][0]["chap_password"] = "different-fixture-value"
    else:
        tpg["node_acls"][0]["chap_mutual_userid"] = "unexpected-user"
        tpg["node_acls"][0]["chap_mutual_password"] = "different-fixture-value"
    with pytest.raises(readback.LioReadbackError) as caught:
        _verify(document, chap=not disabled)
    rendered = str(caught.value)
    assert CHAP_FIXTURE not in rendered
    assert "different-fixture-value" not in rendered
    assert "unexpected-user" not in rendered


def test_disabled_chap_exact_fixture_passes() -> None:
    evidence = _verify(_document(chap=False), chap=False)
    assert evidence["chap_configured"] is False
    assert evidence["chap_user_matches"] is True
    assert evidence["chap_secret_matches"] is True


@pytest.mark.parametrize("remaining", ["target", "backstore", "both"])
def test_removal_absence_rejects_each_remaining_identity(remaining: str) -> None:
    document = {"storage_objects": [], "targets": []}
    if remaining in {"backstore", "both"}:
        document["storage_objects"].append(
            {
                "name": readback.managed_backstore_name(SERVICE_ID),
                "plugin": "block",
                "dev": DEVICE_FIXTURE,
            }
        )
    if remaining in {"target", "both"}:
        document["targets"].append({"fabric": "iscsi", "wwn": TARGET_IQN, "tpgs": []})
    with pytest.raises(readback.LioReadbackError) as caught:
        readback.verify_managed_absent(document, service_id=SERVICE_ID, target_iqn=TARGET_IQN)
    assert caught.value.code == "connectivity_lio_readback_removal_incomplete"


def test_apply_readback_precedes_state_write_and_preserves_a1_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "saveconfig.json"
    _write(path, _document())
    monkeypatch.setattr(executor, "RTSLIB_SAVECONFIG_PATH", path)
    monkeypatch.setattr(executor, "_load_state", lambda: {})
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"iscsi": {"available": True}}},
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_targetcli", lambda values: commands.append(list(values)))
    reads = 0
    original_read = executor._managed_apply_readback

    def tracked_read(*args: Any) -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return original_read(*args)

    monkeypatch.setattr(executor, "_managed_apply_readback", tracked_read)
    saves: list[dict[str, Any]] = []

    def save_state(services: Any) -> None:
        assert reads == 1
        saves.append(dict(services))

    monkeypatch.setattr(executor, "_save_state", save_state)
    monkeypatch.setattr(
        executor,
        "_ensure_backing_file",
        lambda _config: pytest.fail("managed readback allocated a file"),
    )
    monkeypatch.setattr(
        executor,
        "_unlink_backing_file",
        lambda *_args, **_kwargs: pytest.fail("managed readback unlinked backing data"),
    )
    config = _config()
    result = executor.apply(SERVICE_ID, config_hash(config), config, CHAP_FIXTURE)
    backstore = readback.managed_backstore_name(SERVICE_ID)
    assert commands == [
        [
            f"/backstores/block create {backstore} {DEVICE_FIXTURE}",
            f"/iscsi create {TARGET_IQN}",
            f"/iscsi/{TARGET_IQN}/tpg1/luns create /backstores/block/{backstore}",
            f"/iscsi/{TARGET_IQN}/tpg1 set attribute "
            "generate_node_acls=0 demo_mode_write_protect=1",
            f"/iscsi/{TARGET_IQN}/tpg1/portals delete 0.0.0.0 3260",
            f"/iscsi/{TARGET_IQN}/tpg1/portals create 192.0.2.10 3260",
            f"/iscsi/{TARGET_IQN}/tpg1/portals create 192.0.2.11 3260",
            f"/iscsi/{TARGET_IQN}/tpg1/acls create {INITIATORS[0]}",
            f"/iscsi/{TARGET_IQN}/tpg1/acls/{INITIATORS[0]} set auth "
            f"userid=hoardarr_readback password={CHAP_FIXTURE}",
            f"/iscsi/{TARGET_IQN}/tpg1/acls create {INITIATORS[1]}",
            f"/iscsi/{TARGET_IQN}/tpg1/acls/{INITIATORS[1]} set auth "
            f"userid=hoardarr_readback password={CHAP_FIXTURE}",
        ]
    ]
    assert len(saves) == 1
    assert result["readback"]["state"] == "active"


def test_apply_readback_failure_cleans_once_and_never_writes_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "saveconfig.json"
    wrong = _document()
    wrong["targets"][1]["tpgs"][0]["luns"][0]["index"] = 1
    _write(path, wrong)
    monkeypatch.setattr(executor, "RTSLIB_SAVECONFIG_PATH", path)
    monkeypatch.setattr(executor, "_load_state", lambda: {})
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"iscsi": {"available": True}}},
    )
    commands: list[list[str]] = []

    def targetcli(values: list[str]) -> None:
        commands.append(list(values))
        if len(commands) == 2:
            _write(path, {"storage_objects": [], "targets": []})

    monkeypatch.setattr(executor, "_targetcli", targetcli)
    monkeypatch.setattr(executor, "_save_state", lambda _services: pytest.fail("state written"))
    monkeypatch.setattr(
        executor,
        "_unlink_backing_file",
        lambda *_args, **_kwargs: pytest.fail("backing data touched"),
    )
    config = _config()
    with pytest.raises(executor.ExecutorFailure) as caught:
        executor.apply(SERVICE_ID, config_hash(config), config, CHAP_FIXTURE)
    assert caught.value.needs_attention is True
    assert caught.value.code == "connectivity_lio_readback_mismatch"
    assert len(commands) == 2
    assert commands[1] == [
        f"/iscsi delete {TARGET_IQN}",
        f"/backstores/block delete {readback.managed_backstore_name(SERVICE_ID)}",
    ]


def test_uncertain_apply_cleanup_is_needs_attention_without_state_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "saveconfig.json"
    _write(path, _document())
    monkeypatch.setattr(executor, "RTSLIB_SAVECONFIG_PATH", path)
    monkeypatch.setattr(executor, "_load_state", lambda: {})
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"iscsi": {"available": True}}},
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(executor, "_targetcli", lambda values: calls.append(list(values)))
    monkeypatch.setattr(executor, "_save_state", lambda _services: pytest.fail("state written"))
    config = _config()
    wrong_credential_fixture = "wrong-fixture-value"
    with pytest.raises(executor.ExecutorFailure) as caught:
        executor.apply(SERVICE_ID, config_hash(config), config, wrong_credential_fixture)
    assert caught.value.code == "connectivity_lio_readback_cleanup_uncertain"
    assert caught.value.needs_attention is True
    assert len(calls) == 2
    assert wrong_credential_fixture not in str(caught.value)


def test_remove_readback_precedes_state_write_and_failure_never_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "saveconfig.json"
    config = _config()
    _write(path, _document())
    monkeypatch.setattr(executor, "RTSLIB_SAVECONFIG_PATH", path)
    monkeypatch.setattr(executor, "_load_state", lambda: {SERVICE_ID: config})
    commands: list[list[str]] = []

    def targetcli_success(values: list[str]) -> None:
        commands.append(list(values))
        _write(path, {"storage_objects": [], "targets": []})

    monkeypatch.setattr(executor, "_targetcli", targetcli_success)
    reads = 0
    original_read = executor._managed_absence_readback

    def tracked_read(*args: Any) -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return original_read(*args)

    monkeypatch.setattr(executor, "_managed_absence_readback", tracked_read)
    saves: list[dict[str, Any]] = []
    monkeypatch.setattr(
        executor,
        "_save_state",
        lambda services: (reads == 1) and saves.append(dict(services)),
    )
    result = executor.remove(SERVICE_ID, config_hash(config), config, False)
    assert len(commands) == 1
    assert saves == [{}]
    assert result["backing_data_deleted"] is False
    assert result["readback"]["state"] == "absent"

    _write(path, _document())
    commands.clear()
    saves.clear()
    reads = 0
    monkeypatch.setattr(executor, "_targetcli", lambda values: commands.append(list(values)))
    with pytest.raises(executor.ExecutorFailure) as caught:
        executor.remove(SERVICE_ID, config_hash(config), config, False)
    assert caught.value.needs_attention is True
    assert len(commands) == 1
    assert saves == []


def test_remove_readback_failure_preserves_prior_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saveconfig = tmp_path / "saveconfig.json"
    state = tmp_path / "services.json"
    config = _config()
    _write(saveconfig, _document())
    monkeypatch.setattr(executor, "RTSLIB_SAVECONFIG_PATH", saveconfig)
    monkeypatch.setattr(executor, "STATE_FILE", state)
    executor._save_state({SERVICE_ID: config})
    before = state.read_bytes()
    calls: list[list[str]] = []
    monkeypatch.setattr(executor, "_targetcli", lambda values: calls.append(list(values)))
    with pytest.raises(executor.ExecutorFailure):
        executor.remove(SERVICE_ID, config_hash(config), config, False)
    assert len(calls) == 1
    assert state.read_bytes() == before


def test_targetcli_saveconfig_mutation_script_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str | None, int]] = []
    monkeypatch.setattr(executor, "_command", lambda name: name)

    def run(
        command: list[str], *, input_text: str | None = None, timeout: int = 60, **_kwargs: Any
    ) -> str:
        calls.append((command, input_text, timeout))
        return ""

    monkeypatch.setattr(executor, "_run", run)
    executor._targetcli(["/iscsi create iqn.fixture"])
    assert calls == [(["targetcli"], "/iscsi create iqn.fixture\nsaveconfig\nexit\n", 120)]


def test_delete_backing_data_fails_before_every_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    monkeypatch.setattr(executor, "_load_state", lambda: pytest.fail("state read"))
    monkeypatch.setattr(executor, "_targetcli", lambda _values: pytest.fail("targetcli called"))
    monkeypatch.setattr(executor, "_read_lio_saveconfig", lambda: pytest.fail("readback called"))
    monkeypatch.setattr(executor, "_save_state", lambda _services: pytest.fail("state written"))
    with pytest.raises(executor.ExecutorFailure) as caught:
        executor.remove(SERVICE_ID, config_hash(config), config, True)
    assert caught.value.code == "connectivity_managed_zvol_delete_forbidden"
