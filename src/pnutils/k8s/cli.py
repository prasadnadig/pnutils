"""Safe-by-default CLI for creating or replacing Kubernetes Secrets.

Preview mode does not contact Kubernetes or write files. The ``kubectl`` apply
backend stages Secret values in temporary files; cleanup is best-effort, with
residual risks from abrupt termination, APFS copy-on-write, backups, snapshots,
and the source environment or shell history. If cleanup reports a path, delete
it and rotate the credential or reissue the certificate.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pnutils.exceptions import PnUtilsError, ValidationError
from pnutils.k8s.kubectl import ClusterTarget
from pnutils.k8s.secrets import (
    SecretSpec,
    apply_secret,
    get_cluster_target,
    secret_data_from_env,
)
from pnutils.k8s.tls import (
    generate_service_certificate,
    load_certificate_pair,
    service_dns_names,
)
from pnutils.ssl_certs.tls import _openssl_san_argument

__all__ = ["main"]

_SECURITY_NOTE = (
    "Security note:\n"
    "  Preview writes no files. The Python client keeps values in memory; the\n"
    "  kubectl apply backend stages values in 0600 files inside a 0700 directory.\n"
    "  Cleanup overwrites and removes files on normal exit, but SIGKILL or power\n"
    "  loss may leave them behind, and overwrite is best-effort on APFS and other\n"
    "  copy-on-write filesystems. Backups, snapshots, the shell environment, and\n"
    "  shell history may also retain values. If cleanup reports a path, delete it\n"
    "  and rotate the credential or reissue the certificate."
)


class _HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    """Show defaults while preserving multi-line epilogs."""


def _print_changes(spec: SecretSpec, details: Sequence[str] = ()) -> None:
    print("The following Secret will be applied:")
    for line in spec.summary():
        print(f"  {line}")
    for line in details:
        print(f"  {line}")


def _interactive_confirm(
    target: ClusterTarget, spec: SecretSpec, details: Sequence[str] = ()
) -> bool:
    print("\nConfirmation required before applying:")
    print("Target Kubernetes cluster:")
    for line in target.lines():
        print(f"  {line}")
    _print_changes(spec, details)
    if not sys.stdin.isatty():
        raise PnUtilsError("confirmation needs a terminal; pass --force in automation")
    return input('\nType exactly "yes" to apply these changes: ').strip() == "yes"


def _preview_generated_tls(
    name: str,
    namespace: str,
    details: Sequence[str],
) -> None:
    """Describe a generated TLS Secret without creating certificate files."""
    print("\n--apply was not set; Kubernetes was not contacted.")
    print(f"Secret {name} (kubernetes.io/tls) in namespace {namespace}")
    print("keys created or replaced: tls.crt, tls.key")
    for line in details:
        print(line)
    print("certificate generation is deferred until --apply")
    print("No files were written.")
    print("Re-run with --apply to apply it.")


def _finish(
    spec: SecretSpec,
    apply: bool,
    force: bool,
    backend: str,
    details: Sequence[str] = (),
) -> None:
    if not apply:
        print("\n--apply was not set; Kubernetes was not contacted.")
        _print_changes(spec, details)
        print("No files were written.")
        print("Re-run with --apply to apply it.")
        return
    if force:
        print("\nConfirmation skipped for automation; applying to:")
        for line in get_cluster_target(backend).lines():
            print(f"  {line}")
        _print_changes(spec, details)

    def confirm(target: ClusterTarget, pending: SecretSpec) -> bool:
        return _interactive_confirm(target, pending, details)

    result = apply_secret(spec, confirm=None if force else confirm, backend=backend)
    print(f"\n{result}")
    print("Restart any workload that already read the previous value.")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True, help="Secret name")
    parser.add_argument("--namespace", default="default", help="Kubernetes namespace")
    parser.add_argument("--apply", action="store_true", help="Apply to the current context")
    parser.add_argument("--force", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument(
        "--backend",
        choices=("auto", "client", "kubectl"),
        default="auto",
        help="How to reach the cluster; auto prefers the Python client",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pnutils-k8s",
        description="Kubernetes helpers. Nothing is applied unless --apply is given.",
        formatter_class=_HelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    env = sub.add_parser(
        "push-env-secret",
        help="Store an environment variable in an Opaque Secret",
        description=(
            "Read a value from an environment variable and store it under one key of an "
            "Opaque Secret. The value is never accepted as a flag and never printed."
        ),
        epilog=(
            "Example:\n"
            "  export SECRET_VALUE=...\n"
            "  pnutils-k8s push-env-secret --name payments-token --namespace team-a \\\n"
            "      --key token --value-env SECRET_VALUE --apply\n"
            "\n"
            f"{_SECURITY_NOTE}\n"
        ),
        formatter_class=_HelpFormatter,
    )
    _add_common(env)
    env.add_argument("--key", default="token", help="Key inside the Secret")
    env.add_argument("--value-env", default="SECRET_VALUE", help="Environment variable to read")

    tls = sub.add_parser(
        "push-tls-secret",
        help="Create a kubernetes.io/tls Secret",
        description=(
            "Package an existing certificate pair, or generate a self-signed certificate "
            "covering the in-cluster DNS names of a Service, as a TLS Secret."
        ),
        epilog=(
            "Example:\n"
            "  pnutils-k8s push-tls-secret --name payments-tls --namespace team-a \\\n"
            "      --service-name payments --apply\n"
            "\n"
            f"{_SECURITY_NOTE}\n"
        ),
        formatter_class=_HelpFormatter,
    )
    _add_common(tls)
    tls.add_argument("--service-name", default="service", help="Service used to derive DNS SANs")
    tls.add_argument("--dns-name", action="append", default=[], help="Extra DNS SAN; repeatable")
    tls.add_argument("--ip-address", action="append", default=[], help="Extra IP SAN; repeatable")
    tls.add_argument("--validity-days", type=int, default=825, help="Self-signed lifetime in days")
    tls.add_argument("--cert-file", help="Existing PEM certificate instead of generating one")
    tls.add_argument("--key-file", help="Private key matching --cert-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Kubernetes Secret CLI and return its process exit status."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.force and not args.apply:
        parser.error("--force only applies together with --apply")

    try:
        details: list[str] = []
        if args.command == "push-env-secret":
            data = secret_data_from_env({args.key: args.value_env})
            spec = SecretSpec.generic(args.name, args.namespace, data)
        else:
            if bool(args.cert_file) != bool(args.key_file):
                parser.error("--cert-file and --key-file must be provided together")
            if args.cert_file:
                certificate = load_certificate_pair(args.cert_file, args.key_file)
                details.append("certificate: loaded from --cert-file and --key-file")
            else:
                if args.validity_days <= 0:
                    raise ValidationError("validity days must be a positive integer")
                dns_names = service_dns_names(args.service_name, args.namespace) + args.dns_name
                _openssl_san_argument(dns_names, args.ip_address)
                details = ["certificate: self-signed", f"DNS SANs: {', '.join(dns_names)}"]
                if args.ip_address:
                    details.append(f"IP SANs: {', '.join(args.ip_address)}")
                details.append(f"validity: {args.validity_days} days")
                if not args.apply:
                    _preview_generated_tls(
                        args.name,
                        args.namespace,
                        details,
                    )
                    return 0
                certificate = generate_service_certificate(
                    args.service_name,
                    args.namespace,
                    extra_dns_names=args.dns_name,
                    ip_addresses=args.ip_address,
                    validity_days=args.validity_days,
                )
                print("Generated a self-signed certificate; every caller must pin it.")
            spec = SecretSpec.tls(args.name, args.namespace, certificate)
        _finish(spec, args.apply, args.force, args.backend, details)
    except (PnUtilsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
