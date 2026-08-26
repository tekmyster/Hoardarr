#!/usr/bin/env python3
"""Build a self-contained Hoardarr Python release bundle for Ubuntu.

The builder intentionally targets one platform.  Building on that platform lets
pip select the same binary wheels that the offline installer will consume and
avoids accidentally shipping Windows or macOS artifacts.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import tomllib

TARGET_OS_ID = "ubuntu"
TARGET_OS_VERSION = "24.04"
TARGET_ARCHITECTURE = "amd64"
TARGET_MACHINE = "x86_64"
TARGET_PYTHON = "3.12"
MANIFEST_NAME = "SHA256SUMS"
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9._+-]*)?$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class ReleasePlan:
    version: str
    release_id: str
    bundle_name: str
    output: str
    target_os: str
    target_os_version: str
    target_architecture: str
    target_python: str
    copied_paths: tuple[str, ...]


class BuildError(RuntimeError):
    """A release cannot be built safely."""


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: str) -> PurePosixPath:
    """Validate a manifest path and return its canonical POSIX form."""

    if not value or "\\" in value or any(ord(char) < 32 for char in value):
        raise BuildError(f"unsafe bundle path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BuildError(f"unsafe bundle path: {value!r}")
    if str(path) != value:
        raise BuildError(f"non-canonical bundle path: {value!r}")
    return path


def iter_bundle_files(root: Path) -> Iterable[tuple[str, Path]]:
    """Yield regular files in deterministic order and reject symbolic links."""

    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise BuildError(f"symbolic links are not allowed in bundles: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BuildError(f"non-regular bundle entry: {path}")
        relative = path.relative_to(root).as_posix()
        safe_relative_path(relative)
        if relative != MANIFEST_NAME:
            yield relative, path


def write_manifest(root: Path) -> Path:
    """Write a deterministic GNU sha256sum-compatible manifest."""

    entries = [
        f"{sha256_file(path)}  {relative}\n"
        for relative, path in iter_bundle_files(root)
    ]
    if not entries:
        raise BuildError("refusing to create an empty release manifest")
    manifest = root / MANIFEST_NAME
    manifest.write_text("".join(entries), encoding="utf-8", newline="\n")
    return manifest


def verify_manifest(root: Path) -> None:
    """Verify the manifest, exact file set, paths, entry types, and hashes."""

    manifest = root / MANIFEST_NAME
    if not manifest.is_file() or manifest.is_symlink():
        raise BuildError(f"{MANIFEST_NAME} is missing or is not a regular file")
    expected: dict[str, str] = {}
    pattern = re.compile(r"^([0-9a-f]{64})  ([^\n]+)$")
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        match = pattern.fullmatch(line)
        if not match:
            raise BuildError(f"malformed manifest line {number}")
        digest, relative = match.groups()
        safe_relative_path(relative)
        if relative == MANIFEST_NAME or relative in expected:
            raise BuildError(f"duplicate or reserved manifest path on line {number}")
        expected[relative] = digest
    if not expected:
        raise BuildError("release manifest is empty")
    actual = {relative for relative, _ in iter_bundle_files(root)}
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        raise BuildError(
            f"bundle file set mismatch: missing={missing!r} extra={extra!r}"
        )
    for relative, path in iter_bundle_files(root):
        if sha256_file(path) != expected[relative]:
            raise BuildError(f"SHA-256 mismatch: {relative}")


def validate_hashed_requirements(path: Path) -> None:
    """Require an exact, hashed, registry-only runtime requirements export."""

    text = path.read_text(encoding="utf-8")
    logical_lines: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""
    if pending:
        raise BuildError("runtime requirements end with a continuation")
    if not logical_lines:
        raise BuildError("runtime requirements export is empty")
    for line in logical_lines:
        if line.startswith(("-e ", "--editable", "http:", "https:", "git+", "file:")):
            raise BuildError(f"non-registry runtime requirement is not allowed: {line}")
        requirement = line.split(" ; ", maxsplit=1)[0]
        if "==" not in requirement or " --hash=sha256:" not in line:
            raise BuildError(f"runtime requirement is not exact and hashed: {line}")


def _project_metadata(root: Path) -> tuple[str, str]:
    pyproject = root / "backend" / "pyproject.toml"
    locks = (root / "backend" / "uv.lock", root / "frontend" / "package-lock.json")
    if not pyproject.is_file() or any(not lock.is_file() for lock in locks):
        raise BuildError(
            "backend/pyproject.toml, backend/uv.lock, and frontend/package-lock.json are required"
        )
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    version = str(metadata["version"])
    if not VERSION_RE.fullmatch(version):
        raise BuildError(f"unsupported project version: {version!r}")
    commit, _epoch = source_revision(root)
    release_id = f"{version}-{commit[:12]}"
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise BuildError(f"unsafe release identifier: {release_id!r}")
    return version, release_id


def source_revision(root: Path) -> tuple[str, int]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        epoch_text = subprocess.run(
            ["git", "show", "-s", "--format=%ct", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise BuildError("release builds require a Git source checkout") from exc
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit) or not epoch_text.isdecimal():
        raise BuildError("Git returned an invalid source revision")
    return commit, int(epoch_text)


def validate_clean_source(root: Path) -> None:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise BuildError("could not verify the release source checkout") from exc
    if status.strip():
        raise BuildError("release source checkout contains uncommitted files")


def create_plan(root: Path, output_dir: Path) -> ReleasePlan:
    version, release_id = _project_metadata(root)
    bundle_name = f"hoardarr-{release_id}-ubuntu24.04-amd64-cp312"
    copied_paths = (
        "scripts/install.sh",
        "scripts/bootstrap.py",
        "scripts/detect-hardware.py",
        "scripts/export-nas-source-evidence.py",
        "hardware/",
        "packages/",
        "systemd/",
        "config/hoardarr.env",
        "docs/",
        "requirements/runtime.lock",
        "requirements/hoardarr.lock",
        "wheels/",
        "frontend/",
        "evidence/",
        "RELEASE.json",
        MANIFEST_NAME,
    )
    return ReleasePlan(
        version=version,
        release_id=release_id,
        bundle_name=bundle_name,
        output=str((output_dir / bundle_name).resolve()),
        target_os=TARGET_OS_ID,
        target_os_version=TARGET_OS_VERSION,
        target_architecture=TARGET_ARCHITECTURE,
        target_python=TARGET_PYTHON,
        copied_paths=copied_paths,
    )


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key] = value.strip().strip('"')
    return values


def validate_build_host() -> None:
    release = _read_os_release()
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    failures: list[str] = []
    if sys.platform != "linux":
        failures.append(f"host platform is {sys.platform}, expected linux")
    if (
        release.get("ID") != TARGET_OS_ID
        or release.get("VERSION_ID") != TARGET_OS_VERSION
    ):
        failures.append(
            f"host OS is {release.get('ID', 'unknown')} {release.get('VERSION_ID', 'unknown')}, "
            f"expected {TARGET_OS_ID} {TARGET_OS_VERSION}"
        )
    if platform.machine() != TARGET_MACHINE:
        failures.append(
            f"host machine is {platform.machine()}, expected {TARGET_MACHINE}"
        )
    if python_version != TARGET_PYTHON:
        failures.append(f"builder Python is {python_version}, expected {TARGET_PYTHON}")
    if importlib.util.find_spec("pip") is None:
        failures.append(
            "builder interpreter has no pip module (run with Ubuntu /usr/bin/python3)"
        )
    if failures:
        raise BuildError("incompatible build host:\n- " + "\n- ".join(failures))


def _run(command: Sequence[str], *, cwd: Path) -> None:
    printable = " ".join(command)
    print(f"+ {printable}", flush=True)
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise BuildError(f"required build command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise BuildError(
            f"build command failed with exit code {exc.returncode}: {printable}"
        ) from exc


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise BuildError(f"required source directory is missing: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise BuildError(f"source tree contains a symbolic link: {path}")
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        else:
            raise BuildError(f"source tree contains a non-regular entry: {path}")


def _copy_release_assets(root: Path, staging: Path) -> None:
    individual = {
        root / "scripts" / "install-release-bundle.sh": staging
        / "scripts"
        / "install.sh",
        root / "scripts" / "bootstrap.py": staging / "scripts" / "bootstrap.py",
        root / "scripts" / "detect-hardware.py": staging
        / "scripts"
        / "detect-hardware.py",
        root / "scripts" / "export-nas-source-evidence.py": staging
        / "scripts"
        / "export-nas-source-evidence.py",
        root / "packaging" / "config" / "hoardarr.env": staging
        / "config"
        / "hoardarr.env",
    }
    for source, destination in individual.items():
        if not source.is_file() or source.is_symlink():
            raise BuildError(f"required regular source file is missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    os.chmod(staging / "scripts" / "install.sh", 0o755)
    os.chmod(staging / "scripts" / "bootstrap.py", 0o555)
    os.chmod(staging / "scripts" / "detect-hardware.py", 0o555)
    os.chmod(staging / "scripts" / "export-nas-source-evidence.py", 0o555)

    _copy_tree(root / "packaging" / "hardware", staging / "hardware")
    _copy_tree(root / "packaging" / "packages", staging / "packages")
    _copy_tree(root / "packaging" / "systemd", staging / "systemd")

    docs = (
        "backend.md",
        "arr-integration.md",
        "disk-quarantine.md",
        "hardware-support.md",
        "updates.md",
        "release-bundles.md",
        "Import.md",
    )
    for name in docs:
        source = root / "docs" / "development" / name
        if not source.is_file():
            raise BuildError(f"required release documentation is missing: {source}")
        destination = staging / "docs" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _write_release_metadata(root: Path, staging: Path, plan: ReleasePlan) -> None:
    commit, source_epoch = source_revision(root)
    metadata = {
        "schema": 1,
        "name": "hoardarr",
        "version": plan.version,
        "release_id": plan.release_id,
        "target": {
            "os_id": TARGET_OS_ID,
            "os_version": TARGET_OS_VERSION,
            "architecture": TARGET_ARCHITECTURE,
            "machine": TARGET_MACHINE,
            "python": TARGET_PYTHON,
        },
        "source_commit": commit,
        "built_at": datetime.fromtimestamp(source_epoch, UTC).isoformat(),
    }
    (staging / "RELEASE.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _build_wheels(root: Path, staging: Path, plan: ReleasePlan, uv: str) -> None:
    backend = root / "backend"
    requirements_dir = staging / "requirements"
    wheels_dir = staging / "wheels"
    requirements_dir.mkdir(parents=True)
    wheels_dir.mkdir(parents=True)
    runtime_lock = requirements_dir / "runtime.lock"

    _run(
        [
            uv,
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements.txt",
            "--output-file",
            str(runtime_lock),
        ],
        cwd=backend,
    )
    validate_hashed_requirements(runtime_lock)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--only-binary=:all:",
            "--dest",
            str(wheels_dir),
            "--requirement",
            str(runtime_lock),
        ],
        cwd=backend,
    )
    _run(
        [uv, "build", "--wheel", "--out-dir", str(wheels_dir), "--no-create-gitignore"],
        cwd=backend,
    )

    artifacts = sorted(wheels_dir.iterdir())
    if not artifacts or any(
        not item.is_file() or item.suffix != ".whl" for item in artifacts
    ):
        raise BuildError(
            "wheelhouse contains a missing, non-regular, or non-wheel artifact"
        )
    project_wheels = [item for item in artifacts if item.name.startswith("hoardarr-")]
    if len(project_wheels) != 1:
        raise BuildError(f"expected one Hoardarr wheel, found {len(project_wheels)}")
    project_wheel = project_wheels[0]
    normalized_version = plan.version.replace("-", "_")
    if not project_wheel.name.startswith(f"hoardarr-{normalized_version}-"):
        raise BuildError(
            f"Hoardarr wheel version does not match release: {project_wheel.name}"
        )
    project_lock = requirements_dir / "hoardarr.lock"
    project_lock.write_text(
        f"hoardarr=={plan.version} --hash=sha256:{sha256_file(project_wheel)}\n",
        encoding="utf-8",
        newline="\n",
    )
    _verify_offline_install(staging)


def _build_frontend(root: Path, staging: Path, npm: str) -> None:
    frontend = root / "frontend"
    package = frontend / "package.json"
    lock = frontend / "package-lock.json"
    if not package.is_file() or not lock.is_file():
        raise BuildError(
            "frontend/package.json and frontend/package-lock.json are required"
        )
    _run([npm, "ci", "--no-audit", "--no-fund"], cwd=frontend)
    _collect_frontend_licenses(frontend, staging)
    _run([npm, "run", "build"], cwd=frontend)
    output = frontend / "dist"
    if not (output / "index.html").is_file():
        raise BuildError("frontend build did not produce dist/index.html")
    _copy_tree(output, staging / "frontend")


def _safe_evidence_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if not result or len(result) > 160:
        raise BuildError(f"unsafe evidence package name: {value!r}")
    return result


def _collect_frontend_licenses(frontend: Path, staging: Path) -> None:
    lock_path = frontend / "package-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(
            f"could not read frontend package lock for license evidence: {exc}"
        ) from exc
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise BuildError("frontend package lock lacks the packages inventory")
    destination = staging / "evidence" / "licenses" / "npm"
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    components: list[dict[str, object]] = []
    node_modules = (frontend / "node_modules").resolve()
    for package_path, metadata in sorted(packages.items()):
        if not package_path or not package_path.startswith("node_modules/"):
            continue
        if not isinstance(metadata, dict):
            raise BuildError(f"invalid package-lock metadata for {package_path}")
        version = metadata.get("version")
        if not isinstance(version, str) or not version:
            raise BuildError(f"package-lock entry lacks a version: {package_path}")
        name = package_path.removeprefix("node_modules/")
        unresolved_source = frontend / package_path
        source = unresolved_source.resolve()
        if (
            node_modules not in source.parents
            or not source.is_dir()
            or unresolved_source.is_symlink()
        ):
            raise BuildError(f"unsafe or missing npm package directory: {package_path}")
        package_destination = destination / _safe_evidence_name(name)
        license_files: list[str] = []
        for candidate in sorted(source.iterdir()):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if not re.match(
                r"^(licen[cs]e|copying|notice)(\..*)?$", candidate.name, re.I
            ):
                continue
            package_destination.mkdir(parents=True, exist_ok=True)
            target = package_destination / candidate.name
            shutil.copyfile(candidate, target)
            license_files.append(target.relative_to(staging).as_posix())
        license_value = metadata.get("license")
        records.append(
            {
                "ecosystem": "npm",
                "license": license_value
                if isinstance(license_value, str)
                else "not-reported",
                "license_files": license_files,
                "name": name,
                "version": version,
            }
        )
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:npm/{name}@{version}",
                "licenses": [
                    {
                        "license": {
                            "name": license_value
                            if isinstance(license_value, str)
                            else "Not reported"
                        }
                    }
                ],
            }
        )
    if not records:
        raise BuildError("frontend dependency license inventory is empty")
    _write_json(
        staging / "evidence" / "npm-licenses.json", {"schema": 1, "packages": records}
    )
    _write_json(staging / "evidence" / "npm-components.json", components)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _collect_wheel_licenses(staging: Path) -> list[dict[str, object]]:
    destination = staging / "evidence" / "licenses" / "python"
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    components: list[dict[str, object]] = []
    for wheel in sorted((staging / "wheels").glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            metadata_paths = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise BuildError(
                    f"wheel does not contain exactly one METADATA file: {wheel.name}"
                )
            message = email.parser.Parser().parsestr(
                archive.read(metadata_paths[0]).decode("utf-8", errors="strict")
            )
            name = message.get("Name")
            version = message.get("Version")
            if not name or not version:
                raise BuildError(f"wheel metadata lacks name/version: {wheel.name}")
            license_value = (
                message.get("License-Expression")
                or message.get("License")
                or "not-reported"
            )
            package_destination = destination / _safe_evidence_name(name)
            license_files: list[str] = []
            prefix = metadata_paths[0].removesuffix("METADATA")
            for member in sorted(archive.namelist()):
                basename = PurePosixPath(member).name
                if member.endswith("/") or not member.startswith(prefix):
                    continue
                if "/licenses/" not in member and not re.match(
                    r"^(licen[cs]e|copying|notice)(\..*)?$", basename, re.I
                ):
                    continue
                package_destination.mkdir(parents=True, exist_ok=True)
                target = package_destination / _safe_evidence_name(basename)
                target.write_bytes(archive.read(member))
                license_files.append(target.relative_to(staging).as_posix())
        records.append(
            {
                "ecosystem": "python",
                "license": license_value,
                "license_files": license_files,
                "name": name,
                "version": version,
                "wheel": f"wheels/{wheel.name}",
                "sha256": sha256_file(wheel),
            }
        )
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
                "hashes": [{"alg": "SHA-256", "content": sha256_file(wheel)}],
                "licenses": [{"license": {"name": license_value}}],
            }
        )
    if not records:
        raise BuildError("Python dependency license inventory is empty")
    _write_json(
        staging / "evidence" / "python-licenses.json",
        {"schema": 1, "packages": records},
    )
    return components


def _write_release_evidence(root: Path, staging: Path, plan: ReleasePlan) -> None:
    python_components = _collect_wheel_licenses(staging)
    npm_components = json.loads(
        (staging / "evidence" / "npm-components.json").read_text(encoding="utf-8")
    )
    if not isinstance(npm_components, list):
        raise BuildError("npm component evidence is invalid")
    _write_json(
        staging / "evidence" / "SBOM.cdx.json",
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "name": "hoardarr",
                    "version": plan.version,
                }
            },
            "components": python_components + npm_components,
        },
    )
    commit, source_epoch = source_revision(root)
    _write_json(
        staging / "evidence" / "provenance.json",
        {
            "schema": 1,
            "source_commit": commit,
            "source_date_epoch": source_epoch,
            "target": {
                "architecture": TARGET_ARCHITECTURE,
                "os": f"{TARGET_OS_ID}-{TARGET_OS_VERSION}",
                "python": TARGET_PYTHON,
            },
            "inputs": {
                "backend/uv.lock": sha256_file(root / "backend" / "uv.lock"),
                "frontend/package-lock.json": sha256_file(
                    root / "frontend" / "package-lock.json"
                ),
            },
        },
    )
    _write_json(
        staging / "evidence" / "vulnerability-status.json",
        {
            "status": "release-build-snapshot-pending",
            "release_gate": "blocked-until-executed-audits-are-attached-to-the-appliance-evidence",
            "scope": ["Python locked dependencies", "npm locked dependencies"],
        },
    )


def _verify_offline_install(staging: Path) -> None:
    """Prove that the wheelhouse can create an importable environment offline."""

    with tempfile.TemporaryDirectory(
        prefix=".offline-verify-", dir=staging.parent
    ) as temporary:
        venv = Path(temporary) / "venv"
        _run([sys.executable, "-m", "venv", str(venv)], cwd=staging)
        python = venv / "bin" / "python"
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--isolated",
                "--no-index",
                "--no-input",
                "--disable-pip-version-check",
                "--only-binary=:all:",
                "--require-hashes",
                "--find-links",
                str(staging / "wheels"),
                "--requirement",
                str(staging / "requirements" / "runtime.lock"),
                "--requirement",
                str(staging / "requirements" / "hoardarr.lock"),
            ],
            cwd=staging,
        )
        _run([str(python), "-c", "import hoardarr"], cwd=staging)


def build_bundle(root: Path, output_dir: Path, *, uv: str, npm: str) -> Path:
    validate_build_host()
    validate_clean_source(root)
    _commit, source_epoch = source_revision(root)
    os.environ.setdefault("SOURCE_DATE_EPOCH", str(source_epoch))
    plan = create_plan(root, output_dir)
    destination = Path(plan.output)
    if destination.exists() or destination.is_symlink():
        raise BuildError(f"release destination already exists: {destination}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{plan.bundle_name}-", dir=output_dir
    ) as temporary:
        staging = Path(temporary) / plan.bundle_name
        staging.mkdir()
        _copy_release_assets(root, staging)
        _build_frontend(root, staging, npm)
        _build_wheels(root, staging, plan, uv)
        _write_release_metadata(root, staging, plan)
        _write_release_evidence(root, staging, plan)
        write_manifest(staging)
        staging.replace(destination)
    return destination


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "build"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--output-dir",
            type=Path,
            default=Path("dist/releases"),
            help="parent directory for the versioned release bundle",
        )
    subparsers.choices["build"].add_argument(
        "--uv", default=os.environ.get("UV", "uv"), help="uv executable"
    )
    subparsers.choices["build"].add_argument(
        "--npm", default=os.environ.get("NPM", "npm"), help="npm executable"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = repository_root()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    try:
        if args.command == "plan":
            print(
                json.dumps(
                    asdict(create_plan(root, output_dir)), indent=2, sort_keys=True
                )
            )
            return 0
        destination = build_bundle(root, output_dir, uv=args.uv, npm=args.npm)
        print(f"Release bundle: {destination}")
        print(f"Manifest SHA-256: {sha256_file(destination / MANIFEST_NAME)}")
        return 0
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
