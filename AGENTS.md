# AGENTS.md

## Purpose

Project-specific guidance for `pnutils`, plus links to reusable standards in
the sibling [coding-agent-standards repository](../coding-agent-standards/AGENTS.md).

The linked documents cover different file types and scenarios. They are not
all applicable to every project or task. Use a standard only when its stated
scope matches the files or behavior being changed; do not add a Makefile,
Dockerfile, Helm chart, or operator-script layout just to satisfy an unrelated
standard.

Before applying a relevant shared standard, read it from the sibling checkout.
If the checkout or requested standard is missing or unreadable, tell the user
explicitly; do not silently skip it or pretend its guidance was followed.

## pnutils Project Guidance

- This is a typed, pip-installable Python package using the `src/pnutils/`
  layout. Keep the public package surface focused on `k8s`, `scripts`, and
  `ssl_certs`, plus shared package internals such as `exceptions.py`.
- Support Python 3.9 and newer. Preserve that floor in syntax, dependencies,
  packaging metadata, and tests unless the project explicitly changes it.
- Keep the core install dependency-free. Put optional third-party packages in
  the appropriate `pyproject.toml` extra and load them lazily through
  `pnutils._internal.optional.require` where practical.
- Keep public exports deliberate: update the relevant submodule's `__all__`
  and package-root lazy exports when changing the supported API.
- Keep credentials and private key material out of logs, command-line values,
  and object representations. Preserve preview-by-default and explicit apply
  behavior for mutating Kubernetes workflows.
- Add or update focused tests under `tests/` for behavior and public contracts.
  Use `uv` for environment and package management.
- Keep generated API documentation source-driven: add docstrings for public
  modules, classes, functions, and methods; do not maintain duplicate symbol
  inventories in READMEs. Update module `__all__` when the supported surface
  changes.
- For public API changes, pass the Ruff docstring rules and strict MkDocs build
  enforced by `.github/workflows/docs.yml`.

## Shared Standards Index

Use the [coding-agent-standards index](../coding-agent-standards/AGENTS.md) to
discover shared guidance. Read the specific linked standard(s) that apply to
the files or behavior in the current task; the index may grow as new standards
are added, so do not rely on a copied list here.

## Validation

Run checks from the repository root:

```bash
uv sync --all-extras --group docs
uv run pytest
uv run ruff check .
uv run ruff check src/pnutils --select D100,D101,D102,D103
uv run mypy
uv run mkdocs build --strict
```

For packaging changes, also build and inspect the distribution with the
project's `build` and `twine` development dependencies.