from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.dependencies import require_auth, require_roles
from app.errors import register_error_handlers
from app.models import Role
from tests.conftest import bearer

SECRET = "test-secret-that-is-at-least-32-characters-long"


class TestMe:
    def test_returns_the_profile_of_the_logged_in_user(self, client, employee):
        res = client.get("/api/users/me", headers=bearer(employee))

        assert res.status_code == 200
        assert res.json() == {
            "id": employee.id,
            "name": "Eli Employee",
            "email": "eli@example.test",
            "role": "employee",
            "department": {"id": employee.department_id, "name": "Engineering"},
            "manager": {"id": employee.manager_id, "name": "Mona Manager"},
            "emailVerified": True,
            "mustChangePassword": False,
        }

    def test_never_returns_the_password_hash(self, client, employee):
        res = client.get("/api/users/me", headers=bearer(employee))

        assert "passwordHash" not in res.json()
        assert employee.password_hash not in res.text

    def test_shows_an_empty_department_and_manager_as_null(self, client, make_user):
        user = make_user()

        body = client.get("/api/users/me", headers=bearer(user)).json()

        assert body["department"] is None
        assert body["manager"] is None

    def test_is_allowed_while_a_password_change_is_pending(self, client, make_user):
        user = make_user(must_change_password=True)

        res = client.get("/api/users/me", headers=bearer(user))

        assert res.status_code == 200
        assert res.json()["mustChangePassword"] is True

    @pytest.mark.parametrize(
        "header",
        [
            None,
            "Bearer not-a-jwt",
            "Basic abc",
            "Bearer " + jwt.encode({"sub": "1", "role": "employee", "exp": 9999999999}, "another-secret" * 3),
            "Bearer "
            + jwt.encode({"sub": "1", "role": "employee", "exp": datetime.now(UTC) - timedelta(1)}, SECRET),
            "Bearer " + jwt.encode({"sub": "1", "role": "admin"}, SECRET),
        ],
        ids=["no token", "malformed", "not bearer", "wrong secret", "expired", "no expiry"],
    )
    def test_rejects_bad_tokens(self, client, employee, header):
        headers = {"Authorization": header} if header else {}

        res = client.get("/api/users/me", headers=headers)

        assert res.status_code == 401
        assert res.json() == {"error": {"code": "UNAUTHORIZED", "message": "Authentication required."}}

    def test_rejects_an_unsigned_token(self, client, employee):
        header, payload, _ = bearer(employee)["Authorization"].removeprefix("Bearer ").split(".")
        unsigned = f"{header}.{payload}."

        assert client.get("/api/users/me", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401

    def test_rejects_a_valid_token_of_a_user_who_was_deactivated_since(self, client, db, employee):
        headers = bearer(employee)
        employee.is_active = False
        db.merge(employee)
        db.commit()

        assert client.get("/api/users/me", headers=headers).status_code == 401


@pytest.fixture
def guarded_client():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/protected", dependencies=[Depends(require_auth())])
    def protected() -> dict:
        return {"ok": True}

    @app.get("/admins", dependencies=[Depends(require_roles(Role.admin, Role.super_admin))])
    def admins() -> dict:
        return {"ok": True}

    with TestClient(app) as c:
        yield c


class TestAuthDependencies:
    def test_blocks_other_endpoints_until_a_temporary_password_is_changed(self, guarded_client, make_user):
        user = make_user(must_change_password=True)

        res = guarded_client.get("/protected", headers=bearer(user))

        assert res.status_code == 403
        assert res.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    def test_uses_the_role_from_the_database_not_the_token(self, guarded_client, make_user):
        user = make_user(role=Role.employee)

        res = guarded_client.get("/admins", headers=bearer(user, role=Role.admin))

        assert res.status_code == 403
        assert res.json()["error"]["code"] == "FORBIDDEN"

    def test_lets_an_allowed_role_through(self, guarded_client, make_user):
        admin = make_user(role=Role.admin)

        assert guarded_client.get("/admins", headers=bearer(admin)).status_code == 200
