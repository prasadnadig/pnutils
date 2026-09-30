"""Create or replace Kubernetes Secrets.

Two backends are available. ``client`` talks to the API directly through the
official ``kubernetes`` package: values go from memory straight into the HTTPS
request body, never touching disk or a process argument list. ``kubectl``
shells out to the binary, which is useful where no Python packages can be
installed; there, values are staged in ``0600`` files so they stay out of
``kubectl``'s argv, and those files are shredded afterwards.

``auto`` prefers ``client`` and falls back to ``kubectl``.

Security note: the client backend keeps values in memory; the kubectl backend
stages them in ``0600`` files inside a ``0700`` directory and shreds them on
normal cleanup. SIGKILL or power loss may leave files behind, and overwrite is
best-effort on APFS or other copy-on-write filesystems. Backups, snapshots,
environment variables, and shell history may retain values. If cleanup warns
about a path, delete it and rotate the credential or reissue the certificate.
"""

from __future__ import annotations

import os
from base64 import b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.util import find_spec
from shutil import which

from pnutils._internal.optional import require
from pnutils.exceptions import (
    ConfirmationDeclined,
    KubeApiError,
    MissingDependencyError,
    ValidationError,
)
from pnutils.k8s.kubectl import ClusterTarget
from pnutils.k8s.tls import CertificatePair

__all__ = ["SecretSpec", "apply_secret", "get_cluster_target", "secret_data_from_env"]

_BACKENDS = ("client", "kubectl")
_GENERIC_TYPE = "Opaque"
_TLS_TYPE = "kubernetes.io/tls"
_TLS_SECRET_KEYS = ("tls.crt", "tls.key")
_FIELD_MANAGER = "pnutils"

ConfirmSecretCallback = Callable[[ClusterTarget, "SecretSpec"], bool]


@dataclass(frozen=True)
class SecretSpec:
    """Desired Secret contents, ready to apply to a cluster.

    ``data`` contains plaintext values and is excluded from ``repr``. Use the
    named constructors to create generic or TLS Secrets.

    Attributes:
        name: Kubernetes Secret name.
        namespace: Namespace where the Secret will be stored.
        secret_type: Kubernetes Secret type, such as ``Opaque`` or
            ``kubernetes.io/tls``.
        data: Plaintext values keyed by the names workloads will read. Values
            are hidden from the object's string representation.
    """

    name: str
    namespace: str
    secret_type: str
    data: Mapping[str, str] = field(repr=False)

    @classmethod
    def generic(cls, name: str, namespace: str, data: Mapping[str, str]) -> SecretSpec:
        """Build an Opaque Secret from key/value pairs.

        Args:
            name: Name to assign to the Secret.
            namespace: Namespace where the Secret will be created or updated.
            data: Non-empty mapping from Secret key to plaintext value.

        Returns:
            A specification that can be passed to :func:`apply_secret`.

        Raises:
            ValidationError: If ``data`` is empty.
        """
        if not data:
            raise ValidationError("at least one Secret key/value pair is required")
        return cls(name, namespace, _GENERIC_TYPE, dict(data))

    @classmethod
    def tls(
        cls, name: str, namespace: str, certificate: CertificatePair
    ) -> SecretSpec:
        """Build a TLS Secret from a certificate and its matching private key.

        Args:
            name: Name to assign to the Secret.
            namespace: Namespace where the Secret will be created or updated.
            certificate: PEM certificate and private key to store as ``tls.crt``
                and ``tls.key``.

        Returns:
            A ``kubernetes.io/tls`` specification for :func:`apply_secret`.

        Note:
            The private key is held in memory and is excluded from the
            specification's string representation.
        """
        return cls(
            name,
            namespace,
            _TLS_TYPE,
            {
                "tls.crt": certificate.certificate_pem,
                "tls.key": certificate.private_key_pem,
            },
        )

    @property
    def keys(self) -> tuple[str, ...]:
        """Return the Secret data keys without exposing their values.

        Returns:
            The key names in the same order they appear in ``data``.
        """
        return tuple(self.data)

    def summary(self) -> list[str]:
        """Describe the intended Secret change without including secret values.

        Returns:
            Display-ready lines with the Secret name, type, namespace, and keys.
        """
        return [
            f"Secret {self.name} ({self.secret_type}) in namespace {self.namespace}",
            f"keys managed by pnutils: {', '.join(self.keys)}",
        ]


def _resolve_backend(backend: str = "auto") -> str:
    """Pick ``client`` or ``kubectl``; ``auto`` prefers the Python client.

    Raises :class:`ValidationError` for an unknown choice and
    :class:`MissingDependencyError` when auto-detection finds neither backend.
    """
    if backend in _BACKENDS:
        return backend
    if backend != "auto":
        raise ValidationError(f"backend must be 'auto' or one of {_BACKENDS}, got {backend!r}")
    if find_spec("kubernetes") is not None:
        return "client"
    if which("kubectl") is not None:
        return "kubectl"
    raise MissingDependencyError("kubernetes", "k8s")


def secret_data_from_env(key_to_env: Mapping[str, str]) -> dict[str, str]:
    """Read Secret values from environment variables.

    Map each Kubernetes Secret key to the name of the environment variable
    containing its value. This keeps values out of command-line arguments.

    Args:
        key_to_env: Mapping such as ``{"password": "DB_PASSWORD"}``.

    Returns:
        A new mapping from Secret keys to the values read from the environment.

    Raises:
        ValidationError: If any requested environment variable is unset or empty.

    Example:
        Read a password without putting its value in the command line::

            data = secret_data_from_env({"password": "DB_PASSWORD"})
            spec = SecretSpec.generic("database", "default", data)
    """
    values: dict[str, str] = {}
    for secret_key, env_var in key_to_env.items():
        value = os.environ.get(env_var)
        if not value:
            raise ValidationError(f"environment variable {env_var} is not set or is empty")
        values[secret_key] = value
    return values


def get_cluster_target(backend: str = "auto") -> ClusterTarget:
    """Identify the cluster selected by the chosen backend's configuration.

    This reads the client configuration or current kubectl context; it does
    not make a request to the Kubernetes API.

    Args:
        backend: ``"client"`` or ``"kubectl"`` to choose explicitly.
            ``"auto"`` prefers the Python client, then falls back to kubectl.

    Returns:
        The context, cluster, API server URL, and user selected by the backend.

    Raises:
        ValidationError: If ``backend`` is not ``"auto"``, ``"client"``, or
            ``"kubectl"``.
        MissingDependencyError: If automatic selection finds no usable backend.
    """
    if _resolve_backend(backend) == "client":
        from pnutils.k8s.client import _current_target

        return _current_target()

    from pnutils.k8s.kubectl import describe_target

    return describe_target()


def _apply_with_client(spec: SecretSpec) -> str:
    from pnutils.k8s.client import KubernetesClient

    models = require("kubernetes.client", "k8s")
    rest = require("kubernetes.client.rest", "k8s")
    encoded_data = {
        key: b64encode(value.encode("utf-8")).decode("ascii")
        for key, value in spec.data.items()
    }
    body = models.V1Secret(
        metadata=models.V1ObjectMeta(name=spec.name, namespace=spec.namespace),
        type=spec.secret_type,
        data=encoded_data,
    )
    try:
        with KubernetesClient() as kube:
            kube.core.patch_namespaced_secret(
                name=spec.name,
                namespace=spec.namespace,
                body=body,
                field_manager=_FIELD_MANAGER,
                _content_type="application/apply-patch+yaml",
            )
    except rest.ApiException as exc:
        raise KubeApiError(
            f"could not apply Secret {spec.name}: {exc.reason}", exc.status
        ) from exc
    return f"secret/{spec.name} applied"


def _apply_with_kubectl(spec: SecretSpec) -> str:
    from pnutils.k8s.kubectl import apply_manifest, render_manifest
    from pnutils.scripts.secure import temporary_secret_directory, write_secret_file

    # Values are staged in 0600 files so they never appear in kubectl's argv.
    with temporary_secret_directory(prefix="pnutils-secret-") as workdir:
        paths = {
            key: write_secret_file(workdir, key.replace("/", "_"), value)
            for key, value in spec.data.items()
        }
        kind = "tls" if spec.secret_type == _TLS_TYPE else "generic"
        args = ["-n", spec.namespace, "create", "secret", kind, spec.name]
        if kind == "tls":
            args += [f"--cert={paths['tls.crt']}", f"--key={paths['tls.key']}"]
        else:
            args += [f"--from-file={key}={path}" for key, path in paths.items()]
        manifest = render_manifest(args)
    return apply_manifest(manifest, server_side=True, field_manager=_FIELD_MANAGER)


def apply_secret(
    spec: SecretSpec,
    confirm: ConfirmSecretCallback | None = None,
    backend: str = "auto",
) -> str:
    """Create or update a Secret using Kubernetes Server-Side Apply.

    Both backends record ``pnutils`` as the field manager. Fields previously
    managed by pnutils are reconciled with ``spec``; a conflict with another
    field manager raises an error instead of silently taking ownership.

    This function changes the selected cluster immediately unless ``confirm``
    is provided. Existing workloads may need a restart to read updated values.
    The kubectl backend briefly stages values in protected temporary files;
    see the module security note for cleanup limitations.

    Args:
        spec: Secret name, namespace, type, and plaintext values to apply.
        confirm: Optional callback called with the target cluster and ``spec``.
            The operation proceeds only if it returns ``True``.
        backend: ``"client"`` or ``"kubectl"`` to choose explicitly.
            ``"auto"`` prefers the Python client, then falls back to kubectl.

    Returns:
        kubectl's apply output or a short confirmation from the client backend.

    Raises:
        ConfirmationDeclined: If the confirmation callback returns ``False``.
        ValidationError: If ``backend`` is not supported.
        MissingDependencyError: If the selected backend is unavailable.
        KubeApiError: If the Kubernetes API rejects the change.
        KubectlError: If the kubectl backend cannot run or apply the manifest.

    Example:
        Read values from the environment, inspect the target, then explicitly
        confirm the change before applying it::

            def confirm(target, secret):
                print("\\n".join(target.lines()))
                return input("Apply this Secret? [y/N] ").strip().lower() == "y"

            spec = SecretSpec.generic(
                "database", "default",
                secret_data_from_env({"password": "DB_PASSWORD"}),
            )
            result = apply_secret(spec, confirm=confirm)
    """
    chosen = _resolve_backend(backend)
    if confirm is not None and not confirm(get_cluster_target(chosen), spec):
        raise ConfirmationDeclined(f"apply of Secret {spec.name} was declined")
    if chosen == "client":
        return _apply_with_client(spec)
    return _apply_with_kubectl(spec)
