from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from hoardarr.db.models import TopologyPlan, utc_now


class TopologyPlanError(ValueError):
    pass


_TEMPLATES: dict[str, dict[str, Any]] = {
    "generic-8-bay": {
        "id": "generic-8-bay",
        "name": "Generic 8-bay server",
        "description": "One server chassis, one storage controller, and eight configurable bays.",
        "controller_count": 1,
        "enclosures": [{"id": "chassis-bays", "label": "Server bays", "bay_count": 8}],
    },
    "generic-12-bay": {
        "id": "generic-12-bay",
        "name": "Generic 12-bay server",
        "description": "One server chassis, one storage controller, and twelve configurable bays.",
        "controller_count": 1,
        "enclosures": [{"id": "chassis-bays", "label": "Server bays", "bay_count": 12}],
    },
    "generic-24-bay-shelf": {
        "id": "generic-24-bay-shelf",
        "name": "Server with 24-bay shelf",
        "description": "One server controller connected to a generic twenty-four-bay disk shelf.",
        "controller_count": 1,
        "enclosures": [{"id": "shelf-1", "label": "Disk shelf 1", "bay_count": 24}],
    },
    "generic-dual-path-shelf": {
        "id": "generic-dual-path-shelf",
        "name": "Dual-path 24-bay shelf",
        "description": "Two planned controller paths to one generic twenty-four-bay disk shelf.",
        "controller_count": 2,
        "enclosures": [{"id": "shelf-1", "label": "Disk shelf 1", "bay_count": 24}],
    },
}


def topology_plan_templates() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in _TEMPLATES.values()]


def _initial_document(template_id: str) -> dict[str, Any]:
    template = _TEMPLATES.get(template_id)
    if template is None:
        raise TopologyPlanError("The selected planning template is not supported.")
    controllers = [
        {
            "id": f"controller-{index + 1}",
            "label": f"Controller {chr(65 + index)}",
            "state": "planned",
        }
        for index in range(template["controller_count"])
    ]
    controller_ids = [item["id"] for item in controllers]
    enclosures = [
        {**item, "controller_ids": controller_ids}
        for item in deepcopy(template["enclosures"])
    ]
    return {
        "schema_version": 1,
        "chassis": {"id": "host", "label": "Hoardarr host"},
        "controllers": controllers,
        "enclosures": enclosures,
        "changes": [],
        "notes": "",
    }


def create_topology_plan(
    session: Session, *, name: str, template_id: str, created_by: str
) -> TopologyPlan:
    plan = TopologyPlan(
        name=name.strip(),
        template_id=template_id,
        plan_json=_initial_document(template_id),
        created_by=created_by,
    )
    session.add(plan)
    session.flush()
    return plan


def update_topology_plan(
    plan: TopologyPlan, *, revision: int, name: str, document: dict[str, Any]
) -> None:
    if plan.revision != revision:
        raise TopologyPlanError(
            "This plan changed in another browser. Reload it before saving your changes."
        )
    plan.name = name.strip()
    plan.plan_json = document
    plan.revision += 1
    plan.updated_at = utc_now()


def topology_plan_document(plan: TopologyPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "template_id": plan.template_id,
        "revision": plan.revision,
        "plan": plan.plan_json,
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
    }
