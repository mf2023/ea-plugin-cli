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

"""encre-plugin — the Encre Agent plugin market unified CLI.

    encre-plugin init     <name>       scaffold an ea-plugin-* package
    encre-plugin build    [pkg_dir]    policy-check + wheel/sdist + digests
    encre-plugin verify   [pkg_dir]    isolated install + entry-point smoke
    encre-plugin publish  [pkg_dir]    twine upload + registry PR
    encre-plugin catalog  regen|check  rebuild / validate catalog.json

Build info (``dist/ea-build-info.json``) is the handoff between stages:
it carries the catalog entry with per-artifact sha256 digests so the
registry CI and the market installer never have to re-hash by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC
from pathlib import Path
from typing import Any

from .rules import (
    PACKAGE_PREFIX,
    RESERVED_PREFIXES,
    VENDOR_TIERS,
    build_catalog_entry,
    find_ea_entrypoint,
    merge_catalog,
    normalize_module,
    read_pyproject,
    sha256_file,
    validate_for_publish,
)

BUILD_INFO = "ea-build-info.json"

_PROBE = """
import importlib, json, sys
module_name, _, factory = sys.argv[1].partition(":")
plugin = getattr(importlib.import_module(module_name), factory)()
print(json.dumps(plugin.manifest.to_dict()))
"""


def _fail(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _host_python() -> str:
    """Interpreter the CLI drives `python -m build / venv / twine` with.

    A PyInstaller-frozen exe is not a Python interpreter, so the compiled
    ``encre-plugin`` resolves a real system Python (>= 3.11) from PATH
    instead.  An unfrozen run keeps ``sys.executable``, so dev environments
    (editable installs, project venvs) behave exactly as before.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    probe = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    for name in ("python", "python3", "py"):
        cand = shutil.which(name)
        if cand and subprocess.run([cand, "-c", probe], capture_output=True).returncode == 0:
            return cand
    _fail("no Python 3.11+ interpreter found on PATH — the downloaded encre-plugin shells out to a real Python for build/verify/publish")
    return ""  # unreachable; _fail raises


def _ensure_pytools(py: str, *mods: str) -> None:
    """Make the host interpreter able to import the PyPA helper modules.

    The download-and-run promise: nobody pip-installs a toolchain before
    using the exe, so a missing helper (build / twine) is installed into
    the host interpreter on first use — silent when already present.
    """
    missing = [m for m in mods if subprocess.run([py, "-c", f"import {m}"], capture_output=True).returncode != 0]
    if not missing:
        return
    print(f"one-time setup: installing {', '.join(missing)} for the publishing toolchain ...")
    r = _run([py, "-m", "pip", "install", "--quiet", "--user", *missing])
    if r.returncode != 0:
        # --user is refused inside venvs; fall back to a plain install.
        r = _run([py, "-m", "pip", "install", "--quiet", *missing])
    if r.returncode != 0:
        _fail(
            f"could not install {', '.join(missing)} into {py} — install manually:\n  {py} -m pip install {' '.join(missing)}\n{r.stderr}"
        )


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False, env=env)


def _load_manifest(pkg_dir: Path) -> dict[str, Any]:
    """Run the package's factory (declared in ea.plugins) and read its manifest.

    Executed in a subprocess against the host interpreter: the frozen CLI exe
    has no ``encre`` importable in itself, and the author's consent to run
    their own plugin code is expressed by invoking the CLI.  The market never
    imports anything this way — it reads the catalog entry baked at build
    time instead.
    """
    py = read_pyproject(pkg_dir)
    ep = find_ea_entrypoint(py)
    if ep is None:
        _fail('no [project.entry-points."ea.plugins"] entry found')
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(pkg_dir), os.environ.get("PYTHONPATH")]))}
    r = subprocess.run([_host_python(), "-c", _PROBE, ep[1]], cwd=str(pkg_dir), env=env, text=True, capture_output=True)
    if r.returncode != 0:
        _fail(f"loading plugin factory {ep[1]!r} failed:\n{r.stderr}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        _fail(f"factory {ep[1]!r} printed non-JSON: {r.stdout[:200]!r} ({exc})")
        return {}  # unreachable


# ── init ──────────────────────────────────────────────────────────────

_PYPROJECT_TMPL = """\
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{dist}"
version = "0.1.0"
description = "{title}"
readme = "README.md"
requires-python = ">=3.11"
authors = [{{ name = "Author", email = "author@example.com" }}]
license = {{ text = "Apache-2.0" }}
dependencies = ["encre-harness>=0.5.0"]

[project.entry-points."ea.plugins"]
{ep_name} = "{module}.plugin:create_plugin"

[tool.setuptools.packages.find]
where = ["."]
include = ["{module}*"]

[tool.setuptools.package-data]
{module} = ["ui/**/*", "skills/**/*"]
"""

_PLUGIN_PY = '''"""{title} — Encre Agent plugin."""

from __future__ import annotations

from encre.plugins.types import EncrePlugin, PluginManifest


class {cls}Plugin(EncrePlugin):
    manifest = PluginManifest(
        name="{name}",
        version="0.1.0",
        description="{title}",
        icon="puzzle",
        author="Author",
        license="Apache-2.0",
        tags=["example"],
        tier="user",
        min_ea_version="0.5.0",
        permissions=["tools:register"],
        provides_tools=[],
    )

    def get_tools(self):
        return []


def create_plugin() -> {cls}Plugin:
    """Entry point factory (ea.plugins)."""
    return {cls}Plugin()
'''

_README_TMPL = """\
# {dist}

Encre Agent plugin `{name}`, built with `encre-plugin`.

- `encre-plugin build` — policy check + wheel/sdist + sha256 digests
- `encre-plugin verify` — isolated install smoke test
- `encre-plugin publish` — PyPI upload + registry PR

Frontend assets (if any) must be PREBUILT into `{module}/ui/` before
publishing; the installer never runs a build step.
"""


def cmd_init(args: argparse.Namespace) -> None:
    name = args.name
    dist = name if name.startswith(PACKAGE_PREFIX) else f"{PACKAGE_PREFIX}{name}"
    module = normalize_module(dist)
    root = (Path(args.dir) if args.dir else Path.cwd()) / dist
    if root.exists():
        _fail(f"{root} already exists")
    title = name.replace("-", " ").strip().capitalize()
    cls = "".join(part.capitalize() for part in module.replace("_", " ").split() if part.isalnum()) or "Plugin"
    (root / module).mkdir(parents=True)
    (root / module / "ui").mkdir()
    (root / module / "ui" / ".gitkeep").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(_PYPROJECT_TMPL.format(dist=dist, title=title, ep_name=name, module=module), encoding="utf-8")
    (root / module / "__init__.py").write_text(f'"""{dist}."""\n', encoding="utf-8")
    (root / module / "plugin.py").write_text(_PLUGIN_PY.format(title=title, cls=cls, name=name), encoding="utf-8")
    (root / "README.md").write_text(_README_TMPL.format(dist=dist, name=name, module=module), encoding="utf-8")
    print(f"created {dist}/ (module {module}) — edit {module}/plugin.py, then run: encre-plugin build {dist}")


# ── build ─────────────────────────────────────────────────────────────


def _build_one(pkg_dir: Path, *, allowed_tiers: tuple[str, ...] | None = None, no_isolation: bool = False) -> dict[str, Any]:
    """Validate + build one package and return its catalog entry.

    Writes ``dist/ea-build-info.json`` alongside the wheel so the publish
    stage never re-hashes.  ``allowed_tiers`` widens the gate for the vendor
    pipeline; ``no_isolation`` skips the per-package venv/setuptools download
    (the batch build reuses the interpreter's already-present build backend).
    Raises SystemExit on policy or build failure.
    """
    py = read_pyproject(pkg_dir)
    manifest = _load_manifest(pkg_dir)
    group = (py.get("project", {}).get("entry-points") or {}).get("ea.plugins") or {}
    problems = validate_for_publish(py, manifest, extra_entry_points=max(0, len(group) - 1), allowed_tiers=allowed_tiers)
    if problems:
        print(f"publish policy violations ({pkg_dir.name}):")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    dist = str(py["project"]["name"])
    host = _host_python()
    if no_isolation:
        # A no-isolation build runs the backend in the host interpreter, so
        # wheel/setuptools must be importable there (the batch build relies
        # on this to skip a per-package venv + network fetch 249 times over).
        _ensure_pytools(host, "build", "wheel", "setuptools")
    else:
        _ensure_pytools(host, "build")
    dist_dir = pkg_dir / "dist"
    # Clean stale distributions from earlier builds: leftover old-version
    # wheels would otherwise leak into the artifacts dict and make twine
    # re-upload files PyPI already has ("file already exists" abort).
    if dist_dir.is_dir():
        for stale in dist_dir.iterdir():
            if stale.suffix in {".whl", ".gz"} or stale.name == BUILD_INFO:
                stale.unlink()
    cmd = [host, "-m", "build", "--outdir", "dist"] + (["--no-isolation"] if no_isolation else [])
    # PYTHONSAFEPATH: `python -m build` otherwise prepends the package dir to
    # sys.path, where a repo-level build.py would shadow pypa-build.
    r = _run(cmd, cwd=pkg_dir, env={**os.environ, "PYTHONSAFEPATH": "1"})
    if r.returncode != 0:
        print(f"python -m build failed:\n{r.stdout}\n{r.stderr}", file=sys.stderr)
        raise SystemExit(1)
    artifacts = {
        f.name: {"sha256": sha256_file(f), "size": f.stat().st_size}
        for f in sorted((pkg_dir / "dist").iterdir())
        if f.suffix in {".whl", ".gz"} and f.name != BUILD_INFO
    }
    if not artifacts:
        print("build produced no wheel/sdist", file=sys.stderr)
        raise SystemExit(1)
    entry = build_catalog_entry(manifest, pypi_package=dist, artifacts=artifacts, published_at=_now())
    info = pkg_dir / "dist" / BUILD_INFO
    info.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def cmd_build(args: argparse.Namespace) -> None:
    entry = _build_one(Path(args.pkg_dir).resolve())
    print(f"built {len(entry['artifacts'])} artifact(s); catalog entry -> dist/{BUILD_INFO}")


# ── vendor (batch build the official packages) ────────────────────────


def cmd_vendor(args: argparse.Namespace) -> None:
    """Batch-build every official EA package into a registry-ready staging dir.

    Walks ``--src`` for ``ea-tool-*`` / ``ea-skill-*`` / ``ea-plugin-*``
    directories, builds each wheel with the vendor tier gate (bundled
    ``system-default`` allowed), and drops ``catalog.d/<name>.json`` + the
    wheels under ``--out``.  This is a GENERATOR: it never uploads to PyPI
    or opens a PR — run ``encre-plugin publish`` (or the registry PR flow)
    per package after reviewing the staging output.
    """
    src = Path(args.src).resolve()
    out = Path(args.out).resolve()
    catalog_dir = out / "catalog.d"
    wheels_dir = out / "wheels"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    wheels_dir.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        _fail(f"no such package source: {src}")

    dirs = [
        d
        for d in sorted(src.iterdir())
        if d.is_dir()
        and (d / "pyproject.toml").is_file()
        and d.name.startswith(RESERVED_PREFIXES)
        and (not args.filter or any(f in d.name for f in args.filter))
    ]
    if not dirs:
        _fail(f"no {RESERVED_PREFIXES} packages under {src}")

    built, failed = 0, []
    for pkg in dirs:
        try:
            entry = _build_one(pkg, allowed_tiers=VENDOR_TIERS, no_isolation=not args.isolation)
        except SystemExit:
            failed.append(pkg.name)
            if not args.keep_going:
                _fail(f"{pkg.name} failed (use --keep-going to continue past failures)")
            continue
        name = str(entry["name"])
        (catalog_dir / f"{name}.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for fname in entry["artifacts"]:
            src_file = pkg / "dist" / fname
            if src_file.is_file():
                shutil.copy2(src_file, wheels_dir / fname)
        built += 1

    print(f"vendor build: {built} built, {len(failed)} failed -> {out}")
    if failed:
        print("failed packages: " + ", ".join(failed), file=sys.stderr)
    print(
        "next: review catalog.d/, then `encre-plugin publish` each wheel "
        "(twine + registry PR); or point the registry CI at this staging dir."
    )


# ── verify ────────────────────────────────────────────────────────────

_PROBE = """
import importlib, json, sys
module_name, _, factory = sys.argv[1].partition(":")
plugin = getattr(importlib.import_module(module_name), factory)()
print(json.dumps(plugin.manifest.to_dict()))
"""


def cmd_verify(args: argparse.Namespace) -> None:
    pkg_dir = Path(args.pkg_dir).resolve()
    py = read_pyproject(pkg_dir)
    ep = find_ea_entrypoint(py)
    if ep is None:
        _fail("no ea.plugins entry point to verify")
    wheels = sorted((pkg_dir / "dist").glob("*.whl"))
    if not wheels:
        _fail("no wheel in dist/ — run `encre-plugin build` first")
    tmp = Path(tempfile.mkdtemp(prefix="ea-verify-"))
    try:
        py_bin = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        host = _host_python()
        # System site-packages stay visible: the probe venv must prove the
        # WHEEL is well-formed, not re-resolve the Encre Agent SDK (which is
        # not on PyPI yet during the 0.5.x transition — a bare venv could
        # only fail for reasons unrelated to the package under test).
        r = _run([host, "-m", "venv", "--system-site-packages", str(tmp)])
        if r.returncode != 0:
            _fail(f"venv creation failed: {r.stderr}")
        venv_py = tmp / py_bin
        r = _run([str(venv_py), "-m", "pip", "install", "--quiet", str(wheels[-1])] + (["--no-deps"] if args.no_deps else []))
        if r.returncode != 0:
            _fail(f"wheel install failed:\n{r.stderr}")
        r = _run([str(venv_py), "-c", _PROBE, ep[1]])
        if r.returncode != 0:
            hint = ""
            if "No module named 'encre'" in r.stderr or "ModuleNotFoundError" in r.stderr:
                hint = (
                    "\n  hint: the probe venv cannot see the Encre Agent SDK — until "
                    "`encre-harness` is on PyPI, run verify with\n        PYTHONPATH "
                    "pointing at your local harness/ and core/ source trees."
                )
            _fail(f"entry point probe failed:\n{r.stderr}{hint}")
        manifest = json.loads(r.stdout)
        if not manifest.get("name") or not manifest.get("version"):
            _fail("probe manifest is missing name/version")
        print(f"verify ok: {manifest.get('name')} v{manifest.get('version')} imports and declares a valid manifest")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── publish ───────────────────────────────────────────────────────────

# Canonical registry (central repository) for the Encre Agent plugin market.
REGISTRY_URL = "https://github.com/mf2023/ea-cwh.git"


def _registry_cache_dir() -> Path:
    """Persistent home for the CLI-managed registry clone."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "encre-plugin-cli" / "registry"


def _resolve_registry(args: argparse.Namespace) -> Path:
    """Local checkout to open the PR from: --registry-dir, else a cached clone.

    A missing cache clone is fetched shallowly from ``--registry-url``; an
    existing one is re-synced onto the remote's default branch so the entry
    commit is always cut from current main.
    """
    import re

    url = args.registry_url
    if not re.fullmatch(r"(https?://|file://|ssh://|git@)[^\s]+", url or ""):
        _fail(f"--registry-url does not look like a git remote: {url!r}")
    if args.registry_dir:
        registry = Path(args.registry_dir).resolve()
        if not (registry / ".git").exists():
            _fail(f"{registry} is not a git checkout")
    else:
        registry = _registry_cache_dir()
        if not (registry / ".git").exists():
            registry.parent.mkdir(parents=True, exist_ok=True)
            r = _run(["git", "clone", "--quiet", url, str(registry)])
            if r.returncode != 0:
                _fail(f"cloning the registry failed:\n{r.stderr}")
    for cmd in (
        ["git", "fetch", "--quiet", "origin"],
        ["git", "checkout", "--quiet", "-f", "main"],
        ["git", "reset", "--quiet", "--hard", "origin/main"],
    ):
        r = _run(cmd, cwd=registry)
        if r.returncode != 0:
            _fail(f"`{' '.join(cmd)}` in {registry} failed:\n{r.stderr}")
    return registry


def cmd_publish(args: argparse.Namespace) -> None:
    pkg_dir = Path(args.pkg_dir).resolve()
    info_path = pkg_dir / "dist" / BUILD_INFO
    if not info_path.exists():
        _fail(f"dist/{BUILD_INFO} missing — run `encre-plugin build` first")
    entry = json.loads(info_path.read_text(encoding="utf-8"))
    dist_files = [str(f) for f in sorted((pkg_dir / "dist").iterdir()) if f.suffix in {".whl", ".gz"}]
    if args.dry_run:
        print(f"[dry-run] would upload: {dist_files}")
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return
    if args.no_upload:
        print("skipping PyPI upload (--no-upload); registry PR half only")
    else:
        host = _host_python()
        _ensure_pytools(host, "twine")
        r = _run([host, "-m", "twine", "upload", *dist_files])
        if r.returncode != 0:
            _fail(f"twine upload failed:\n{r.stdout}\n{r.stderr}")
        print(f"uploaded {entry['pypi_package']} {entry['version']} to PyPI")
    registry = _resolve_registry(args)
    _registry_pr(registry, entry)


def _registry_pr(registry: Path, entry: dict[str, Any]) -> None:
    """Drop the catalog entry into the checkout and open the registry PR.

    The branch is cut from origin/main BEFORE the entry is written, so the
    PR can only ever carry the single catalogue file — never leftover
    worktree state.
    """
    name, version = str(entry["name"]), str(entry["version"])
    branch = f"publish/{name}-{version}"
    r = _run(["git", "checkout", "-B", branch, "origin/main"], cwd=registry)
    if r.returncode != 0:
        _fail(f"cannot branch the registry checkout:\n{r.stderr}")
    cdir = registry / "catalog.d"
    cdir.mkdir(exist_ok=True)
    target = cdir / f"{name}.json"
    target.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rel = str(target.relative_to(registry))
    for cmd in (
        ["git", "add", rel],
        ["git", "commit", "-m", f"catalog: {name} {version}"],
        ["git", "push", "-u", "origin", branch],
    ):
        r = _run(cmd, cwd=registry)
        if r.returncode != 0:
            print(f"warning: `{' '.join(cmd)}` failed:\n{r.stderr}", file=sys.stderr)
            print("entry written locally; open the PR manually", file=sys.stderr)
            return
    body = (
        f"Published `{entry['pypi_package']}=={version}` to PyPI.\n\n"
        f"CI verifies the merged catalog schema and every artifact digest "
        f"against PyPI before this may merge."
    )
    r = _run(["gh", "pr", "create", "--fill", "--title", f"catalog: {name} {version}", "--body", body, "--head", branch], cwd=registry)
    if r.returncode != 0:
        print(f"warning: gh pr create failed:\n{r.stderr}", file=sys.stderr)
        print(f"branch `{branch}` pushed — open the PR manually", file=sys.stderr)
    else:
        print(r.stdout.strip())


# ── catalog ───────────────────────────────────────────────────────────


def cmd_catalog_regen(args: argparse.Namespace) -> None:
    from ea_cwh import validate_catalog

    registry = Path(args.registry)
    files = sorted((registry / "catalog.d").glob("*.json"))
    if not files:
        _fail(f"no entry files under {registry / 'catalog.d'}")
    entries = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    doc = merge_catalog(entries, registry_url=args.registry_url)
    problems = validate_catalog(doc)
    if problems:
        print("catalog violations (refusing to write):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        raise SystemExit(1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(doc['plugins'])} entries -> {out}")


def cmd_catalog_check(args: argparse.Namespace) -> None:
    from ea_cwh import validate_catalog

    doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
    problems = validate_catalog(doc)
    for p in problems:
        print(f"  - {p}")
    if problems:
        raise SystemExit(1)
    print("catalog ok")


# ── plumbing ──────────────────────────────────────────────────────────


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="encre-plugin", description="Encre Agent plugin market unified CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold an ea-plugin-* package")
    p.add_argument("name")
    p.add_argument("--dir", help="parent directory (default: cwd)")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("build", help="policy check + wheel/sdist + sha256 digests")
    p.add_argument("pkg_dir", nargs="?", default=".")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("vendor", help="batch-build the official EA packages into a registry-ready staging dir")
    p.add_argument("--src", default="core/ea_tools", help="official package source tree (default: core/ea_tools)")
    p.add_argument("--out", default="dist/vendor", help="staging output dir (default: dist/vendor)")
    p.add_argument("--filter", action="append", help="only packages whose name contains this substring (repeatable)")
    p.add_argument("--keep-going", action="store_true", help="continue past per-package failures")
    p.add_argument("--isolation", action="store_true", help="build each wheel in a fresh isolated env (slower, needs network)")
    p.set_defaults(fn=cmd_vendor)

    p = sub.add_parser("verify", help="isolated install + entry-point smoke test")
    p.add_argument("pkg_dir", nargs="?", default=".")
    p.add_argument("--no-deps", action="store_true", help="skip dependency resolution")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("publish", help="upload to PyPI + registry PR")
    p.add_argument("pkg_dir", nargs="?", default=".")
    p.add_argument("--registry-dir", help="existing registry checkout (default: cached clone of --registry-url)")
    p.add_argument("--registry-url", default=REGISTRY_URL, help=f"registry git remote (default: {REGISTRY_URL})")
    p.add_argument("--no-upload", action="store_true", help="skip twine; open the registry PR only")
    p.add_argument("--dry-run", action="store_true", help="print instead of uploading")
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("catalog", help="central index maintenance")
    csub = p.add_subparsers(dest="catalog_command", required=True)
    c = csub.add_parser("regen", help="merge catalog.d/*.json into catalog.json")
    c.add_argument("--registry", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--registry-url", default="https://pypi.org")
    c.set_defaults(fn=cmd_catalog_regen)
    c = csub.add_parser("check", help="validate a catalog.json file")
    c.add_argument("file")
    c.set_defaults(fn=cmd_catalog_check)

    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
