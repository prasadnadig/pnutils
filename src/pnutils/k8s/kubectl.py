"""Thin, typed wrapper around the ``kubectl`` binary."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass

from pnutils.exceptions import CommandError, KubectlError, ValidationError
from pnutils.scripts.process import CommandResult, run_command

__all__ = [
    "ClusterTarget",
    "apply_manifest",
    "describe_target",
    "kubectl_path",
    "render_manifest",
    "run_kubectl",
]


@dataclass(frozen=True)
class ClusterTarget:
    """Identity of the cluster selected by the current kubeconfig context.

    Attributes:
        context: Active kubeconfig context name.
        cluster: Cluster name referenced by that context.
        server: Kubernetes API server URL.
        user: User identity referenced by the context.
    """

    context: str
    cluster: str
    server: str
    user: str

    def lines(self) -> list[str]:
        """Return the cluster identity as display-ready lines."""
        return [
            f"context: {self.context}",
            f"cluster: {self.cluster}",
            f"server: {self.server}",
            f"user: {self.user}",
        ]


def kubectl_path(binary: str = "kubectl") -> str:
    """Find the executable used for kubectl commands.

    Args:
        binary: Executable name or path to locate.

    Returns:
        The executable's absolute path.

    Raises:
        KubectlError: If the executable cannot be found on ``PATH``.
    """
    found = shutil.which(binary)
    if found is None:
        raise KubectlError(f"{binary!r} was not found on PATH")
    return found


def run_kubectl(
    args: Sequence[str],
    binary: str | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> CommandResult:
    """Run a kubectl command and capture its standard output and error.

    Arguments are passed directly to the executable, without a shell. Use
    ``input_text`` to send data on standard input, for example a YAML manifest.

    Args:
        args: kubectl arguments, such as ``["get", "pods", "-n", "default"]``.
        binary: Executable to run; defaults to the first ``kubectl`` on ``PATH``.
        input_text: Optional text to pass to the command on standard input.
        check: If true, raise on a nonzero exit status. If false, return that
            status in the result so the caller can handle it.

    Returns:
        The command's arguments, exit status, standard output, and standard error.

    Raises:
        KubectlError: If kubectl cannot be found or exits unsuccessfully while
            ``check`` is true.
        OSError: If an explicitly supplied executable cannot be started.
    """
    executable = binary or kubectl_path()
    try:
        return run_command([executable, *args], input_text=input_text, check=check)
    except KubectlError:
        raise
    except CommandError as exc:
        raise KubectlError(str(exc)) from exc


def describe_target(binary: str | None = None) -> ClusterTarget:
    """Show which cluster the current kubeconfig context selects.

    This reads local kubeconfig information; it does not contact the cluster.
    If kubectl cannot provide a field, that field is returned as
    ``"<unknown>"``.

    Args:
        binary: kubectl executable to run, or ``None`` to find it on ``PATH``.

    Returns:
        The active context, cluster name, API server URL, and user name.

    Raises:
        KubectlError: If kubectl is not available on ``PATH``.
        OSError: If an explicitly supplied executable cannot be started.
    """
    executable = binary or kubectl_path()

    def value(args: Sequence[str]) -> str:
        result = run_kubectl(args, binary=executable, check=False)
        return result.stdout.strip() or "<unknown>"

    view = ["config", "view", "--minify", "-o"]
    return ClusterTarget(
        context=value(["config", "current-context"]),
        cluster=value([*view, "jsonpath={.clusters[0].name}"]),
        server=value([*view, "jsonpath={.clusters[0].cluster.server}"]),
        user=value([*view, "jsonpath={.users[0].name}"]),
    )


def render_manifest(args: Sequence[str], binary: str | None = None) -> str:
    """Turn kubectl create arguments into YAML without contacting the cluster.

    For example, ``["create", "namespace", "demo"]`` returns the namespace
    manifest that kubectl would create. The supplied arguments should not
    already include ``--dry-run`` or ``-o``; this function adds client-side
    dry-run and YAML output options.

    Args:
        args: kubectl create arguments describing the resource to render.
        binary: kubectl executable to run, or ``None`` to find it on ``PATH``.

    Returns:
        The generated YAML text. No resource is created in the cluster.

    Raises:
        KubectlError: If kubectl cannot render the requested resource.
        OSError: If an explicitly supplied executable cannot be started.
    """
    return run_kubectl([*args, "--dry-run=client", "-o", "yaml"], binary=binary).stdout


def apply_manifest(
    manifest: str,
    binary: str | None = None,
    server_side: bool = False,
    field_manager: str | None = None,
) -> str:
    """Apply a YAML manifest to the cluster selected by the current context.

    This command contacts and changes the selected cluster. Check the target
    first with :func:`describe_target`, and use :func:`render_manifest` when you
    only need to inspect generated YAML.

    Args:
        manifest: YAML resource definitions sent to ``kubectl apply -f -``.
        binary: kubectl executable to run, or ``None`` to find it on ``PATH``.
        server_side: Use Kubernetes Server-Side Apply when true.
        field_manager: Name recorded by Server-Side Apply as the field owner.
            Required when ``server_side`` is true and disallowed otherwise.

    Returns:
        kubectl's output with surrounding whitespace removed.

    Raises:
        ValidationError: If ``server_side`` and ``field_manager`` are inconsistent.
        KubectlError: If kubectl cannot apply the manifest.
        OSError: If an explicitly supplied executable cannot be started.

    Example:
        Apply only when the selected context matches the expected development
        context. Replace ``development`` with the context you intend to use::

            target = describe_target()
            print("\\n".join(target.lines()))
            if target.context != "development":
                raise RuntimeError("Refusing to apply to an unexpected context")
            result = apply_manifest("apiVersion: v1\\nkind: Namespace\\n...")
    """
    if server_side and not field_manager:
        raise ValidationError("field_manager is required for server-side apply")
    if not server_side and field_manager is not None:
        raise ValidationError("field_manager requires server_side=True")
    args = ["apply"]
    if server_side:
        args.append("--server-side")
    if field_manager is not None:
        args.append(f"--field-manager={field_manager}")
    args += ["-f", "-"]
    return run_kubectl(args, binary=binary, input_text=manifest).stdout.strip()
