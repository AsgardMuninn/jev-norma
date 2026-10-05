"""Tests del fetcher resiliente (TDD, sin red en CI)."""
import pytest
import urllib.error

import jev_norma.fetch as fetchmod
from jev_norma import fetch_html

BROWSER_UA_HINT = "Mozilla/5.0"


def test_fetch_rejects_non_http():
    with pytest.raises(ValueError):
        fetch_html("not-a-url")


def test_fetch_uses_browser_ua():
    """El primer intento debe usar una User-Agent de navegador (no urllib default)."""
    assert fetchmod.BUILTIN_UA.startswith(BROWSER_UA_HINT)
    assert "python-urllib" not in fetchmod.BUILTIN_UA.lower()


def test_fetch_retries_then_raises(monkeypatch):
    """Dos fallos transitorios -> un retry -> éxito; nunca cuelga."""
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"<html></html>"

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise urllib.error.URLError(ConnectionError("temporary"))
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    html = fetch_html("https://example.com/pol")
    assert html == "<html></html>"
    assert calls["n"] == 3


def test_fetch_gives_up_on_http_404(monkeypatch):
    """Un 404 (no transitorio) no se reintenta: declina con error claro."""
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="404"):
        fetch_html("https://example.com/na")


def test_fetch_returns_str(monkeypatch):
    """El resultado es str UTF-8, no bytes."""

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return "<html><body><p>cláusula</p></body></html>".encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: FakeResp())
    assert isinstance(fetch_html("https://example.com/pol"), str)