"""CORS allowlist behaviour: FRONTEND_ORIGINS (comma list) + FRONTEND_ORIGIN_REGEX + local regex.

Builds a tiny app with the same middleware arguments main.py derives, so the
production parser/regex are exercised without re-importing main with new env.
"""
import importlib
import os

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

PLANNED = ("https://paaxmusic.app,https://www.paaxmusic.app,https://demo.paaxmusic.app,"
           "https://uzielsandoval.com,https://www.uzielsandoval.com")
PREVIEW_RE = r"https://[a-z0-9-]+\.paax-demo\.pages\.dev"


def _build(origins_env: str, regex_env: str = ""):
    """Re-evaluate main.py's CORS derivation under the given env, return a TestClient."""
    os.environ["FRONTEND_ORIGINS"] = origins_env
    os.environ["FRONTEND_ORIGIN_REGEX"] = regex_env
    import main as m
    importlib.reload(m)   # recomputes _extra_origins / _allow_origin_regex
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    app.add_middleware(CORSMiddleware, allow_origins=m._extra_origins,
                       allow_origin_regex=m._allow_origin_regex, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    return TestClient(app), m


@pytest.fixture(autouse=True)
def _restore_env():
    saved = {k: os.environ.get(k) for k in ("FRONTEND_ORIGINS", "FRONTEND_ORIGIN_REGEX")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import main as m
    importlib.reload(m)


def _preflight(client, origin):
    return client.options("/ping", headers={"Origin": origin,
                                            "Access-Control-Request-Method": "GET",
                                            "Access-Control-Request-Headers": "authorization,content-type"})


def test_parser_accepts_comma_separated_list_with_spaces():
    _, m = _build(" https://paaxmusic.app , https://demo.paaxmusic.app,, ")
    assert m._extra_origins == ["https://paaxmusic.app", "https://demo.paaxmusic.app"]


def test_planned_allowlist_preflight_and_authenticated_requests():
    client, _ = _build(PLANNED)
    for origin in PLANNED.split(","):
        pf = _preflight(client, origin)
        assert pf.status_code == 200, origin
        assert pf.headers["access-control-allow-origin"] == origin
        assert pf.headers["access-control-allow-credentials"] == "true"
        assert "authorization" in pf.headers["access-control-allow-headers"].lower()
        r = client.get("/ping", headers={"Origin": origin, "Authorization": "Bearer x"})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin


def test_unlisted_origin_gets_no_cors_headers_but_request_still_served():
    client, _ = _build(PLANNED)
    pf = _preflight(client, "https://evil.example")
    assert pf.status_code == 400                      # preflight refused
    r = client.get("/ping", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    # http:// variant of a listed host is NOT allowed (scheme is part of the origin)
    assert "access-control-allow-origin" not in client.get(
        "/ping", headers={"Origin": "http://paaxmusic.app"}).headers


def test_requests_without_origin_header_unaffected():
    """Mobile apps (Dio/http on Android/iOS) send no Origin header — CORS never applies."""
    client, _ = _build(PLANNED)
    r = client.get("/ping", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


def test_local_dev_origins_always_allowed():
    client, _ = _build(PLANNED)
    for origin in ["http://localhost:8087", "http://127.0.0.1:54321", "http://localhost",
                   "http://192.168.1.20:3000", "http://10.0.2.2:8000", "https://localhost:7357"]:
        assert client.get("/ping", headers={"Origin": origin}).headers.get(
            "access-control-allow-origin") == origin, origin
    # regex is anchored: a look-alike public host is rejected
    assert "access-control-allow-origin" not in client.get(
        "/ping", headers={"Origin": "http://localhost.evil.example"}).headers


def test_cloudflare_pages_preview_regex_is_optional_and_anchored():
    client, _ = _build(PLANNED, PREVIEW_RE)
    ok = "https://3f2a1b9c.paax-demo.pages.dev"
    assert _preflight(client, ok).headers.get("access-control-allow-origin") == ok
    branch = "https://feat-x.paax-demo.pages.dev"
    assert client.get("/ping", headers={"Origin": branch}).headers.get(
        "access-control-allow-origin") == branch
    for bad in ["https://x.other.pages.dev", "https://paax-demo.pages.dev.evil.example",
                "http://3f2a1b9c.paax-demo.pages.dev"]:
        assert "access-control-allow-origin" not in client.get("/ping", headers={"Origin": bad}).headers, bad
    # without the env var, previews are not allowed
    client2, _ = _build(PLANNED, "")
    assert "access-control-allow-origin" not in client2.get("/ping", headers={"Origin": ok}).headers
