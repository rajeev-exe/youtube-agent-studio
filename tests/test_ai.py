from ai import AIClient


def test_ai_request_uses_configured_timeout_and_fails_closed(cfg):
    cfg.ai_timeout_seconds = 2
    cfg.max_retries = 1

    class SlowSession:
        timeout_seen = None

        def post(self, *args, **kwargs):
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
    assert 0 < session.timeout_seen <= 2
