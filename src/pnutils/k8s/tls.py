"""Certificates for in-cluster Kubernetes Services.

The X.509 machinery lives in :mod:`pnutils.ssl_certs.tls`; this module only
adds the Kubernetes-specific naming rules.
"""

from __future__ import annotations

from collections.abc import Sequence

from pnutils.ssl_certs.tls import (
    CertificatePair,
    generate_self_signed_certificate,
    load_certificate_pair,
)

__all__ = [
    "CertificatePair",
    "generate_service_certificate",
    "load_certificate_pair",
    "service_dns_names",
]


def service_dns_names(service_name: str, namespace: str) -> list[str]:
    """List standard cluster DNS names for a Kubernetes Service.

    Args:
        service_name: Name assigned to the Service.
        namespace: Namespace containing the Service.

    Returns:
        The short name and the namespace, ``svc``, and cluster-local forms.
    """
    return [
        service_name,
        f"{service_name}.{namespace}",
        f"{service_name}.{namespace}.svc",
        f"{service_name}.{namespace}.svc.cluster.local",
    ]


def generate_service_certificate(
    service_name: str,
    namespace: str,
    extra_dns_names: Sequence[str] = (),
    ip_addresses: Sequence[str] = (),
    validity_days: int = 825,
) -> CertificatePair:
    """Create a self-signed certificate for a Service's cluster DNS names.

    The certificate includes the standard DNS names returned by
    :func:`service_dns_names`, plus any additional DNS names or IP addresses.
    Because it is self-signed, clients must be configured to trust it.

    Args:
        service_name: Name assigned to the Service and certificate common name.
        namespace: Namespace containing the Service.
        extra_dns_names: Additional DNS subject alternative names to include.
        ip_addresses: IP subject alternative names to include.
        validity_days: Number of days before the certificate expires; must be
            positive.

    Returns:
        The generated PEM certificate and matching private key, held in memory.

    Raises:
        ValidationError: If validity or an IP address is invalid, or no subject
            alternative name can be generated.
        CommandError: If OpenSSL is missing or cannot generate the certificate.
    """
    names = service_dns_names(service_name, namespace) + list(extra_dns_names)
    return generate_self_signed_certificate(
        service_name, dns_names=names, ip_addresses=ip_addresses, validity_days=validity_days
    )
