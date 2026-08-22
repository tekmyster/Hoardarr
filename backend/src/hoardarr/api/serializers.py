from __future__ import annotations

from hoardarr.db.models import (
    ApiToken,
    ConnectivityService,
    HardwareSnapshot,
    IntegrationConnection,
    Operation,
    OperationEvent,
    Plan,
    User,
    WizardSession,
)


def connectivity_document(service: ConnectivityService) -> dict[str, object]:
    return {
        "id": service.id,
        "protocol": service.protocol,
        "name": service.name,
        "config": service.config_json,
        "status": service.status,
        "state": service.state_json,
        "error": service.last_error_json,
        "created_at": service.created_at,
        "updated_at": service.updated_at,
    }


def user_document(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
    }


def token_document(token: ApiToken) -> dict[str, object]:
    return {
        "id": token.id,
        "name": token.name,
        "scopes": token.scopes_json,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "created_at": token.created_at,
    }


def operation_document(operation: Operation) -> dict[str, object]:
    return {
        "id": operation.id,
        "kind": operation.kind,
        "status": operation.status,
        "resource": (
            {"type": operation.resource_type, "id": operation.resource_id}
            if operation.resource_type and operation.resource_id
            else None
        ),
        "result": operation.result_json,
        "error": operation.error_json,
        "cancel_requested": operation.cancel_requested,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
    }


def event_document(event: OperationEvent) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "type": event.event_type,
        "message": event.message,
        "data": event.data_json,
        "created_at": event.created_at,
    }


def snapshot_document(snapshot: HardwareSnapshot, *, include_payload: bool) -> dict[str, object]:
    document: dict[str, object] = {
        "id": snapshot.id,
        "operation_id": snapshot.operation_id,
        "detector_schema_version": snapshot.detector_schema_version,
        "source": snapshot.source,
        "sha256": snapshot.sha256,
        "captured_at": snapshot.captured_at,
    }
    if include_payload:
        document["hardware"] = snapshot.payload_json
    return document


def integration_document(connection: IntegrationConnection) -> dict[str, object]:
    return {
        "id": connection.id,
        "adapter": connection.adapter,
        "name": connection.name,
        "expected_product": connection.expected_product,
        "base_url": connection.base_url,
        "approved_ips": connection.approved_ips_json,
        "allow_localhost": connection.allow_localhost,
        "verify_tls": connection.verify_tls,
        "status": connection.status,
        "discovered_product": connection.discovered_product,
        "product_version": connection.product_version,
        "capabilities": connection.capabilities_json,
        "state": connection.state_json,
        "last_checked_at": connection.last_checked_at,
        "created_at": connection.created_at,
        "updated_at": connection.updated_at,
    }


def wizard_document(wizard: WizardSession) -> dict[str, object]:
    return {
        "id": wizard.id,
        "workflow": wizard.workflow,
        "workflow_version": wizard.workflow_version,
        "mode": wizard.mode,
        "status": wizard.status,
        "current_step": wizard.current_step,
        "revision": wizard.revision,
        "hardware_snapshot_id": wizard.hardware_snapshot_id,
        "answers": wizard.answers_json,
        "plan_id": wizard.plan_id,
        "created_at": wizard.created_at,
        "updated_at": wizard.updated_at,
    }


def plan_document(plan: Plan) -> dict[str, object]:
    return {
        "id": plan.id,
        "wizard_session_id": plan.wizard_session_id,
        "revision": plan.revision,
        "kind": plan.kind,
        "sha256": plan.sha256,
        "document": plan.document_json,
        "created_at": plan.created_at,
    }
