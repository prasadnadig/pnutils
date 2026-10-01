"""Tests for Kubernetes helpers, CLI safety, and certificate behavior."""

from __future__ import annotations

import stat

import pytest

from pnutils.exceptions import ConfirmationDeclined, KubectlError, ValidationError
from pnutils.k8s import cli, client, kubectl, secrets, tls
from pnutils.scripts import CommandResult
from pnutils.scripts import secure as secure_helpers
from pnutils.scripts.secure import (
    remove_secret_file,
    temporary_secret_directory,
    write_secret_file,
)
from pnutils.ssl_certs import tls as certs


def test_secure_temp_dir_is_private_and_cleans_up():
    with temporary_secret_directory() as path:
        created = write_secret_file(path, "value", "s3cret")
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        assert stat.S_IMODE(created.stat().st_mode) == 0o600
    assert not path.exists()


def test_shred_removes_file(tmp_path):
    target = tmp_path / "key"
    target.write_text("secret")
    assert remove_secret_file(target) is True
    assert not target.exists()
    assert remove_secret_file(target) is True


def test_certificate_repr_hides_private_key():
    certificate = certs.CertificatePair(
        certificate_pem="CERT", private_key_pem="PRIVATE"
    )
    assert "PRIVATE" not in repr(certificate)
    assert certificate.private_key_pem == "PRIVATE"


def test_load_certificate_pair(tmp_path):
    (tmp_path / "tls.crt").write_text("CERT")
    (tmp_path / "tls.key").write_text("KEY")
    loaded = certs.load_certificate_pair(tmp_path / "tls.crt", tmp_path / "tls.key")
    assert (loaded.certificate_pem, loaded.private_key_pem) == ("CERT", "KEY")


def test_openssl_san_argument():
    assert certs._openssl_san_argument(["a.example"], ["10.0.0.1"]) == "DNS:a.example,IP:10.0.0.1"


@pytest.mark.parametrize("bad", [{"ip_addresses": ["nope"]}, {}])
def test_openssl_san_argument_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        certs._openssl_san_argument(**bad)


def test_service_dns_names():
    assert tls.service_dns_names("payments", "team-a") == [
        "payments",
        "payments.team-a",
        "payments.team-a.svc",
        "payments.team-a.svc.cluster.local",
    ]


@pytest.mark.parametrize("kwargs", [{"validity_days": 0}, {"key_bits": 512}])
def test_generate_self_signed_validates_input(kwargs):
    with pytest.raises(ValidationError):
        certs.generate_self_signed_certificate("payments", **kwargs)


def test_kubectl_path_missing_binary():
    with pytest.raises(KubectlError):
        kubectl.kubectl_path("definitely-not-a-real-binary")


def test_resolve_backend_prefers_client():
    assert secrets._resolve_backend("auto") == "client"
    assert secrets._resolve_backend("kubectl") == "kubectl"


def test_resolve_backend_rejects_unknown():
    with pytest.raises(ValidationError):
        secrets._resolve_backend("telepathy")


def test_kubernetes_client_keeps_api_clients_instance_local(monkeypatch):
    contexts = []
    api_clients = []

    def load_configuration(context, in_cluster):
        contexts.append(context)
        return object(), False

    class FakeApiClient:
        def __init__(self, configuration):
            self.configuration = configuration
            self.closed = False
            api_clients.append(self)

        def close(self):
            self.closed = True

    class FakeApi:
        def __init__(self, api_client):
            self.api_client = api_client

    fake_kubernetes = type(
        "FakeKubernetes",
        (),
        {"ApiClient": FakeApiClient, "CoreV1Api": FakeApi, "AppsV1Api": FakeApi},
    )
    monkeypatch.setattr(client, "require", lambda name, extra: fake_kubernetes)
    monkeypatch.setattr(client, "_load_configuration", load_configuration)

    with client.KubernetesClient(context="east") as east, client.KubernetesClient(
        context="west"
    ) as west:
        assert east.core.api_client is not west.core.api_client
        assert east.apps.api_client is east.core.api_client
        assert west.apps.api_client is west.core.api_client

    assert contexts == ["east", "west"]
    assert all(api_client.closed for api_client in api_clients)


def test_load_configuration_uses_explicit_context_without_in_cluster_probe(monkeypatch):
    calls = []

    class Configuration:
        host = "https://kubernetes.example"

    class ConfigException(Exception):
        pass

    def load_kube_config(context=None, client_configuration=None):
        calls.append((context, client_configuration))

    def load_incluster_config(**kwargs):
        pytest.fail("explicit context should select local kubeconfig")

    fake_client = type("FakeClient", (), {"Configuration": Configuration})
    fake_config = type(
        "FakeConfig",
        (),
        {
            "ConfigException": ConfigException,
            "load_kube_config": staticmethod(load_kube_config),
            "load_incluster_config": staticmethod(load_incluster_config),
        },
    )
    monkeypatch.setattr(
        client,
        "require",
        lambda name, extra: fake_client if name == "kubernetes.client" else fake_config,
    )

    configuration, is_in_cluster = client._load_configuration(context="production")

    assert calls == [("production", configuration)]
    assert is_in_cluster is False


def test_load_configuration_falls_back_to_kubeconfig(monkeypatch):
    calls = []

    class Configuration:
        host = "https://kubernetes.example"

    class ConfigException(Exception):
        pass

    def load_incluster_config(client_configuration):
        raise ConfigException("not running in a cluster")

    def load_kube_config(context=None, client_configuration=None):
        calls.append((context, client_configuration))

    fake_client = type("FakeClient", (), {"Configuration": Configuration})
    fake_config = type(
        "FakeConfig",
        (),
        {
            "ConfigException": ConfigException,
            "load_kube_config": staticmethod(load_kube_config),
            "load_incluster_config": staticmethod(load_incluster_config),
        },
    )
    monkeypatch.setattr(
        client,
        "require",
        lambda name, extra: fake_client if name == "kubernetes.client" else fake_config,
    )

    configuration, is_in_cluster = client._load_configuration()

    assert calls == [(None, configuration)]
    assert is_in_cluster is False


def test_client_backend_uses_server_side_apply_and_encoded_data(monkeypatch):
    seen = {}

    class Model:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ApiException(Exception):
        pass

    class CoreApi:
        def patch_namespaced_secret(self, **kwargs):
            seen.update(kwargs)

    class FakeKubernetesClient:
        core = CoreApi()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    fake_models = type("FakeModels", (), {"V1Secret": Model, "V1ObjectMeta": Model})
    fake_rest = type("FakeRest", (), {"ApiException": ApiException})
    monkeypatch.setattr(client, "KubernetesClient", FakeKubernetesClient)
    monkeypatch.setattr(
        secrets,
        "require",
        lambda name, extra: fake_models if name == "kubernetes.client" else fake_rest,
    )

    result = secrets.apply_secret(
        secrets.SecretSpec.generic("payments-token", "payments", {"token": "s3cret"}),
        backend="client",
    )

    assert result == "secret/payments-token applied"
    assert seen["body"].metadata.name == "payments-token"
    assert seen["body"].data == {"token": "czNjcmV0"}
    assert seen["field_manager"] == "pnutils"
    assert seen["_content_type"] == "application/apply-patch+yaml"


def test_kubectl_manifest_apply_uses_server_side_field_manager(monkeypatch):
    seen = {}

    def fake_run(args, binary=None, input_text=None, check=True):
        seen["args"] = list(args)
        seen["input_text"] = input_text
        return CommandResult(args=list(args), returncode=0, stdout="applied", stderr="")

    monkeypatch.setattr(kubectl, "run_kubectl", fake_run)

    assert kubectl.apply_manifest(
        "kind: Secret", server_side=True, field_manager="pnutils"
    ) == "applied"
    assert seen["args"] == [
        "apply",
        "--server-side",
        "--field-manager=pnutils",
        "-f",
        "-",
    ]
    assert seen["input_text"] == "kind: Secret"


def test_server_side_manifest_apply_requires_field_manager():
    with pytest.raises(ValidationError, match="field_manager is required"):
        kubectl.apply_manifest("kind: Secret", server_side=True)


def test_secret_data_from_env(monkeypatch):
    monkeypatch.setenv("PNUTILS_TEST_TOKEN", "abc")
    assert secrets.secret_data_from_env({"token": "PNUTILS_TEST_TOKEN"}) == {"token": "abc"}


def test_secret_data_from_env_rejects_missing(monkeypatch):
    monkeypatch.delenv("PNUTILS_TEST_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        secrets.secret_data_from_env({"token": "PNUTILS_TEST_TOKEN"})


def test_generic_secret_spec_hides_values_from_repr():
    spec = secrets.SecretSpec.generic("payments-token", "team-a", {"token": "s3cret"})
    assert spec.secret_type == "Opaque"
    assert spec.keys == ("token",)
    assert "s3cret" not in repr(spec)
    assert "s3cret" not in " ".join(spec.summary())


def test_generic_secret_spec_requires_values():
    with pytest.raises(ValidationError):
        secrets.SecretSpec.generic("name", "default", {})


def test_tls_secret_spec_carries_both_keys():
    spec = secrets.SecretSpec.tls(
        "payments-tls",
        "team-a",
        certs.CertificatePair(certificate_pem="CERT", private_key_pem="KEY"),
    )
    assert spec.secret_type == "kubernetes.io/tls"
    assert spec.keys == secrets._TLS_SECRET_KEYS
    assert "KEY" not in repr(spec)


def test_kubectl_backend_keeps_values_out_of_argv(monkeypatch):
    seen: dict[str, list[str]] = {}

    def fake_render(args, binary=None):
        seen["args"] = list(args)
        return "apiVersion: v1\nkind: Secret\n"

    def fake_apply(manifest, binary=None, server_side=False, field_manager=None):
        seen["apply"] = (server_side, field_manager)
        return "secret/n applied"

    monkeypatch.setattr(kubectl, "render_manifest", fake_render)
    monkeypatch.setattr(kubectl, "apply_manifest", fake_apply)
    monkeypatch.setattr(secrets, "get_cluster_target", lambda backend="auto": None)

    spec = secrets.SecretSpec.generic("payments-token", "team-a", {"token": "s3cret"})
    assert secrets.apply_secret(spec, backend="kubectl") == "secret/n applied"
    assert "s3cret" not in " ".join(seen["args"])
    assert any(arg.startswith("--from-file=token=") for arg in seen["args"])
    assert seen["apply"] == (True, "pnutils")


def test_kubectl_backend_uses_tls_flags(monkeypatch):
    seen: dict[str, list[str]] = {}

    def fake_render(args, binary=None):
        seen["args"] = list(args)
        return "apiVersion: v1\nkind: Secret\n"

    def fake_apply(manifest, binary=None, server_side=False, field_manager=None):
        seen["apply"] = (server_side, field_manager)
        return "secret/n applied"

    monkeypatch.setattr(kubectl, "render_manifest", fake_render)
    monkeypatch.setattr(kubectl, "apply_manifest", fake_apply)

    spec = secrets.SecretSpec.tls(
        "t", "ns", certs.CertificatePair(certificate_pem="CERT", private_key_pem="KEY")
    )
    secrets.apply_secret(spec, backend="kubectl")

    joined = " ".join(seen["args"])
    assert "--cert=" in joined and "--key=" in joined
    assert "CERT" not in joined and "KEY" not in joined
    assert seen["apply"] == (True, "pnutils")


def test_kubectl_backend_preserves_non_opaque_secret_type(monkeypatch):
    seen: dict[str, list[str]] = {}

    def fake_render(args, binary=None):
        seen["args"] = list(args)
        return "apiVersion: v1\nkind: Secret\n"

    monkeypatch.setattr(kubectl, "render_manifest", fake_render)
    monkeypatch.setattr(
        kubectl, "apply_manifest", lambda *a, **k: "secret/pull applied"
    )

    # kubectl would otherwise default this to Opaque and kubelet would ignore it.
    spec = secrets.SecretSpec(
        "pull", "apps", "kubernetes.io/dockerconfigjson", {".dockerconfigjson": "{}"}
    )
    secrets.apply_secret(spec, backend="kubectl")

    assert "--type=kubernetes.io/dockerconfigjson" in seen["args"]


def test_kubectl_backend_cleans_secret_files_when_render_fails(tmp_path, monkeypatch):
    staging_dir = tmp_path / "pnutils-secret-test"

    def make_temp_dir(prefix):
        staging_dir.mkdir()
        return str(staging_dir)

    def fail_render(args, binary=None):
        raise KubectlError("render failed")

    monkeypatch.setattr(secure_helpers.tempfile, "mkdtemp", make_temp_dir)
    monkeypatch.setattr(kubectl, "render_manifest", fail_render)

    spec = secrets.SecretSpec.generic("payments-token", "team-a", {"token": "secret"})
    with pytest.raises(KubectlError, match="render failed"):
        secrets.apply_secret(spec, backend="kubectl")

    assert not staging_dir.exists()


def test_apply_secret_aborts_when_declined(monkeypatch):
    spec = secrets.SecretSpec.generic("n", "ns", {"token": "v"})
    monkeypatch.setattr(secrets, "get_cluster_target", lambda backend="auto": None)
    monkeypatch.setattr(secrets, "_apply_with_client", lambda plan: pytest.fail("must not apply"))

    with pytest.raises(ConfirmationDeclined):
        secrets.apply_secret(spec, confirm=lambda target, spec: False, backend="client")


def test_apply_secret_applies_when_confirmed(monkeypatch):
    spec = secrets.SecretSpec.generic("n", "ns", {"token": "v"})
    monkeypatch.setattr(secrets, "get_cluster_target", lambda backend="auto": None)
    monkeypatch.setattr(secrets, "_apply_with_client", lambda spec: "secret/n applied")

    result = secrets.apply_secret(spec, confirm=lambda target, spec: True, backend="client")
    assert result == "secret/n applied"


def test_batch_confirmation_lists_every_secret_once(monkeypatch, capsys):
    class FakeTarget:
        def lines(self):
            return ["cluster: demo", "server: https://kubernetes.example"]

    specs = [
        secrets.SecretSpec.generic("one", "team-a", {"token": "a"}),
        secrets.SecretSpec.generic("two", "team-a", {"token": "b"}),
    ]
    monkeypatch.setattr(secrets, "get_cluster_target", lambda backend="auto": FakeTarget())
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    secrets.confirm_secrets(specs, backend="kubectl", notes=("restarts workloads",))

    out = capsys.readouterr().out
    assert "cluster: demo" in out
    assert "Secret one" in out
    assert "Secret two" in out
    assert "restarts workloads" in out


def test_cli_tls_preview_defers_certificate_generation(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "generate_service_certificate",
        lambda *args, **kwargs: pytest.fail("preview must not generate a certificate"),
    )
    monkeypatch.setattr(
        cli,
        "apply_secret",
        lambda *args, **kwargs: pytest.fail("preview must not apply a Secret"),
    )

    result = cli.main(
        [
            "push-tls-secret",
            "--name",
            "payments-tls",
            "--namespace",
            "team-a",
            "--service-name",
            "payments",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "payments.team-a.svc.cluster.local" in output
    assert "generation is deferred until --apply" in output
    assert "Kubernetes was not contacted" in output
    assert "No files were written." in output


def test_cli_tls_preview_validates_ip_without_generating_certificate(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "generate_service_certificate",
        lambda *args, **kwargs: pytest.fail("preview must not generate a certificate"),
    )

    result = cli.main(
        [
            "push-tls-secret",
            "--name",
            "payments-tls",
            "--service-name",
            "payments",
            "--ip-address",
            "not-an-ip",
        ]
    )

    assert result == 1
    assert "not a valid IP address" in capsys.readouterr().err


def test_cli_force_prints_target_before_apply(monkeypatch, capsys):
    monkeypatch.setenv("PNUTILS_TEST_TOKEN", "not-for-output")
    target = kubectl.ClusterTarget(
        context="test-context",
        cluster="test-cluster",
        server="https://kubernetes.example",
        user="test-user",
    )
    monkeypatch.setattr(cli, "get_cluster_target", lambda backend: target)
    seen = {}

    def fake_apply(plan, confirm, backend):
        seen["output_before_apply"] = capsys.readouterr().out
        assert confirm is None
        return "secret/payments-token created"

    monkeypatch.setattr(cli, "apply_secret", fake_apply)

    result = cli.main(
        [
            "push-env-secret",
            "--name",
            "payments-token",
            "--namespace",
            "team-a",
            "--value-env",
            "PNUTILS_TEST_TOKEN",
            "--apply",
            "--force",
        ]
    )

    assert result == 0
    assert "context: test-context" in seen["output_before_apply"]
    assert "cluster: test-cluster" in seen["output_before_apply"]
    assert "Secret payments-token (Opaque) in namespace team-a" in seen["output_before_apply"]
    assert "not-for-output" not in seen["output_before_apply"]
    assert "secret/payments-token created" in capsys.readouterr().out
