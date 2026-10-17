# Name Change — Agent Guardian → Strixora

## Summary

| | |
|---|---|
| **Previous name** | Agent Guardian |
| **New name** | Strixora |
| **Effective date** | 2026-10-17 |
| **Previous repository** | https://github.com/Wrybill-env/AgentGuardian |
| **Canonical repository** | https://github.com/Wrybill-env/Strixora |

## Purpose

"Agent Guardian" was a descriptive working title for this project (an external
safety/reliability layer for AI coding agents). The project has been renamed to
**Strixora** as its permanent product name, and the GitHub repository was renamed
in place so that issues, stars, releases, tags, branches and history are
preserved.

## Relationship to the existing `v0.1.0` release

This is a **branding change only**. It is **not** a new software release:

- The version stays **`0.1.0`**.
- The **`v0.1.0`** git tag and GitHub Release are unchanged; the tag still points
  at commit `aae47cb` and no commit, tag, or history was rewritten.
- No code behaviour, safety semantics, capabilities, CLI behaviour, or test
  expectation was changed by this rename.
- Nothing was published to PyPI before or after the rename.

## What changed, and what deliberately did not

### Changed (user-facing branding)

- Product name in `README.md`, documentation, module docstrings, the CLI
  description, the CLI help banner, the license copyright line, the social
  preview asset, and release presentation text.
- Distribution/package name in `pyproject.toml`: `agent-guardian` → `strixora`.
- Repository URLs, issue links, and badges → the new canonical URL.
- The repository name on GitHub: `Wrybill-env/AgentGuardian` →
  `Wrybill-env/Strixora`.

### Unchanged (intentional)

| Identifier | Value | Reason |
|---|---|---|
| Python import package | `guardian` | Keeps every import (`import guardian`, `guardian.core`, …), the public API, and the existing test suite working. Renaming the module would touch nearly every file with no functional benefit. |
| CLI command / entry point | `guardian`, `python -m guardian` | Preserved so existing scripts, docs and automation keep working. |
| ACP client identifier | `"agent-guardian"` | A wire-level protocol identifier sent to agents; kept for compatibility with anything that logs or allowlists the client name. |
| Local checkout directory | `C:\Projects\AgentGuardian` | The local working directory was not moved; local paths that reference it remain accurate. |
| `v0.1.0` tag and release | unchanged | History preserved. |

## Compatibility and migration

- **Existing code keeps working**: `import guardian` is unchanged and the
  `guardian` CLI command is unchanged.
- **Installing by distribution name**: the distribution is now named `strixora`.
  Nothing was ever published to PyPI under either name, so install from the
  repository. `pip install -e .` still works and still provides the `guardian`
  command.
- **Git remotes**: the previous URL redirects to the new one, but updating is
  recommended:

  ```bash
  git remote set-url origin https://github.com/Wrybill-env/Strixora.git
  ```

## Scope of the name

"Strixora" has been adopted as the product name. **No claim is made** that the
name is globally unique, trademark-cleared, or legally available in any
jurisdiction — that has not been verified.
