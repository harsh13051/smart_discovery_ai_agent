from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import PASSWORD, new_client, register
from discovery.db import AuthSession, User


def test_register_success_sets_session_and_hashes_password(client, app):
    user = register(client)
    assert user["email"] == "asha@example.com" and "password" not in user and "password_hash" not in user
    assert "sid" in client.cookies
    with app.state.sf() as db:
        row = db.scalar(select(User).where(User.email == "asha@example.com"))
        assert row.password_hash.startswith("scrypt$") and PASSWORD not in row.password_hash


def test_register_validation_errors(client):
    r = client.post("/api/auth/register", json={"name": "", "email": "nope", "password": "123"})
    assert r.status_code == 422
    fields = r.json()["fields"]
    assert set(fields) == {"name", "email", "password"}


def test_register_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={"name": "A", "email": "a@b.co", "password": "abcdefghij"})
    assert r.status_code == 422 and "password" in r.json()["fields"]


def test_duplicate_registration_is_conflict_case_insensitive(client, app):
    register(client)
    other = new_client(app)
    r = other.post("/api/auth/register", json={"name": "X", "email": "ASHA@example.com", "password": PASSWORD})
    assert r.status_code == 409


def test_login_success_and_invalid_login(client, app):
    register(client)
    fresh = new_client(app)
    bad = fresh.post("/api/auth/login", json={"email": "asha@example.com", "password": "wrong-pass1"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Invalid email or password."
    unknown = fresh.post("/api/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert unknown.status_code == 401 and unknown.json()["detail"] == bad.json()["detail"]  # no account enumeration
    ok = fresh.post("/api/auth/login", json={"email": "asha@example.com", "password": PASSWORD})
    assert ok.status_code == 200 and fresh.get("/api/auth/me").json()["user"]["name"] == "Asha"


def test_session_persists_across_requests_and_clients_sharing_cookie(client, app):
    register(client)
    assert client.get("/api/auth/me").status_code == 200
    restored = new_client(app)  # a "refreshed browser" that still holds the cookie
    restored.cookies.set("sid", client.cookies["sid"])
    assert restored.get("/api/auth/me").status_code == 200


def test_logout_invalidates_session_server_side(client, app):
    register(client)
    old = client.cookies["sid"]
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    replay = new_client(app)
    replay.cookies.set("sid", old)  # reuse of the captured token must fail
    assert replay.get("/api/auth/me").status_code == 401
    with app.state.sf() as db:
        assert db.scalar(select(AuthSession)) is None


def test_protected_routes_require_authentication(client):
    for method, path in [("get", "/api/conversations"), ("get", "/api/favourites"), ("get", "/api/restaurants/search?location=x"),
                         ("get", "/api/restaurant?id=osm:node/1"), ("put", "/api/profile")]:
        r = getattr(client, method)(path) if method == "get" else client.put(path, json={})
        assert r.status_code == 401, path


def test_expired_session_rejected(client, app):
    from datetime import timedelta
    from discovery.db import utcnow
    register(client)
    with app.state.sf() as db:
        s = db.scalar(select(AuthSession))
        s.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_csrf_required_for_state_changing_requests(app):
    c = TestClient(app)  # no CSRF cookie/header
    r = c.post("/api/auth/register", json={"name": "A", "email": "a@b.co", "password": PASSWORD})
    assert r.status_code == 403
    c.get("/api/auth/csrf")
    c.headers["X-CSRF-Token"] = "wrong"
    assert c.post("/api/auth/login", json={"email": "a@b.co", "password": PASSWORD}).status_code == 403


def test_login_rate_limited(client, app):
    register(client)
    other = new_client(app)
    codes = [other.post("/api/auth/login", json={"email": "asha@example.com", "password": "bad-pass-1"}).status_code for _ in range(10)]
    assert 429 in codes and codes[0] == 401


def test_session_cookie_flags(client):
    r = client.post("/api/auth/register", json={"name": "A", "email": "flags@example.com", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_security_headers_present(client):
    r = client.get("/api/status")
    assert r.headers["x-content-type-options"] == "nosniff" and "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_profile_update_and_validation(user_client):
    r = user_client.put("/api/profile", json={"name": "Asha R", "diet": "vegetarian", "default_location": "Baner, Pune", "budget_per_person": 500})
    assert r.status_code == 200 and r.json()["user"]["preferences"]["diet"] == "vegetarian"
    assert user_client.get("/api/auth/me").json()["user"]["name"] == "Asha R"
    assert user_client.put("/api/profile", json={"diet": "carnivore"}).status_code == 422
    assert user_client.put("/api/profile", json={"budget_per_person": -5}).status_code == 422
