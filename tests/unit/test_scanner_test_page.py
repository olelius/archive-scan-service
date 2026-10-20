"""测试页静态资源与现有 API 覆盖验证，不访问扫描仪。"""

from html.parser import HTMLParser
from types import SimpleNamespace

from fastapi.testclient import TestClient


class ApiButtons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.endpoints = set()
        self.websocket = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "button" and attributes.get("data-path"):
            self.endpoints.add((attributes["data-method"].lower(), attributes["data-path"]))
        if attributes.get("data-websocket") == "/api/v1/events":
            self.websocket = True


def test_test_page_serves_utf8_html_and_covers_every_existing_api():
    from app.main import create_app

    context = SimpleNamespace(
        settings=SimpleNamespace(allowed_origins=()),
        start=lambda: None,
        close=lambda: None,
    )
    application = create_app(context=context)
    with TestClient(application) as client:
        response = client.get("/test")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "扫描接口测试台" in response.text
        parser = ApiButtons()
        parser.feed(response.text)
        expected = {
            (method, path)
            for path, methods in client.get("/openapi.json").json()["paths"].items()
            for method in methods
        }
        assert len(expected) == 17
        assert parser.endpoints == expected
        assert parser.websocket is True
