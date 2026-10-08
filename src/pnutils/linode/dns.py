"""Plan and execute explicit Linode DNS record changes without implicit ownership.

Plans retain raw API snapshots for review and drift detection. Credentials stay
in environment variables, never in plans. Record writes are not transactional;
an interrupted apply must be inspected and replanned before retrying.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pnutils.exceptions import ValidationError

__all__ = ["plan_records", "validate_record"]

_TYPES = {"A", "AAAA", "CNAME", "TXT", "NS", "MX", "CAA", "SRV", "PTR"}
_TTLS = {300, 3600, 7200, 14400, 28800, 57600, 86400, 172800, 345600, 604800, 1209600, 2419200}
_FIELDS = {
    "type",
    "name",
    "target",
    "ttl_sec",
    "priority",
    "weight",
    "port",
    "service",
    "protocol",
    "tag",
}


def _hostname(value: str) -> str:
    normalized = value.rstrip(".").lower()
    if (
        not normalized
        or len(normalized) > 253
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in normalized.split(".")
        )
    ):
        raise ValidationError(f"invalid hostname: {value!r}")
    return normalized


def _name(value: str, zone: str) -> str:
    normalized = value.rstrip(".").lower()
    if normalized in ("@", "", zone):
        return ""
    if normalized.endswith(f".{zone}"):
        normalized = normalized[: -(len(zone) + 1)]
    elif value.endswith("."):
        raise ValidationError(f"record name {value!r} is outside zone {zone!r}")
    if "*" in normalized or len(f"{normalized}.{zone}") > 253:
        raise ValidationError("wildcard or oversized record names are not supported")
    if any(
        not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label)
        for label in normalized.split(".")
    ):
        raise ValidationError(f"invalid record name: {value!r}")
    return normalized


def validate_record(zone: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a desired record using Linode's writable fields.

    Args:
        zone: Authoritative zone name.
        record: Record mapping using API field names; ``@`` denotes the apex.

    Returns:
        A normalized mapping suitable for the Linode record API.

    Raises:
        ValidationError: A field, target, TTL, or record combination is invalid.
    """
    zone = _hostname(zone)
    unknown = set(record) - _FIELDS
    if unknown:
        raise ValidationError(f"unknown record fields: {sorted(unknown)}")
    kind = str(record.get("type", "")).upper()
    if kind not in _TYPES:
        raise ValidationError(f"unsupported record type: {kind!r}")
    if not isinstance(record.get("name", "@"), str):
        raise ValidationError("record name must be a string")
    name = _name(str(record.get("name", "@")), zone)
    target = record.get("target")
    if not isinstance(target, str) or not target or "REPLACE_ME" in target:
        raise ValidationError("record target must be a resolved, nonempty string")
    ttl = record.get("ttl_sec", 300)
    if type(ttl) is not int or ttl not in _TTLS:
        raise ValidationError(f"ttl_sec must be a supported Linode TTL: {sorted(_TTLS)}")
    result: dict[str, Any] = {"type": kind, "name": name, "target": target, "ttl_sec": ttl}
    if kind in {"A", "AAAA"}:
        try:
            address = ipaddress.ip_address(target)
        except ValueError as exc:
            raise ValidationError(f"invalid {kind} address") from exc
        if address.version != (4 if kind == "A" else 6):
            raise ValidationError(f"wrong address family for {kind}")
        result["target"] = str(address)
    if kind in {"CNAME", "NS", "MX", "PTR", "SRV"}:
        result["target"] = _hostname(target)
    if kind == "CNAME" and not name:
        raise ValidationError("zone apex cannot be a CNAME")
    if kind == "CNAME" and result["target"] == f"{name}.{zone}":
        raise ValidationError("CNAME cannot point to itself")
    permitted = set()
    if kind in {"MX", "SRV"}:
        permitted.add("priority")
        result["priority"] = record.get("priority", 0)
    if kind == "SRV":
        permitted.update({"weight", "port", "service", "protocol"})
        for field in ("weight", "port", "service", "protocol"):
            if field not in record:
                raise ValidationError(f"SRV requires {field}")
            result[field] = record[field]
        for field in ("service", "protocol"):
            if not isinstance(result[field], str) or not re.fullmatch(
                r"[a-zA-Z0-9-]+", result[field]
            ):
                raise ValidationError(f"SRV {field} must be an unprefixed label")
    if kind == "CAA":
        permitted.add("tag")
        if record.get("tag") not in {"issue", "issuewild", "iodef"}:
            raise ValidationError("CAA requires tag issue, issuewild, or iodef")
        result["tag"] = record["tag"]
    for field in {"priority", "weight", "port", "service", "protocol", "tag"} & set(record):
        if field not in permitted:
            raise ValidationError(f"{field} is not valid for {kind}")
    for field, maximum in (("priority", 255), ("weight", 65535), ("port", 65535)):
        if field in result and (
            type(result[field]) is not int or not 0 <= result[field] <= maximum
        ):
            raise ValidationError(f"{field} must be an integer from 0 to {maximum}")
    return result


def _payload(record: Mapping[str, Any]) -> dict[str, Any]:
    kind = record.get("type")
    fields = {"type", "name", "target", "ttl_sec"}
    if kind in {"MX", "SRV"}:
        fields.add("priority")
    if kind == "SRV":
        fields.update({"weight", "port", "service", "protocol"})
    if kind == "CAA":
        fields.add("tag")
    payload = {key: value for key, value in record.items() if key in fields and value is not None}
    if kind in {"CNAME", "NS", "MX", "PTR", "SRV"}:
        payload["target"] = str(payload["target"]).lower().rstrip(".")
    return payload


def _key(record: Mapping[str, Any]) -> tuple[str, str]:
    return str(record["name"]), str(record["type"])


def _protected(record: Mapping[str, Any]) -> bool:
    name = str(record.get("name", "")).lower()
    return (
        record.get("type") == "SOA"
        or (record.get("type") == "NS" and name in {"", "@"})
        or "_acme-challenge" in name.split(".")
    )


def _owner(record: Mapping[str, Any]) -> str:
    name = str(record.get("name", ""))
    if record.get("type") == "SRV":
        prefix = f"_{record.get('service', '')}._{record.get('protocol', '')}"
        return f"{prefix}.{name}" if name else prefix
    return name


def plan_records(
    zone: str,
    current: Sequence[Mapping[str, Any]],
    desired: Sequence[Mapping[str, Any]],
    managed: Sequence[Mapping[str, Any]] = (),
    mode: str = "reconcile",
    allow_zone_wipe: bool = False,
) -> dict[str, Any]:
    """Build a reviewable plan without contacting Linode or modifying records.

    Args:
        zone: Authoritative zone name.
        current: Complete raw API record list, including IDs.
        desired: Desired writable records.
        managed: Explicit ``name``/``type`` pairs that may be changed.
        mode: ``reconcile``, ``clean``, or destructive ``replace``.
        allow_zone_wipe: Permit an empty managed scope for clean/replace only.

    Returns:
        JSON-compatible snapshot and ordered record operations.

    Raises:
        ValidationError: Ownership, protected records, or DNS conflicts are invalid.
    """
    zone = _hostname(zone)
    if mode not in {"reconcile", "clean", "replace"}:
        raise ValidationError("mode must be reconcile, clean, or replace")
    if mode == "clean" and desired:
        raise ValidationError("clean mode requires an empty records list")
    if allow_zone_wipe and (managed or mode == "reconcile"):
        raise ValidationError("zone wipe requires clean/replace and an empty managed scope")
    wanted = [validate_record(zone, record) for record in desired]
    encoded = [json.dumps(record, sort_keys=True) for record in wanted]
    if len(set(encoded)) != len(encoded):
        raise ValidationError("duplicate desired record")
    scope: set[tuple[str, str]] = set()
    for entry in managed:
        if set(entry) != {"name", "type"} or str(entry["type"]).upper() not in _TYPES:
            raise ValidationError("managedRecordSets require only name and a supported type")
        scope.add((_name(str(entry["name"]), zone), str(entry["type"]).upper()))
    if not scope and not allow_zone_wipe:
        raise ValidationError("an explicit managedRecordSets scope is required")
    if not allow_zone_wipe and any(_key(record) not in scope for record in wanted):
        raise ValidationError("desired record falls outside managedRecordSets")
    selected = [
        dict(record)
        for record in current
        if (allow_zone_wipe or _key(record) in scope) and not _protected(record)
    ]
    if any(_protected(record) for record in wanted):
        raise ValidationError("apex NS and ACME challenge records are protected")
    if any(_protected({"name": name, "type": kind}) for name, kind in scope):
        raise ValidationError("managed scope includes protected records")
    retained = [dict(record) for record in current if record not in selected]
    operations: list[dict[str, Any]] = []
    remaining = selected.copy()
    for record in wanted:
        identical = next((entry for entry in remaining if _payload(entry) == record), None)
        if identical is not None and mode == "reconcile":
            remaining.remove(identical)
        else:
            operations.append({"action": "create", "record": record})
        retained.append(record)
    for name in {_owner(record) for record in retained}:
        at_name = [record for record in retained if _owner(record) == name]
        cnames = [record for record in at_name if record.get("type") == "CNAME"]
        if cnames and (len(cnames) != 1 or len(at_name) != 1):
            raise ValidationError(f"CNAME conflicts with other records at {name or '@'}")
    if mode == "reconcile":
        for operation in operations:
            replacement = next(
                (entry for entry in remaining if _key(entry) == _key(operation["record"])), None
            )
            if replacement is not None:
                remaining.remove(replacement)
                operation["action"] = "update"
                operation["recordId"] = replacement["id"]
    deletes = [{"action": "delete", "record": entry} for entry in remaining]
    return {
        "zone": zone,
        "mode": mode,
        "snapshot": [dict(entry) for entry in current],
        "operations": deletes + operations,
        "allowZoneWipe": allow_zone_wipe,
    }
