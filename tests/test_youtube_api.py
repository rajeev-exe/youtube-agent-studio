from youtube_api import YouTubeAPI


class FakeResponse:
    status_code = 200
    content = b'{"items": []}'

    def json(self):
        return {"items": []}


class FakeSession:
    def __init__(self):
        self.call = None

    def request(self, method, url, **kwargs):
        self.call = (method, url, kwargs)
        return FakeResponse()


def test_search_sends_q_parameter_with_read_only_get(cfg):
    cfg.youtube_api_key = "mock-key"
    session = FakeSession()
    api = YouTubeAPI(cfg, session=session)

    assert api.search(
        q="family animation",
        region="IN",
        language="en",
        published_after=None,
        max_results=25,
    ) == []

    method, url, kwargs = session.call
    assert method == "GET"
    assert url.endswith("/search")
    assert kwargs["params"]["q"] == "family animation"
    assert kwargs["params"]["type"] == "video"
    assert kwargs["headers"] == {"x-goog-api-key": "mock-key"}
