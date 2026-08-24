from __future__ import annotations

import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "docs" / "planning" / "unified-product-roadmap.md"
CSV_PATH = ROOT / "docs" / "planning" / "execution-gap-matrix.csv"
MD_PATH = ROOT / "docs" / "planning" / "execution-gap-matrix.md"
ROW = re.compile(r"^\| ([A-Z]+-\d{2}) \|(?: (P\d) \|)? (.*?) \| (.*?) \| (.*?) \|$")


def normalized_status(evidence: str) -> str:
    upper = evidence.upper()
    if upper.startswith("SOFTWARE VERIFIED") or upper.startswith("VERIFIED —"):
        return "VERIFIED"
    if upper.startswith("VERIFIED IN ISOLATION"):
        return "VERIFIED IN ISOLATION"
    if upper.startswith(("HARDWARE VALIDATION PENDING", "PHYSICAL VALIDATION PENDING")):
        return "PHYSICAL VALIDATION PENDING"
    if upper.startswith(("EXTERNAL BLOCKED", "BLOCKED")):
        return "EXTERNAL BLOCKED"
    if upper.startswith("IMPLEMENTED"):
        return "IMPLEMENTED"
    if upper.startswith("IN PROGRESS"):
        return "IN PROGRESS"
    return "NOT STARTED"


def dependencies(task_id: str) -> str:
    family, raw_number = task_id.split("-")
    number = int(raw_number)
    explicit = {
        "WEB-02": "WEB-01", "WEB-04": "WEB-01", "WEB-05": "WEB-03;WEB-04",
        "WEB-06": "FLEET-23;FLEET-24;WEB-04", "WEB-11": "WEB-02;WEB-04;WEB-05",
        "WEB-14": "WEB-11;FLEET-11", "WEB-15": "WEB-02;WEB-11;WEB-12;WEB-13;WEB-14",
        "EDGE-02": "WEB-04", "EDGE-03": "FLEET-11", "EDGE-24": "EDGE-02;EDGE-03;EDGE-06",
        "FLEET-11": "FLEET-01;FLEET-06;FLEET-10", "FLEET-16": "FLEET-10",
        "FLEET-21": "FLEET-01;FLEET-16", "FLEET-23": "FLEET-11;FLEET-14",
        "FLEET-32": "FLEET-07;FLEET-09;FLEET-23", "FLEET-33": "FLEET-16;FLEET-17;FLEET-23",
        "COMM-01": "WEB-04;FLEET-23", "ARCH-06": "HA-03;HA-04;HA-05;HA-07",
        "HA-04": "HA-03", "HA-05": "HA-03", "HA-06": "HA-03;HA-04;HA-05;HA-07",
        "HA-07": "HA-03", "HA-08": "HA-03", "HA-09": "HA-03;HA-08",
        "HA-10": "HA-04;HA-05;HA-06;HA-07;HA-09;LAB-01;LAB-03",
        "LAB-03": "LAB-01;LAB-02", "LAB-04": "LAB-03", "LAB-05": "LAB-01;FLEET-17",
        "LAB-06": "LAB-01;LAB-03;FLEET-07", "VOLUME-02": "VOLUME-01",
        "VOLUME-03": "VOLUME-01;VOLUME-02", "VOLUME-04": "VOLUME-01;VOLUME-02",
        "VOLUME-11": "VOLUME-03;VOLUME-04", "VOLUME-12": "VOLUME-04;VOLUME-05;VOLUME-06;VOLUME-08;VOLUME-11",
    }
    if task_id in explicit:
        return explicit[task_id]
    if number > 1 and family in {"LIFE", "DRAIN", "EXPAND", "IMPORT", "BACKUP", "AUTO", "MEDIA", "VOLUME", "HA", "LAB"}:
        return f"{family}-{number - 1:02d}"
    return ""


def main() -> None:
    records: list[dict[str, str]] = []
    for line in ROADMAP.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if not match:
            continue
        task_id, priority, description, acceptance, evidence = match.groups()
        status = normalized_status(evidence)
        records.append({
            "task_id": task_id,
            "priority": priority or ("P0" if task_id.startswith("RC-") else ""),
            "description": description,
            "dependencies": dependencies(task_id),
            "current_status": status,
            "implementation_evidence": evidence if status != "NOT STARTED" else "",
            "ui_evidence": "See roadmap acceptance evidence" if "UI" in evidence or "browser" in evidence.lower() else "",
            "test_evidence": evidence if "test" in evidence.lower() or "run `" in evidence.lower() else "",
            "deployment_evidence": evidence if "beta" in evidence.lower() or "deployed" in evidence.lower() else "",
            "hardware_validation_requirement": "Matching physical provider/hardware execution" if "physical" in evidence.lower() or task_id == "HW-16" else "None identified",
            "external_blocker": evidence if status in {"EXTERNAL BLOCKED", "PHYSICAL VALIDATION PENDING"} else "",
            "next_action": "None; preserve with regression coverage" if status == "VERIFIED" else ("Execute matching physical certification without protected disks" if status == "PHYSICAL VALIDATION PENDING" else f"Implement and prove: {acceptance}"),
        })
    fieldnames = list(records[0])
    with CSV_PATH.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["current_status"]] = counts.get(record["current_status"], 0) + 1
    lines = [
        "# Hoardarr execution gap matrix",
        "",
        "Generated from the canonical unified roadmap by `scripts/build-execution-gap-matrix.py`.",
        "The CSV is authoritative for per-task execution fields; the roadmap remains authoritative for requirement wording.",
        "",
        "## Queue summary",
        "",
        f"- Total tasks: {len(records)}",
        *[f"- {key}: {counts[key]}" for key in sorted(counts)],
        "",
        "## Active selection rule",
        "",
        "Select the highest-priority task whose dependencies are VERIFIED, VERIFIED IN ISOLATION, or otherwise sufficient for software work. A physical or external blocker applies only to the irreducible validation boundary and never blocks independent software work.",
        "",
        "## Machine-readable matrix",
        "",
        "See [execution-gap-matrix.csv](execution-gap-matrix.csv). It tracks Task ID, priority, description, dependencies, status, implementation/UI/test/deployment evidence, hardware validation, external blocker, and next action for every roadmap row.",
    ]
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
