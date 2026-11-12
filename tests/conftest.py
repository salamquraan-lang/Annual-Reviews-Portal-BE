import os

os.environ.update(
    {
        "NODE_ENV": "test",
        "DATABASE_URL": "sqlite://",
        "JWT_ACCESS_SECRET": "test-secret-that-is-at-least-32-characters-long",
        "APP_URL": "http://localhost:5173",
        "SMTP_HOST": "",
        "AUTH_RATE_LIMIT_MAX": "20",
        "RATE_LIMIT_MAX": "300",
    }
)

import re
from collections.abc import Iterator
from datetime import datetime

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.mailer import Mail
from app.main import create_app
from app.models import Department, Role, User, utcnow
from app.rate_limit import limiter
from app.security import create_access_token

PASSWORD = "Password123"
PASSWORD_HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(4)).decode()


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> None:
        self.sent.append(mail)

    def token(self, path: str, index: int = -1) -> str:
        match = re.search(rf"/{path}\?token=(\S+)", self.sent[index].text)
        assert match, f"no {path} link in mail"
        return match.group(1)


@pytest.fixture(autouse=True)
def _fresh_database() -> Iterator[None]:
    Base.metadata.create_all(engine)
    limiter.reset()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def client(mailer: FakeMailer) -> Iterator[TestClient]:
    with TestClient(create_app(mailer=mailer)) as c:
        yield c


@pytest.fixture
def make_user(db: Session):
    def factory(**overrides) -> User:
        values = {
            "name": "Eli Employee",
            "email": "eli@example.test",
            "password_hash": PASSWORD_HASH,
            "role": Role.employee,
            "is_active": True,
            "email_verified_at": datetime(2026, 1, 1),
            "must_change_password": False,
        } | overrides
        user = User(**values)
        db.add(user)
        db.commit()
        return user

    return factory


@pytest.fixture
def employee(db: Session, make_user) -> User:
    department = Department(name="Engineering")
    db.add(department)
    db.commit()
    manager = make_user(name="Mona Manager", email="mona@example.test", role=Role.manager)
    return make_user(department_id=department.id, manager_id=manager.id)


def bearer(user: User, role: Role | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, role or user.role)}"}


def login(client: TestClient, email: str = "eli@example.test", password: str = PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


__all__ = ["PASSWORD", "FakeMailer", "bearer", "login", "utcnow"]
