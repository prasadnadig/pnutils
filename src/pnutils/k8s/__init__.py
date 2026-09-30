"""Kubernetes utilities.

The Python client backend requires the ``k8s`` extra
(``pip install 'pnutils[k8s]'``); the ``kubectl`` backend needs only the binary
on ``PATH``.
"""

from __future__ import annotations

from pnutils.k8s.client import KubernetesClient
from pnutils.k8s.kubectl import ClusterTarget
from pnutils.k8s.secrets import (
    SecretSpec,
    apply_secret,
    get_cluster_target,
    secret_data_from_env,
)
from pnutils.k8s.tls import (
    CertificatePair,
    generate_service_certificate,
    load_certificate_pair,
    service_dns_names,
)

__all__ = [
    "CertificatePair",
    "ClusterTarget",
    "KubernetesClient",
    "SecretSpec",
    "apply_secret",
    "generate_service_certificate",
    "get_cluster_target",
    "load_certificate_pair",
    "secret_data_from_env",
    "service_dns_names",
]
