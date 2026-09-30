"""Generic X.509 certificate helpers.

Protocol agnostic: nothing here is specific to Kubernetes. Generation shells
out to ``openssl``, so no Python cryptography dependency is required.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which

from pnutils.exceptions import CommandError, ValidationError
from pnutils.scripts.process import run_command
from pnutils.scripts.secure import temporary_secret_directory

__all__ = ["CertificatePair", "generate_self_signed_certificate", "load_certificate_pair"]


@dataclass(frozen=True)
class CertificatePair:
    """A PEM certificate and its private key, held in memory.

    ``private_key_pem`` is excluded from ``repr`` so the key cannot leak
    through logs or tracebacks.

    Attributes:
        certificate_pem: Certificate text in PEM format.
        private_key_pem: Matching private-key text in PEM format; omitted from
            the object's string representation.
    """

    certificate_pem: str
    private_key_pem: str = field(repr=False)


def load_certificate_pair(
    certificate_file: str | Path, private_key_file: str | Path
) -> CertificatePair:
    """Read a certificate and private key from PEM files.

    This reads the files as text but does not check that the key matches the
    certificate or that either file contains valid PEM data.

    Args:
        certificate_file: Path to the certificate PEM file.
        private_key_file: Path to the private-key PEM file.

    Returns:
        Both file contents in a :class:`CertificatePair`; the private key is
        hidden from its string representation.

    Raises:
        OSError: If either file cannot be read.
        UnicodeDecodeError: If either file is not valid UTF-8 text.
    """
    return CertificatePair(
        certificate_pem=Path(certificate_file).read_text(encoding="utf-8"),
        private_key_pem=Path(private_key_file).read_text(encoding="utf-8"),
    )


def _openssl_san_argument(
    dns_names: Sequence[str] = (), ip_addresses: Sequence[str] = ()
) -> str:
    """Build an OpenSSL SAN value after validating IP addresses."""
    for address in ip_addresses:
        try:
            ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValidationError(f"not a valid IP address: {address}") from exc
    entries = [f"DNS:{name}" for name in dns_names] + [f"IP:{ip}" for ip in ip_addresses]
    if not entries:
        raise ValidationError("at least one DNS name or IP address is required")
    return ",".join(entries)


def generate_self_signed_certificate(
    common_name: str,
    dns_names: Sequence[str] = (),
    ip_addresses: Sequence[str] = (),
    validity_days: int = 825,
    key_bits: int = 2048,
) -> CertificatePair:
    """Generate a self-signed RSA certificate and its private key.

    The private key is staged in a protected temporary directory while OpenSSL
    runs, then read into memory and removed during cleanup. Cleanup is
    best-effort; see :mod:`pnutils.scripts.secure` for its limitations. A
    self-signed certificate is not trusted automatically: configure each
    client to trust it before using it for TLS.

    Args:
        common_name: Certificate common name. Also used as a DNS subject
            alternative name when ``dns_names`` is empty.
        dns_names: DNS subject alternative names to include.
        ip_addresses: IP subject alternative names to include.
        validity_days: Certificate lifetime in days; must be positive.
        key_bits: RSA key size; must be at least 2048 bits.

    Returns:
        The certificate and matching private key as PEM text held in memory.

    Raises:
        ValidationError: If the lifetime, key size, IP address, or subject
            alternative names are invalid.
        CommandError: If OpenSSL is unavailable or certificate generation fails.

    Example:
        Generate a certificate for a local development service::

            pair = generate_self_signed_certificate(
                "localhost", dns_names=["localhost"], ip_addresses=["127.0.0.1"]
            )
    """
    if validity_days <= 0:
        raise ValidationError("validity_days must be a positive integer")
    if key_bits < 2048:
        raise ValidationError("key_bits must be at least 2048")
    san = _openssl_san_argument(list(dns_names) or [common_name], ip_addresses)

    if which("openssl") is None:
        raise CommandError("openssl is required to generate a self-signed certificate")

    with temporary_secret_directory(prefix="pnutils-tls-") as workdir:
        cert_path = workdir / "tls.crt"
        key_path = workdir / "tls.key"
        run_command(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                f"rsa:{key_bits}",
                "-nodes",
                "-days",
                str(validity_days),
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-subj",
                f"/CN={common_name}",
                "-addext",
                f"subjectAltName={san}",
            ]
        )
        return CertificatePair(
            certificate_pem=cert_path.read_text(encoding="utf-8"),
            private_key_pem=key_path.read_text(encoding="utf-8"),
        )
