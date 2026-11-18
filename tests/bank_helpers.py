"""Fixtures and helpers shared by the question bank and template tests."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models import Cycle, CycleStatus, CycleType, Role, Template, User
from app.services import templates as template_service
from tests.conftest import bearer


@pytest.fixture
def admin(make_user) -> User:
    return make_user(name="Ada Admin", email="admin@example.test", role=Role.admin)


@pytest.fixture
def super_admin(make_user) -> User:
    return make_user(name="Sam Super", email="super@example.test", role=Role.super_admin)


@pytest.fixture
def auth(admin) -> dict[str, str]:
    """Headers of a signed-in admin."""
    return bearer(admin)


@pytest.fixture
def make_cycle(db: Session):
    """Create a cycle with its four empty templates, as creating a cycle does (T9)."""
    count = 0

    def factory(status: CycleStatus = CycleStatus.draft, year: int | None = None, **overrides) -> Cycle:
        nonlocal count
        count += 1
        year = year or 2020 + count
        values = {
            "name": f"Annual {year}",
            "type": CycleType.annual,
            "year": year,
            "start_date": date(year, 1, 1),
            "end_date": date(year, 12, 31),
            # Only one cycle may be open at a time, so "opened" cycles default to closed.
            "status": status,
        } | overrides
        cycle = Cycle(**values)
        db.add(cycle)
        db.flush()
        template_service.create_default_templates(db, cycle)
        db.commit()
        return cycle

    return factory


class Bank:
    """Shortcuts for building criteria, questions and templates through the API."""

    def __init__(self, client, headers, db: Session) -> None:
        self.client = client
        self.headers = headers
        self.db = db

    def criterion(self, name: str = "Communication", description: str | None = None) -> int:
        res = self.client.post("/api/criteria", headers=self.headers, json={"name": name, "description": description})
        assert res.status_code == 201, res.text
        return res.json()["id"]

    def question(self, criterion_id: int, text: str = "Explains ideas clearly.", kind: str = "rating") -> int:
        res = self.client.post(
            "/api/questions", headers=self.headers, json={"criterionId": criterion_id, "text": text, "kind": kind}
        )
        assert res.status_code == 201, res.text
        return res.json()["id"]

    def template(self, cycle: Cycle, review_type: str = "manager") -> int:
        res = self.client.get(f"/api/cycles/{cycle.id}/templates", headers=self.headers)
        assert res.status_code == 200, res.text
        return next(item["id"] for item in res.json() if item["reviewType"] == review_type)

    def add(self, template_id: int, question_id: int, weight: int | None = None):
        body = {"questionId": question_id} | ({"weight": weight} if weight is not None else {})
        return self.client.post(f"/api/templates/{template_id}/questions", headers=self.headers, json=body)

    def get_template(self, template_id: int) -> dict:
        res = self.client.get(f"/api/templates/{template_id}", headers=self.headers)
        assert res.status_code == 200, res.text
        return res.json()

    def set_status(self, cycle: Cycle, status: CycleStatus) -> None:
        self.db.get(Cycle, cycle.id).status = status
        self.db.commit()

    def use_in(self, cycle: Cycle, *question_ids: int, review_type: str = "manager") -> int:
        """Put questions into one template of a draft cycle. Returns the template id."""
        template_id = self.template(cycle, review_type)
        for question_id in question_ids:
            assert self.add(template_id, question_id).status_code == 201
        return template_id


@pytest.fixture
def bank(client, auth, db: Session) -> Bank:
    return Bank(client, auth, db)


def order(template: dict) -> list[int]:
    """The question ids of a template response, in form order."""
    return [question["questionId"] for section in template["sections"] for question in section["questions"]]


def positions(template: dict) -> list[int]:
    return [question["position"] for section in template["sections"] for question in section["questions"]]


def template_ids(db: Session, cycle: Cycle) -> dict[str, int]:
    rows = db.query(Template).filter(Template.cycle_id == cycle.id).all()
    return {row.review_type.value: row.id for row in rows}
