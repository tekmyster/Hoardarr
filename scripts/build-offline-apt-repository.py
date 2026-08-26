#!/usr/bin/env python3
"""Build and verify Hoardarr's signed, ISO-local Ubuntu package repository."""

from __future__ import annotations

import argparse
import email.parser
import gzip
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "packaging" / "packages"
POLICY_PATH = ROOT / "packaging" / "offline" / "package-policy.json"
OWNER_INTAKE_PATH = ROOT / "packaging" / "offline" / "owner-workbook-intake.json"
PROVIDERS_PATH = ROOT / "packaging" / "hardware" / "providers.json"
VENDOR_PATH = ROOT / "packaging" / "hardware" / "vendor-tools.json"
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DISPOSITIONS = {
    "included-and-installed",
    "included-but-feature-disabled",
    "sidecar-manual-offline-import",
    "not-supported",
}


class OfflineRepositoryError(RuntimeError):
    """A fail-closed offline repository validation or build failure."""


@dataclass(frozen=True)
class PackagePlan:
    roots: tuple[str, ...]
    compatibility_families: tuple[dict[str, Any], ...]
    matrix: dict[str, Any]
    policy: dict[str, Any]


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflineRepositoryError(
            f"could not read JSON metadata {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise OfflineRepositoryError(f"JSON metadata must be an object: {path}")
    return value


def _manifest(path: Path) -> list[str]:
    if not path.is_file():
        raise OfflineRepositoryError(f"package manifest is missing: {path}")
    values: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.split("#", 1)[0].strip()
        if not value:
            continue
        if not PACKAGE_RE.fullmatch(value):
            raise OfflineRepositoryError(
                f"unsafe package at {path}:{number}: {value!r}"
            )
        if value in values:
            raise OfflineRepositoryError(
                f"duplicate package at {path}:{number}: {value}"
            )
        values.append(value)
    if not values:
        raise OfflineRepositoryError(f"package manifest is empty: {path}")
    return values


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise OfflineRepositoryError(f"{label} must be a list of non-empty strings")
    if len(set(value)) != len(value):
        raise OfflineRepositoryError(f"{label} contains duplicates")
    return value


def _compatibility_families(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value:
        raise OfflineRepositoryError(
            "compatibility_families must be a non-empty list"
        )
    families: list[dict[str, Any]] = []
    family_ids: set[str] = set()
    all_members: set[str] = set()
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict) or set(item) != {
            "id",
            "members",
            "version_policy",
        }:
            raise OfflineRepositoryError(
                f"compatibility family {number} has an invalid schema"
            )
        identifier = item["id"]
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", identifier)
            or identifier in family_ids
        ):
            raise OfflineRepositoryError(
                f"compatibility family {number} has an unsafe or duplicate id"
            )
        members = _string_list(
            item["members"], f"compatibility family {identifier} members"
        )
        if not members:
            raise OfflineRepositoryError(
                f"compatibility family {identifier} has no members"
            )
        unsafe = [member for member in members if not PACKAGE_RE.fullmatch(member)]
        duplicate_members = sorted(set(members) & all_members)
        if unsafe or duplicate_members:
            raise OfflineRepositoryError(
                f"compatibility family {identifier} has unsafe or duplicate members: "
                f"unsafe={unsafe!r} duplicates={duplicate_members!r}"
            )
        if item["version_policy"] != "single-candidate-version":
            raise OfflineRepositoryError(
                f"compatibility family {identifier} has an unsupported version policy"
            )
        family_ids.add(identifier)
        all_members.update(members)
        families.append(
            {
                "id": identifier,
                "members": tuple(members),
                "version_policy": item["version_policy"],
            }
        )
    return tuple(families)


def build_plan() -> PackagePlan:
    policy = _json(POLICY_PATH)
    if policy.get("schema_version") != 1:
        raise OfflineRepositoryError("unsupported offline package policy schema")
    profiles = _string_list(policy.get("profiles"), "profiles")
    compatibility_families = _compatibility_families(
        policy.get("compatibility_families")
    )
    owner_intake = _json(OWNER_INTAKE_PATH)
    owner_rows = owner_intake.get("rows")
    if not isinstance(owner_rows, list) or [
        item.get("row") for item in owner_rows if isinstance(item, dict)
    ] != list(range(4, 51)):
        raise OfflineRepositoryError(
            "owner workbook intake must preserve every package row 4 through 50"
        )
    if owner_intake.get("source", {}).get("sha256") != policy.get(
        "owner_workbook", {}
    ).get("sha256"):
        raise OfflineRepositoryError(
            "owner workbook intake and policy digests do not match"
        )
    roots_by_profile: dict[str, list[str]] = {}
    roots: set[str] = set()
    for profile in profiles:
        if Path(profile).name != profile:
            raise OfflineRepositoryError(f"unsafe profile name: {profile!r}")
        values = _manifest(PACKAGE_ROOT / profile)
        roots_by_profile[profile] = values
        roots.update(values)

    disabled = set(
        _string_list(
            policy.get("included_feature_disabled"), "included_feature_disabled"
        )
    )
    unknown_disabled = sorted(disabled - roots)
    if unknown_disabled:
        raise OfflineRepositoryError(
            "feature-disabled packages are absent from selected profiles: "
            + ", ".join(unknown_disabled)
        )

    providers = _json(PROVIDERS_PATH)
    provider_packages: set[str] = set()
    for collection in (
        providers.get("platform_recommendations", []),
        providers.get("providers", []),
    ):
        if not isinstance(collection, list):
            raise OfflineRepositoryError("provider collections must be lists")
        for provider in collection:
            if not isinstance(provider, dict):
                raise OfflineRepositoryError("provider entries must be objects")
            provider_packages.update(
                _string_list(provider.get("packages", []), "provider packages")
            )
    missing_provider_packages = sorted(provider_packages - roots)
    if missing_provider_packages:
        raise OfflineRepositoryError(
            "provider packages are absent from the offline profiles: "
            + ", ".join(missing_provider_packages)
        )

    vendor = _json(VENDOR_PATH)
    vendor_ids: set[str] = set()
    for item in vendor.get("tools", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            vendor_ids.add(item["id"])
    sidecars = set(_string_list(policy.get("manual_sidecars"), "manual_sidecars"))
    if sidecars != vendor_ids:
        raise OfflineRepositoryError(
            "manual sidecars must exactly cover the vendor catalog: "
            f"missing={sorted(vendor_ids - sidecars)!r} extra={sorted(sidecars - vendor_ids)!r}"
        )

    candidates: list[dict[str, Any]] = []
    for package in sorted(roots):
        profiles_for_package = sorted(
            profile
            for profile, packages in roots_by_profile.items()
            if package in packages
        )
        disposition = (
            "included-but-feature-disabled"
            if package in disabled
            else "included-and-installed"
        )
        candidates.append(
            {
                "candidate": package,
                "disposition": disposition,
                "package": package,
                "profiles": profiles_for_package,
                "provider_required": package in provider_packages,
                "service_policy": "disabled-until-configured"
                if package in disabled
                else "no-package-daemon-authorized",
                "source": "ubuntu-archive",
            }
        )
    for identifier in sorted(sidecars):
        candidates.append(
            {
                "candidate": identifier,
                "disposition": "sidecar-manual-offline-import",
                "package": None,
                "reason": "Vendor payload is license-gated or lacks an approved redistributable artifact; native providers remain available.",
                "source": "owner-supplied-checksum-pinned-sidecar",
            }
        )
    unsupported = policy.get("not_supported")
    if not isinstance(unsupported, list) or not unsupported:
        raise OfflineRepositoryError("not_supported must be a non-empty list")
    for item in unsupported:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("candidate"), str)
            or not isinstance(item.get("reason"), str)
        ):
            raise OfflineRepositoryError(
                "each not_supported entry requires candidate and reason"
            )
        candidates.append(
            {
                "candidate": item["candidate"],
                "disposition": "not-supported",
                "package": None,
                "reason": item["reason"],
                "source": "reconciled-product-boundary",
            }
        )
    names = [item["candidate"] for item in candidates]
    if len(names) != len(set(names)):
        raise OfflineRepositoryError("compatibility candidates are not unique")
    if {item["disposition"] for item in candidates} - DISPOSITIONS:
        raise OfflineRepositoryError("compatibility matrix has an invalid disposition")

    matrix = {
        "schema_version": 1,
        "generated_from": {
            "package_policy": str(POLICY_PATH.relative_to(ROOT)).replace("\\", "/"),
            "owner_workbook_intake": str(OWNER_INTAKE_PATH.relative_to(ROOT)).replace(
                "\\", "/"
            ),
            "provider_catalog": str(PROVIDERS_PATH.relative_to(ROOT)).replace(
                "\\", "/"
            ),
            "vendor_catalog": str(VENDOR_PATH.relative_to(ROOT)).replace("\\", "/"),
        },
        "target": policy["target"],
        "command_aliases": policy.get("command_aliases", {}),
        "allowed_enabled_units": policy.get("allowed_enabled_units", []),
        "denied_units": policy.get("denied_units", []),
        "official_evidence": policy.get("official_evidence", {}),
        "owner_workbook": policy.get("owner_workbook", {}),
        "compatibility_families": [
            {
                "id": family["id"],
                "members": list(family["members"]),
                "version_policy": family["version_policy"],
            }
            for family in compatibility_families
        ],
        "candidates": candidates,
    }
    return PackagePlan(
        roots=tuple(sorted(roots)),
        compatibility_families=compatibility_families,
        matrix=matrix,
        policy=policy,
    )


def _run(
    command: Sequence[str], *, cwd: Path | None = None, stdout: Any = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or (
            result.stdout.strip() if isinstance(result.stdout, str) else ""
        )
        raise OfflineRepositoryError(
            f"command failed ({result.returncode}): {' '.join(command)}\n{detail}"
        )
    return result


def _require_linux_target(plan: PackagePlan) -> None:
    if sys.platform != "linux":
        raise OfflineRepositoryError(
            "repository construction requires the supported Ubuntu Linux build host"
        )
    os_release = Path("/etc/os-release").read_text(encoding="utf-8")
    expected_release = plan.policy["target"]["release"]
    if (
        "ID=ubuntu" not in os_release
        or f'VERSION_ID="{expected_release}"' not in os_release
    ):
        raise OfflineRepositoryError(
            f"repository construction requires Ubuntu {expected_release}"
        )
    architecture = _run(["dpkg", "--print-architecture"]).stdout.strip()
    if architecture != plan.policy["target"]["architecture"]:
        raise OfflineRepositoryError(
            f"repository construction requires architecture {plan.policy['target']['architecture']}"
        )
    for command in (
        "apt-cache",
        "apt-ftparchive",
        "apt-get",
        "dpkg-deb",
        "gpg",
        "gpgv",
    ):
        if shutil.which(command) is None:
            raise OfflineRepositoryError(
                f"required build command is unavailable: {command}"
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _candidate(package: str) -> str:
    output = _run(["apt-cache", "policy", package]).stdout
    match = re.search(r"^\s*Candidate:\s*(\S+)\s*$", output, re.MULTILINE)
    if match is None or match.group(1) == "(none)":
        raise OfflineRepositoryError(
            f"Ubuntu archive has no candidate for required package: {package}"
        )
    return match.group(1)


def _origin(package: str, version: str) -> dict[str, str]:
    output = _run(["apt-cache", "policy", package]).stdout
    lines = output.splitlines()
    version_index = next(
        (index for index, line in enumerate(lines) if version in line), None
    )
    if version_index is None:
        raise OfflineRepositoryError(
            f"could not identify Ubuntu origin for {package}={version}"
        )
    pattern = re.compile(r"\s+\d+\s+(\S+)\s+(\S+)/(\S+)\s+(\S+)\s+Packages")
    for line in lines[version_index + 1 : version_index + 6]:
        match = pattern.match(line)
        if match:
            uri, pocket, component, architecture = match.groups()
            return {
                "uri": uri,
                "pocket": pocket,
                "component": component,
                "architecture": architecture,
            }
    raise OfflineRepositoryError(
        f"could not identify Ubuntu component for {package}={version}"
    )


def _deb_fields(path: Path) -> dict[str, str]:
    fields = (
        "Package",
        "Version",
        "Architecture",
        "Source",
        "Depends",
        "Pre-Depends",
        "Homepage",
    )
    control = _run(["dpkg-deb", "-f", str(path)]).stdout
    message = email.parser.Parser().parsestr(control)
    values = {field: (message.get(field) or "").strip() for field in fields}
    if not values["Package"] or not values["Version"] or not values["Architecture"]:
        raise OfflineRepositoryError(
            f"Debian package metadata is incomplete: {path.name}"
        )
    return values


def _download_closure(
    plan: PackagePlan, work: Path
) -> tuple[dict[str, str], dict[str, dict[str, str]], list[Path]]:
    root_versions = {package: _candidate(package) for package in plan.roots}
    family_versions: dict[str, dict[str, str]] = {}
    for family in plan.compatibility_families:
        resolved = {
            package: _candidate(package) for package in family["members"]
        }
        versions = set(resolved.values())
        if len(versions) != 1:
            raise OfflineRepositoryError(
                f"compatibility family {family['id']} candidate versions differ: "
                + ", ".join(
                    f"{package}={version}"
                    for package, version in sorted(resolved.items())
                )
            )
        family_versions[family["id"]] = resolved
    closure_versions = dict(root_versions)
    for resolved in family_versions.values():
        for package, version in resolved.items():
            existing = closure_versions.get(package)
            if existing is not None and existing != version:
                raise OfflineRepositoryError(
                    f"root and compatibility candidate versions differ for {package}"
                )
            closure_versions[package] = version
    state = work / "apt-state"
    archives = work / "archives"
    (archives / "partial").mkdir(parents=True)
    state.write_text("", encoding="utf-8")
    exact = [
        f"{package}={version}" for package, version in sorted(closure_versions.items())
    ]
    _run(
        [
            "apt-get",
            "-o",
            f"Dir::State::status={state}",
            "-o",
            f"Dir::Cache::archives={archives}",
            "-o",
            "Debug::NoLocking=1",
            "-o",
            "Acquire::Languages=none",
            "-o",
            "APT::Get::Download-Only=true",
            "--yes",
            "--download-only",
            "--no-install-recommends",
            "install",
            *exact,
        ]
    )
    debs = sorted(archives.glob("*.deb"))
    if not debs:
        raise OfflineRepositoryError("APT resolved no package payload")
    downloaded_records = [_deb_fields(path) for path in debs]
    identities = [
        (record["Package"], record["Architecture"]) for record in downloaded_records
    ]
    if len(identities) != len(set(identities)):
        raise OfflineRepositoryError("resolved closure contains duplicate binary identity")
    downloaded = {record["Package"]: record["Version"] for record in downloaded_records}
    missing = sorted(
        {package.split(":", 1)[0] for package in closure_versions} - set(downloaded)
    )
    if missing:
        raise OfflineRepositoryError(
            "resolved closure omitted required exact inputs: " + ", ".join(missing)
        )
    mismatched = sorted(
        f"{package}: expected {version}, downloaded {downloaded.get(package)}"
        for package, version in closure_versions.items()
        if downloaded.get(package) != version
    )
    if mismatched:
        raise OfflineRepositoryError(
            "resolved closure exact-version mismatch: " + "; ".join(mismatched)
        )
    return root_versions, family_versions, debs


def _copy_licenses(debs: Iterable[Path], evidence: Path, extraction: Path) -> None:
    for deb in debs:
        _run(["dpkg-deb", "-x", str(deb), str(extraction)])
    licenses = evidence / "licenses"
    licenses.mkdir(parents=True)
    for deb in debs:
        fields = _deb_fields(deb)
        package = fields["Package"]
        source = extraction / "usr" / "share" / "doc" / package / "copyright"
        try:
            resolved = source.resolve(strict=True)
        except FileNotFoundError as exc:
            raise OfflineRepositoryError(
                f"package lacks readable Debian copyright metadata: {package}"
            ) from exc
        doc_root = (extraction / "usr" / "share" / "doc").resolve()
        if doc_root not in resolved.parents or not resolved.is_file():
            raise OfflineRepositoryError(
                f"package copyright path escapes the extracted documentation tree: {package}"
            )
        shutil.copyfile(resolved, licenses / f"{package}.copyright")


def _resolved_family_evidence(
    plan: PackagePlan,
    family_versions: dict[str, dict[str, str]],
    package_records: list[dict[str, Any]],
) -> dict[str, Any]:
    package_identities = {
        (item["name"], item["architecture"]) for item in package_records
    }
    if len(package_identities) != len(package_records):
        raise OfflineRepositoryError("package manifest contains duplicate binary identity")
    family_members = {
        member
        for family in plan.compatibility_families
        for member in family["members"]
    }
    package_identities_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in package_records:
        if item["name"] in family_members:
            package_identities_by_name.setdefault(item["name"], []).append(item)
    resolved_families: list[dict[str, Any]] = []
    for family in plan.compatibility_families:
        resolved = family_versions.get(family["id"])
        if resolved is None or set(resolved) != set(family["members"]):
            raise OfflineRepositoryError(
                f"compatibility family evidence is incomplete: {family['id']}"
            )
        if len(set(resolved.values())) != 1:
            raise OfflineRepositoryError(
                f"compatibility family evidence versions differ: {family['id']}"
            )
        omitted: list[str] = []
        for package, version in resolved.items():
            identities = package_identities_by_name.get(package, [])
            if (
                len(identities) != 1
                or identities[0]["architecture"] not in {"all", "amd64"}
                or identities[0]["version"] != version
            ):
                omitted.append(package)
        if omitted:
            raise OfflineRepositoryError(
                f"package manifest omitted compatibility family members: {omitted!r}"
            )
        resolved_families.append(
            {
                "id": family["id"],
                "members": [
                    {"name": package, "version": resolved[package]}
                    for package in family["members"]
                ],
                "resolved_version": next(iter(set(resolved.values()))),
                "version_policy": family["version_policy"],
            }
        )
    return {"schema_version": 1, "families": resolved_families}


def _write_repo_metadata(
    staging: Path,
    plan: PackagePlan,
    root_versions: dict[str, str],
    family_versions: dict[str, dict[str, str]],
    debs: list[Path],
    signing_key: str,
    vulnerability_report: Path | None,
) -> None:
    pool = staging / "pool" / "main"
    binary = staging / "dists" / "noble" / "main" / "binary-amd64"
    evidence = staging / "evidence"
    pool.mkdir(parents=True)
    binary.mkdir(parents=True)
    evidence.mkdir(parents=True)
    copied: list[Path] = []
    for deb in debs:
        destination = pool / deb.name
        shutil.copyfile(deb, destination)
        copied.append(destination)

    packages = _run(["apt-ftparchive", "packages", "pool"], cwd=staging).stdout
    (binary / "Packages").write_text(packages, encoding="utf-8", newline="\n")
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=(binary / "Packages.gz").open("wb"), mtime=0
    ) as zipped:
        zipped.write(packages.encode())
    release_path = staging / "dists" / "noble" / "Release"
    release = _run(
        [
            "apt-ftparchive",
            "-o",
            "APT::FTPArchive::Release::Origin=Hoardarr",
            "-o",
            "APT::FTPArchive::Release::Label=Hoardarr Offline Appliance",
            "-o",
            "APT::FTPArchive::Release::Suite=noble",
            "-o",
            "APT::FTPArchive::Release::Codename=noble",
            "-o",
            "APT::FTPArchive::Release::Architectures=amd64",
            "-o",
            "APT::FTPArchive::Release::Components=main",
            "release",
            "dists/noble",
        ],
        cwd=staging,
    ).stdout
    release_path.write_text(release, encoding="utf-8", newline="\n")
    _run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--local-user",
            signing_key,
            "--clearsign",
            "--output",
            str(release_path.parent / "InRelease"),
            str(release_path),
        ]
    )
    _run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--local-user",
            signing_key,
            "--armor",
            "--detach-sign",
            "--output",
            str(release_path.parent / "Release.gpg"),
            str(release_path),
        ]
    )
    keyring = staging / "hoardarr-offline-archive-keyring.gpg"
    with keyring.open("wb") as handle:
        _run(["gpg", "--batch", "--export", signing_key], stdout=handle)
    if not keyring.stat().st_size:
        raise OfflineRepositoryError("signing key export is empty")
    _run(["gpgv", "--keyring", str(keyring), str(release_path.parent / "InRelease")])

    package_records: list[dict[str, Any]] = []
    extraction = staging.parent / f".{staging.name}.licenses"
    extraction.mkdir()
    try:
        _copy_licenses(copied, evidence, extraction)
    finally:
        shutil.rmtree(extraction, ignore_errors=True)
    for deb in copied:
        fields = _deb_fields(deb)
        package = fields["Package"]
        version = fields["Version"]
        origin = _origin(package, version)
        package_records.append(
            {
                "architecture": fields["Architecture"],
                "component": origin["component"],
                "copyright": f"evidence/licenses/{package}.copyright",
                "declared_dependencies": {
                    "depends": fields["Depends"],
                    "pre_depends": fields["Pre-Depends"],
                },
                "file": f"pool/main/{deb.name}",
                "homepage": fields["Homepage"],
                "license": "see-debian-copyright",
                "name": package,
                "pocket": origin["pocket"],
                "sha256": _sha256(deb),
                "size": deb.stat().st_size,
                "source": fields["Source"] or package,
                "source_uri": origin["uri"],
                "version": version,
            }
        )
    package_records.sort(key=lambda item: (item["name"], item["architecture"]))
    family_evidence = _resolved_family_evidence(
        plan, family_versions, package_records
    )
    _atomic_json(
        evidence / "package-manifest.json",
        {"schema_version": 1, "packages": package_records},
    )
    _atomic_json(evidence / "compatibility-matrix.json", plan.matrix)
    _atomic_json(
        evidence / "compatibility-families.json",
        family_evidence,
    )
    (evidence / "root-package-versions.txt").write_text(
        "".join(
            f"{package}={version}\n"
            for package, version in sorted(root_versions.items())
        ),
        encoding="utf-8",
        newline="\n",
    )
    components = [
        {
            "type": "library",
            "name": item["name"],
            "version": item["version"],
            "purl": f"pkg:deb/ubuntu/{item['name']}@{item['version']}?arch={item['architecture']}&distro=noble",
            "hashes": [{"alg": "SHA-256", "content": item["sha256"]}],
            "licenses": [{"license": {"name": "See Debian copyright metadata"}}],
        }
        for item in package_records
    ]
    _atomic_json(
        evidence / "SBOM.cdx.json",
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "components": components,
        },
    )
    provenance = {
        "schema_version": 1,
        "build_host": {
            "machine": platform.machine(),
            "os_release": Path("/etc/os-release")
            .read_text(encoding="utf-8")
            .splitlines(),
            "python": platform.python_version(),
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "repository": {"architecture": "amd64", "component": "main", "suite": "noble"},
        "signing_key_fingerprint": signing_key,
    }
    _atomic_json(evidence / "provenance.json", provenance)
    if vulnerability_report is not None:
        if not vulnerability_report.is_file() or vulnerability_report.is_symlink():
            raise OfflineRepositoryError("vulnerability report must be a regular file")
        shutil.copyfile(vulnerability_report, evidence / "vulnerability-status.json")
    else:
        _atomic_json(
            evidence / "vulnerability-status.json",
            {
                "status": "not-executed",
                "release_gate": "blocked",
                "reason": "A production repository build must supply an executed Ubuntu security-status snapshot.",
            },
        )


def _write_tree_manifest(root: Path) -> None:
    links = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_symlink()
    ]
    if links:
        raise OfflineRepositoryError(
            f"offline repository contains symbolic links: {links!r}"
        )
    entries = []
    for path in sorted(
        item for item in root.rglob("*") if item.is_file() and item.name != "SHA256SUMS"
    ):
        relative = path.relative_to(root).as_posix()
        entries.append(f"{_sha256(path)}  {relative}\n")
    (root / "SHA256SUMS").write_text("".join(entries), encoding="utf-8", newline="\n")


def verify_repository(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise OfflineRepositoryError("offline repository must be a real directory")
    links = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_symlink()
    ]
    if links:
        raise OfflineRepositoryError(
            f"offline repository contains symbolic links: {links!r}"
        )
    required = (
        "dists/noble/InRelease",
        "dists/noble/Release",
        "dists/noble/Release.gpg",
        "dists/noble/main/binary-amd64/Packages",
        "dists/noble/main/binary-amd64/Packages.gz",
        "evidence/SBOM.cdx.json",
        "evidence/compatibility-matrix.json",
        "evidence/compatibility-families.json",
        "evidence/package-manifest.json",
        "evidence/provenance.json",
        "evidence/root-package-versions.txt",
        "evidence/vulnerability-status.json",
        "hoardarr-offline-archive-keyring.gpg",
        "SHA256SUMS",
    )
    for relative in required:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise OfflineRepositoryError(
                f"offline repository file is missing or unsafe: {relative}"
            )
    expected: dict[str, str] = {}
    for number, line in enumerate(
        (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if not match or match.group(2) in expected or "\\" in match.group(2):
            raise OfflineRepositoryError(f"invalid SHA256SUMS line {number}")
        manifest_path = Path(match.group(2))
        if manifest_path.is_absolute() or ".." in manifest_path.parts:
            raise OfflineRepositoryError(f"unsafe SHA256SUMS path at line {number}")
        expected[manifest_path.as_posix()] = match.group(1)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise OfflineRepositoryError(
            f"offline repository tree mismatch: missing={sorted(set(expected) - actual)!r} extra={sorted(actual - set(expected))!r}"
        )
    for relative, digest in expected.items():
        if _sha256(root / relative) != digest:
            raise OfflineRepositoryError(
                f"offline repository digest mismatch: {relative}"
            )
    package_manifest = _json(root / "evidence" / "package-manifest.json")
    family_evidence = _json(root / "evidence" / "compatibility-families.json")
    compatibility_matrix = _json(root / "evidence" / "compatibility-matrix.json")
    if family_evidence.get("schema_version") != 1 or set(family_evidence) != {
        "schema_version",
        "families",
    }:
        raise OfflineRepositoryError("unsupported compatibility-family evidence schema")
    records = package_manifest.get("packages")
    families = family_evidence.get("families")
    if not isinstance(records, list) or not isinstance(families, list):
        raise OfflineRepositoryError("package or compatibility-family evidence is invalid")
    manifest_identities_by_name: dict[str, list[tuple[str, str]]] = {}
    manifest_identities: set[tuple[str, str]] = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("name"), str)
            or not isinstance(record.get("version"), str)
            or not isinstance(record.get("architecture"), str)
            or (record["name"], record["architecture"]) in manifest_identities
        ):
            raise OfflineRepositoryError("package manifest has invalid binary identities")
        manifest_identities.add((record["name"], record["architecture"]))
        manifest_identities_by_name.setdefault(record["name"], []).append(
            (record["architecture"], record["version"])
        )
    repository_versions: dict[str, str] = {}
    package_index = (root / "dists/noble/main/binary-amd64/Packages").read_text(
        encoding="utf-8"
    )
    for stanza in package_index.split("\n\n"):
        if not stanza.strip():
            continue
        parsed = email.parser.Parser().parsestr(stanza)
        name = (parsed.get("Package") or "").strip()
        version = (parsed.get("Version") or "").strip()
        architecture = (parsed.get("Architecture") or "").strip()
        if not name or not version or not architecture:
            raise OfflineRepositoryError("repository Packages has incomplete metadata")
        identity = f"{name}:{architecture}"
        if identity in repository_versions:
            raise OfflineRepositoryError("repository Packages has duplicate binary identity")
        repository_versions[identity] = version
    declarations = compatibility_matrix.get("compatibility_families")
    if not isinstance(declarations, list):
        raise OfflineRepositoryError("compatibility matrix omits family declarations")
    declared_by_id = {
        item.get("id"): item
        for item in declarations
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if len(declared_by_id) != len(declarations):
        raise OfflineRepositoryError("compatibility matrix has invalid family declarations")
    family_ids: set[str] = set()
    for family in families:
        if (
            not isinstance(family, dict)
            or set(family)
            != {"id", "members", "resolved_version", "version_policy"}
            or not isinstance(family.get("id"), str)
            or family["id"] in family_ids
            or family.get("version_policy") != "single-candidate-version"
            or not isinstance(family.get("resolved_version"), str)
            or not family["resolved_version"]
            or not isinstance(family.get("members"), list)
        ):
            raise OfflineRepositoryError("compatibility-family evidence is invalid")
        family_ids.add(family["id"])
        declaration = declared_by_id.get(family["id"])
        member_names: set[str] = set()
        ordered_member_names: list[str] = []
        for member in family["members"]:
            manifest_identities_for_member = manifest_identities_by_name.get(
                member.get("name") if isinstance(member, dict) else "", []
            )
            manifest_architecture = (
                manifest_identities_for_member[0][0]
                if len(manifest_identities_for_member) == 1
                else ""
            )
            manifest_version = (
                manifest_identities_for_member[0][1]
                if len(manifest_identities_for_member) == 1
                else ""
            )
            if (
                not isinstance(member, dict)
                or set(member) != {"name", "version"}
                or not isinstance(member.get("name"), str)
                or not PACKAGE_RE.fullmatch(member["name"])
                or not isinstance(member.get("version"), str)
                or not member["version"]
                or member["name"] in member_names
                or member.get("version") != family["resolved_version"]
                or len(manifest_identities_for_member) != 1
                or manifest_architecture not in {"all", "amd64"}
                or manifest_version != member.get("version")
                or repository_versions.get(
                    f"{member['name']}:{manifest_architecture}"
                )
                != member.get("version")
            ):
                raise OfflineRepositoryError(
                    f"compatibility family {family['id']} is incomplete or incoherent"
                )
            member_names.add(member["name"])
            ordered_member_names.append(member["name"])
        if (
            declaration is None
            or declaration.get("version_policy") != family["version_policy"]
            or declaration.get("members") != ordered_member_names
        ):
            raise OfflineRepositoryError(
                f"compatibility family {family['id']} differs from its declaration"
            )
    if family_ids != set(declared_by_id):
        raise OfflineRepositoryError("compatibility-family evidence omits declarations")
    if shutil.which("gpgv") is not None:
        _run(
            [
                "gpgv",
                "--keyring",
                str(root / "hoardarr-offline-archive-keyring.gpg"),
                str(root / "dists/noble/InRelease"),
            ]
        )


def build_repository(
    output: Path, signing_key: str, vulnerability_report: Path | None
) -> None:
    plan = build_plan()
    _require_linux_target(plan)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise OfflineRepositoryError(f"output already exists: {output}")
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.", dir=output.parent
    ) as temporary:
        work = Path(temporary)
        staging = work / "repository"
        staging.mkdir()
        root_versions, family_versions, debs = _download_closure(plan, work)
        _write_repo_metadata(
            staging,
            plan,
            root_versions,
            family_versions,
            debs,
            signing_key,
            vulnerability_report,
        )
        _write_tree_manifest(staging)
        verify_repository(staging)
        staging.replace(output)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan")
    build = subparsers.add_parser("build")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--signing-key", required=True)
    build.add_argument("--vulnerability-report", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("repository", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            print(json.dumps(build_plan().matrix, indent=2, sort_keys=True))
        elif args.command == "build":
            if not re.fullmatch(r"[0-9A-Fa-f]{16,64}", args.signing_key):
                raise OfflineRepositoryError(
                    "signing key must be an explicit fingerprint"
                )
            build_repository(args.output, args.signing_key, args.vulnerability_report)
            print(f"Offline repository: {args.output.resolve()}")
        else:
            verify_repository(args.repository.resolve())
            print(f"Verified offline repository: {args.repository.resolve()}")
        return 0
    except OfflineRepositoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
