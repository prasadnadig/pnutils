"""Standalone DNS CLI: fetch, validate, preview, and explicitly approve changes.

Tokens are read from environment variables only. Preview writes no files unless
plan export is requested; exported raw records may contain sensitive TXT data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from pnutils.exceptions import PnUtilsError, ValidationError
from pnutils.linode.client import DNSClient
from pnutils.linode.workflow import load_document, run_manifest, validate_manifest

__all__ = ["main"]


def main(
    argv: list[str] | None = None,
    intent_factory: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> int:
    """Run DNS commands, optionally deriving intent through a consumer adapter.

    Args:
        argv: CLI arguments, or process arguments when omitted.
        intent_factory: Convert loaded consumer policy into DNS intent and review
            context. Called again after approval to reject changed discovery.

    Returns:
        Zero on success, one on a validation, approval, or provider failure.
    """
    parser = argparse.ArgumentParser(
        prog="pnutils-linode dns",
        description="DNS records: preview by default, never implicit apply.",
        epilog=(
            "Security note: tokens stay in memory/environment; exported plans contain raw DNS data."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="Fetch raw existing records without modifying them")
    show.add_argument("--zone", required=True)
    show.add_argument("--token-env", default="LINODE_API_TOKEN")
    validate = commands.add_parser(
        "validate", help="Validate desired input without contacting Linode"
    )
    validate.add_argument("--file", required=True)
    records = commands.add_parser("records", help="Preview or explicitly apply desired DNS records")
    records.add_argument("--file", required=True)
    records.add_argument("--apply", action="store_true")
    records.add_argument("--force", action="store_true", help="Skip approval, only with --apply")
    records.add_argument("--plan-out", help="Export a new 0600 review plan; refuses overwrite")
    records.add_argument("--plan-in", help="Require an exact saved plan match before applying")
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            client = DNSClient(args.token_env)
            print(json.dumps(client.records(client.zone_id(args.zone)), indent=2, sort_keys=True))
        elif args.command == "validate":
            specs = validate_manifest(load_document(args.file))
            print(
                f"Validated {len(specs)} zones offline; credentials and live conflicts not checked."
            )
        else:
            source = load_document(args.file)
            manifest, context = intent_factory(source) if intent_factory else (source, {})

            def check_discovery() -> None:
                if intent_factory and intent_factory(load_document(args.file)) != (
                    manifest,
                    context,
                ):
                    raise ValidationError("discovery or profile changed after review; replan")

            run_manifest(
                manifest,
                apply=args.apply,
                force=args.force,
                plan_out=args.plan_out,
                approved_plan=load_document(args.plan_in) if args.plan_in else None,
                pre_apply=check_discovery,
                review_context=context if intent_factory else None,
            )
    except (PnUtilsError, OSError, EOFError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0