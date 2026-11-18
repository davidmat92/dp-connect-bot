"""Tests: v3-Kurz-Token (/chat/init) + exakte CORS-Pruefung.

Laufen ohne Netz und ohne echte DB: Session-Speicher, whois und track_event
werden ersetzt. Start: python -m pytest tests/
"""

import hashlib
import hmac

import pytest
from flask import Flask

from dp_connect_bot import app as app_module
from dp_connect_bot.routes import webchat
from dp_connect_bot.services import history, woocommerce

SECRET = "00" * 32
TS = 1795000000
# Testvektor aus der Spezifikation (Shop-Neubau, 18.11.2026)
VECTOR = "v3.3.1795000000.ef3ddde654701cef9a5657224441fa961696e6f9430a1a6a7fbf14706923b72b"


def make_token(uid, ts, secret=SECRET):
    sig = hmac.new(secret.encode(), f"v3|{uid}|{ts}".encode(), hashlib.sha256).hexdigest()
    return f"v3.{uid}.{ts}.{sig}"


# --- _validate_v3_auth -------------------------------------------------------

def test_testvektor_gueltig():
    assert make_token(3, TS) == VECTOR
    assert webchat._validate_v3_auth(3, VECTOR, secret=SECRET, now=TS)
    assert webchat._validate_v3_auth("3", VECTOR, secret=SECRET, now=TS)


@pytest.mark.parametrize("now", [TS + 300, TS - 300])
def test_grenze_5_minuten_gueltig(now):
    assert webchat._validate_v3_auth(3, VECTOR, secret=SECRET, now=now)


def test_abgelaufen():
    assert not webchat._validate_v3_auth(3, VECTOR, secret=SECRET, now=TS + 301)


def test_zukunft():
    assert not webchat._validate_v3_auth(3, VECTOR, secret=SECRET, now=TS - 301)


def test_falsche_uid():
    assert not webchat._validate_v3_auth(4, VECTOR, secret=SECRET, now=TS)


def test_falsche_signatur():
    bad = VECTOR[:-1] + ("0" if VECTOR[-1] != "0" else "1")
    assert not webchat._validate_v3_auth(3, bad, secret=SECRET, now=TS)
    assert not webchat._validate_v3_auth(3, make_token(3, TS, secret="11" * 32), secret=SECRET, now=TS)


@pytest.mark.parametrize("auth", [
    None, "", "v3", "v3.3.1795000000", "v2.3.1795000000." + "0" * 64,
    VECTOR + ".x", "v3.03.1795000000." + VECTOR.split(".")[3],
    "v3.-3.1795000000." + "0" * 64, "v3.0.1795000000." + "0" * 64,
    "v3.3.abc." + "0" * 64, "v3.3.1795000000.zz", 12345,
])
def test_kaputtes_format(auth):
    assert not webchat._validate_v3_auth(3, auth, secret=SECRET, now=TS)


def test_leeres_secret_nie_gueltig():
    assert not webchat._validate_v3_auth(3, make_token(3, TS, secret=""), secret="", now=TS)
    assert not webchat._validate_v3_auth(3, VECTOR, secret="", now=TS)


# --- /chat/init mit v3 ------------------------------------------------------

class FakeSessions:
    def __init__(self):
        self.store = {}

    def get(self, chat_id, archive_callback=None):
        return self.store.setdefault(chat_id, {})

    def save(self, chat_id, session):
        self.store[chat_id] = session


@pytest.fixture
def client(monkeypatch):
    sessions = FakeSessions()
    whois_calls = []

    def fake_whois(uid):
        whois_calls.append(uid)
        return {"name": "Test Kunde", "email": "kunde@example.test", "firma": "Testfirma"} if uid == 3 else None

    monkeypatch.setattr(webchat, "session_manager", sessions)
    monkeypatch.setattr(webchat, "V3_BOT_SECRET", SECRET)
    monkeypatch.setattr(webchat.time, "time", lambda: TS)
    monkeypatch.setattr(woocommerce, "fetch_whois", fake_whois)
    monkeypatch.setattr(history, "track_event", lambda *a, **k: None)
    webchat._init_hits.clear()

    app = Flask(__name__)
    app_module.register_cors(app)
    app.register_blueprint(webchat.webchat_bp)
    c = app.test_client()
    c.sessions = sessions
    c.whois_calls = whois_calls
    return c


def init(tc, **body):
    body.setdefault("visitor_id", "test-visitor")
    r = tc.post("/chat/init", json=body)
    assert r.status_code == 200
    chat_id = r.get_json()["chat_id"]
    return r.get_json(), tc.sessions.store[chat_id]


def test_init_v3_gueltig_holt_whois(client):
    data, s = init(client, client="v3", wp_user_id=3, v3_auth=VECTOR,
                   wp_email="gefaelscht@evil.test", wp_display_name="Fake")
    assert s["is_guest"] is False
    assert s["client"] == "v3"
    assert s["verified"] == {"customer_id": 3, "name": "Test Kunde",
                             "firma": "Testfirma", "email": "kunde@example.test"}
    assert s["user_info"]["wp_email"] == "kunde@example.test"  # nie aus dem Request
    assert client.whois_calls == [3]
    assert "Test Kunde" in data["message"]


def test_init_v3_whois_fehlt_bleibt_verifiziert_ohne_name(client, monkeypatch):
    monkeypatch.setattr(woocommerce, "fetch_whois", lambda uid: None)
    _, s = init(client, client="v3", wp_user_id=3, v3_auth=VECTOR)
    assert s["is_guest"] is False
    assert s["verified"]["customer_id"] == 3
    assert s["verified"]["email"] == ""


def test_init_v3_falsche_uid_ist_gast(client):
    _, s = init(client, client="v3", wp_user_id=4, v3_auth=VECTOR)
    assert s["is_guest"] is True
    assert "verified" not in s
    assert client.whois_calls == []


def test_init_v3_ohne_token_ist_gast_auch_mit_wp_auth(client):
    _, s = init(client, client="v3", wp_user_id=3, wp_email="a@b.test", wp_auth="123.abc")
    assert s["is_guest"] is True
    assert "verified" not in s


def test_init_v3_ungueltig_verwirft_alten_status(client):
    init(client, client="v3", wp_user_id=3, v3_auth=VECTOR)
    _, s = init(client, client="v3", wp_user_id=3, v3_auth=make_token(3, TS - 400))
    assert s["is_guest"] is True
    assert "verified" not in s
    assert "wp_email" not in s.get("user_info", {})


def test_init_v3_leeres_secret_ist_gast(client, monkeypatch):
    monkeypatch.setattr(webchat, "V3_BOT_SECRET", "")
    _, s = init(client, client="v3", wp_user_id=3, v3_auth=VECTOR)
    assert s["is_guest"] is True


# --- CORS ---------------------------------------------------------------------

@pytest.mark.parametrize("origin", [
    "https://dpconnect.de", "https://www.dpconnect.de", "https://v3.dpconnect.de",
    "http://localhost", "http://localhost:3000",
])
def test_cors_erlaubt(client, origin):
    r = client.open("/chat/init", method="OPTIONS", headers={"Origin": origin})
    assert r.headers.get("Access-Control-Allow-Origin") == origin


@pytest.mark.parametrize("origin", [
    "https://dpconnect.de.fremd.example", "https://dpconnect.de:8443", "http://dpconnect.de",
    "https://v3.dpconnect.de.evil.test", "http://localhost.evil.test", "http://localhost:3000@evil.test",
    "https://localhost:3000", "null", "",
])
def test_cors_abgelehnt(client, origin):
    r = client.open("/chat/init", method="OPTIONS", headers={"Origin": origin})
    assert "Access-Control-Allow-Origin" not in r.headers


# --- alter wp_auth-Weg bleibt unveraendert -------------------------------------

def test_alter_wp_auth_weg_unveraendert(client, monkeypatch):
    from dp_connect_bot.services import bot_config
    monkeypatch.setattr(webchat, "WP_BOT_SECRET", "wp-test-secret")
    monkeypatch.setattr(bot_config, "load_bot_config", lambda: {"webchat_require_signed_auth": True})
    sig = hmac.new(b"wp-test-secret", f"7|a@b.test|{TS}".encode(), hashlib.sha256).hexdigest()
    _, s = init(client, wp_user_id=7, wp_email="a@b.test", wp_display_name="Alt", wp_auth=f"{TS}.{sig}")
    assert s["is_guest"] is False
    assert s["verified"]["email"] == "a@b.test"
    assert "client" not in s
    assert client.whois_calls == []


# --- fetch_whois (HTTP gemockt) -----------------------------------------------

class FakeResp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


def test_fetch_whois_ok_und_header(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return FakeResp(200, {"uid": 3, "name": "N", "email": "e@x.test", "firma": "F"})

    monkeypatch.setattr(woocommerce, "WP_BOT_SECRET", "s")
    monkeypatch.setattr(woocommerce.requests, "post", fake_post)
    assert woocommerce.fetch_whois(3) == {"name": "N", "email": "e@x.test", "firma": "F"}
    assert seen["url"].endswith("/wp-json/dp/v1/bot/whois")
    assert seen["json"] == {"uid": 3}
    assert seen["headers"]["X-Bot-Secret"] == "s"


@pytest.mark.parametrize("resp", [FakeResp(404), FakeResp(401), FakeResp(200, {"uid": 9, "email": "x"})])
def test_fetch_whois_fehler_none(monkeypatch, resp):
    monkeypatch.setattr(woocommerce, "WP_BOT_SECRET", "s")
    monkeypatch.setattr(woocommerce.requests, "post", lambda *a, **k: resp)
    assert woocommerce.fetch_whois(3) is None


def test_fetch_whois_ohne_secret_kein_call(monkeypatch):
    monkeypatch.setattr(woocommerce, "WP_BOT_SECRET", "")
    monkeypatch.setattr(woocommerce.requests, "post", lambda *a, **k: pytest.fail("kein Call erwartet"))
    assert woocommerce.fetch_whois(3) is None


# --- Fix alter Weg: ungueltige Signatur verwirft frueheren Status --------------

def _wp_sig(uid, email, ts, secret="wp-test-secret"):
    return f"{ts}." + hmac.new(secret.encode(), f"{uid}|{email}|{ts}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def wp_enforce(monkeypatch):
    from dp_connect_bot.services import bot_config
    monkeypatch.setattr(webchat, "WP_BOT_SECRET", "wp-test-secret")
    monkeypatch.setattr(bot_config, "load_bot_config", lambda: {"webchat_require_signed_auth": True})


def test_alter_weg_ungueltige_signatur_nicht_mehr_verified(client, wp_enforce):
    init(client, wp_user_id=7, wp_email="a@b.test", wp_auth=_wp_sig(7, "a@b.test", TS))
    _, s = init(client, wp_user_id=7, wp_email="a@b.test", wp_auth=f"{TS}.{'0' * 64}")
    assert s["is_guest"] is True
    assert "verified" not in s
    assert "wp_email" not in s.get("user_info", {})


def test_alter_weg_logout_nicht_mehr_verified(client, wp_enforce):
    init(client, wp_user_id=7, wp_email="a@b.test", wp_auth=_wp_sig(7, "a@b.test", TS))
    _, s = init(client)  # gleicher Browser, ausgeloggt
    assert s["is_guest"] is True
    assert "verified" not in s
