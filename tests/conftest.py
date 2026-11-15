import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent


def _test_database_url() -> str:
    """The URL of the MySQL test database, from TEST_DATABASE_URL or from a .env.test file.

    The tests run on MySQL only. SQLite is not supported: it ignores row locks and never raises the
    duplicate-key error, so a green run on it can hide real bugs.
    """
    url = os.environ.get("TEST_DATABASE_URL", "")
    env_file = ROOT / ".env.test"
    if not url and env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "TEST_DATABASE_URL":
                url = value.strip().strip("\"'")
    if not url:
        pytest.exit(
            "TEST_DATABASE_URL is not set. Put it in the environment or in a .env.test file, e.g.\n"
            "  TEST_DATABASE_URL=mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/employee_review_test",
            returncode=2,
        )
    parsed = make_url(url)
    if not parsed.drivername.startswith("mysql"):
        pytest.exit(f"TEST_DATABASE_URL must be a MySQL URL, not '{parsed.drivername}'.", returncode=2)
    # The suite drops and empties tables. Never let it run on a development or production database.
    if not (parsed.database or "").endswith("_test"):
        pytest.exit(f"Refusing to use '{parsed.database}'. A test database name must end in _test.", returncode=2)
    return url


os.environ.update(
    {
        "NODE_ENV": "test",
        "DATABASE_URL": _test_database_url(),
        "JWT_ACCESS_SECRET": "test-secret-that-is-at-least-32-characters-long",
        "APP_URL": "http://localhost:5173",
        "SMTP_HOST": "",
        "AUTH_RATE_LIMIT_MAX": "20",
        "RATE_LIMIT_MAX": "300",
    }
)

import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

import bcrypt
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.mailer import Mail
from app.main import create_app
from app.models import Cycle, CycleStatus, CycleType, Department, Role, User, utcnow
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


def _build_schema() -> None:
    """Build the schema with the real migrations, so the tests see the same indexes as production.

    Base.metadata.create_all would miss the ones that only exist in a migration.
    """
    with engine.begin() as conn:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        for (table,) in conn.exec_driver_sql("SHOW TABLES").fetchall():
            conn.exec_driver_sql(f"DROP TABLE `{table}`")
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")


def _empty_tables() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        for table in Base.metadata.sorted_tables:
            conn.exec_driver_sql(f"TRUNCATE TABLE `{table.name}`")
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    _build_schema()


@pytest.fixture(autouse=True)
def _fresh_database(_schema: None) -> Iterator[None]:
    # The migrations built the tables once. Each test starts from empty ones.
    limiter.reset()
    yield
    _empty_tables()


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


@pytest.fixture
def super_admin(make_user) -> User:
    return make_user(name="Sam Super", email="super@example.test", role=Role.super_admin)


@pytest.fixture
def admin(make_user) -> User:
    return make_user(name="Ada Admin", email="admin@example.test", role=Role.admin)


@pytest.fixture
def make_department(db: Session):
    def factory(name: str = "Engineering", parent: Department | None = None, is_active: bool = True) -> Department:
        department = Department(name=name, parent_id=parent.id if parent else None, is_active=is_active)
        db.add(department)
        db.commit()
        return department

    return factory


@pytest.fixture
def make_cycle(db: Session):
    def factory(**overrides) -> Cycle:
        values = {
            "name": "Annual 2026",
            "type": CycleType.annual,
            "year": 2026,
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 12, 31),
            "status": CycleStatus.draft,
        } | overrides
        cycle = Cycle(**values)
        db.add(cycle)
        db.commit()
        return cycle

    return factory


def error_code(response) -> str:
    """The error code of a failed response."""
    return response.json()["error"]["code"]


__all__ = ["PASSWORD", "FakeMailer", "bearer", "error_code", "login", "utcnow"]
