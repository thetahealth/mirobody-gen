"""The generic chat transport refuses redirects and never echoes a malformed credential.

urllib's redirect handler would re-send the Authorization header to the redirect target, and an
invalid header value (e.g. a trailing "\\r" from a CRLF .env) raises an error quoting the header.
"""

import http.server
import threading
import urllib.error

import pytest

from mirobody_gen.llm import client


def test_redirect_is_refused_and_target_never_sees_the_credential():
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.URLError, match="redirect"):
            client.post_chat(f"http://127.0.0.1:{server.server_port}", "synthetic-token", b"{}", 5)
    finally:
        server.shutdown()
    assert hits == [("/chat/completions", "Bearer synthetic-token")]


@pytest.mark.parametrize("token", ["synthetic-token\r", "synthetic token", "synthetic-\x00token"])
def test_malformed_credential_is_refused_without_echo(token, capsys):
    with pytest.raises(SystemExit) as exc:
        client.post_chat("http://127.0.0.1:9", token, b"{}", 1)
    assert "synthetic" not in str(exc.value)


def test_environment_credentials_are_stripped(monkeypatch):
    monkeypatch.setattr(client, "_dotenv", lambda: {})
    for key in ("LLM_BASE_URL", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1\r")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-token\r")
    assert client.endpoint() == ("https://example.invalid/v1", "synthetic-token")
