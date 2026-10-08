"""Load DNS intent and coordinate preview, approval, drift checks, and apply.

YAML/JSON intent uses schemaVersion 1. Plans contain DNS records, not API token
values; exported plans may nevertheless contain sensitive TXT data. Preview
writes no files unless plan export is explicitly requested.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pnutils._internal.optional import require
from pnutils.exceptions import ConfirmationDeclined, PnUtilsError, ValidationError
from pnutils.linode.client import DNSClient
from pnutils.linode.dns import plan_records

__all__ = ["load_document", "validate_manifest", "run_manifest"]


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate input key: {key}")
        result[key] = value
    return result


def load_document(path: str | Path) -> dict[str, Any]:
    """Read a YAML or JSON mapping, rejecting duplicate keys and malformed input."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            value = json.loads(text, object_pairs_hook=_unique_pairs)
        else:
            yaml = require("yaml", "linode")

            unique_loader: Any = type("UniqueLoader", (yaml.SafeLoader,), {})

            def mapping(loader: Any, node: Any) -> dict[str, Any]:
                return _unique_pairs(
                    [
                        (loader.construct_object(key), loader.construct_object(item))
                        for key, item in node.value
                    ]
                )

            unique_loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
            try:
                value = yaml.load(text, Loader=unique_loader)
            except yaml.YAMLError:
                raise ValidationError(f"invalid YAML document: {path}") from None
    except ValidationError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ValidationError(f"cannot read document {path}: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise ValidationError("document must contain a mapping")
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate schema-versioned DNS intent offline; return normalized zone specs.

    Every zone requires an explicit managed record-set scope, unless a clean or
    replace request separately acknowledges a whole-zone wipe. This validates
    input and desired conflicts, not live credentials or DNS propagation.
    """
    if set(manifest) != {"schemaVersion", "zones"} or manifest["schemaVersion"] != 1:
        raise ValidationError("DNS input requires schemaVersion: 1 and zones only")
    zones = manifest["zones"]
    if not isinstance(zones, list) or not zones:
        raise ValidationError("zones must be a nonempty list")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for spec in zones:
        if not isinstance(spec, dict) or set(spec) - {
            "name",
            "tokenEnv",
            "mode",
            "managedRecordSets",
            "records",
            "allowZoneWipe",
        }:
            raise ValidationError("invalid zone spec or unknown fields")
        name = spec.get("name")
        token_env = spec.get("tokenEnv")
        if not isinstance(name, str) or not isinstance(token_env, str):
            raise ValidationError("each zone requires name and tokenEnv strings")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise ValidationError("tokenEnv must name an environment variable")
        records = spec.get("records", [])
        managed = spec.get("managedRecordSets", [])
        wipe = spec.get("allowZoneWipe", False)
        if (
            not isinstance(records, list)
            or not isinstance(managed, list)
            or any(not isinstance(entry, dict) for entry in [*records, *managed])
            or type(wipe) is not bool
        ):
            raise ValidationError("records and managedRecordSets must be lists of mappings")
        plan = plan_records(name, [], records, managed, spec.get("mode", "reconcile"), wipe)
        name = plan["zone"]
        if name in names:
            raise ValidationError(f"duplicate zone: {name}")
        names.add(name)
        normalized.append(
            {
                "name": name,
                "tokenEnv": token_env,
                "mode": plan["mode"],
                "records": records,
                "managedRecordSets": managed,
                "allowZoneWipe": wipe,
            }
        )
    return normalized


def _fingerprint(records: list[dict[str, Any]]) -> str:
    return json.dumps(sorted(records, key=lambda entry: int(entry["id"])), sort_keys=True)


def run_manifest(
    manifest: Mapping[str, Any],
    *,
    apply: bool = False,
    force: bool = False,
    plan_out: str | Path | None = None,
    approved_plan: Mapping[str, Any] | None = None,
    pre_apply: Callable[[], None] | None = None,
    review_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Show live records and a plan, then mutate only after explicit approval.

    Args:
        manifest: Schema-versioned desired DNS input.
        apply: Enable mutations; otherwise preview only.
        force: Skip interactive confirmation, only with apply.
        plan_out: Explicit export path, created exclusively with mode 0600.
        approved_plan: Saved plan that must match current input and live state.
        pre_apply: Consumer drift check, run after approval before any writes.
        review_context: Consumer identity and discovery facts bound to a saved plan.

    Returns:
        The reviewed JSON-compatible plan, without credential values.

    Raises:
        ValidationError: Input or a reviewed snapshot has changed.
        ConfirmationDeclined: The operator declined; no writes occurred.
        PnUtilsError: Provider failure, possibly after partial application.
    """
    if force and not apply:
        raise ValidationError("--force requires --apply")
    if approved_plan is not None and not apply:
        raise ValidationError("applying a saved plan requires --apply")
    specs = validate_manifest(manifest)
    clients: list[tuple[DNSClient, int]] = []
    plans: list[dict[str, Any]] = []
    for spec in specs:
        client = DNSClient(spec["tokenEnv"])
        zone_id = client.zone_id(spec["name"])
        current = client.records(zone_id)
        plan = plan_records(
            spec["name"],
            current,
            spec["records"],
            spec["managedRecordSets"],
            spec["mode"],
            spec["allowZoneWipe"],
        )
        plan["zoneId"] = zone_id
        plans.append(plan)
        clients.append((client, zone_id))
        print(f"\nCurrent records for {spec['name']} (raw API fields):")
        print(json.dumps(current, indent=2, sort_keys=True))
        print("Proposed operations:")
        print(json.dumps(plan["operations"], indent=2, sort_keys=True))
    reviewed = {
        "schemaVersion": 1,
        "intent": {"schemaVersion": 1, "zones": specs},
        "zonePlans": plans,
    }
    if review_context is not None:
        reviewed["context"] = dict(review_context)
    if approved_plan is not None and reviewed != approved_plan:
        raise ValidationError(
            "saved plan differs from current intent or DNS state; replan and approve"
        )
    if plan_out is not None:
        import os

        descriptor = os.open(plan_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(reviewed, handle, indent=2, sort_keys=True)
            handle.write("\n")
    if not apply:
        print("\nPreview only. No DNS writes. Re-run with --apply to request changes.")
        return reviewed
    if not any(plan["operations"] for plan in plans):
        print("No changes needed.")
        return reviewed
    if not force:
        if not sys.stdin.isatty():
            raise ValidationError("confirmation requires a terminal; use --apply --force in CI")
        acknowledgement = "WIPE" if any(plan["allowZoneWipe"] for plan in plans) else "yes"
        if (
            input(f'Type exactly "{acknowledgement}" to apply this plan: ').strip()
            != acknowledgement
        ):
            raise ConfirmationDeclined("DNS apply declined; no records changed")
    if pre_apply is not None:
        pre_apply()
    for (client, zone_id), plan in zip(clients, plans):
        if _fingerprint(client.records(zone_id)) != _fingerprint(plan["snapshot"]):
            raise ValidationError(f"DNS state changed for {plan['zone']}; replan before applying")
    completed = 0
    try:
        for (client, zone_id), plan, spec in zip(clients, plans, specs):
            for operation in plan["operations"]:
                if operation["action"] == "delete":
                    client.delete(zone_id, int(operation["record"]["id"]))
                elif operation["action"] == "update":
                    client.update(zone_id, int(operation["recordId"]), operation["record"])
                else:
                    client.create(zone_id, operation["record"])
                completed += 1
            actual = client.records(zone_id)
            verification_scope = spec["managedRecordSets"]
            if plan["allowZoneWipe"] and plan["mode"] == "replace":
                verification_scope = [
                    {"name": record.get("name", "@"), "type": record["type"]}
                    for record in spec["records"]
                ]
                verification_scope.extend(
                    {"name": record["name"], "type": record["type"]}
                    for record in actual
                    if record["type"] != "SOA"
                    and not (record["type"] == "NS" and not record["name"])
                    and "_acme-challenge" not in record["name"].split(".")
                )
            verification_mode = "clean" if not spec["records"] else "reconcile"
            remaining = plan_records(
                plan["zone"],
                actual,
                spec["records"],
                verification_scope,
                verification_mode,
                plan["allowZoneWipe"] and not verification_scope,
            )
            if remaining["operations"]:
                raise PnUtilsError(f"post-apply verification differs for {plan['zone']}")
    except (PnUtilsError, ValueError, KeyError) as exc:
        raise PnUtilsError(
            f"DNS apply stopped after {completed} confirmed operations: {exc}. "
            "Some changes may have committed; fetch current records and replan. "
            "Do not blindly retry."
        ) from None
    print(
        f"Applied and API-verified {completed} operations. DNS caches may still hold old records."
    )
    return reviewed
