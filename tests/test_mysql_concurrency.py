"""Tests for races and MySQL-specific behavior: row locks, unique indexes and the 1062 error."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from app.main import create_app
from app.models import CycleStatus, CycleType, Department
from app.services import cycles as cycles_service
from app.services import departments as departments_service
from tests.conftest import bearer, error_code


def run_together(calls):
    """Run the calls at the same moment, each with its own client. Returns the responses in order."""
    barrier = Barrier(len(calls))

    def worker(call):
        with TestClient(create_app()) as client:
            barrier.wait()
            return call(client)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(worker, calls))


class TestDuplicateKeyIsMappedTo409:
    """The checks before a write are only a friendly early answer. The unique index is the real guard.

    Each test switches the early check off, so the request reaches the index and MySQL raises 1062.
    """

    def test_department_name(self, client, super_admin, make_department, monkeypatch):
        make_department("Sales")
        other = make_department("Support")
        monkeypatch.setattr(departments_service, "_name_is_taken", lambda *args, **kwargs: False)

        res = client.patch(f"/api/departments/{other.id}", json={"name": "Sales"}, headers=bearer(super_admin))

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_NAME_TAKEN"

    def test_department_name_on_create(self, client, super_admin, make_department, monkeypatch):
        make_department("Sales")
        monkeypatch.setattr(departments_service, "_name_is_taken", lambda *args, **kwargs: False)

        res = client.post("/api/departments", json={"name": "Sales"}, headers=bearer(super_admin))

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_NAME_TAKEN"

    def test_cycle_year_and_type(self, client, admin, make_cycle, monkeypatch):
        make_cycle(year=2026, type=CycleType.annual)
        monkeypatch.setattr(cycles_service, "_year_type_taken", lambda *args, **kwargs: False)

        res = client.post(
            "/api/cycles",
            json={"name": "Again", "type": "annual", "year": 2026, "startDate": "2026-01-01", "endDate": "2026-12-31"},
            headers=bearer(admin),
        )

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_YEAR_TYPE_TAKEN"

    def test_second_open_cycle(self, client, admin, make_cycle, monkeypatch):
        make_cycle(year=2025, status=CycleStatus.open)
        draft = make_cycle(year=2026)
        monkeypatch.setattr(cycles_service, "_other_open_cycle_exists", lambda *args, **kwargs: False)

        res = client.post(f"/api/cycles/{draft.id}/open", headers=bearer(admin))

        assert res.status_code == 409
        assert error_code(res) == "ANOTHER_CYCLE_OPEN"

    def test_the_failed_request_leaves_the_data_unchanged(self, client, admin, make_cycle, monkeypatch):
        make_cycle(year=2025, status=CycleStatus.open)
        draft = make_cycle(year=2026)
        monkeypatch.setattr(cycles_service, "_other_open_cycle_exists", lambda *args, **kwargs: False)
        client.post(f"/api/cycles/{draft.id}/open", headers=bearer(admin))

        body = client.get(f"/api/cycles/{draft.id}", headers=bearer(admin)).json()

        assert body["status"] == "draft"


class TestSimultaneousRequests:
    def test_two_cycles_cannot_both_be_opened(self, admin, make_cycle):
        first = make_cycle(year=2025)
        second = make_cycle(year=2026)
        headers = bearer(admin)

        responses = run_together(
            [
                lambda c: c.post(f"/api/cycles/{first.id}/open", headers=headers),
                lambda c: c.post(f"/api/cycles/{second.id}/open", headers=headers),
            ]
        )

        assert sorted(r.status_code for r in responses) == [200, 409]
        loser = next(r for r in responses if r.status_code == 409)
        assert error_code(loser) == "ANOTHER_CYCLE_OPEN"

    def test_two_cycles_cannot_both_be_reopened(self, admin, make_cycle):
        first = make_cycle(year=2025, status=CycleStatus.closed)
        second = make_cycle(year=2026, status=CycleStatus.closed)
        headers = bearer(admin)

        responses = run_together(
            [
                lambda c: c.post(f"/api/cycles/{first.id}/reopen", headers=headers),
                lambda c: c.post(f"/api/cycles/{second.id}/reopen", headers=headers),
            ]
        )

        assert sorted(r.status_code for r in responses) == [200, 409]

    def test_one_cycle_cannot_be_closed_twice(self, admin, make_cycle):
        cycle = make_cycle(status=CycleStatus.open)
        headers = bearer(admin)

        responses = run_together([lambda c: c.post(f"/api/cycles/{cycle.id}/close", headers=headers)] * 2)

        assert sorted(r.status_code for r in responses) == [200, 409]
        loser = next(r for r in responses if r.status_code == 409)
        assert error_code(loser) == "CYCLE_INVALID_STATUS"

    def test_two_cycles_cannot_be_created_for_the_same_year_and_type(self, admin):
        body = {"name": "Annual", "type": "annual", "year": 2026, "startDate": "2026-01-01", "endDate": "2026-12-31"}
        headers = bearer(admin)

        responses = run_together([lambda c: c.post("/api/cycles", json=body, headers=headers)] * 2)

        assert sorted(r.status_code for r in responses) == [201, 409]
        loser = next(r for r in responses if r.status_code == 409)
        assert error_code(loser) == "CYCLE_YEAR_TYPE_TAKEN"

    def test_two_departments_cannot_be_created_with_the_same_name(self, super_admin):
        headers = bearer(super_admin)

        responses = run_together([lambda c: c.post("/api/departments", json={"name": "Sales"}, headers=headers)] * 2)

        assert sorted(r.status_code for r in responses) == [201, 409]
        loser = next(r for r in responses if r.status_code == 409)
        assert error_code(loser) == "DEPARTMENT_NAME_TAKEN"

    def test_two_moves_cannot_create_a_loop(self, client, super_admin, make_department, db):
        """A under B and B under A at once. Without the row locks both would succeed.

        One try is not enough to see a race, so the pair is sent many times.
        """
        a = make_department("A")
        b = make_department("B")
        headers = bearer(super_admin)

        for round_number in range(25):
            db.execute(update(Department).values(parent_id=None))
            db.commit()

            responses = run_together(
                [
                    lambda c: c.put(f"/api/departments/{a.id}/parent", json={"parentId": b.id}, headers=headers),
                    lambda c: c.put(f"/api/departments/{b.id}/parent", json={"parentId": a.id}, headers=headers),
                ]
            )

            assert sorted(r.status_code for r in responses) == [200, 409], f"round {round_number}"
            loser = next(r for r in responses if r.status_code == 409)
            assert error_code(loser) == "DEPARTMENT_LOOP"
            assert client.get("/api/departments/tree", headers=headers).status_code == 200

    def test_a_department_cannot_get_a_user_while_it_is_being_deactivated(
        self, client, super_admin, make_department, make_user
    ):
        """Deactivate and move-users on the same department at the same moment never leave users in an inactive one."""
        source = make_department("Source")
        target = make_department("Target")
        make_user(department_id=source.id)
        headers = bearer(super_admin)

        responses = run_together(
            [
                lambda c: c.patch(f"/api/departments/{source.id}", json={"isActive": False}, headers=headers),
                lambda c: c.post(
                    f"/api/departments/{source.id}/move-users", json={"targetDepartmentId": target.id}, headers=headers
                ),
            ]
        )

        assert all(r.status_code in (200, 409) for r in responses)
        detail = client.get(f"/api/departments/{source.id}", headers=headers).json()
        assert not (detail["isActive"] is False and detail["activeUserCount"] > 0)


class TestMysqlBehaviour:
    def test_department_names_differ_only_by_case_clash(self, client, super_admin, make_department):
        """MySQL compares text without regard to case, so "Sales" and "sales" are the same name."""
        make_department("Sales")

        res = client.post("/api/departments", json={"name": "sales"}, headers=bearer(super_admin))

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_NAME_TAKEN"

    def test_the_name_search_ignores_case(self, client, super_admin, make_department):
        make_department("Human Resources")

        body = client.get("/api/departments", params={"search": "HUMAN"}, headers=bearer(super_admin)).json()

        assert body["total"] == 1

    def test_the_database_refuses_an_end_date_before_the_start_date(self, db, make_cycle):
        with pytest.raises(DBAPIError):
            make_cycle(start_date=date(2026, 2, 1), end_date=date(2026, 1, 1))
        db.rollback()
