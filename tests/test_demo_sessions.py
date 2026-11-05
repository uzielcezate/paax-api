"""Demo sessions: service lifecycle (create / status / end / sweep) + /v2/demo router."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app_container
from main import app
from services.demo.demo_session_service import (
    DEMO_FLAG, PASSWORD_LENGTH, DemoConfig, DemoError, DemoSessionService,
    generate_demo_password, password_meets_policy,
)
from services.rate_limit import RateLimiter
from tests.conftest import FakeGateway


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeAdmin:
    def __init__(self, fail_create=False):
        self.created, self.deleted = [], []
        self.fail_create = fail_create
        self._n = 0

    async def create_user(self, attributes):
        if self.fail_create:
            raise RuntimeError("gotrue down")
        self._n += 1
        uid = f"demo-uid-{self._n}"
        self.created.append((uid, attributes))
        # Mimic the signup trigger: a profile row appears for the new user.
        return {"user": {"id": uid, "email": attributes["email"]}}

    async def delete_user(self, id, should_soft_delete=False):
        self.deleted.append(id)


class FakeTokens:
    def __init__(self, fail_grant=False):
        self.fail_grant = fail_grant
        self.granted = []
        self.users = {}  # access_token -> user dict

    async def password_grant(self, email, password):
        if self.fail_grant:
            raise DemoError(502, "DEMO_TOKEN_EXCHANGE_FAILED", "nope")
        self.granted.append((email, password))
        tok = f"at-{len(self.granted)}"
        return {"access_token": tok, "refresh_token": f"rt-{len(self.granted)}",
                "token_type": "bearer", "expires_in": 3600}

    async def get_user(self, access_token):
        return self.users.get(access_token)


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


T0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)


def _service(gw=None, admin=None, tokens=None, clock=None, trigger_profiles=True):
    gw = gw or FakeGateway({"profiles": [], "demo_sessions": []})
    admin = admin or FakeAdmin()
    tokens = tokens or FakeTokens()
    clock = clock or Clock(T0)
    if trigger_profiles:
        # emulate the DB trigger: create_user → profiles row
        orig = admin.create_user

        async def create_with_trigger(attrs):
            res = await orig(attrs)
            gw._tables.setdefault("profiles", []).append(
                {"id": res["user"]["id"], "username": attrs["user_metadata"]["username"],
                 "onboarding_completed": False})
            return res
        admin.create_user = create_with_trigger
    svc = DemoSessionService(gateway=gw, auth_admin=admin, token_client=tokens,
                             config=DemoConfig(session_seconds=600), now=clock)
    return svc, gw, admin, tokens, clock


# ── service ──────────────────────────────────────────────────────────────────

async def test_create_mints_isolated_identity_with_server_expiry():
    svc, gw, admin, tokens, clock = _service()
    out = await svc.create(client="portfolio")

    uid, attrs = admin.created[0]
    assert attrs["email_confirm"] is True
    assert attrs["app_metadata"][DEMO_FLAG] is True          # the guard's marker
    assert attrs["email"].endswith("@demo.paaxmusic.app")
    assert len(attrs["password"]) >= 32
    # password is exchanged, never returned
    assert "password" not in str(out)
    assert out["auth"]["access_token"] == "at-1" and out["auth"]["refresh_token"] == "rt-1"

    # profile completed so the Flutter startup machine goes straight to the shell
    prof = gw._tables["profiles"][0]
    assert prof["onboarding_completed"] is True
    assert prof["display_name"] == "Demo Visitor" and prof["birth_date"] and prof["country_code"]

    # authoritative expiry = start + 600 s
    row = gw._tables["demo_sessions"][0]
    assert row["user_id"] == uid
    assert row["expires_at"] == "2026-10-30T12:10:00Z"
    assert out["session"]["expires_at"] == "2026-10-30T12:10:00Z"
    assert out["session"]["duration_seconds"] == 600

    # no follows are seeded — Home shows real discovery sections, not fake taste
    assert "user_followed_artists" not in gw._tables
    assert "user_followed_genres" not in gw._tables


async def test_two_visitors_get_independent_identities_and_expiries():
    svc, gw, admin, tokens, clock = _service()
    a = await svc.create()
    clock.now = T0 + timedelta(seconds=90)
    b = await svc.create()
    assert a["session"]["id"] != b["session"]["id"]
    assert a["auth"]["access_token"] != b["auth"]["access_token"]
    assert b["session"]["expires_at"] == "2026-10-30T12:11:30Z"
    assert a["session"]["expires_at"] == "2026-10-30T12:10:00Z"
    assert admin.created[0][1]["email"] != admin.created[1][1]["email"]


async def test_create_rolls_back_identity_when_token_exchange_fails():
    svc, gw, admin, tokens, clock = _service(tokens=FakeTokens(fail_grant=True))
    with pytest.raises(DemoError) as exc:
        await svc.create()
    assert exc.value.code == "DEMO_TOKEN_EXCHANGE_FAILED"
    assert admin.deleted == ["demo-uid-1"]            # no orphan identity


async def test_create_maps_gotrue_failure():
    svc, *_ = _service(admin=FakeAdmin(fail_create=True), trigger_profiles=False)
    with pytest.raises(DemoError) as exc:
        await svc.create()
    assert exc.value.status == 502 and exc.value.code == "DEMO_IDENTITY_CREATE_FAILED"


# ── one-time password policy (Supabase: >= 8 chars, lower+upper+digit+symbol) ─

_ALLOWED_PW_CHARS = set(string.ascii_letters + string.digits + "!@#$%^&*()_+-=[]{}|;:,.<>?")


def test_generated_passwords_always_satisfy_the_supabase_policy():
    seen = set()
    for _ in range(5000):
        pw = generate_demo_password()
        assert len(pw) == PASSWORD_LENGTH >= 8
        assert password_meets_policy(pw), "missing a required character class"
        assert set(pw) <= _ALLOWED_PW_CHARS
        seen.add(pw)
    assert len(seen) == 5000, "passwords must be unique (CSPRNG)"


def test_generated_passwords_are_not_deterministic():
    assert generate_demo_password() != generate_demo_password()


def test_generate_demo_password_rejects_lengths_that_cannot_hold_every_class():
    with pytest.raises(ValueError):
        generate_demo_password(7)


def test_policy_checker_matches_the_dashboard_rules():
    assert password_meets_policy("aB3!aB3!")
    assert not password_meets_policy("aB3!aB3")          # < 8
    assert not password_meets_policy("ab3!ab3!ab3!")     # no uppercase
    assert not password_meets_policy("AB3!AB3!AB3!")     # no lowercase
    assert not password_meets_policy("aBc!aBc!aBc!")     # no digit
    assert not password_meets_policy("aB3aB3aB3aB3")     # no symbol
    assert not password_meets_policy(secrets.token_urlsafe(32).replace("-", "a").replace("_", "a"))


def test_regression_token_urlsafe_did_not_satisfy_the_policy_reliably():
    # The previous generator: only `-`/`_` count as symbols, so a noticeable
    # share of outputs had none. This is why creation returned 502 in production.
    misses = sum(not password_meets_policy(secrets.token_urlsafe(32)) for _ in range(400))
    assert misses > 0


class AuthWeakPasswordError(Exception):
    """Name mirrors supabase-py's exception; the service matches on the type name."""


class WeakOnceAdmin(FakeAdmin):
    def __init__(self, weak_times=1):
        super().__init__()
        self.weak_times = weak_times
        self.attempts = []

    async def create_user(self, attributes):
        self.attempts.append(attributes["password"])
        if len(self.attempts) <= self.weak_times:
            raise AuthWeakPasswordError("Password should contain at least one character of each: ...")
        return await super().create_user(attributes)


async def test_create_user_gets_a_policy_compliant_password_and_the_grant_uses_the_same_one():
    svc, gw, admin, tokens, clock = _service()
    await svc.create()
    _, attrs = admin.created[0]
    assert password_meets_policy(attrs["password"])
    assert tokens.granted[0] == (attrs["email"], attrs["password"])


async def test_create_retries_exactly_once_on_weak_password_with_a_fresh_password(caplog):
    admin = WeakOnceAdmin(weak_times=1)
    svc, gw, admin, tokens, clock = _service(admin=admin)
    with caplog.at_level("WARNING", logger="paax.demo"):
        out = await svc.create()
    assert len(admin.attempts) == 2 and admin.attempts[0] != admin.attempts[1]
    assert all(password_meets_policy(p) for p in admin.attempts)
    assert len(admin.created) == 1 and admin.deleted == []            # exactly one identity, no orphan
    assert tokens.granted[0][1] == admin.attempts[1]                   # grant uses the accepted password
    assert out["session"]["id"] == admin.created[0][0]
    # the warning names the error type only — never the password
    assert "AuthWeakPasswordError" in caplog.text
    for p in admin.attempts:
        assert p not in caplog.text and p not in str(out)


async def test_create_does_not_retry_weak_password_more_than_once():
    admin = WeakOnceAdmin(weak_times=5)
    svc, gw, admin, tokens, clock = _service(admin=admin)
    with pytest.raises(DemoError) as exc:
        await svc.create()
    assert exc.value.status == 502 and exc.value.code == "DEMO_IDENTITY_CREATE_FAILED"
    assert len(admin.attempts) == 2 and admin.created == [] and admin.deleted == []
    assert tokens.granted == []


async def test_create_does_not_retry_other_gotrue_failures():
    class CountingFailAdmin(FakeAdmin):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def create_user(self, attributes):
            self.calls += 1
            raise RuntimeError("gotrue down")

    admin = CountingFailAdmin()
    svc, *_ = _service(admin=admin, trigger_profiles=False)
    with pytest.raises(DemoError):
        await svc.create()
    assert admin.calls == 1


async def test_create_after_weak_password_retry_still_rolls_back_on_later_failure():
    admin = WeakOnceAdmin(weak_times=1)
    svc, gw, admin, tokens, clock = _service(admin=admin, tokens=FakeTokens(fail_grant=True))
    with pytest.raises(DemoError) as exc:
        await svc.create()
    assert exc.value.code == "DEMO_TOKEN_EXCHANGE_FAILED"
    assert len(admin.created) == 1 and admin.deleted == [admin.created[0][0]]   # no orphan identity


async def test_status_is_server_authoritative_and_expires():
    svc, gw, admin, tokens, clock = _service()
    out = await svc.create()
    tok = out["auth"]["access_token"]
    tokens.users[tok] = {"id": out["session"]["id"], "app_metadata": {DEMO_FLAG: True}}

    clock.now = T0 + timedelta(seconds=100)
    st = await svc.status(tok)
    assert st["session"]["active"] is True and st["session"]["remaining_seconds"] == 500

    clock.now = T0 + timedelta(seconds=601)
    st = await svc.status(tok)
    assert st["session"]["active"] is False and st["session"]["remaining_seconds"] == 0


async def test_status_rejects_non_demo_and_invalid_tokens():
    svc, gw, admin, tokens, clock = _service()
    tokens.users["real"] = {"id": "real-user", "app_metadata": {}}
    with pytest.raises(DemoError) as e1:
        await svc.status("real")
    assert e1.value.status == 403
    with pytest.raises(DemoError) as e2:
        await svc.status("garbage")
    assert e2.value.status == 401


async def test_end_marks_and_deletes_identity():
    svc, gw, admin, tokens, clock = _service()
    out = await svc.create()
    tok = out["auth"]["access_token"]
    tokens.users[tok] = {"id": out["session"]["id"], "app_metadata": {DEMO_FLAG: True}}
    res = await svc.end(tok)
    assert res["session"]["ended"] is True
    assert gw._tables["demo_sessions"][0]["ended_at"] == "2026-10-30T12:00:00Z"
    assert admin.deleted == [out["session"]["id"]]


async def test_sweep_deletes_only_expired_past_grace():
    gw = FakeGateway({"profiles": [], "demo_sessions": [
        {"user_id": "old", "expires_at": "2026-10-30T11:50:00Z"},   # expired 10 min ago
        {"user_id": "fresh", "expires_at": "2026-10-30T12:05:00Z"},  # still active
        {"user_id": "edge", "expires_at": "2026-10-30T11:59:30Z"},   # expired 30 s ago (< grace)
    ]})
    svc, gw, admin, tokens, clock = _service(gw=gw)
    n = await svc.sweep_expired(grace_seconds=60)
    assert n == 1 and admin.deleted == ["old"]


# ── router ───────────────────────────────────────────────────────────────────

class _Svc:
    def __init__(self):
        self.created = 0
        self.ended = []

    async def sweep_expired(self, **kw):
        return 0

    async def create(self, client="portfolio"):
        self.created += 1
        return {"session": {"id": f"u{self.created}", "expires_at": "x", "duration_seconds": 600},
                "auth": {"access_token": "at", "refresh_token": "rt"},
                "user": {"id": f"u{self.created}", "username": "demo_1"}}

    async def status(self, token):
        if token != "good":
            raise DemoError(401, "DEMO_UNAUTHORIZED", "bad")
        return {"session": {"id": "u1", "active": True, "remaining_seconds": 42}}

    async def end(self, token):
        self.ended.append(token)
        return {"session": {"id": "u1", "active": False, "ended": True}}


class _Redis:
    """Minimal in-memory INCR/EXPIRE/TTL so the fixed-window limiter is exercised."""

    def __init__(self):
        self.d = {}

    async def incr(self, k):
        self.d[k] = self.d.get(k, 0) + 1
        return self.d[k]

    async def expire(self, k, s):
        return True

    async def ttl(self, k):
        return 10


@pytest.fixture
def demo_client(monkeypatch):
    c = app_container.container
    snapshot = c.__dict__.copy()
    c.demo = _Svc()
    c.rate_limiter = RateLimiter(_Redis())
    import dataclasses
    import config
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, demo_enabled=True, demo_rate_limit_per_ip=2))
    yield TestClient(app), c
    c.__dict__.update(snapshot)


def test_router_create_status_end(demo_client):
    client, c = demo_client
    r = client.post("/v2/demo/sessions", json={"client": "portfolio"}, headers={"X-Real-IP": "203.0.113.9"})
    assert r.status_code == 201
    body = r.json()
    assert body["auth"]["refresh_token"] == "rt" and body["launch"]["web_url"].endswith("demo=1")
    assert r.headers["cache-control"] == "no-store"

    assert client.get("/v2/demo/sessions/me").status_code == 401
    ok = client.get("/v2/demo/sessions/me", headers={"Authorization": "Bearer good"})
    assert ok.status_code == 200 and ok.json()["session"]["remaining_seconds"] == 42
    bad = client.get("/v2/demo/sessions/me", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "DEMO_UNAUTHORIZED"

    e = client.post("/v2/demo/sessions/end", headers={"Authorization": "Bearer good"})
    assert e.status_code == 200 and c.demo.ended == ["good"]


def test_router_rate_limits_per_ip_using_edge_real_ip_header(demo_client):
    """Normal Cloudflare ingress: Railway's edge sets X-Real-IP to the visitor
    (derived from CF-Connecting-IP); the caller-visible copies are also present
    but must not matter."""
    client, c = demo_client
    h = {"X-Real-IP": "203.0.113.9", "CF-Connecting-IP": "203.0.113.9",
         "X-Forwarded-For": "203.0.113.9, 172.71.0.1"}
    assert client.post("/v2/demo/sessions", headers=h).status_code == 201
    assert client.post("/v2/demo/sessions", headers=h).status_code == 201
    r = client.post("/v2/demo/sessions", headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "DEMO_RATE_LIMITED"
    assert "Retry-After" in r.headers
    # a different visitor is unaffected
    assert client.post("/v2/demo/sessions", headers={"X-Real-IP": "198.51.100.7"}).status_code == 201


def test_router_kill_switch(demo_client, monkeypatch):
    client, c = demo_client
    import dataclasses
    import config
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, demo_enabled=False))
    r = client.post("/v2/demo/sessions")
    assert r.status_code == 503 and r.json()["error"]["code"] == "DEMO_DISABLED"


def test_existing_endpoints_untouched_by_demo_router(demo_client):
    client, _ = demo_client
    # Legacy + normalized routes still mounted; nothing requires auth.
    paths = {r.path for r in app.routes}
    for p in ["/health", "/v2/search", "/v2/artist/{artist_id}", "/v2/album/{album_id}",
              "/v2/artists/{artist_id}", "/v2/home", "/lyrics"]:
        assert p in paths
    assert client.get("/health").status_code == 200


# ── Public API key selection (publishable preferred, legacy anon fallback) ──────

def _settings_with(**kw):
    import dataclasses
    import config
    base = dataclasses.replace(config.settings, supabase_url="https://x.supabase.co",
                               supabase_service_role_key="srv", demo_enabled=True,
                               supabase_publishable_key="", supabase_anon_key="")
    return dataclasses.replace(base, **kw)


def test_publishable_key_preferred_over_legacy_anon():
    s = _settings_with(supabase_publishable_key="sb_publishable_abc", supabase_anon_key="eyJ.legacy")
    assert s.supabase_public_api_key == "sb_publishable_abc"
    assert s.supabase_public_api_key_kind == "publishable"
    assert s.demo_configured


def test_legacy_anon_key_still_works_alone():
    s = _settings_with(supabase_anon_key="eyJ.legacy")
    assert s.supabase_public_api_key == "eyJ.legacy"
    assert s.supabase_public_api_key_kind == "anon"
    assert s.demo_configured


def test_demo_not_configured_without_any_public_key():
    s = _settings_with()
    assert s.supabase_public_api_key == "" and s.supabase_public_api_key_kind == "none"
    assert not s.demo_configured


def test_gotrue_http_sends_key_as_opaque_apikey_header_and_rejects_secret():
    from services.demo.demo_session_service import GoTrueHttp
    c = GoTrueHttp("https://x.supabase.co/", "sb_publishable_abc")
    assert c._headers["apikey"] == "sb_publishable_abc"
    assert "Authorization" not in c._headers          # never used as a bearer / JWT
    assert c._base == "https://x.supabase.co/auth/v1"
    with pytest.raises(ValueError):
        GoTrueHttp("https://x.supabase.co", "sb_secret_nope")


def test_demo_is_disabled_by_default_when_env_unset(monkeypatch):
    """Production safety: with DEMO_ENABLED unset the feature is off and the router answers 503."""
    import importlib
    import config
    monkeypatch.delenv("DEMO_ENABLED", raising=False)
    fresh = importlib.reload(config).Settings()
    assert fresh.demo_enabled is False
    assert fresh.demo_configured is False


class _Req:
    """Bare request stand-in for trusted_client_ip unit tests."""

    def __init__(self, headers, peer="10.9.9.9"):
        self.headers = headers
        self.client = type("C", (), {"host": peer})() if peer is not None else None


def test_trusted_client_ip_reads_only_the_edge_header():
    """Topology: client -> Cloudflare -> Railway edge -> app. The ONLY address the
    caller cannot pre-fill is X-Real-IP (replaced by the Railway edge). Everything
    the caller can write — CF-Connecting-IP, X-Forwarded-For, the socket peer in
    production — is ignored."""
    from api.demo_router import trusted_client_ip

    # normal Cloudflare ingress: X-Real-IP already carries the visitor
    assert trusted_client_ip(_Req({"x-real-ip": "198.51.100.7",
                                   "cf-connecting-ip": "198.51.100.7",
                                   "x-forwarded-for": "198.51.100.7, 172.71.0.1"})) == "198.51.100.7"
    # direct-to-Railway-edge path: the caller forges Cloudflare's header, but the
    # edge wrote X-Real-IP from the real socket -> the real address wins
    assert trusted_client_ip(_Req({"x-real-ip": "203.0.113.9",
                                   "cf-connecting-ip": "1.1.1.1",
                                   "x-forwarded-for": "1.1.1.1, 2.2.2.2"})) == "203.0.113.9"
    assert trusted_client_ip(_Req({"x-real-ip": " 203.0.113.9 "})) == "203.0.113.9"
    # caller-controlled headers alone never produce an identity
    assert trusted_client_ip(_Req({"cf-connecting-ip": "198.51.100.7"})) is None
    assert trusted_client_ip(_Req({"x-forwarded-for": "198.51.100.7"})) is None
    assert trusted_client_ip(_Req({})) is None                      # socket peer ignored
    assert trusted_client_ip(_Req({}, peer=None)) is None


def test_trusted_client_ip_rejects_malformed_values_and_buckets_ipv6_per_64():
    from api.demo_router import trusted_client_ip

    for bad in ("", "   ", "not-an-ip", "203.0.113.9, 10.0.0.1", "203.0.113.9:443",
                "999.1.1.1", "2001:db8::1, 2001:db8::2", "203.0.113.0/24"):
        assert trusted_client_ip(_Req({"x-real-ip": bad})) is None, bad
    # IPv6 visitors are keyed on their /64 so rotating interface ids is free no more
    a = trusted_client_ip(_Req({"x-real-ip": "2001:db8:abcd:12::1"}))
    b = trusted_client_ip(_Req({"x-real-ip": "2001:db8:abcd:12:ffff::9"}))
    other = trusted_client_ip(_Req({"x-real-ip": "2001:db8:abcd:13::1"}))
    assert a == b == "2001:db8:abcd:12::/64" and other != a
    assert trusted_client_ip(_Req({"x-real-ip": "::ffff:203.0.113.9"})) is not None


def test_trusted_client_ip_socket_peer_only_when_header_disabled(monkeypatch):
    """DEMO_CLIENT_IP_HEADER='' is the local-development escape hatch (no edge in
    front). It must be an explicit opt-in, never the default fallback."""
    import dataclasses
    import config
    from api.demo_router import trusted_client_ip

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, demo_client_ip_header=""))
    assert trusted_client_ip(_Req({"cf-connecting-ip": "1.1.1.1"}, peer="127.0.0.1")) == "127.0.0.1"
    assert trusted_client_ip(_Req({}, peer="testclient")) is None     # still validated
    assert trusted_client_ip(_Req({}, peer=None)) is None


def test_router_rate_limit_cannot_be_evaded_by_forged_forwarding_headers(demo_client):
    """Same real client (X-Real-IP constant, as the edge writes it) rotating forged
    CF-Connecting-IP / X-Forwarded-For values on every call still hits the limit."""
    client, c = demo_client
    for i in range(2):
        assert client.post("/v2/demo/sessions",
                           headers={"X-Real-IP": "203.0.113.9",
                                    "CF-Connecting-IP": f"10.0.0.{i}",
                                    "X-Forwarded-For": f"10.0.0.{i}, 10.1.1.{i}"}).status_code == 201
    r = client.post("/v2/demo/sessions", headers={"X-Real-IP": "203.0.113.9",
                                                 "CF-Connecting-IP": "10.0.0.99",
                                                 "X-Forwarded-For": "10.0.0.99"})
    assert r.status_code == 429 and c.demo.created == 2


def test_router_fails_closed_without_trusted_client_identity(demo_client, caplog):
    """No X-Real-IP (request did not cross the public edge, or the edge contract
    changed) or a malformed one: 503, nothing minted, and the address is never
    logged. Forged caller headers do not substitute for the missing identity."""
    import logging
    client, c = demo_client
    with caplog.at_level(logging.WARNING, logger="paax.demo"):
        for h in ({}, {"CF-Connecting-IP": "198.51.100.7"}, {"X-Forwarded-For": "198.51.100.7"},
                  {"X-Real-IP": "198.51.100.7, 10.0.0.1"}, {"X-Real-IP": "garbage"},
                  {"X-Real-IP": "unknown"}):
            r = client.post("/v2/demo/sessions", headers=h)
            assert r.status_code == 503, h
            assert r.json()["error"]["code"] == "DEMO_UNAVAILABLE"
            assert r.headers["Retry-After"] == "60"
    assert c.demo.created == 0
    assert "198.51.100.7" not in caplog.text and "garbage" not in caplog.text
    assert "client identity untrusted" in caplog.text
    # /me and /end are token-gated, not identity-gated
    assert client.get("/v2/demo/sessions/me", headers={"Authorization": "Bearer good"}).status_code == 200


def test_router_global_limit_still_applies_across_distinct_trusted_ips(demo_client, monkeypatch):
    import dataclasses
    import config
    client, c = demo_client
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, demo_enabled=True, demo_rate_limit_per_ip=100, demo_rate_limit_global=3))
    for i in range(3):
        assert client.post("/v2/demo/sessions", headers={"X-Real-IP": f"203.0.113.{i}"}).status_code == 201
    r = client.post("/v2/demo/sessions", headers={"X-Real-IP": "203.0.113.200"})
    assert r.status_code == 429 and c.demo.created == 3


def test_router_disabled_wins_over_identity_and_limiter(demo_client, monkeypatch):
    """Kill switch is checked first: with DEMO_ENABLED=false even a perfectly
    trusted request gets DEMO_DISABLED (what production answers today)."""
    import dataclasses
    import config
    client, c = demo_client
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, demo_enabled=False))
    for h in ({"X-Real-IP": "203.0.113.9"}, {}):
        r = client.post("/v2/demo/sessions", headers=h)
        assert r.status_code == 503 and r.json()["error"]["code"] == "DEMO_DISABLED"
    assert c.demo.created == 0


def test_client_ip_header_defaults_to_x_real_ip(monkeypatch):
    import importlib
    import config
    monkeypatch.delenv("DEMO_CLIENT_IP_HEADER", raising=False)
    assert importlib.reload(config).Settings().demo_client_ip_header == "x-real-ip"
    monkeypatch.setenv("DEMO_CLIENT_IP_HEADER", "X-Real-IP")
    assert importlib.reload(config).Settings().demo_client_ip_header == "x-real-ip"
    monkeypatch.delenv("DEMO_CLIENT_IP_HEADER", raising=False)
    importlib.reload(config)


# ── fail-closed rate limiting ────────────────────────────────────────────────

class _BrokenRedis:
    async def incr(self, k):
        raise ConnectionError("redis down")


def test_create_fails_closed_when_rate_limit_cannot_be_enforced(demo_client):
    """Minting identities must never run unlimited: with Redis absent or failing the
    create endpoint answers 503 DEMO_UNAVAILABLE instead of degrading open."""
    client, c = demo_client
    for limiter in (None, RateLimiter(None), RateLimiter(_BrokenRedis())):
        c.rate_limiter = limiter
        r = client.post("/v2/demo/sessions")
        assert r.status_code == 503, limiter
        assert r.json()["error"]["code"] == "DEMO_UNAVAILABLE"
        assert r.headers["Retry-After"] == "60"
    assert c.demo.created == 0
    # status/end are token-gated, not limiter-gated: still reachable.
    assert client.get("/v2/demo/sessions/me", headers={"Authorization": "Bearer good"}).status_code == 200


def test_create_degrades_open_only_when_explicitly_configured(demo_client, monkeypatch):
    client, c = demo_client
    import dataclasses
    import config
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, demo_require_rate_limiter=False))
    c.rate_limiter = RateLimiter(None)
    assert client.post("/v2/demo/sessions", headers={"X-Real-IP": "203.0.113.9"}).status_code == 201
    # the identity requirement is NOT relaxed by DEMO_REQUIRE_RATE_LIMITER=false
    assert client.post("/v2/demo/sessions").status_code == 503


def test_require_rate_limiter_defaults_on(monkeypatch):
    import importlib
    import config
    monkeypatch.delenv("DEMO_REQUIRE_RATE_LIMITER", raising=False)
    assert importlib.reload(config).Settings().demo_require_rate_limiter is True
