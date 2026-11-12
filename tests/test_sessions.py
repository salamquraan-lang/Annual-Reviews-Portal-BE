from datetime import timedelta

from sqlalchemy import select

from app.models import RefreshToken, utcnow
from app.security import decode_access_token, hash_token
from tests.conftest import PASSWORD, bearer, login


class TestLogin:
    def test_returns_an_access_token_and_sets_an_httponly_refresh_cookie(self, client, employee):
        res = login(client)

        assert res.status_code == 200
        body = res.json()
        assert set(body) == {"accessToken", "mustChangePassword"}
        assert body["mustChangePassword"] is False
        payload = decode_access_token(body["accessToken"])
        assert payload["sub"] == str(employee.id)
        assert payload["role"] == "employee"

        cookie = res.headers["set-cookie"]
        assert cookie.startswith("refresh_token=")
        assert "httponly" in cookie.lower()
        assert "samesite=strict" in cookie.lower()
        assert "path=/api/auth" in cookie.lower()

    def test_stores_only_the_hash_of_the_refresh_token(self, client, db, employee):
        res = login(client)

        raw = res.cookies["refresh_token"]
        row = db.scalar(select(RefreshToken))
        assert row.token_hash == hash_token(raw)
        assert row.token_hash != raw
        assert row.expires_at > utcnow()

    def test_ignores_letter_case_and_spaces_in_the_email(self, client, employee):
        assert login(client, email="  ELI@Example.Test ").status_code == 200

    def test_same_error_for_a_wrong_password_and_an_unknown_email(self, client, db, employee):
        wrong_password = login(client, password="Wrong1234")
        unknown_email = login(client, email="who@example.test", password="Wrong1234")

        assert wrong_password.status_code == 401
        assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert unknown_email.status_code == wrong_password.status_code
        assert unknown_email.json() == wrong_password.json()
        assert db.scalar(select(RefreshToken)) is None

    def test_refuses_a_deactivated_user_with_the_same_generic_error(self, client, make_user):
        make_user(is_active=False)

        res = login(client)

        assert res.status_code == 401
        assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_asks_an_unverified_user_to_verify_only_when_the_password_is_right(self, client, make_user):
        make_user(email_verified_at=None)

        right = login(client)
        wrong = login(client, password="Wrong1234")

        assert right.status_code == 403
        assert right.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
        assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_tells_a_user_with_a_temporary_password_to_change_it(self, client, make_user):
        make_user(must_change_password=True)

        res = login(client)

        assert res.status_code == 200
        assert res.json()["mustChangePassword"] is True

    def test_rejects_a_missing_password(self, client):
        res = client.post("/api/auth/login", json={"email": "eli@example.test"})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_rejects_a_password_longer_than_bcrypt_allows(self, client, employee):
        assert login(client, password=PASSWORD + "x" * 80).status_code == 401


class TestRefresh:
    def test_rejects_a_request_without_a_refresh_cookie(self, client):
        assert client.post("/api/auth/refresh").status_code == 401

    def test_replaces_the_refresh_token_and_returns_a_new_access_token(self, client, db, employee):
        old = login(client).cookies["refresh_token"]

        res = client.post("/api/auth/refresh")

        assert res.status_code == 200
        assert decode_access_token(res.json()["accessToken"])["sub"] == str(employee.id)
        new = res.cookies["refresh_token"]
        assert new != old
        assert db.scalars(select(RefreshToken.token_hash)).all() == [hash_token(new)]

    def test_an_old_refresh_token_cannot_be_used_again(self, client, employee):
        old = login(client).cookies["refresh_token"]
        client.post("/api/auth/refresh")
        client.cookies.clear()
        client.cookies.set("refresh_token", old, path="/api/auth")

        assert client.post("/api/auth/refresh").status_code == 401

    def test_rejects_an_unknown_token_and_clears_the_cookie(self, client):
        client.cookies.set("refresh_token", "stolen", path="/api/auth")

        res = client.post("/api/auth/refresh")

        assert res.status_code == 401
        assert 'refresh_token=""' in res.headers["set-cookie"] or "Max-Age=0" in res.headers["set-cookie"]

    def test_rejects_an_expired_refresh_token(self, client, db, employee):
        login(client)
        db.scalar(select(RefreshToken)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

        assert client.post("/api/auth/refresh").status_code == 401
        assert db.scalar(select(RefreshToken)) is None

    def test_rejects_the_refresh_token_of_a_deactivated_user(self, client, db, employee):
        login(client)
        employee.is_active = False
        db.merge(employee)
        db.commit()

        assert client.post("/api/auth/refresh").status_code == 401


class TestLogout:
    def test_deletes_only_this_device_session(self, client, db, employee, mailer):
        login(client)
        from fastapi.testclient import TestClient

        from app.main import create_app

        with TestClient(create_app(mailer=mailer)) as other_device:
            login(other_device)
            res = client.post("/api/auth/logout")
            assert res.status_code == 204
            assert other_device.post("/api/auth/refresh").status_code == 200
        assert client.post("/api/auth/refresh").status_code == 401

    def test_succeeds_without_a_session(self, client):
        assert client.post("/api/auth/logout").status_code == 204


class TestLogoutAll:
    def test_requires_a_logged_in_user(self, client):
        assert client.post("/api/auth/logout-all").status_code == 401

    def test_deletes_every_session_of_the_user(self, client, db, employee, make_user):
        login(client)
        login(client)
        other = make_user(email="other@example.test")
        login(client, email=other.email)

        res = client.post("/api/auth/logout-all", headers=bearer(employee))

        assert res.status_code == 204
        assert db.scalars(select(RefreshToken.user_id)).all() == [other.id]
