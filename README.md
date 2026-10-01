<div align="center">

# ea-plugin-cli

**Encre Agent** Plugin Market Unified CLI

[PyPI](https://pypi.org/project/ea-plugin-cli/) | [Changelog](https://github.com/mf2023/ea-cwh/releases)

<a href="https://github.com/mf2023/ea-plugin-cli" target="_blank">
    <img alt="GitHub" src="https://img.shields.io/badge/GitHub-ea--plugin--cli-181717?style=flat-square&logo=github"/>
</a>
<a href="https://pypi.org/project/ea-plugin-cli/" target="_blank">
    <img alt="PyPI" src="https://img.shields.io/badge/PyPI-ea--plugin--cli-3775A9?style=flat-square&logo=pypi"/>
</a>
<a href="https://www.python.org/" target="_blank">
    <img alt="Python" src="https://img.shields.io/badge/Python-3.14%2B-3776AB?style=flat-square&logo=python&logoColor=white"/>
</a>
<a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank">
    <img alt="License" src="https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=flat-square"/>
</a>

The single toolchain that takes a plugin from scaffold to the central registry. After publishing, the registry CI merges catalog entries into the `ea-cwh` index package, which the market backend renders locally.

</div>

<h2 align="center">🤖 What is ea-plugin-cli?</h2>

`encre-plugin` covers the full plugin lifecycle for the **Encre Agent** plugin market:

- **Scaffold** — generate a ready-to-publish `ea-plugin-<name>` package skeleton
- **Build** — enforce central-registry policy, compile a wheel, digest every artifact
- **Verify** — smoke-test the wheel in an isolated venv (install, import, manifest probe)
- **Publish** — upload to PyPI, then open the central-registry catalog PR automatically

### Installation

```
Ready to run: python build.py cli compiles a single-file executable for the
current platform (build/cli/encre-plugin[.exe] — no pip install required; it
shells out to a host Python found on PATH for build/venv/twine and
auto-installs any missing components on first use)

Or as a module: pip install ea-plugin-cli   # development: pip install -e cli/
```

<h2 align="center">⭐ Commands</h2>

| Command | Purpose |
|:------|:------|
| `encre-plugin init <name>` | Scaffold `ea-plugin-<name>` (pyproject + `ea.plugins` entry point + `create_plugin` factory + `ui/` directory) |
| `encre-plugin build [pkg_dir]` | Run central-registry policy checks → `python -m build` (wheel + sdist) → sha256 per artifact → write `dist/ea-build-info.json` (draft catalog entry) |
| `encre-plugin vendor` | Official batch build: walk `core/ea_tools/*` (accepts the `ea-tool-`/`ea-skill-` namespaces and the `system-default` tier), build + digest each package with the same policy, stage `catalog.d/<name>.json` and wheels into `dist/vendor/` (staging only — no upload, no PR). Invoked by `python build.py vendor` |
| `encre-plugin verify [pkg_dir]` | Install the wheel into a throwaway venv, import the entry-point factory and probe the manifest |
| `encre-plugin publish [pkg_dir]` | `twine upload` to PyPI, then open a central-registry PR: the entry lands in the registry checkout's `catalog.d/`, git branch is cut from origin/main, committed, pushed, `gh pr create` |
| `encre-plugin catalog regen` | Merge the registry's `catalog.d/*.json` → validate → write the `ea-cwh` `catalog.json` |
| `encre-plugin catalog check <file>` | Validate a single `catalog.json` |

<h2 align="center">📦 publish ↔ Central Registry</h2>

- `--registry-dir <dir>` — use an existing registry checkout; when omitted, the CLI clones `https://github.com/mf2023/ea-cwh.git` into a local cache (`%LOCALAPPDATA%` / `~/.cache` → `encre-plugin-cli/registry`) and hard-syncs it to origin/main every run
- `--registry-url <git url>` — override the default remote (point at a fork or a local bare repository to rehearse the whole flow)
- `--no-upload` — skip the twine upload, run the registry-PR half only
- `--dry-run` — print the files that would be uploaded and the catalog entry; no remote action
- The PR branch carries exactly one file (`catalog.d/<name>.json`): the branch is cut from origin/main **before** the entry is written, so no dirty worktree state can leak into the PR
- Transition note: `verify` needs `encre-harness` to be importable inside the probe venv; for local development either `pip install -e harness/` or use `--no-deps` plus a `PYTHONPATH` pointing at the source trees

<h2 align="center">🛡️ Central Registry Policy (enforced at build time)</h2>

| # | Rule |
|:------|:------|
| 1 | The PyPI distribution name must start with a reserved prefix — `ea-plugin-` (community) or `ea-tool-` / `ea-skill-` (vendor namespaces) |
| 2 | Must declare `[project.entry-points."ea.plugins"]`; **one wheel carries exactly one plugin** |
| 3 | Manifest `name/version/description/author/license` must be non-empty, `manifest.version` must match the pyproject version, and `tier` must be `user` (vendor builds may also ship `system-default`) |
| 4 | Must declare `min_ea_version` or `engines["encre"]` |
| 5 | Packages that declare UI (`provides_ui` / `contributes.ui`) must ship it **pre-compiled** under `ui/` and include it as package data — the install side never executes builds |

<h2 align="center">🔧 Environment Variables</h2>

- PyPI credentials: `TWINE_USERNAME` / `TWINE_PASSWORD` (CI uses trusted publishing and needs neither)
- The automatic PR requires the registry checkout to have `git remote add origin` and a logged-in `gh` CLI

<h2 align="center">🌏 Community & License</h2>

- Issues and PRs are welcome!
- Central registry: https://github.com/mf2023/ea-cwh
- CLI repository: https://github.com/mf2023/ea-plugin-cli

<div align="center">

## 📄 License

<p align="center">
  <a href="https://www.apache.org/licenses/LICENSE-2.0">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache License 2.0">
  </a>
</p>

This project uses the **Apache License 2.0**.

</div>
