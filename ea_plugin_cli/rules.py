#!/usr/bin/env python3

# Copyright © 2025-2026 Wenze Wei. All Rights Reserved.
#
# This file is part of Encre.
# The Encre project belongs to the Dunimd Team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# DISCLAIMER: Users must comply with applicable AI regulations.
# Non-compliance may result in service termination or legal liability.

"""Shared publish-time rules for the plugin central repository.

Pure functions over already-parsed data (pyproject dict, manifest dict,
artifact digests) so both the CLI and the registry-repo CI can import
this module without encre-harness or any plugin code being installed.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from datetime import UTC
from pathlib import Path
from typing import Any

#: Community prefix: what ``encre-plugin init`` scaffolds and what authors
#: are expected to use.
PACKAGE_PREFIX = "ea-plugin-"

#: Reserved PyPI namespaces accepted by the central repository.  ea-tool-/
#: ea-skill- are the vendor prefixes the Encre build pipeline publishes the
#: official packages under — one registry, three namespaces, same gates.
RESERVED_PREFIXES = ("ea-plugin-", "ea-tool-", "ea-skill-")

#: Only third-party tiers may enter the central repository by default; the
#: vendor pipeline widens this to the bundled system-default tier.
CENTRAL_TIERS = {"user"}
VENDOR_TIERS = ("user", "system-default")

_ENTRY_TARGET_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


def normalize_module(name: str) -> str:
    """Turn a plugin name into a PEP 508-ish importable module slug."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Hex sha256 digest of a distribution file (streamed)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_pyproject(pkg_dir: Path) -> dict[str, Any]:
    """Parse ``pyproject.toml`` from a plugin source directory.

    Raises:
        FileNotFoundError: the file is missing.
        tomllib.TOMLDecodeError: the file is malformed.
    """
    with (pkg_dir / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def find_ea_entrypoint(py: dict[str, Any]) -> tuple[str, str] | None:
    """Return the plugin's ``(ep_name, "module:factory")`` EA entry point.

    Looks at ``[project.entry-points."ea.plugins"]`` — the same group the
    registry's entry-point discovery consumes.  Returns ``None`` when the
    package declares no EA entry point.
    """
    group = (py.get("project", {}).get("entry-points") or {}).get("ea.plugins") or {}
    if not group:
        return None
    # A package publishing several plugins is not supported by the central
    # repository (one catalog entry per distribution); take the first and
    # let validation flag multi-entry packages explicitly.
    ((name, target),) = group.items()
    return str(name), str(target)


def validate_for_publish(
    py: dict[str, Any],
    manifest: dict[str, Any],
    *,
    extra_entry_points: int = 0,
    allowed_tiers: tuple[str, ...] | None = None,
) -> list[str]:
    """Check a plugin package against the central-repository policy.

    Args:
        py: The parsed ``pyproject.toml``.
        manifest: ``PluginManifest.to_dict()`` output from the package.
        extra_entry_points: Count of additional ``ea.plugins`` entries
            beyond the first (multi-plugin wheels).
        allowed_tiers: Tiers permitted into the index; ``None`` means the
            community default (``user`` only).  The vendor pipeline passes
            :data:`VENDOR_TIERS` so bundled ``system-default`` packages can
            be published to the same registry.

    Returns:
        Violation messages; empty means the package may be published.
    """
    problems: list[str] = []
    tiers = set(allowed_tiers) if allowed_tiers else set(CENTRAL_TIERS)
    project = py.get("project", {})
    dist = str(project.get("name", ""))
    if not dist.startswith(RESERVED_PREFIXES):
        problems.append(f"PyPI distribution name {dist!r} must start with one of {RESERVED_PREFIXES}")
    ep = find_ea_entrypoint(py)
    if ep is None:
        problems.append('missing [project.entry-points."ea.plugins"] declaration')
    elif extra_entry_points:
        problems.append("multi-plugin wheels are not supported (one catalog entry per distribution)")
    elif not _ENTRY_TARGET_RE.match(ep[1]):
        problems.append(f"entry point target {ep[1]!r} must be 'module:factory'")

    name = str(manifest.get("name", "")).strip()
    version = str(manifest.get("version", "")).strip()
    if not name:
        problems.append("manifest.name is required")
    if ep is not None and name:
        # Market identity contract: after install the registry keys the plugin
        # by its entry-point name, and the catalog addresses it by
        # manifest.name — those must agree or a market install registers under
        # one name and confirms/updates/uninstalls under another.  Community
        # scaffold makes them identical (``demo``/``demo``); the official
        # vendor packages key the entry point by the bare suffix
        # (``agentmail``) while manifest.name carries the reserved namespace
        # (``ea-skill-agentmail``), so the ep key may equal the name minus its
        # leading reserved prefix.
        bare = next((name.removeprefix(p) for p in RESERVED_PREFIXES if name.startswith(p)), name)
        if ep[0] != name and ep[0] != bare:
            problems.append(f"entry-point key {ep[0]!r} must equal manifest.name {name!r} (or its namespace-stripped form {bare!r})")
    if not version:
        problems.append("manifest.version is required")
    elif version != str(project.get("version", "")):
        problems.append(f"manifest.version {version!r} != pyproject version {project.get('version')!r}")
    if not str(manifest.get("description", "")).strip():
        problems.append("manifest.description is required for market display")
    if not str(manifest.get("author", "")).strip():
        problems.append("manifest.author is required for market display")
    if not str(manifest.get("license", "")).strip():
        problems.append("manifest.license is required")
    tier = str(manifest.get("tier", ""))
    if tier not in tiers:
        problems.append(f"manifest.tier {tier!r} not allowed in the central repository (use one of {sorted(tiers)})")
    for key in ("permissions", "tags", "dependencies"):
        value = manifest.get(key, [])
        if not isinstance(value, list):
            problems.append(f"manifest.{key} must be a list")
    if not str(manifest.get("min_ea_version", "")).strip() and not manifest.get("engines"):
        problems.append("manifest must declare min_ea_version or engines['encre']")

    # Frontend rule: anything the plugin wants rendered in the desktop must
    # ship PREBUILT inside the wheel — no build steps at install time.
    wants_ui = bool(manifest.get("provides_ui") or (manifest.get("contributes") or {}).get("ui"))
    if wants_ui:
        pkg_data = py.get("tool", {}).get("setuptools", {}).get("package-data") or {}
        patterns = [p for plist in pkg_data.values() for p in (plist if isinstance(plist, list) else [plist])]
        if not any("ui" in str(p) for p in patterns):
            problems.append("plugin declares UI but package-data ships no ui/ assets (frontend must be prebuilt into the wheel)")
    return problems


def build_catalog_entry(
    manifest: dict[str, Any],
    *,
    pypi_package: str,
    artifacts: dict[str, dict[str, Any]],
    published_at: str,
) -> dict[str, Any]:
    """Assemble the ea-cwh catalog entry for one published plugin.

    Args:
        manifest: ``PluginManifest.to_dict()`` output.
        pypi_package: The reserved-prefix PyPI distribution name.
        artifacts: ``{filename: {"sha256": ..., "size": ...}}``.
        published_at: ISO-8601 UTC timestamp of the PyPI upload.

    Returns:
        A catalog entry document (validated downstream by ea-cwh's
        ``validate_catalog`` before it reaches the market).
    """
    provides = {
        "tools": list(manifest.get("provides_tools") or []),
        "skills": list(manifest.get("provides_skills") or []),
        "backends": list(manifest.get("provides_backends") or []),
        "context": list(manifest.get("provides_context") or []),
        "ui": list(manifest.get("provides_ui") or []),
        "gateways": list(manifest.get("provides_gateways") or []),
        "memory": list(manifest.get("provides_memory") or []),
    }
    return {
        "name": manifest.get("name", ""),
        "pypi_package": pypi_package,
        "version": manifest.get("version", ""),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "icon": manifest.get("icon", ""),
        "license": manifest.get("license", ""),
        "homepage": manifest.get("homepage", ""),
        "repository": manifest.get("repository", ""),
        "docs": manifest.get("docs", ""),
        "email": manifest.get("email", ""),
        "tags": list(manifest.get("tags") or []),
        "permissions": list(manifest.get("permissions") or []),
        "provides": {k: v for k, v in provides.items() if v},
        "tier": manifest.get("tier", "user"),
        "min_encre_version": manifest.get("min_ea_version", "") or (manifest.get("engines") or {}).get("encre", ""),
        "artifacts": artifacts,
        "published_at": published_at,
        "yanked": False,
    }


def merge_catalog(entries: list[dict[str, Any]], *, registry_url: str = "https://pypi.org") -> dict[str, Any]:
    """Merge per-plugin entries into a full catalog document.

    Sorting by plugin name keeps regenerated documents diff-stable, so CI
    diffs show real changes only.
    """
    from ea_cwh import SCHEMA_VERSION  # schema lives in the index package

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "registry": registry_url,
        "plugins": sorted(entries, key=lambda e: str(e.get("name", ""))),
    }


def _utc_now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
