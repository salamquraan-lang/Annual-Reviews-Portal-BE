from datetime import timedelta

import bcrypt
import pytest
from sqlalchemy import select

from app.models import AuthToken, AuthTokenType, RefreshToken, User, utcnow
from app.security import hash_token
from tests.conftest import PASSWORD, bearer, login

RESET_MESSAGE = "If this email is registered, we have sent a link to reset the password."
RESET_URL = "/api/auth/reset-password"
VALIDATE_URL = "/api/auth/reset-password/validate"


def change(client, user, current=PASSWORD, new="NewPass123"):
    return client.patch(
        RESET_URL,
        headers=bearer(user),
        json={"currentPassword": current, "newPassword": new},
    )


def reset(client, token, new="Brandnew123", **kwargs):
    return client.patch(RESET_URL, json={"token": token, "newPassword": new}, **kwargs)


def validate(client, token):
    return client.post(VALIDATE_URL, json={"token": token})


def reset_token(client, mailer, email="eli@example.test"):
    client.post("/api/auth/forgot-password", json={"email": email})
    return mailer.token("reset-password")


class TestResetPasswordWithCurrentPassword:
    def test_requires_a_logged_in_user(self, client):
        res = client.patch(RESET_URL, json={"currentPassword": PASSWORD, "newPassword": "NewPass123"})

        assert res.status_code == 401

    def test_refuses_a_wrong_current_password(self, client, db, employee):
        res = change(client, employee, current="Wrong1234")

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "WRONG_PASSWORD"
        db.refresh(employee)
        assert bcrypt.checkpw(PASSWORD.encode(), employee.password_hash.encode())

    def test_refuses_a_new_password_equal_to_the_current_one(self, client, employee):
        res = change(client, employee, new=PASSWORD)

        assert res.status_code == 400
        assert res.json()["error"]["details"] == [
            {"field": "newPassword", "message": "The new password must be different from the current one."}
        ]

    @pytest.mark.parametrize(
        ("password", "message"),
        [
            ("Short1", "Password must be at least 8 characters."),
            ("onlyletters", "Password must contain a digit."),
            ("12345678", "Password must contain a letter."),
            ("a1" * 37, "Password must be at most 72 characters."),
        ],
    )
    def test_enforces_the_password_policy(self, client, employee, password, message):
        res = change(client, employee, new=password)

        assert res.status_code == 400
        assert res.json()["error"]["details"][0]["message"] == message

    def test_stores_the_new_password_hashed_and_ends_only_other_sessions(self, client, db, employee, mailer):
        login(client)
        from fastapi.testclient import TestClient

        from app.main import create_app

        with TestClient(create_app(mailer=mailer)) as other_device:
            login(other_device)
            res = change(client, employee)
            assert res.status_code == 200
            assert other_device.post("/api/auth/refresh").status_code == 401
        assert client.post("/api/auth/refresh").status_code == 200

        db.expire_all()
        user = db.get(User, employee.id)
        assert bcrypt.checkpw(b"NewPass123", user.password_hash.encode())
        assert login(client).status_code == 401

    def test_clears_the_temporary_password_flag(self, client, db, make_user):
        user = make_user(must_change_password=True)

        assert change(client, user).status_code == 200
        db.refresh(user)
        assert user.must_change_password is False

    def test_refuses_a_deactivated_user(self, client, make_user):
        user = make_user(is_active=False)

        assert change(client, user).status_code == 401


class TestForgotPassword:
    def test_emails_a_reset_link_that_expires_in_30_minutes(self, client, db, mailer, employee):
        res = client.post("/api/auth/forgot-password", json={"email": "eli@example.test"})

        assert res.status_code == 202
        assert res.json() == {"message": RESET_MESSAGE}
        token = mailer.token("reset-password")
        row = db.scalar(select(AuthToken))
        assert row.type == AuthTokenType.reset_password
        assert row.token_hash == hash_token(token)
        minutes = (row.expires_at - utcnow()).total_seconds() / 60
        assert 29 < minutes <= 30

    def test_same_answer_for_an_unknown_email_and_sends_nothing(self, client, mailer):
        res = client.post("/api/auth/forgot-password", json={"email": "nobody@example.test"})

        assert res.status_code == 202
        assert res.json() == {"message": RESET_MESSAGE}
        assert mailer.sent == []

    def test_sends_nothing_to_a_deactivated_user(self, client, mailer, make_user):
        make_user(is_active=False)

        client.post("/api/auth/forgot-password", json={"email": "eli@example.test"})

        assert mailer.sent == []


class TestValidateResetLink:
    def test_a_fresh_link_is_valid(self, client, mailer, employee):
        token = reset_token(client, mailer)

        res = validate(client, token)

        assert res.status_code == 200
        assert res.json() == {"valid": True}

    def test_validating_does_not_use_up_the_link(self, client, mailer, employee):
        token = reset_token(client, mailer)

        validate(client, token)

        assert validate(client, token).json() == {"valid": True}

    def test_a_used_link_is_not_valid(self, client, mailer, employee):
        token = reset_token(client, mailer)
        reset(client, token)

        assert validate(client, token).json() == {"valid": False}

    def test_an_expired_link_is_not_valid(self, client, db, mailer, employee):
        token = reset_token(client, mailer)
        db.scalar(select(AuthToken)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

        assert validate(client, token).json() == {"valid": False}

    def test_a_link_of_a_deactivated_user_is_not_valid(self, client, db, mailer, employee):
        token = reset_token(client, mailer)
        db.get(User, employee.id).is_active = False
        db.commit()

        assert validate(client, token).json() == {"valid": False}

    def test_a_verification_link_is_not_a_valid_reset_link(self, client, mailer, make_user):
        make_user(email_verified_at=None)
        client.post("/api/auth/resend-verification", json={"email": "eli@example.test"})
        token = mailer.token("verify-email")

        assert validate(client, token).json() == {"valid": False}

    def test_an_unknown_token_is_not_valid(self, client):
        assert validate(client, "unknown").json() == {"valid": False}

    @pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": "   "}])
    def test_a_missing_token_is_rejected(self, client, body):
        res = client.post(VALIDATE_URL, json=body)

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_the_token_is_no_longer_accepted_in_the_url(self, client, mailer, employee):
        token = reset_token(client, mailer)

        res = client.get(VALIDATE_URL, params={"token": token})

        assert res.status_code == 405


class TestResetPasswordWithLink:
    def test_sets_the_new_password_and_ends_every_session(self, client, db, mailer, employee):
        login(client)
        token = reset_token(client, mailer)

        res = reset(client, token)

        assert res.status_code == 200
        assert db.scalar(select(RefreshToken)) is None
        assert login(client, password="Brandnew123").status_code == 200
        assert login(client).status_code == 401

    def test_does_not_need_a_logged_in_user_or_the_current_password(self, client, mailer, employee):
        token = reset_token(client, mailer)

        res = reset(client, token, headers={"Authorization": "Bearer not-a-real-token"})

        assert res.status_code == 200

    def test_marks_the_email_verified_since_the_user_opened_a_link_sent_to_it(
        self, client, db, mailer, make_user
    ):
        user = make_user(email_verified_at=None)

        reset(client, reset_token(client, mailer, user.email))

        db.refresh(user)
        assert user.email_verified_at is not None

    def test_the_link_works_only_once(self, client, mailer, employee):
        token = reset_token(client, mailer)
        reset(client, token)

        res = reset(client, token, new="Another123")

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"

    def test_rejects_a_weak_password_without_using_up_the_link(self, client, mailer, employee):
        token = reset_token(client, mailer)

        res = reset(client, token, new="weak")

        assert res.status_code == 400
        assert validate(client, token).json() == {"valid": True}


class TestResetPasswordRequest:
    MESSAGE = "Send exactly one of token or currentPassword."

    def test_refuses_a_token_together_with_the_current_password(self, client, db, mailer, employee):
        token = reset_token(client, mailer)

        res = client.patch(
            RESET_URL,
            headers=bearer(employee),
            json={"token": token, "currentPassword": PASSWORD, "newPassword": "Brandnew123"},
        )

        assert res.status_code == 400
        assert res.json()["error"]["details"][0]["message"] == self.MESSAGE
        assert validate(client, token).json() == {"valid": True}
        db.refresh(employee)
        assert bcrypt.checkpw(PASSWORD.encode(), employee.password_hash.encode())

    def test_refuses_a_new_password_alone(self, client, employee):
        res = client.patch(RESET_URL, headers=bearer(employee), json={"newPassword": "Brandnew123"})

        assert res.status_code == 400
        assert res.json()["error"]["details"][0]["message"] == self.MESSAGE

    def test_requires_a_new_password(self, client, mailer, employee):
        res = client.patch(RESET_URL, json={"token": reset_token(client, mailer)})

        assert res.status_code == 400
        assert res.json()["error"]["details"] == [
            {"field": "newPassword", "message": "This field is required."}
        ]

    def test_post_is_no_longer_accepted(self, client, mailer, employee):
        token = reset_token(client, mailer)

        res = client.post(RESET_URL, json={"token": token, "newPassword": "Brandnew123"})

        assert res.status_code == 405
        assert validate(client, token).json() == {"valid": True}

    def test_the_change_password_endpoint_is_gone(self, client, employee):
        res = client.post(
            "/api/auth/change-password",
            headers=bearer(employee),
            json={"currentPassword": PASSWORD, "newPassword": "NewPass123"},
        )

        assert res.status_code == 404
