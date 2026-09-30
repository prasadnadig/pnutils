# pnutils

A grab-bag of Python utility modules, one submodule per context: Kubernetes,
shell scripting, and X.509 certificates.

Requires Python 3.9 or newer. CI tests Python 3.9 through 3.14.

## Install

```bash
uv add pnutils                   # core, zero dependencies
uv add 'pnutils[k8s]'            # + kubernetes client
uv add 'pnutils[all]'            # all optional runtime integrations
```

The equivalent pip installs are:

```bash
pip install pnutils              # core, zero dependencies
pip install 'pnutils[k8s]'       # + kubernetes client
pip install 'pnutils[all]'       # everything
```

## Quickstart

### Choose a Kubernetes backend

The Python API and CLI support three backend selections:

- `auto` (default): use the Python client when the `k8s` extra is installed;
    otherwise use `kubectl`. A client configuration or API failure is not
    silently retried through `kubectl`.
- `client`: use the official Kubernetes Python client. Install
    `pnutils[k8s]`; values are sent from memory in the API request.
- `kubectl`: invoke the `kubectl` binary on `PATH`. This avoids the Python
    extra, but stages Secret contents in temporary files during apply.

Example preview, interactive apply, and non-interactive apply:

```bash
export SECRET_VALUE=REPLACE_ME
pnutils-k8s push-env-secret --name payments-token --namespace team-a
pnutils-k8s push-env-secret --name payments-token --namespace team-a --apply
pnutils-k8s push-env-secret --name payments-token --namespace team-a \
        --backend kubectl --apply --force
```

Preview is the default, contacts no cluster, and writes no files. Applying
requires `--apply`; interactive runs show the target and require typing `yes`.
Use `--force` only with `--apply` in trusted automation. Both backends use
Server-Side Apply with the `pnutils` field manager; conflicts with fields owned
by another manager are reported instead of silently taking ownership.

## Layout

```
src/pnutils/
├── __about__.py        # single source of truth for the version
├── exceptions.py       # PnUtilsError and friends
├── _internal/          # private helpers (optional-dependency loading, ...)
├── k8s/                # Kubernetes helpers        [extra: k8s]
├── scripts/            # subprocess, logging and secret-file helpers
└── ssl_certs/          # X.509 certificate generation (needs openssl)
```

Submodules are loaded lazily, so importing `pnutils` never pulls in an optional
dependency you have not installed.

## Module guides

- [Kubernetes (`pnutils.k8s`)](src/pnutils/k8s/README.md): cluster reads,
    Secret specifications and apply, TLS-for-Service helpers, and the `pnutils-k8s`
    command.
- [Script helpers (`pnutils.scripts`)](src/pnutils/scripts/README.md): safe
    subprocess execution, logging setup, and short-lived secret files.
- [X.509 certificates (`pnutils.ssl_certs`)](src/pnutils/ssl_certs/README.md):
    generate self-signed certificates or load an existing PEM pair.

The private `pnutils._internal` package supports these public modules and is
not a consumer-facing API.

## API Reference

The [generated API reference](https://prasadnadig.github.io/pnutils/) documents
the public modules from their source docstrings. It is built and deployed by
GitHub Actions; module guides focus on usage and operational behavior.

### Update and preview the documentation

Edit public API docstrings under `src/pnutils/` to update generated API pages.
Edit the Markdown files under [`docs/`](docs/) to update the overview and
handwritten reference pages. Do not edit `site/`; MkDocs regenerates it.

From the repository root, install the documentation tools and start a live
preview:

```bash
uv sync --group docs
uv run mkdocs serve
```

Open <http://127.0.0.1:8000>. The preview rebuilds when source docstrings or
documentation files change. To generate a static site instead, run
`uv run mkdocs build --strict`; the output is written to `site/`.

### Documentation CI and publishing

The [documentation workflow](.github/workflows/docs.yml) runs for pull requests
and pushes to `main`. It checks public API docstrings with Ruff and builds the
site with strict MkDocs validation. Pull requests are checked but not published;
a successful push to `main` uploads the generated `site/` and deploys it to
GitHub Pages.

## Usage

```python
from pnutils.scripts import run_command
from pnutils.ssl_certs import generate_self_signed_certificate

run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
cert = generate_self_signed_certificate("payments", dns_names=["payments.example"])
```

```python
from pnutils.k8s import KubernetesClient, SecretSpec, apply_secret, secret_data_from_env

with KubernetesClient() as kube:
    kube.list_pods(namespace="default")

# Apply a Secret from an environment variable.
data = secret_data_from_env({"token": "SECRET_VALUE"})
spec = SecretSpec.generic("payments-token", "team-a", data)
apply_secret(spec)
```

The same workflows are available as a CLI, which previews by default and asks
for confirmation before touching a cluster:

```bash
export SECRET_VALUE=...
pnutils-k8s push-env-secret --name payments-token --namespace team-a --apply
pnutils-k8s push-tls-secret --name payments-tls --namespace team-a \
    --service-name payments --apply
```

Secrets reach the cluster through the Python client by default and fall back to
the `kubectl` binary when the `k8s` extra is not installed; pick explicitly with
`--backend`.

## Security

The Python client keeps Secret values in memory and sends them in the HTTPS API
request body. The `kubectl` apply backend stages values in files with `0600`
permissions inside a `0700` temporary directory so they do not appear in
process arguments. On normal exit, pnutils overwrites and removes those files.
This is best-effort, not guaranteed erasure: `SIGKILL` or power loss can leave
files behind, and overwrite-in-place may not erase old blocks on APFS or other
copy-on-write or log-structured filesystems. Backups and snapshots may retain
copies; environment variables and shell history may retain the source value.

If the CLI warns that a temporary path could not be removed, delete that exact
path and rotate the credential or reissue the certificate. TLS preview does not
generate a certificate or write files; generated key material is created only
when `--apply` is used.

## Adding a new submodule

1. Create `src/pnutils/<name>/__init__.py` re-exporting the public API.
2. Add `"<name>"` to `_SUBMODULES` in [src/pnutils/__init__.py](src/pnutils/__init__.py).
3. If it needs a third-party package, add an extra in
   [pyproject.toml](pyproject.toml) and load it via
   `pnutils._internal.optional.require(...)`.
4. Add tests under `tests/test_<name>.py`.

## Development

```bash
uv sync --all-extras --group docs
uv run pytest
uv run ruff check .
uv run ruff check src/pnutils --select D100,D101,D102,D103
uv run mypy
uv run mkdocs build --strict
```

## Releasing

Bump `__version__` in [src/pnutils/__about__.py](src/pnutils/__about__.py), tag
the commit, and publish a GitHub Release — the publish workflow builds and
uploads to PyPI via trusted publishing.

## License

MIT
