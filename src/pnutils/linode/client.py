"""Supported Linode v4 DNS API transport with credentials held only in memory.

Reads paginate fully. Writes are deliberately not retried: a network failure
may occur after the provider has committed a change. Inspect and replan then.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from pnutils.exceptions import PnUtilsError, ValidationError

__all__ = ["DNSClient"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        """Refuse redirects so authorization headers cannot leave the API host."""
        return None


class DNSClient:
    """Access existing DNS zones using a token named by an environment variable.

    Args:
        token_env: Environment variable containing a Linode API token.

    Raises:
        ValidationError: The named variable is unset or empty.
    """

    def __init__(self, token_env: str) -> None:
        token = os.environ.get(token_env, "")
        if not token:
            raise ValidationError(f"required credential variable is unset: {token_env}")
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api.linode.com/v4{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise PnUtilsError(f"Linode {method} {path} failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise PnUtilsError(f"Linode {method} {path} failed ({type(exc).__name__})") from None
        if not isinstance(payload, dict):
            raise PnUtilsError("Linode returned an invalid response")
        return payload

    def _list(self, path: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._request("GET", f"{path}?page={page}&page_size=500")
            data = payload.get("data")
            if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
                raise PnUtilsError("Linode returned an invalid collection")
            records.extend(data)
            pages = payload.get("pages")
            if type(pages) is not int or pages < 1:
                raise PnUtilsError("Linode returned invalid pagination")
            if page >= pages:
                return records
            page += 1

    def zone_id(self, zone: str) -> int:
        """Find an existing zone visible to this credential; never create a zone."""
        matches = [
            entry
            for entry in self._list("/domains")
            if str(entry.get("domain", "")).lower().rstrip(".") == zone.lower().rstrip(".")
        ]
        if len(matches) != 1 or type(matches[0].get("id")) is not int:
            raise ValidationError(f"zone must exist and be uniquely visible: {zone}")
        if matches[0].get("type") != "master":
            raise ValidationError(f"zone is not a writable primary zone: {zone}")
        return int(matches[0]["id"])

    def records(self, zone_id: int) -> list[dict[str, Any]]:
        """Fetch all records as returned by the API, including read-only fields."""
        return self._list(f"/domains/{zone_id}/records")

    def create(self, zone_id: int, record: dict[str, Any]) -> None:
        """Create one validated record; uncertain failures must not be blindly retried."""
        self._request("POST", f"/domains/{zone_id}/records", record)

    def delete(self, zone_id: int, record_id: int) -> None:
        """Delete one explicitly approved record by its provider ID."""
        self._request("DELETE", f"/domains/{zone_id}/records/{record_id}")

    def update(self, zone_id: int, record_id: int, record: dict[str, Any]) -> None:
        """Update one approved record without deleting it or changing its type."""
        self._request(
            "PUT",
            f"/domains/{zone_id}/records/{record_id}",
            {key: value for key, value in record.items() if key != "type"},
        )
