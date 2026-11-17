from ai import AIClient
from ai import extract_json
import requests


def test_ai_request_uses_configured_timeout_and_fails_closed(cfg):
    cfg.ai_timeout_seconds = 2
    cfg.max_retries = 1

    class SlowSession:
        timeout_seen = None
        calls = 0

        def post(self, *args, **kwargs):
            self.calls += 1
            self.timeout_seen = kwargs["timeout"]
            raise TimeoutError("simulated AI timeout")

    client = AIClient(cfg)
    client.checked = True
    client.usable["ollama"] = True
    session = SlowSession()
    client.session = session

    result = client.chat_json(
        "system",
        "user",
        lambda value: value,
        "test-timeout",
    )

    assert result is None
    assert session.calls == 1
    assert isinstance(session.timeout_seen, tuple)
    assert 0 < session.timeout_seen[0] <= session.timeout_seen[1] <= 2


def test_extract_json_handles_fenced_json():
    assert extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_transport_timeout_is_not_retried_and_logs_candidate_namespace(cfg, caplog):
    cfg.max_retries = 3

    class SlowSession:
        calls = 0

        def post(self, *args, **kwargs):
            self.calls += 1
            raise requests.ReadTimeout("slow inference")

    client = AIClient(cfg)
    client.checked = True
    client.usable["ollama"] = True
    client.session = SlowSession()

    result = client.chat_json(
        "system", "small prompt", lambda value: value, "classify:video123"
    )

    assert result is None
    assert client.session.calls == 1
    assert "AI (ollama) request failed for classify:video123: ReadTimeout" in caplog.text


def test_malformed_response_gets_one_correction_attempt(cfg):
    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": self.body}}

    class Session:
        calls = 0

        def post(self, *args, **kwargs):
            self.calls += 1
            return Response("not json" if self.calls == 1 else '{"ok": true}')

    client = AIClient(cfg)
    client.checked = True
    client.usable["ollama"] = True
    client.session = Session()
    result = client.chat_json("system", "prompt", lambda value: value, "bad-json")

    assert result == ({"ok": True}, "ollama")
    assert client.session.calls == 2
