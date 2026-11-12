from datetime import timedelta

import bcrypt
from sqlalchemy import select

from app.models import AuthToken, AuthTokenType, User, utcnow
from app.security import hash_token

SIGNUP_MESSAGE = "If this email can be used, we have sent a verification link to it. Check your inbox."


def signup(client, **overrides):
    body = {"name": "New Person", "email": "new@example.test", "password": "Secret123"} | overrides
    return client.post("/api/auth/signup", json=body)


class TestSignup:
    def test_rejects_invalid_input_with_field_errors(self, client, db):
        res = signup(client, name="A", email="nope", password="short")

        assert res.status_code == 400
        error = res.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert {d["field"] for d in error["details"]} == {"name", "email", "password"}
        assert {d["message"] for d in error["details"]} >= {
            "Name must be at least 2 characters.",
            "Enter a valid email address.",
            "Password must be at least 8 characters.",
        }
        assert db.scalar(select(User)) is None

    def test_reports_missing_fields(self, client):
        res = client.post("/api/auth/signup", json={})

        assert res.status_code == 400
        assert {d["field"] for d in res.json()["error"]["details"]} == {"name", "email", "password"}

    def test_creates_unverified_employee_with_hashed_password(self, client, db, mailer):
        res = signup(client, name="  New Person ", email="  New@Example.TEST ")

        assert res.status_code == 202
        assert res.json() == {"message": SIGNUP_MESSAGE}
        user = db.scalar(select(User))
        assert user.name == "New Person"
        assert user.email == "new@example.test"
        assert user.role == "employee"
        assert user.email_verified_at is None
        assert user.password_hash != "Secret123"
        assert bcrypt.checkpw(b"Secret123", user.password_hash.encode())

    def test_emails_a_verification_link_and_stores_only_its_hash(self, client, db, mailer):
        signup(client)

        assert [m.to for m in mailer.sent] == ["new@example.test"]
        token = mailer.token("verify-email")
        row = db.scalar(select(AuthToken))
        assert row.type == AuthTokenType.verify_email
        assert row.token_hash == hash_token(token)
        assert row.token_hash != token

    def test_same_answer_for_a_verified_email_without_creating_or_emailing(
        self, client, db, mailer, make_user
    ):
        make_user(email="taken@example.test")

        res = signup(client, email="taken@example.test")

        assert res.status_code == 202
        assert res.json() == {"message": SIGNUP_MESSAGE}
        assert len(db.scalars(select(User)).all()) == 1
        assert mailer.sent == []

    def test_resends_the_link_for_an_unverified_account_instead_of_duplicating(
        self, client, db, mailer, make_user
    ):
        make_user(email="pending@example.test", email_verified_at=None)

        res = signup(client, email="pending@example.test")

        assert res.status_code == 202
        assert len(db.scalars(select(User)).all()) == 1
        assert len(mailer.sent) == 1

    def test_a_new_link_replaces_older_unused_links(self, client, db, mailer, make_user):
        make_user(email_verified_at=None)
        client.post("/api/auth/resend-verification", json={"email": "eli@example.test"})
        client.post("/api/auth/resend-verification", json={"email": "eli@example.test"})

        rows = db.scalars(select(AuthToken)).all()
        assert len(rows) == 1
        assert rows[0].token_hash == hash_token(mailer.token("verify-email"))


class TestVerifyEmail:
    def test_verifies_the_email(self, client, db, mailer):
        signup(client)

        res = client.post("/api/auth/verify-email", json={"token": mailer.token("verify-email")})

        assert res.status_code == 200
        db.expire_all()
        assert db.scalar(select(User)).email_verified_at is not None

    def test_link_works_only_once(self, client, mailer):
        signup(client)
        token = mailer.token("verify-email")
        client.post("/api/auth/verify-email", json={"token": token})

        res = client.post("/api/auth/verify-email", json={"token": token})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"

    def test_rejects_an_expired_link(self, client, db, mailer):
        signup(client)
        db.scalar(select(AuthToken)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

        res = client.post("/api/auth/verify-email", json={"token": mailer.token("verify-email")})

        assert res.status_code == 400

    def test_rejects_an_unknown_link(self, client):
        res = client.post("/api/auth/verify-email", json={"token": "unknown"})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"

    def test_rejects_a_reset_link_used_as_a_verification_link(self, client, mailer, employee):
        client.post("/api/auth/forgot-password", json={"email": employee.email})

        res = client.post("/api/auth/verify-email", json={"token": mailer.token("reset-password")})

        assert res.status_code == 400

    def test_rejects_the_link_of_a_deactivated_user(self, client, db, mailer):
        signup(client)
        db.scalar(select(User)).is_active = False
        db.commit()

        res = client.post("/api/auth/verify-email", json={"token": mailer.token("verify-email")})

        assert res.status_code == 400

    def test_rejects_an_empty_token(self, client):
        res = client.post("/api/auth/verify-email", json={"token": "   "})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


class TestResendVerification:
    def test_sends_a_new_link_to_an_unverified_account(self, client, mailer, make_user):
        make_user(email_verified_at=None)

        res = client.post("/api/auth/resend-verification", json={"email": "ELI@example.test"})

        assert res.status_code == 202
        assert len(mailer.sent) == 1

    def test_same_answer_for_unknown_and_verified_emails_and_sends_nothing(self, client, mailer, make_user):
        make_user()

        unknown = client.post("/api/auth/resend-verification", json={"email": "nobody@example.test"})
        verified = client.post("/api/auth/resend-verification", json={"email": "eli@example.test"})

        assert unknown.status_code == verified.status_code == 202
        assert unknown.json() == verified.json()
        assert mailer.sent == []
