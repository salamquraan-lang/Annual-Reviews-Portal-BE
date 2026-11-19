import pytest
from sqlalchemy import func, select, update

from app.models import (
    Assignment,
    AssignmentStatus,
    Cycle,
    CycleParticipant,
    CycleStatus,
    ReviewType,
    User,
)
from tests.conftest import bearer, error_code

BASE = "/api/cycles"


@pytest.fixture
def headers(admin):
    return bearer(admin)


@pytest.fixture
def ready_cycle(admin, employee, make_cycle, give_templates, give_everyone_a_department):
    """A draft cycle that can be opened: it has templates and every active user has a department."""
    cycle = make_cycle()
    give_templates(cycle)
    give_everyone_a_department()
    return cycle


def open_it(client, headers, cycle):
    return client.post(f"{BASE}/{cycle.id}/open", headers=headers)


def participant_rows(db, cycle):
    return db.execute(
        select(CycleParticipant.user_id, CycleParticipant.department_id, CycleParticipant.manager_id).where(
            CycleParticipant.cycle_id == cycle.id
        )
    ).all()


class TestSnapshot:
    def test_every_active_user_becomes_a_participant(self, client, db, headers, ready_cycle, admin, employee, make_user):
        make_user(name="Gone Gary", email="gone@example.test", is_active=False)

        assert open_it(client, headers, ready_cycle).status_code == 200

        rows = participant_rows(db, ready_cycle)
        assert {row.user_id for row in rows} == {admin.id, employee.id, employee.manager_id}
        eli = next(row for row in rows if row.user_id == employee.id)
        assert eli.department_id == employee.department_id
        assert eli.manager_id == employee.manager_id

    def test_creates_self_manager_and_upward_assignments(self, client, db, headers, ready_cycle, admin, employee):
        assert open_it(client, headers, ready_cycle).status_code == 200

        rows = db.execute(
            select(Assignment.reviewer_id, Assignment.reviewee_id, Assignment.review_type, Assignment.status).where(
                Assignment.cycle_id == ready_cycle.id
            )
        ).all()
        mona = employee.manager_id
        assert {(row.reviewer_id, row.reviewee_id, row.review_type) for row in rows} == {
            (admin.id, admin.id, ReviewType.self_),
            (mona, mona, ReviewType.self_),
            (employee.id, employee.id, ReviewType.self_),
            (mona, employee.id, ReviewType.manager),
            (employee.id, mona, ReviewType.upward),
        }
        assert len(rows) == 5
        assert {row.status for row in rows} == {AssignmentStatus.pending}

    def test_the_snapshot_does_not_follow_later_changes(
        self, client, db, headers, ready_cycle, employee, make_department
    ):
        original = employee.department_id
        sales = make_department("Sales")
        assert open_it(client, headers, ready_cycle).status_code == 200

        db.execute(update(User).where(User.id == employee.id).values(department_id=sales.id))
        db.commit()

        row = next(row for row in participant_rows(db, ready_cycle) if row.user_id == employee.id)
        assert row.department_id == original


class TestReadiness:
    def test_names_each_user_without_a_department(self, client, headers, make_cycle, admin, employee):
        cycle = make_cycle()

        body = client.get(f"{BASE}/{cycle.id}/readiness", headers=headers).json()

        assert body["ready"] is False
        missing = {p["userId"] for p in body["problems"] if p["code"] == "USER_WITHOUT_DEPARTMENT"}
        assert missing == {admin.id, employee.manager_id}

    def test_reports_a_manager_who_is_not_active_against_the_user(
        self, client, db, headers, make_cycle, give_everyone_a_department, admin, employee
    ):
        cycle = make_cycle()
        give_everyone_a_department()
        db.execute(update(User).where(User.id == employee.manager_id).values(is_active=False))
        db.commit()

        body = client.get(f"{BASE}/{cycle.id}/readiness", headers=headers).json()

        assert body["ready"] is False
        assert [(p["code"], p["userId"]) for p in body["problems"]] == [("MANAGER_NOT_ACTIVE", employee.id)]

    def test_lists_at_most_20_users_then_counts_the_rest(self, client, headers, make_cycle, make_user, admin):
        for number in range(25):
            make_user(name=f"User {number}", email=f"user{number}@example.test")
        cycle = make_cycle()

        body = client.get(f"{BASE}/{cycle.id}/readiness", headers=headers).json()

        problems = [p for p in body["problems"] if p["code"] == "USER_WITHOUT_DEPARTMENT"]
        # 25 new users and the admin make 26. Twenty are named. The last line counts the other 6.
        assert len(problems) == 21
        assert all("userId" in p for p in problems[:20])
        assert "userId" not in problems[20]
        assert problems[20]["message"].startswith("6 more")


class TestOpenRefused:
    def test_a_user_without_a_department_stops_the_open_and_writes_nothing(self, client, db, headers, make_cycle, admin):
        cycle = make_cycle()

        res = open_it(client, headers, cycle)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_NOT_READY"
        details = res.json()["error"]["details"]
        assert {"code": "USER_WITHOUT_DEPARTMENT", "message": f"{admin.name} has no department.", "userId": admin.id} in details
        assert db.scalar(select(Cycle.status).where(Cycle.id == cycle.id)) == CycleStatus.draft
        assert db.scalar(select(func.count()).select_from(CycleParticipant)) == 0
