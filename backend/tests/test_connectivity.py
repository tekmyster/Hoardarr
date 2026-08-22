from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoardarr.api.schemas import ConnectivityServiceRequest
from hoardarr.connectivity import executor
from hoardarr.connectivity.service import config_hash, normalize_connectivity_request


def test_normalizes_nfs_networks() -> None:
    request = ConnectivityServiceRequest(
        protocol="nfs",
        name="media",
        path="/data/media",
        clients=["192.168.1.15/24", "10.0.0.0/8"],
        read_only=True,
    )

    assert normalize_connectivity_request(request, require_secret=True) == {
        "protocol": "nfs",
        "name": "media",
        "path": "/data/media",
        "read_only": True,
        "clients": ["10.0.0.0/8", "192.168.1.0/24"],
    }


def test_normalizes_nexus_fcoe_fabric() -> None:
    request = ConnectivityServiceRequest(
        protocol="fcoe",
        name="archive",
        backing_path="/data/targets/archive.img",
        size_bytes=1024**3,
        interfaces=["enp5s0f1", "enp5s0f0"],
        initiator_wwpns=["10:00:00:11:22:33:44:55"],
    )

    assert normalize_connectivity_request(request, require_secret=True) == {
        "protocol": "fcoe",
        "name": "archive",
        "backing_path": "/data/targets/archive.img",
        "size_bytes": 1024**3,
        "interfaces": ["enp5s0f0", "enp5s0f1"],
        "fcoe_mode": "fabric",
        "dcb_mode": "auto",
        "auto_vlan": True,
        "fip_responder": False,
        "initiator_wwpns": ["10:00:00:11:22:33:44:55"],
    }


def test_fcoe_inventory_exposes_supported_physical_ports(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    supported = tmp_path / "enp5s0f0"
    unsupported = tmp_path / "eth0"
    for interface, address in (
        (supported, "00:11:22:33:44:55"),
        (unsupported, "00:aa:bb:cc:dd:ee"),
    ):
        interface.mkdir()
        (interface / "address").write_text(address, encoding="utf-8")
        (interface / "operstate").write_text("up", encoding="utf-8")
        (interface / "speed").write_text("40000", encoding="utf-8")
    monkeypatch.setattr(executor, "SYS_CLASS_NET", tmp_path)
    monkeypatch.setattr(
        executor,
        "_network_driver",
        lambda path: "i40e" if path.name == "enp5s0f0" else "hv_netvsc",
    )
    monkeypatch.setattr(executor.shutil, "which", lambda _name: None)

    assert executor.fcoe_interface_inventory() == [
        {
            "name": "enp5s0f0",
            "driver": "i40e",
            "mac": "00:11:22:33:44:55",
            "state": "up",
            "speed_mbps": 40000,
            "target_wwpn": "20:00:00:11:22:33:44:55",
            "dcb_owner": "host",
            "online": False,
        }
    ]


def test_fcoe_creates_a_target_for_each_selected_port(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    config = {
        "backing_path": str(tmp_path / "archive.img"),
        "size_bytes": 1024**3,
        "interfaces": ["enp5s0f0", "enp5s0f1"],
        "target_wwpns": ["20:00:00:11:22:33:44:55", "20:00:00:11:22:33:44:66"],
        "fcoe_mode": "fabric",
        "initiator_wwpns": ["10:00:00:aa:bb:cc:dd:ee"],
    }
    commands: list[str] = []
    backing = tmp_path / "archive.img"
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"fcoe": {"available": True}}},
    )
    monkeypatch.setattr(
        executor,
        "_configure_fcoe_interfaces",
        lambda _config: [{"name": "enp5s0f0"}, {"name": "enp5s0f1"}],
    )
    monkeypatch.setattr(executor, "_run", lambda *_args, **_kwargs: "tcm_fc")
    monkeypatch.setattr(executor, "_command", lambda name: name)
    monkeypatch.setattr(executor, "_ensure_backing_file", lambda _config: (backing, True))
    monkeypatch.setattr(executor, "_targetcli", lambda values: commands.extend(values))

    result = executor._apply_fcoe("service-1234567890", config)

    assert "/tcm_fc create 20:00:00:11:22:33:44:55" in commands
    assert "/tcm_fc create 20:00:00:11:22:33:44:66" in commands
    assert (
        commands.count("/tcm_fc/20:00:00:11:22:33:44:55/acls create 10:00:00:aa:bb:cc:dd:ee") == 1
    )
    assert result["mode"] == "fabric"


def test_smb_apply_and_remove_are_idempotent(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    state = tmp_path / "services.json"
    smb = tmp_path / "hoardarr.conf"
    nfs = tmp_path / "hoardarr.exports"
    main = tmp_path / "smb.conf"
    main.write_text("[global]\n", encoding="utf-8")
    monkeypatch.setattr(executor, "STATE_FILE", state)
    monkeypatch.setattr(executor, "SMB_FILE", smb)
    monkeypatch.setattr(executor, "NFS_FILE", nfs)
    monkeypatch.setattr(executor, "SMB_MAIN", main)
    monkeypatch.setattr(executor, "_safe_path", lambda _value, directory: data)
    monkeypatch.setattr(executor, "_account_exists", lambda _username: True)
    monkeypatch.setattr(executor, "_reload_file_services", lambda _protocol: None)
    config = {
        "protocol": "smb",
        "name": "media",
        "path": "/data/media",
        "read_only": False,
        "valid_users": ["media", "viewer"],
        "write_users": ["media"],
        "read_users": ["viewer"],
        "browseable": True,
    }
    digest = config_hash(config)

    assert executor.apply("service-1", digest, config, None)["state"] == "active"
    rendered = smb.read_text(encoding="utf-8")
    assert "[media]" in rendered
    assert "read only = yes" in rendered
    assert "write list = media" in rendered
    assert "read list = viewer" in rendered
    assert executor.apply("service-1", digest, config, None)["state"] == "active"
    assert executor.remove("service-1", digest, config, False)["state"] == "removed"
    assert "[media]" not in smb.read_text(encoding="utf-8")
    assert executor.remove("service-1", digest, config, False)["already_absent"] is True


def test_smb_granular_acl_maps_users_groups_inheritance_and_rolls_back(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    data = tmp_path / "media"
    data.mkdir()
    monkeypatch.setattr(executor, "STATE_FILE", tmp_path / "services.json")
    monkeypatch.setattr(executor, "SMB_FILE", tmp_path / "hoardarr.conf")
    monkeypatch.setattr(executor, "NFS_FILE", tmp_path / "hoardarr.exports")
    main = tmp_path / "smb.conf"
    main.write_text("[global]\n", encoding="utf-8")
    monkeypatch.setattr(executor, "SMB_MAIN", main)
    monkeypatch.setattr(executor, "_safe_path", lambda _value, directory: data)
    monkeypatch.setattr(executor, "_account_exists", lambda name: name in {"media", "viewer"})
    monkeypatch.setattr(executor, "_group_exists", lambda name: name == "storage_admins")
    monkeypatch.setattr(executor, "_command", lambda name: name)
    calls: list[tuple[list[str], str | None]] = []

    def run(command: list[str], *, input_text=None, timeout=60, capture=False):  # type: ignore[no-untyped-def]
        calls.append((command, input_text))
        return "# file: /data/media\nuser::rwx\ngroup::r-x\nother::---\n" if capture else ""

    monkeypatch.setattr(executor, "_run", run)
    monkeypatch.setattr(
        executor,
        "_reload_file_services",
        lambda _protocol: (_ for _ in ()).throw(RuntimeError("reload failed")),
    )
    request = ConnectivityServiceRequest(
        protocol="smb",
        name="media",
        path="/data/media",
        acl_entries=[
            {"kind": "group", "name": "storage_admins", "role": "administrator"},
            {"kind": "user", "name": "media", "role": "media_application"},
            {"kind": "user", "name": "viewer", "role": "media_user"},
        ],
        inherit_acl=True,
    )
    config = normalize_connectivity_request(request, require_secret=True)
    assert config["valid_users"] == ["@storage_admins", "media", "viewer"]
    with pytest.raises(RuntimeError, match="reload failed"):
        executor.apply("service-1", config_hash(config), config, None)
    modifications = [command for command, _input in calls if "-m" in command]
    assert any("g:storage_admins:rwx" in " ".join(command) for command in modifications)
    assert any("d:u:viewer:r-x" in " ".join(command) for command in modifications)
    assert calls[-1][0] == ["setfacl", "--restore=-"]
    assert "other::---" in str(calls[-1][1])


def test_removing_block_target_preserves_backing_file_by_default(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    targets = tmp_path / "targets"
    targets.mkdir()
    backing = targets / "media.img"
    backing.write_bytes(b"data")
    config = {
        "protocol": "iscsi",
        "name": "media",
        "backing_path": str(backing),
        "size_bytes": 1024**3,
        "target_iqn": "iqn.2026-08.local.hoardarr:media",
        "portal_ips": ["0.0.0.0"],
        "initiator_iqns": ["iqn.2026-08.local.client:one"],
        "chap_username": "hoardarr",
        "chap_enabled": True,
    }
    state = tmp_path / "services.json"
    monkeypatch.setattr(executor, "STATE_FILE", state)
    monkeypatch.setattr(executor, "_safe_path", lambda _value, directory: backing)
    monkeypatch.setattr(executor, "_remove_iscsi", lambda _service_id, _config: None)
    executor._save_state({"service-1": config})

    result = executor.remove("service-1", config_hash(config), config, False)

    assert result["backing_data_deleted"] is False
    assert backing.exists()


def test_iscsi_builds_multipath_ready_portals_acls_and_chap(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    backing = tmp_path / "media.img"
    config = normalize_connectivity_request(
        ConnectivityServiceRequest(
            protocol="iscsi",
            name="media",
            backing_path="/data/targets/media.img",
            size_bytes=2 * 1024**3,
            target_iqn="iqn.2026-08.local.hoardarr:media",
            portal_ips=["10.0.0.12", "10.0.0.11", "10.0.0.11"],
            initiator_iqns=[
                "iqn.2026-08.local.client:two",
                "iqn.2026-08.local.client:one",
            ],
            chap_enabled=True,
            chap_username="hoardarr",
            chap_password="multipath-secret-123",
        ),
        require_secret=True,
    )
    commands: list[str] = []
    monkeypatch.setattr(
        executor, "capabilities", lambda: {"protocols": {"iscsi": {"available": True}}}
    )
    monkeypatch.setattr(executor, "_ensure_backing_file", lambda _config: (backing, True))
    monkeypatch.setattr(executor, "_targetcli", lambda values: commands.extend(values))

    executor._apply_iscsi("service-1234567890", config, "multipath-secret-123")

    assert config["portal_ips"] == ["10.0.0.11", "10.0.0.12"]
    assert f"/iscsi/{config['target_iqn']}/tpg1/portals create 10.0.0.11 3260" in commands
    assert f"/iscsi/{config['target_iqn']}/tpg1/portals create 10.0.0.12 3260" in commands
    assert sum("/acls create " in item for item in commands) == 2
    assert sum(" set auth " in item for item in commands) == 2


def test_iscsi_failure_removes_only_a_new_backing_file(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    backing = tmp_path / "media.img"
    backing.write_bytes(b"new")
    config = {
        "backing_path": "/data/targets/media.img",
        "size_bytes": 1024**3,
        "target_iqn": "iqn.2026-08.local.hoardarr:media",
        "portal_ips": ["0.0.0.0"],
        "initiator_iqns": ["iqn.2026-08.local.client:one"],
        "chap_username": None,
        "chap_enabled": False,
    }
    monkeypatch.setattr(
        executor, "capabilities", lambda: {"protocols": {"iscsi": {"available": True}}}
    )
    monkeypatch.setattr(executor, "_ensure_backing_file", lambda _config: (backing, True))
    monkeypatch.setattr(
        executor,
        "_unlink_backing_file",
        lambda _value, missing_ok: (backing.unlink(missing_ok=missing_ok), True)[1],
    )
    monkeypatch.setattr(
        executor,
        "_targetcli",
        lambda _values: (_ for _ in ()).throw(executor.ExecutorFailure("target_failed", "failed")),
    )

    with pytest.raises(executor.ExecutorFailure, match="failed"):
        executor._apply_iscsi("service-1", config, None)
    assert not backing.exists()


def test_connectivity_paths_must_be_canonical(
    tmp_path: Path,
) -> None:
    managed = tmp_path / "managed"
    target = managed / "media"
    target.mkdir(parents=True)

    executor._require_canonical_path(target, target)
    with pytest.raises(executor.ExecutorFailure, match="canonical"):
        executor._require_canonical_path(managed / "child" / ".." / "media", target)


@pytest.mark.parametrize(
    "path",
    [
        "/data/media\n[hostile]",
        "/data/media\rhostile",
        "/data/media;saveconfig",
        "/data/media with spaces",
        "/data/../etc/passwd",
    ],
)
def test_connectivity_request_rejects_config_and_targetcli_path_injection(path: str) -> None:
    with pytest.raises(ValueError, match="safe absolute storage path"):
        ConnectivityServiceRequest(
            protocol="nfs",
            name="media",
            path=path,
            clients=["192.0.2.0/24"],
        )
    with pytest.raises(executor.ExecutorFailure) as failure:
        executor._safe_path(path, directory=False)
    assert failure.value.code == "connectivity_path_invalid"


@pytest.mark.parametrize(
    "secret",
    [
        "contains space here",
        'quote"in-password',
        "backslash\\password",
        "semicolon;password",
        "linefeed\npassword",
    ],
)
def test_iscsi_rejects_secrets_that_can_change_targetcli_script(
    secret: str,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"iscsi": {"available": True}}},
    )
    config = {
        "backing_path": "/data/targets/media.img",
        "size_bytes": 1024**3,
        "target_iqn": "iqn.2026-08.local.hoardarr:media",
        "portal_ips": ["0.0.0.0"],
        "initiator_iqns": ["iqn.2026-08.local.client:one"],
        "chap_username": "hoardarr",
        "chap_enabled": True,
    }

    with pytest.raises(executor.ExecutorFailure, match="password is invalid"):
        executor._apply_iscsi("service-1", config, secret)


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative Linux file operations")
def test_backing_file_mutation_is_descriptor_relative_and_rejects_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backing = tmp_path / "target.img"
    monkeypatch.setattr(executor, "_safe_path", lambda _value, directory: backing)
    monkeypatch.setattr(
        executor,
        "_trusted_backing_parent",
        lambda _path: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY),
    )
    monkeypatch.setattr(
        executor.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"total": 100 * 1024**3, "free": 90 * 1024**3})(),
    )
    path, created = executor._ensure_backing_file(
        {"backing_path": str(backing), "size_bytes": 4096}
    )
    assert (path, created, backing.stat().st_size) == (backing, True, 4096)
    assert executor._unlink_backing_file(str(backing), missing_ok=False) is True
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    backing.symlink_to(victim)
    with pytest.raises(executor.ExecutorFailure, match="identity changed"):
        executor._unlink_backing_file(str(backing), missing_ok=False)
    assert victim.read_text(encoding="utf-8") == "keep"
