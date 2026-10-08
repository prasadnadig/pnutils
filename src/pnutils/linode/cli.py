"""Dispatch provider-wide Linode commands to resource-specific CLIs.

Each resource owns its parser and workflow, so adding Linode VM or disk tools
does not crowd the DNS implementation or create another provider command.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pnutils.linode import dnscli

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Route a provider-level resource command to its dedicated CLI.

    Args:
        argv: Arguments to dispatch, or process arguments when omitted.

    Returns:
        The selected resource CLI's process status.

    Raises:
        SystemExit: When argparse handles help or invalid resource arguments.
    """
    parser = argparse.ArgumentParser(
        add_help=False,
        prog="pnutils-linode",
        description="Linode utilities, grouped by provider resource.",
        epilog="Example: pnutils-linode dns records --file dns.yaml",
    )
    parser.add_argument("resource", choices=("dns",), help="Linode resource utility")
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    args = parser.parse_args(arguments[:1])
    if args.resource == "dns":
        return dnscli.main(arguments[1:])
    parser.error(f"unsupported resource: {args.resource}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())