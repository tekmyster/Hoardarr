from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.db.models import Base, IntegrationConnection, Plan
from hoardarr.operations.service import document_hash
from hoardarr.wizard.service import (
    DEFAULT_LAYOUT,
    WizardConflict,
    WizardStateError,
    WizardValidationError,
    cancel_wizard,
    create_plan,
    create_wizard,
    update_step,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def _complete_default_steps(session: Session, wizard_id: str) -> int:
    wizard = update_step(
        session,
        wizard_id=wizard_id,
        expected_revision=0,
        step="layout",
        answers=DEFAULT_LAYOUT,
    )
    wizard = update_step(
        session,
        wizard_id=wizard_id,
        expected_revision=wizard.revision,
        step="applications",
        answers={},
    )
    return wizard.revision


def test_default_plan_is_review_only_and_hashes_immutable_document(session: Session) -> None:
    wizard = create_wizard(session)
    assert wizard.answers_json == {"layout": DEFAULT_LAYOUT}

    revision = _complete_default_steps(session, wizard.id)
    plan = create_plan(session, wizard_id=wizard.id, expected_revision=revision)

    assert plan.kind == "storage_setup"
    assert plan.document_json["apply_available"] is False
    assert plan.document_json["blockers"][0]["code"] == "storage_selection_required"
    assert plan.document_json["layout"] == DEFAULT_LAYOUT
    assert plan.document_json["summary"] == {
        "directory_actions": 19,
        "servarr_root_folder_actions": 0,
        "servarr_remote_path_mapping_actions": 0,
    }
    assert plan.sha256 == document_hash(plan.document_json)
    assert create_plan(session, wizard_id=wizard.id, expected_revision=revision).id == plan.id


def test_layout_paths_are_normalized_separate_and_advanced_only(session: Session) -> None:
    simple = create_wizard(session)
    with pytest.raises(WizardValidationError, match="Advanced mode"):
        update_step(
            session,
            wizard_id=simple.id,
            expected_revision=0,
            step="layout",
            answers={**DEFAULT_LAYOUT, "work_path": "/fast/work"},
        )

    advanced = create_wizard(session, mode="advanced")
    with pytest.raises(WizardValidationError, match="non-overlapping"):
        update_step(
            session,
            wizard_id=advanced.id,
            expected_revision=0,
            step="layout",
            answers={
                "work_path": "/pool",
                "downloads_path": "/pool/downloads",
                "media_path": "/media",
            },
        )
    with pytest.raises(WizardValidationError, match="normalized"):
        update_step(
            session,
            wizard_id=advanced.id,
            expected_revision=0,
            step="layout",
            answers={
                "work_path": "/pool/../work",
                "downloads_path": "/downloads",
                "media_path": "/media",
            },
        )


def test_answers_reject_nested_credentials_and_non_uuid_integrations(session: Session) -> None:
    wizard = create_wizard(session)
    with pytest.raises(WizardValidationError, match="secrets"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="applications",
            answers={"nested": {"api-key": "must-not-be-stored"}},
        )
    with pytest.raises(WizardValidationError, match="UUID"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="applications",
            answers={"selected_integration_ids": ["servarr-one"]},
        )


def test_ui_draft_is_dated_server_state_without_credentials(session: Session) -> None:
    wizard = create_wizard(session)
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="draft_ui",
        answers={
            "schema": 1,
            "active_step": 5,
            "selected_device_ids": ["serial:test-drive"],
            "storage_role": "individual",
            "account_mode": "generate",
        },
    )

    assert wizard.status == "draft"
    assert wizard.current_step == "draft_ui"
    assert wizard.updated_at is not None
    assert wizard.answers_json["draft_ui"]["selected_device_ids"] == ["serial:test-drive"]

    with pytest.raises(WizardValidationError, match="secrets"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=wizard.revision,
            step="draft_ui",
            answers={
                "active_step": 5,
                "selected_device_ids": ["serial:test-drive"],
                "password": "must-not-be-saved",
            },
        )


def test_plan_contains_capability_appropriate_servarr_actions(session: Session) -> None:
    connection_id = str(uuid.uuid4())
    session.add(
        IntegrationConnection(
            id=connection_id,
            adapter="servarr",
            name="TV",
            expected_product="sonarr",
            discovered_product="sonarr",
            base_url="https://sonarr.example.test",
            approved_ips_json=["10.0.0.20"],
            api_key_ciphertext=b"encrypted",
            status="connected",
            capabilities_json=["root_folders", "remote_path_mappings"],
        )
    )
    wizard = create_wizard(session)
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="layout",
        answers=DEFAULT_LAYOUT,
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="applications",
        answers={
            "selected_integration_ids": [connection_id],
            "remote_path_mappings": [
                {
                    "integration_id": connection_id,
                    "host": "download-client",
                    "remote_path": "/downloads",
                    "local_path": "/data/downloads",
                }
            ],
        },
    )
    plan = create_plan(session, wizard_id=wizard.id, expected_revision=wizard.revision)

    assert plan.document_json["actions"]["servarr_root_folders"] == [
        {
            "action_id": f"servarr-root:{connection_id}",
            "type": "servarr.root_folder.ensure",
            "integration_id": connection_id,
            "product": "sonarr",
            "path": "/data/media/tv",
        }
    ]
    mapping = plan.document_json["actions"]["servarr_remote_path_mappings"][0]
    assert mapping["host"] == "download-client"
    assert mapping["remote_path"] == "/downloads"
    assert mapping["local_path"] == "/data/downloads"


def test_edit_invalidates_pointer_but_keeps_prior_plan(session: Session) -> None:
    wizard = create_wizard(session)
    revision = _complete_default_steps(session, wizard.id)
    plan = create_plan(session, wizard_id=wizard.id, expected_revision=revision)
    original_document = plan.document_json

    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=revision,
        step="applications",
        answers={},
    )
    assert wizard.plan_id is None
    assert wizard.revision == revision + 1
    assert session.get(Plan, plan.id).document_json == original_document

    with pytest.raises(WizardConflict):
        create_plan(session, wizard_id=wizard.id, expected_revision=revision)


def test_cancel_is_revision_guarded_and_terminal(session: Session) -> None:
    wizard = create_wizard(session)
    with pytest.raises(WizardConflict):
        cancel_wizard(session, wizard_id=wizard.id, expected_revision=5)

    wizard = cancel_wizard(session, wizard_id=wizard.id, expected_revision=0)
    assert wizard.status == "cancelled"
    assert wizard.revision == 1
    assert cancel_wizard(session, wizard_id=wizard.id, expected_revision=1).status == "cancelled"
    with pytest.raises(WizardStateError):
        create_plan(session, wizard_id=wizard.id, expected_revision=1)
