"""Focused DNS planning and mutation safety contracts."""

import pytest

from pnutils.exceptions import ValidationError
from pnutils.linode import plan_records

ZONE = "greet.example.org"


def test_dns_cli_entry_point():
    from importlib.metadata import distribution

    from pnutils.linode import cli

    entry_point = next(entry for entry in distribution("pnutils").entry_points
                       if entry.name == "pnutils-linode")
    assert entry_point.value == "pnutils.linode.cli:main"
    assert entry_point.load() is cli.main


def test_linode_dispatcher_lists_dns_resource(capsys):
    from pnutils.linode.cli import main

    assert main(["--help"]) == 0
    assert "{dns}" in capsys.readouterr().out


def test_dns_help_is_nested_under_provider(capsys):
    from pnutils.linode.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["dns", "--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "pnutils-linode dns" in output
    assert "{show,validate,records}" in output
    assert "records" in output
ADDRESS = {"type": "A", "name": "", "target": "203.0.113.10", "ttl_sec": 300}
SCOPE = [{"name": "@", "type": "A"}]


def test_reconcile_preserves_unmanaged_and_is_idempotent():
    current = [
        dict(ADDRESS, id=1),
        {"id": 2, "type": "TXT", "name": "_acme-challenge", "target": "active"},
    ]
    plan = plan_records(ZONE, current, [ADDRESS], SCOPE)
    assert plan["operations"] == []
    assert plan["snapshot"] == current


def test_reconcile_only_deletes_selected_records():
    old = dict(ADDRESS, id=1, target="203.0.113.11")
    other = {"id": 2, "type": "MX", "name": "", "target": "mail.example.org"}
    plan = plan_records(ZONE, [old, other], [ADDRESS], SCOPE)
    assert [entry["action"] for entry in plan["operations"]] == ["update"]
    assert plan["operations"][0]["recordId"] == 1


def test_cname_conflict_rejected_before_apply():
    with pytest.raises(ValidationError, match="CNAME conflicts"):
        plan_records(
            ZONE,
            [dict(ADDRESS, name="www", id=1)],
            [{"name": "www", "type": "CNAME", "target": "other.example.org"}],
            [{"name": "www", "type": "CNAME"}],
        )


def test_wipe_preserves_apex_ns_and_acme():
    protected = [
        {"id": 2, "name": "", "type": "NS", "target": "ns.example.org"},
        {"id": 3, "name": "_acme-challenge", "type": "TXT", "target": "active"},
    ]
    plan = plan_records(
        ZONE, [dict(ADDRESS, id=1), *protected], [], mode="clean", allow_zone_wipe=True
    )
    assert [entry["record"]["id"] for entry in plan["operations"]] == [1]


def test_clean_requires_explicit_scope():
    with pytest.raises(ValidationError, match="explicit"):
        plan_records(ZONE, [], [], mode="clean")


def test_rejects_protected_scope_and_bad_ttl():
    with pytest.raises(ValidationError, match="protected"):
        plan_records(ZONE, [], [], [{"name": "_acme-challenge", "type": "TXT"}])
    with pytest.raises(ValidationError, match="TTL"):
        plan_records(ZONE, [], [dict(ADDRESS, ttl_sec=301)], SCOPE)


def test_cname_idempotence():
    cname = {"type": "CNAME", "name": "www", "target": "other.example.org", "ttl_sec": 300}
    assert not plan_records(ZONE, [dict(cname, id=1)], [cname], [{"name": "www", "type": "CNAME"}])[
        "operations"
    ]


def test_duplicate_yaml_keys_rejected(tmp_path):
    from pnutils.linode import load_document

    path = tmp_path / "dns.yaml"
    path.write_text("schemaVersion: 1\nschemaVersion: 2\n")
    with pytest.raises(ValidationError, match="duplicate"):
        load_document(path)


class FakeClient:
    records_now = []
    writes = []
    drift = False
    reads = 0

    def __init__(self, token_env):
        pass

    def zone_id(self, zone):
        return 10

    def records(self, zone_id):
        type(self).reads += 1
        if self.drift and self.reads > 1:
            return [dict(ADDRESS, id=999)]
        return list(self.records_now)

    def create(self, zone_id, record):
        self.writes.append(("create", record))
        self.records_now.append(dict(record, id=100))

    def delete(self, zone_id, record_id):
        self.writes.append(("delete", record_id))
        self.records_now[:] = [entry for entry in self.records_now if entry["id"] != record_id]

    def update(self, zone_id, record_id, record):
        self.writes.append(("update", record_id, record))
        self.records_now[:] = [
            dict(record, id=record_id) if entry["id"] == record_id else entry
            for entry in self.records_now
        ]


@pytest.fixture
def dns_workflow(monkeypatch):
    from pnutils.linode import workflow

    FakeClient.records_now = []
    FakeClient.writes = []
    FakeClient.drift = False
    FakeClient.reads = 0
    monkeypatch.setattr(workflow, "DNSClient", FakeClient)
    return workflow


def intent():
    return {
        "schemaVersion": 1,
        "zones": [
            {"name": ZONE, "tokenEnv": "TOKEN", "records": [ADDRESS], "managedRecordSets": SCOPE}
        ],
    }


def test_preview_never_writes(dns_workflow):
    dns_workflow.run_manifest(intent())
    assert not FakeClient.writes


def test_decline_never_writes(dns_workflow, monkeypatch):
    from pnutils.exceptions import ConfirmationDeclined

    monkeypatch.setattr(dns_workflow.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    with pytest.raises(ConfirmationDeclined):
        dns_workflow.run_manifest(intent(), apply=True)
    assert not FakeClient.writes


def test_drift_never_writes(dns_workflow):
    FakeClient.drift = True
    with pytest.raises(ValidationError, match="state changed"):
        dns_workflow.run_manifest(intent(), apply=True, force=True)
    assert not FakeClient.writes


def test_apply_and_export_reviewed_plan(dns_workflow, tmp_path):
    path = tmp_path / "plan.json"
    reviewed = dns_workflow.run_manifest(intent(), plan_out=path)
    assert path.stat().st_mode & 0o777 == 0o600
    dns_workflow.run_manifest(intent(), apply=True, force=True, approved_plan=reviewed)
    assert FakeClient.writes == [("create", ADDRESS)]


def test_modified_saved_plan_never_writes(dns_workflow):
    reviewed = dns_workflow.run_manifest(intent())
    reviewed["zonePlans"][0]["operations"] = []
    with pytest.raises(ValidationError, match="saved plan differs"):
        dns_workflow.run_manifest(intent(), apply=True, force=True, approved_plan=reviewed)
    assert not FakeClient.writes


def test_api_irrelevant_defaults_do_not_break_idempotence():
    current = dict(ADDRESS, id=1, priority=0, weight=0, port=0, protocol=None, service=None)
    assert not plan_records(ZONE, [current], [ADDRESS], SCOPE)["operations"]


def test_updates_preserve_existing_record_id(dns_workflow):
    FakeClient.records_now = [dict(ADDRESS, id=12, target="203.0.113.11")]
    dns_workflow.run_manifest(intent(), apply=True, force=True)
    assert FakeClient.writes == [("update", 12, ADDRESS)]


def test_json_duplicate_key_error_is_preserved(tmp_path):
    from pnutils.linode import load_document

    path = tmp_path / "input.json"
    path.write_text('{"schemaVersion": 1, "schemaVersion": 2}')
    with pytest.raises(ValidationError, match="duplicate input key"):
        load_document(path)


def test_whole_zone_replace_verifies_result(dns_workflow):
    FakeClient.records_now = [dict(ADDRESS, id=12, target="203.0.113.11")]
    document = intent()
    document["zones"][0].update(mode="replace", managedRecordSets=[], allowZoneWipe=True)
    dns_workflow.run_manifest(document, apply=True, force=True)
    assert [entry[0] for entry in FakeClient.writes] == ["delete", "create"]


def test_pagination_reads_all_records(monkeypatch):
    from pnutils.linode import DNSClient

    monkeypatch.setenv("TOKEN", "do-not-log")
    client = DNSClient("TOKEN")
    calls = []

    def response(method, path):
        calls.append(path)
        return {"data": [{"id": len(calls)}], "pages": 2}

    monkeypatch.setattr(client, "_request", response)
    assert client.records(10) == [{"id": 1}, {"id": 2}]
    assert len(calls) == 2
    assert "do-not-log" not in repr(client)


def test_update_omits_immutable_type(monkeypatch):
    from pnutils.linode import DNSClient

    monkeypatch.setenv("TOKEN", "do-not-log")
    client = DNSClient("TOKEN")
    calls = []
    monkeypatch.setattr(client, "_request", lambda *args: calls.append(args))
    client.update(10, 12, ADDRESS)
    assert calls[0] == (
        "PUT",
        "/domains/10/records/12",
        {key: value for key, value in ADDRESS.items() if key != "type"},
    )


def test_discovery_drift_never_writes(dns_workflow):
    def changed():
        raise ValidationError("cluster addresses changed")

    with pytest.raises(ValidationError, match="cluster addresses changed"):
        dns_workflow.run_manifest(intent(), apply=True, force=True, pre_apply=changed)
    assert not FakeClient.writes


def test_partial_failure_reports_uncertain_outcome(dns_workflow, monkeypatch):
    from pnutils.exceptions import PnUtilsError

    def fail(*args):
        raise PnUtilsError("network failure")

    monkeypatch.setattr(FakeClient, "create", fail)
    with pytest.raises(PnUtilsError, match="Some changes may have committed"):
        dns_workflow.run_manifest(intent(), apply=True, force=True)


def test_force_without_apply_and_noninteractive_apply_rejected(dns_workflow, monkeypatch):
    with pytest.raises(ValidationError, match="--force requires"):
        dns_workflow.run_manifest(intent(), force=True)
    monkeypatch.setattr(dns_workflow.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValidationError, match="terminal"):
        dns_workflow.run_manifest(intent(), apply=True)
    assert not FakeClient.writes


def test_http_failure_never_exposes_credentials(monkeypatch):
    import urllib.error

    from pnutils.exceptions import PnUtilsError
    from pnutils.linode import DNSClient

    monkeypatch.setenv("TOKEN", "do-not-log")
    client = DNSClient("TOKEN")

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("url", 403, "do-not-log", {}, None)

    monkeypatch.setattr(client._opener, "open", fail)
    with pytest.raises(PnUtilsError, match="HTTP 403") as caught:
        client.records(10)
    assert "do-not-log" not in str(caught.value)


def test_cname_loop_and_wrong_address_family_rejected():
    from pnutils.linode import validate_record

    with pytest.raises(ValidationError, match="itself"):
        validate_record(ZONE, {"name": "www", "type": "CNAME", "target": f"www.{ZONE}"})
    with pytest.raises(ValidationError, match="address family"):
        validate_record(ZONE, dict(ADDRESS, type="AAAA"))
