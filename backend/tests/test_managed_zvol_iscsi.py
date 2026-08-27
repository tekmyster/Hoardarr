from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import hoardarr.connectivity.executor as executor
from hoardarr.connectivity.executor import ExecutorFailure
from hoardarr.connectivity.service import (
    ManagedZvolBindingError,
    resolve_managed_zvol_binding,
)
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.models import Base, StorageVolume

VOLUME_ID = "11111111-1111-4111-8111-111111111111"
CHAP_FIXTURE = "fixture-value-123"


def _factory(tmp_path: Path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'managed-zvol.db').as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
    )
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def _volume(**changes: Any) -> StorageVolume:
    values: dict[str, Any] = {
        "id": VOLUME_ID,
        "stable_identity": "zfs:zvol:tank/hoardarr-lab",
        "name": "Hoardarr lab block volume",
        "provider": "zfs",
        "resource_type": "zvol",
        "provider_resource_id": "tank/hoardarr-lab",
        "presentation": "block",
        "device_path": "/dev/zvol/tank/hoardarr-lab",
        "size_bytes": 8 * 1024**3,
        "lifecycle_state": "active",
        "capabilities_json": {
            "size": {"support": "supported", "availability": "available"},
            "block_presentation": {"support": "supported", "availability": "available"},
        },
    }
    values.update(changes)
    return StorageVolume(**values)


def _binding(tmp_path: Path) -> dict[str, Any]:
    factory = _factory(tmp_path)
    with factory() as session, session.begin():
        session.add(_volume())
    with factory() as session:
        return resolve_managed_zvol_binding(session, storage_volume_id=VOLUME_ID)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"provider": "lvm"}, "connectivity_managed_zvol_ineligible"),
        ({"resource_type": "filesystem"}, "connectivity_managed_zvol_ineligible"),
        ({"resource_type": "snapshot"}, "connectivity_managed_zvol_ineligible"),
        (
            {"provider": "iscsi", "resource_type": "lun"},
            "connectivity_managed_zvol_ineligible",
        ),
        ({"presentation": "filesystem"}, "connectivity_managed_zvol_ineligible"),
        ({"lifecycle_state": "deleting"}, "connectivity_managed_zvol_ineligible"),
        ({"device_path": "/dev/sda"}, "connectivity_managed_zvol_binding_invalid"),
        ({"device_path": "/dev/zd0"}, "connectivity_managed_zvol_binding_invalid"),
        ({"provider_resource_id": "tank/../escape"}, "connectivity_managed_zvol_binding_invalid"),
        ({"provider_resource_id": "tank/bad\nname"}, "connectivity_managed_zvol_binding_invalid"),
        ({"size_bytes": 0}, "connectivity_managed_zvol_binding_invalid"),
        (
            {
                "capabilities_json": {
                    "size": {"support": "supported", "availability": "available"},
                    "block_presentation": {
                        "support": "supported",
                        "availability": "not_reported",
                    },
                }
            },
            "connectivity_managed_zvol_ineligible",
        ),
    ],
)
def test_resolver_rejects_ineligible_or_noncanonical_rows(
    tmp_path: Path, changes: dict[str, Any], code: str
) -> None:
    factory = _factory(tmp_path)
    with factory() as session, session.begin():
        session.add(_volume(**changes))
    with factory() as session, pytest.raises(ManagedZvolBindingError) as caught:
        resolve_managed_zvol_binding(session, storage_volume_id=VOLUME_ID)
    assert caught.value.code == code


def test_resolver_rejects_missing_and_every_bound_field_drift(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session, pytest.raises(ManagedZvolBindingError) as missing:
        resolve_managed_zvol_binding(session, storage_volume_id=VOLUME_ID)
    assert missing.value.code == "connectivity_managed_zvol_not_found"

    with factory() as session, session.begin():
        session.add(_volume())
    with factory() as session:
        binding = resolve_managed_zvol_binding(session, storage_volume_id=VOLUME_ID)
    for field, replacement in (
        ("stable_identity", "zfs:zvol:tank/other"),
        ("provider_resource_id", "tank/other"),
        ("device_path", "/dev/zvol/tank/other"),
        ("size_bytes", 9 * 1024**3),
        ("binding_sha256", "0" * 64),
    ):
        changed = copy.deepcopy(binding)
        changed[field] = replacement
        with factory() as session, pytest.raises(ManagedZvolBindingError):
            resolve_managed_zvol_binding(session, storage_volume_id=VOLUME_ID, expected=changed)


def _config(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": "iscsi",
        "name": "lab-target",
        "managed_zvol_binding": binding,
        "target_iqn": "iqn.2026-08.com.hoardarr:lab",
        "portal_ips": ["192.0.2.10", "192.0.2.11"],
        "initiator_iqns": ["iqn.2026-08.com.hoardarr:initiator"],
        "chap_username": "hoardarr_lab",
        "chap_enabled": True,
    }


def test_managed_apply_and_remove_use_only_block_backstore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts: list[list[str]] = []
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"iscsi": {"available": True}}},
    )
    monkeypatch.setattr(executor, "_targetcli", lambda commands: scripts.append(commands))
    monkeypatch.setattr(
        executor,
        "_ensure_backing_file",
        lambda _config: pytest.fail("managed zvol allocated a backing file"),
    )
    monkeypatch.setattr(
        executor,
        "_unlink_backing_file",
        lambda *_args, **_kwargs: pytest.fail("managed zvol was unlinked"),
    )

    config = _config(_binding(tmp_path))
    validated = executor._validate_common(config)
    executor._apply_iscsi("22222222-2222-4222-8222-222222222222", validated, CHAP_FIXTURE)
    executor._remove_iscsi("22222222-2222-4222-8222-222222222222", validated)

    apply_script = "\n".join(scripts[0])
    remove_script = "\n".join(scripts[1])
    assert "/backstores/block create hoardarr-zvol-" in apply_script
    assert "/backstores/block/hoardarr-zvol-" in apply_script
    assert "/backstores/fileio" not in apply_script + remove_script
    assert "/dev/zvol/tank/hoardarr-lab" in apply_script
    assert "192.0.2.10 3260" in apply_script and "192.0.2.11 3260" in apply_script
    assert "iqn.2026-08.com.hoardarr:initiator" in apply_script
    assert f"userid=hoardarr_lab password={CHAP_FIXTURE}" in apply_script
    assert "/backstores/block delete hoardarr-zvol-" in remove_script

    service_id = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setattr(executor, "_load_state", lambda: {service_id: config})
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(executor, "_save_state", lambda services: saved.append(dict(services)))
    digest = executor.hashlib.sha256(
        executor.json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = executor.remove(service_id, digest, config, False)
    assert result["backing_data_deleted"] is False
    assert saved == [{}]


def test_managed_apply_rollback_is_lio_only_and_delete_data_fails_before_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts: list[list[str]] = []
    monkeypatch.setattr(
        executor,
        "capabilities",
        lambda: {"protocols": {"iscsi": {"available": True}}},
    )

    def targetcli(commands: list[str]) -> None:
        scripts.append(commands)
        if len(scripts) == 1:
            raise ExecutorFailure("synthetic_failure", "synthetic safe failure")

    monkeypatch.setattr(executor, "_targetcli", targetcli)
    monkeypatch.setattr(executor, "_load_state", lambda: pytest.fail("state was read"))
    monkeypatch.setattr(
        executor,
        "_unlink_backing_file",
        lambda *_args, **_kwargs: pytest.fail("managed zvol was unlinked"),
    )
    config = executor._validate_common(_config(_binding(tmp_path)))
    with pytest.raises(ExecutorFailure, match="synthetic safe failure"):
        executor._apply_iscsi("33333333-3333-4333-8333-333333333333", config, CHAP_FIXTURE)
    assert len(scripts) == 2
    assert all("fileio" not in "\n".join(script) for script in scripts)

    with pytest.raises(ExecutorFailure) as caught:
        executor.remove(
            "33333333-3333-4333-8333-333333333333",
            executor.hashlib.sha256(
                executor.json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            config,
            True,
        )
    assert caught.value.code == "connectivity_managed_zvol_delete_forbidden"
