from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.models import Role
from app.security import create_access_token, decode_access_token, hash_token, random_token, verify_password
from tests.conftest import PASSWORD, PASSWORD_HASH, login


class TestSecurityHelpers:
    def test_access_token_round_trips_user_and_role(self):
        payload = decode_access_token(create_access_token(42, Role.manager))

        assert payload["sub"] == "42"
        assert payload["role"] == "manager"

    def test_rejects_a_token_whose_payload_was_changed(self):
        import base64
        import json

        header, _, signature = create_access_token(42, Role.employee).split(".")
        forged = base64.urlsafe_b64encode(json.dumps({"sub": "42", "role": "super_admin"}).encode()).decode()

        assert decode_access_token(f"{header}.{forged.rstrip('=')}.{signature}") is None

    def test_random_tokens_are_unique_and_url_safe(self):
        tokens = {random_token() for _ in range(50)}

        assert len(tokens) == 50
        assert all(len(t) == 43 and t.replace("-", "").replace("_", "").isalnum() for t in tokens)

    def test_token_hash_is_a_stable_64_character_hex_string(self):
        assert hash_token("abc") == hash_token("abc")
        assert len(hash_token("abc")) == 64
        assert hash_token("abc") != hash_token("abd")

    def test_verify_password(self):
        assert verify_password(PASSWORD, PASSWORD_HASH)
        assert not verify_password("Wrong1234", PASSWORD_HASH)
        assert not verify_password(PASSWORD, None)


class TestApp:
    def test_health_check(self, client):
        assert client.get("/api/health").json() == {"status": "ok"}

    def test_unknown_routes_use_the_standard_error_shape(self, client):
        res = client.get("/api/does-not-exist")

        assert res.status_code == 404
        assert res.json() == {"error": {"code": "NOT_FOUND", "message": "Not found."}}

    def test_malformed_json_gives_400(self, client):
        res = client.post(
            "/api/auth/login", content="{bad json", headers={"Content-Type": "application/json"}
        )

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "INVALID_JSON"

    def test_sets_security_headers(self, client):
        headers = client.get("/api/health").headers

        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"

    def test_allows_the_frontend_origin_with_credentials(self, client):
        res = client.options(
            "/api/auth/login",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
        )

        assert res.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert res.headers["access-control-allow-credentials"] == "true"


class TestDocs:
    def test_docs_are_off_in_production(self, monkeypatch):
        monkeypatch.setenv("NODE_ENV", "production")
        get_settings.cache_clear()
        try:
            with TestClient(create_app()) as prod:
                assert prod.get("/api/docs").status_code == 404
                assert prod.get("/api/docs/openapi.json").status_code == 404
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()


class TestRateLimits:
    def test_blocks_an_ip_after_too_many_failed_auth_requests(self, client, employee):
        codes = [login(client, password="Wrong1234").status_code for _ in range(22)]

        assert codes[:20] == [401] * 20
        assert codes[20:] == [429, 429]

    def test_429_says_when_to_retry(self, client, employee):
        for _ in range(20):
            login(client, password="Wrong1234")

        res = login(client, password="Wrong1234")

        assert res.status_code == 429
        assert res.json()["error"]["code"] == "TOO_MANY_REQUESTS"
        assert int(res.headers["retry-after"]) > 0

    def test_successful_logins_do_not_count(self, client, employee):
        assert all(login(client).status_code == 200 for _ in range(25))

    def test_a_fake_forwarded_for_header_does_not_bypass_the_ip_limit(self, client):
        # A different email each time, so only the per-IP limit can block these.
        codes = [
            client.post(
                "/api/auth/login",
                json={"email": f"user{i}@example.test", "password": "Wrong1234"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            ).status_code
            for i in range(22)
        ]

        assert codes[-1] == 429

    def test_blocks_an_email_after_too_many_failed_logins_from_different_ips(self, client, employee):
        from app.rate_limit import limiter

        for _ in range(20):
            login(client, password="Wrong1234")
        limiter.reset_prefix("auth:")

        res = login(client)

        assert res.status_code == 429

    def test_health_check_is_not_limited(self, client):
        for _ in range(25):
            login(client, password="Wrong1234")

        assert client.get("/api/health").status_code == 200
