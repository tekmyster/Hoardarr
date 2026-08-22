from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr import __version__
from hoardarr.addons.service import (
    AddonError,
    debian_package_available,
    install_payload,
    lifecycle_state,
    run_lifecycle_action,
    runtime_command,
    validate_compatibility,
    validate_upgrade,
    verify_manifest,
)
from hoardarr.api.dependencies import database_session, require_scope, require_state_scope
from hoardarr.api.problem import Problem
from hoardarr.api.schemas import AddonInstallRequest, AddonLifecycleRequest
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.migrate import current_database_revision
from hoardarr.db.models import AddonInstallation, utc_now
from hoardarr.operations.service import document_hash

router = APIRouter(prefix="/addons", tags=["addons"])


def _trust_key(path: Path, key_id: str) -> str:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        value = document["keys"][key_id]["ed25519_public_key"]
        base64.b64decode(value, validate=True)
        return str(value)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Problem(
            409,
            "addon_trust_unavailable",
            "Trust unavailable",
            "The add-on trust key is unavailable.",
        ) from exc


@router.get("")
def list_addons(
    _principal: Principal = Depends(require_scope("read")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    items = session.scalars(select(AddonInstallation).order_by(AddonInstallation.name))
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "version": item.version,
                "state": item.state,
                "privileges": item.manifest_json["privileges"],
                "ui": item.manifest_json["ui"],
                "last_error": item.last_error_json,
            }
            for item in items
        ]
    }


@router.post("", status_code=201)
def install_addon(
    payload: AddonInstallRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    settings = request.app.state.settings
    package = settings.addon_inbox / payload.package_name
    try:
        if package.parent != settings.addon_inbox or package.is_symlink():
            raise AddonError("payload_invalid", "Add-on payload path is invalid")
        manifest = verify_manifest(
            payload.manifest,
            payload.signature,
            _trust_key(settings.addon_trust_file, payload.key_id),
        )
        validate_compatibility(
            manifest,
            api_version=1,
            database_revision=current_database_revision(settings.database_url),
            hoardarr_version=__version__,
            package_available=debian_package_available,
        )
        if sorted(payload.approved_privileges) != sorted(manifest["privileges"]):
            raise AddonError("privilege_approval_mismatch", "Approve the exact declared privileges")
        existing = session.scalar(
            select(AddonInstallation).where(AddonInstallation.name == manifest["name"])
        )
        if existing is not None:
            validate_upgrade(existing.version, manifest["version"], existing.state)
        install_path = install_payload(package, manifest, settings.addon_root)
    except AddonError as exc:
        raise Problem(422, exc.code, "Add-on rejected", str(exc)) from exc
    if existing is None:
        record = AddonInstallation(
            name=manifest["name"],
            version=manifest["version"],
            manifest_json=manifest,
            manifest_sha256=document_hash(manifest),
            state="installed",
            last_error_json=None,
        )
        session.add(record)
        audit_action = "addon.install"
    else:
        record = existing
        record.version = manifest["version"]
        record.manifest_json = manifest
        record.manifest_sha256 = document_hash(manifest)
        record.last_error_json = None
        record.updated_at = utc_now()
        audit_action = "addon.update"
    session.flush()
    record_audit(
        session,
        principal=principal,
        action=audit_action,
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="addon",
        target_id=record.id,
        details={
            "name": record.name,
            "version": record.version,
            "privileges": manifest["privileges"],
        },
    )
    return {
        "id": record.id,
        "name": record.name,
        "version": record.version,
        "state": record.state,
        "runtime_command": runtime_command(manifest, install_path),
    }


@router.post("/{addon_id}/lifecycle")
def change_addon(
    addon_id: str,
    payload: AddonLifecycleRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    record = session.get(AddonInstallation, addon_id)
    if record is None:
        raise Problem(404, "addon_not_found", "Not found", "Add-on was not found.")
    try:
        next_state = lifecycle_state(record.state, payload.action)
        install_path = request.app.state.settings.addon_root / record.name / record.version
        if payload.action != "remove":
            run_lifecycle_action(
                record.manifest_json,
                install_path,
                payload.action,
                unit_root=request.app.state.settings.addon_unit_root,
            )
    except AddonError as exc:
        raise Problem(409, exc.code, "Invalid state", str(exc)) from exc
    if next_state == "removed":
        run_lifecycle_action(
            record.manifest_json,
            install_path,
            "remove",
            unit_root=request.app.state.settings.addon_unit_root,
        )
        target = install_path
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        session.delete(record)
    else:
        record.state = next_state
        record.updated_at = utc_now()
    record_audit(
        session,
        principal=principal,
        action=f"addon.{payload.action}",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="addon",
        target_id=addon_id,
        details={"name": record.name},
    )
    return {"id": addon_id, "state": next_state}
