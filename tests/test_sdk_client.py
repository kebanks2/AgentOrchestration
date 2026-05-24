import pytest

from src.sdk.client import OrchestratorClient


def test_client_uses_explicit_api_key(monkeypatch):
    monkeypatch.delenv("AO_API_KEY", raising=False)

    client = OrchestratorClient(
        base_url="https://example.test",
        api_key=" key ",
    )

    assert client.api_key == "key"


def test_client_uses_environment_api_key(monkeypatch):
    monkeypatch.setenv("AO_API_KEY", " env-key ")

    client = OrchestratorClient(base_url="https://example.test")

    assert client.api_key == "env-key"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_client_rejects_missing_api_key(monkeypatch, value):
    monkeypatch.delenv("AO_API_KEY", raising=False)

    with pytest.raises(ValueError, match="AO_API_KEY is required"):
        OrchestratorClient(base_url="https://example.test", api_key=value)


def test_client_builds_authorization_header_after_key_validation(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request):
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr("src.sdk.client.urlopen", fake_urlopen)

    client = OrchestratorClient(
        base_url="https://example.test",
        api_key="secret-key",
    )

    assert client.list_agents() == {"ok": True}
    assert captured["authorization"] == "Bearer secret-key"
