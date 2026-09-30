"""Convenience wrappers over the official ``kubernetes`` client."""

from __future__ import annotations

from typing import Any

from pnutils._internal.optional import require
from pnutils.exceptions import ValidationError
from pnutils.k8s.kubectl import ClusterTarget

__all__ = ["KubernetesClient"]


def _load_configuration(
    context: str | None = None, in_cluster: bool | None = None
) -> tuple[Any, bool]:
    """Build an isolated client configuration and report whether it is in-cluster."""
    client = require("kubernetes.client", "k8s")
    config = require("kubernetes.config", "k8s")
    configuration = client.Configuration()
    if context is not None and in_cluster is True:
        raise ValidationError("context cannot be combined with in_cluster=True")

    if in_cluster is True:
        config.load_incluster_config(client_configuration=configuration)
        return configuration, True
    if context is not None or in_cluster is False:
        config.load_kube_config(context=context, client_configuration=configuration)
        return configuration, False
    try:
        config.load_incluster_config(client_configuration=configuration)
        return configuration, True
    except config.ConfigException:
        config.load_kube_config(client_configuration=configuration)
        return configuration, False


def _current_target(context: str | None = None, in_cluster: bool | None = None) -> ClusterTarget:
    """Describe the cluster the client would talk to, without calling the API."""
    configuration, is_in_cluster = _load_configuration(context=context, in_cluster=in_cluster)
    config = require("kubernetes.config", "k8s")
    host = configuration.host or "<unknown>"
    if is_in_cluster:
        return ClusterTarget(
            context="<in-cluster>", cluster="<in-cluster>", server=host, user="<service-account>"
        )
    _, active = config.list_kube_config_contexts(context=context)
    details = (active or {}).get("context", {})
    return ClusterTarget(
        context=(active or {}).get("name", "<unknown>"),
        cluster=details.get("cluster", "<unknown>"),
        server=host,
        user=details.get("user", "<unknown>"),
    )


class KubernetesClient:
    """Facade for common CoreV1/AppsV1 reads, with raw API clients as escape hatches.

    ``context`` selects a local kubeconfig context and takes precedence over
    automatic in-cluster detection. Set ``in_cluster`` to force service-account
    configuration (``True``) or local kubeconfig (``False``); ``None`` tries
    in-cluster configuration before falling back to kubeconfig. Each instance
    owns its API client and does not change process-wide SDK configuration.
    """

    def __init__(self, context: str | None = None, in_cluster: bool | None = None) -> None:
        """Configure this client's Kubernetes connection.

        Args:
            context: Local kubeconfig context to use. Ignored for in-cluster
                configuration.
            in_cluster: Force service-account configuration when true, force
                kubeconfig when false, or try in-cluster configuration first
                when ``None``.

        Raises:
            MissingDependencyError: If the optional ``k8s`` extra is not installed.
            ValidationError: If a context is combined with ``in_cluster=True``.

        Note:
            This instance owns its API client. Use it as a context manager or
            call :meth:`close` when finished.
        """
        client = require("kubernetes.client", "k8s")
        configuration, _ = _load_configuration(context=context, in_cluster=in_cluster)
        self._api_client = client.ApiClient(configuration)
        self.core = client.CoreV1Api(self._api_client)
        self.apps = client.AppsV1Api(self._api_client)

    def close(self) -> None:
        """Close the HTTP connection pool owned by this client."""
        self._api_client.close()

    def __enter__(self) -> KubernetesClient:
        """Return this client so API calls can be made inside a ``with`` block."""
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Close the owned API client when leaving a ``with`` block."""
        self.close()

    def list_namespaces(self) -> list[str]:
        """List namespace names visible to the configured Kubernetes identity.

        Returns:
            Namespace names as strings. The configured identity must have
            permission to list namespaces.
        """
        return [ns.metadata.name for ns in self.core.list_namespace().items]

    def list_pods(self, namespace: str = "default", label_selector: str = "") -> list[Any]:
        """List pod API objects in one namespace.

        Args:
            namespace: Namespace to search; defaults to ``default``.
            label_selector: Kubernetes label selector, such as
                ``"app=worker"``. An empty string includes every pod in the
                namespace.

        Returns:
            Kubernetes Pod objects, not just their names.
        """
        return self.core.list_namespaced_pod(
            namespace=namespace, label_selector=label_selector
        ).items

    def pod_logs(
        self,
        name: str,
        namespace: str = "default",
        container: str | None = None,
        tail_lines: int | None = None,
    ) -> str:
        """Read log text from a pod.

        Args:
            name: Name of the pod.
            namespace: Namespace containing the pod; defaults to ``default``.
            container: Container whose logs to read. ``None`` lets Kubernetes
                choose the default container.
            tail_lines: Maximum number of recent lines to return. ``None``
                returns the available log output without a line limit.

        Returns:
            The requested log output as text.
        """
        return self.core.read_namespaced_pod_log(
            name=name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
        )

    def list_deployments(self, namespace: str = "default") -> list[Any]:
        """List Deployment API objects in one namespace.

        Args:
            namespace: Namespace to search; defaults to ``default``.

        Returns:
            Kubernetes Deployment objects, including their metadata and spec.
        """
        return self.apps.list_namespaced_deployment(namespace=namespace).items
