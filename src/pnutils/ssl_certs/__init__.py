"""Certificate utilities.

X.509/TLS certificate generation and loading, with no dependency beyond the
``openssl`` binary.
"""

from __future__ import annotations

from pnutils.ssl_certs.tls import (
    CertificatePair,
    generate_self_signed_certificate,
    load_certificate_pair,
)

__all__ = [
    "CertificatePair",
    "generate_self_signed_certificate",
    "load_certificate_pair",
]
