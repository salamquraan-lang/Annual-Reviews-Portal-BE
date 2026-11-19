from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import Cycle, CycleStatus, CycleType, Role
from tests.conftest import bearer, error_code

BASE = "/api/cycles"

NEW_CYCLE = {
    "name": "Annual 2026",
    "type": "annual",
    "year": 2026,
    "startDate": "2026-01-01",
    "endDate": "2026-12-31",
}


def today() -> date:
    return datetime.now(UTC).date()


@pytest.fixture
def headers(admin):
    return bearer(admin)


class TestAccess:
    """Only the admin role manages cycles. The super admin is not allowed (C9)."""

    def test_refuses_a_request_without_a_token(self, client):
        assert client.get(BASE).status_code == 401

    @pytest.mark.parametrize("role", [Role.employee, Role.manager, Role.super_admin])
    def test_refuses_every_other_role(self, client, make_user, make_cycle, role):
        user = make_user(role=role)
        cycle = make_cycle()
        other = bearer(user)

        responses = [
            client.get(BASE, headers=other),
            client.get(f"{BASE}/{cycle.id}", headers=other),
            client.post(BASE, json=NEW_CYCLE, headers=other),
            client.patch(f"{BASE}/{cycle.id}", json={"name": "X"}, headers=other),
            client.delete(f"{BASE}/{cycle.id}", headers=other),
            client.post(f"{BASE}/{cycle.id}/open", headers=other),
        ]

        assert [r.status_code for r in responses] == [403] * 6


class TestList:
    def test_returns_cycles_with_paging_fields(self, client, headers, make_cycle):
        make_cycle()

        body = client.get(BASE, headers=headers).json()

        assert body["page"] == 1
        assert body["pageSize"] == 20
        assert body["total"] == 1
        assert body["items"][0] == {
            "id": body["items"][0]["id"],
            "name": "Annual 2026",
            "type": "annual",
            "year": 2026,
            "startDate": "2026-01-01",
            "endDate": "2026-12-31",
            "status": "draft",
        }

    def test_returns_an_empty_list_when_there_are_no_cycles(self, client, headers):
        body = client.get(BASE, headers=headers).json()

        assert body["items"] == []
        assert body["total"] == 0

    def test_puts_the_newest_year_first(self, client, headers, make_cycle):
        make_cycle(year=2024, name="Old")
        make_cycle(year=2026, name="New")
        make_cycle(year=2025, name="Middle")

        names = [c["name"] for c in client.get(BASE, headers=headers).json()["items"]]

        assert names == ["New", "Middle", "Old"]

    def test_filters_by_status(self, client, headers, make_cycle):
        make_cycle(year=2024, status=CycleStatus.closed)
        make_cycle(year=2025, status=CycleStatus.draft)

        body = client.get(BASE, params={"status": "closed"}, headers=headers).json()

        assert [c["year"] for c in body["items"]] == [2024]

    def test_filters_by_year_given_as_text_in_the_query_string(self, client, headers, make_cycle):
        make_cycle(year=2024)
        make_cycle(year=2025)

        body = client.get(BASE, params={"year": "2025"}, headers=headers).json()

        assert [c["year"] for c in body["items"]] == [2025]

    def test_filters_by_type(self, client, headers, make_cycle):
        make_cycle(type=CycleType.annual)
        make_cycle(type=CycleType.midyear)

        body = client.get(BASE, params={"type": "midyear"}, headers=headers).json()

        assert [c["type"] for c in body["items"]] == ["midyear"]

    def test_pages_the_results(self, client, headers, make_cycle):
        for year in (2023, 2024, 2025):
            make_cycle(year=year)

        body = client.get(BASE, params={"page": 2, "pageSize": 2}, headers=headers).json()

        assert body["total"] == 3
        assert [c["year"] for c in body["items"]] == [2023]

    @pytest.mark.parametrize(
        "params",
        [{"status": "nope"}, {"type": "nope"}, {"year": 1999}, {"year": 2101}, {"page": 0}, {"pageSize": 101}, {"x": 1}],
    )
    def test_rejects_bad_query_values(self, client, headers, params):
        res = client.get(BASE, params=params, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"


class TestGet:
    def test_returns_the_cycle(self, client, headers, make_cycle):
        cycle = make_cycle()

        body = client.get(f"{BASE}/{cycle.id}", headers=headers).json()

        assert body["id"] == cycle.id
        assert body["status"] == "draft"

    def test_returns_404_for_an_unknown_cycle(self, client, headers):
        res = client.get(f"{BASE}/999999", headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "CYCLE_NOT_FOUND"

    @pytest.mark.parametrize("bad_id", ["0", "-1", "abc"])
    def test_rejects_a_bad_id(self, client, headers, bad_id):
        assert client.get(f"{BASE}/{bad_id}", headers=headers).status_code == 400


class TestCurrent:
    def test_returns_the_open_cycle_to_any_signed_in_user(self, client, make_cycle, employee):
        make_cycle(year=2025, status=CycleStatus.closed)
        make_cycle(year=2026, status=CycleStatus.open)

        res = client.get(f"{BASE}/current", headers=bearer(employee))

        assert res.status_code == 200
        assert res.json()["year"] == 2026
        assert res.json()["status"] == "open"

    def test_is_also_open_to_the_super_admin(self, client, make_cycle, super_admin):
        make_cycle(status=CycleStatus.open)

        assert client.get(f"{BASE}/current", headers=bearer(super_admin)).status_code == 200

    def test_returns_404_when_no_cycle_is_open(self, client, make_cycle, employee):
        make_cycle(status=CycleStatus.draft)

        res = client.get(f"{BASE}/current", headers=bearer(employee))

        assert res.status_code == 404
        assert error_code(res) == "NO_OPEN_CYCLE"

    def test_needs_a_token(self, client):
        assert client.get(f"{BASE}/current").status_code == 401


class TestCreate:
    def test_creates_a_draft_cycle(self, client, headers):
        res = client.post(BASE, json=NEW_CYCLE, headers=headers)

        assert res.status_code == 201
        body = res.json()
        assert body["status"] == "draft"
        assert body["name"] == "Annual 2026"
        assert body["startDate"] == "2026-01-01"
        assert res.headers["Location"] == f"/api/cycles/{body['id']}"

    def test_trims_the_name(self, client, headers):
        res = client.post(BASE, json=NEW_CYCLE | {"name": "  Padded  "}, headers=headers)

        assert res.json()["name"] == "Padded"

    def test_refuses_a_second_cycle_of_the_same_year_and_type(self, client, headers, make_cycle):
        make_cycle(year=2026, type=CycleType.annual)

        res = client.post(BASE, json=NEW_CYCLE, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_YEAR_TYPE_TAKEN"

    def test_allows_another_type_in_the_same_year(self, client, headers, make_cycle):
        make_cycle(year=2026, type=CycleType.annual)

        res = client.post(BASE, json=NEW_CYCLE | {"type": "midyear"}, headers=headers)

        assert res.status_code == 201

    def test_allows_a_one_day_cycle(self, client, headers):
        res = client.post(BASE, json=NEW_CYCLE | {"startDate": "2026-05-05", "endDate": "2026-05-05"}, headers=headers)

        assert res.status_code == 201

    @pytest.mark.parametrize(
        "change",
        [
            {"name": ""},
            {"name": "   "},
            {"name": "x" * 121},
            {"type": "weekly"},
            {"year": 1999},
            {"year": 2101},
            {"year": "2026"},
            {"year": 2026.0},
            {"year": None},
            {"startDate": "2027-01-01"},
            {"startDate": "01/01/2026"},
            {"startDate": "2026-13-01"},
            {"startDate": 20260101},
            {"endDate": None},
            {"unknown": 1},
        ],
    )
    def test_rejects_an_invalid_body(self, client, headers, change):
        res = client.post(BASE, json=NEW_CYCLE | change, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"

    @pytest.mark.parametrize("missing", ["name", "type", "year", "startDate", "endDate"])
    def test_rejects_a_missing_field(self, client, headers, missing):
        body = {k: v for k, v in NEW_CYCLE.items() if k != missing}

        res = client.post(BASE, json=body, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"


class TestCopy:
    def test_creates_a_draft_from_an_existing_cycle(self, client, headers, make_cycle):
        source = make_cycle(year=2025, status=CycleStatus.finalized)

        res = client.post(f"{BASE}/{source.id}/copy", json=NEW_CYCLE, headers=headers)

        assert res.status_code == 201
        body = res.json()
        assert body["id"] != source.id
        assert body["status"] == "draft"
        assert body["year"] == 2026
        assert res.headers["Location"] == f"/api/cycles/{body['id']}"

    def test_does_not_change_the_source(self, client, headers, db, make_cycle):
        source = make_cycle(year=2025, status=CycleStatus.closed)

        client.post(f"{BASE}/{source.id}/copy", json=NEW_CYCLE, headers=headers)

        db.expire_all()
        assert db.get(Cycle, source.id).status == CycleStatus.closed

    def test_returns_404_for_an_unknown_source(self, client, headers):
        res = client.post(f"{BASE}/999999/copy", json=NEW_CYCLE, headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "CYCLE_NOT_FOUND"

    def test_refuses_a_year_and_type_that_is_taken(self, client, headers, make_cycle):
        source = make_cycle(year=2025)
        make_cycle(year=2026, type=CycleType.annual)

        res = client.post(f"{BASE}/{source.id}/copy", json=NEW_CYCLE, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_YEAR_TYPE_TAKEN"

    def test_rejects_an_invalid_body(self, client, headers, make_cycle):
        source = make_cycle(year=2025)

        res = client.post(f"{BASE}/{source.id}/copy", json=NEW_CYCLE | {"startDate": "2027-01-01"}, headers=headers)

        assert res.status_code == 400


class TestUpdate:
    def test_changes_only_the_fields_that_are_sent(self, client, headers, make_cycle):
        cycle = make_cycle()

        res = client.patch(f"{BASE}/{cycle.id}", json={"name": "Renamed"}, headers=headers)

        assert res.status_code == 200
        body = res.json()
        assert body["name"] == "Renamed"
        assert body["year"] == 2026
        assert body["startDate"] == "2026-01-01"

    def test_changes_several_fields_at_once(self, client, headers, make_cycle):
        cycle = make_cycle()

        res = client.patch(
            f"{BASE}/{cycle.id}",
            json={"name": "Mid", "type": "midyear", "startDate": "2026-02-01", "endDate": "2026-06-30"},
            headers=headers,
        )

        body = res.json()
        assert (body["name"], body["type"], body["startDate"], body["endDate"]) == (
            "Mid",
            "midyear",
            "2026-02-01",
            "2026-06-30",
        )

    def test_sending_the_stored_values_changes_nothing(self, client, headers, make_cycle):
        cycle = make_cycle()

        res = client.patch(f"{BASE}/{cycle.id}", json={"name": "Annual 2026", "year": 2026}, headers=headers)

        assert res.status_code == 200

    def test_refuses_a_year_and_type_that_another_cycle_has(self, client, headers, make_cycle):
        make_cycle(year=2025)
        cycle = make_cycle(year=2026)

        res = client.patch(f"{BASE}/{cycle.id}", json={"year": 2025}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_YEAR_TYPE_TAKEN"

    def test_refuses_a_start_date_after_the_stored_end_date(self, client, headers, make_cycle):
        cycle = make_cycle(end_date=date(2026, 6, 30))

        res = client.patch(f"{BASE}/{cycle.id}", json={"startDate": "2026-07-01"}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "INVALID_DATE_RANGE"

    def test_refuses_an_end_date_before_the_stored_start_date(self, client, headers, make_cycle):
        cycle = make_cycle(start_date=date(2026, 6, 1))

        res = client.patch(f"{BASE}/{cycle.id}", json={"endDate": "2026-05-31"}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "INVALID_DATE_RANGE"

    @pytest.mark.parametrize("status", [CycleStatus.open, CycleStatus.closed, CycleStatus.finalized])
    def test_only_a_draft_can_be_edited(self, client, headers, make_cycle, status):
        cycle = make_cycle(status=status)

        res = client.patch(f"{BASE}/{cycle.id}", json={"name": "X"}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_NOT_DRAFT"

    def test_returns_404_for_an_unknown_cycle(self, client, headers):
        res = client.patch(f"{BASE}/999999", json={"name": "X"}, headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "CYCLE_NOT_FOUND"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": None},
            {"year": None},
            {"startDate": None},
            {"name": ""},
            {"year": "2026"},
            {"year": 2101},
            {"type": "weekly"},
            {"startDate": "2026-12-01", "endDate": "2026-01-01"},
            {"unknown": 1},
        ],
    )
    def test_rejects_an_invalid_body(self, client, headers, make_cycle, body):
        cycle = make_cycle()

        res = client.patch(f"{BASE}/{cycle.id}", json=body, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"


class TestEndDate:
    @pytest.fixture
    def open_cycle(self, make_cycle):
        return make_cycle(
            status=CycleStatus.open, start_date=today() - timedelta(days=10), end_date=today() + timedelta(days=10)
        )

    def test_extends_the_end_date_of_an_open_cycle(self, client, headers, open_cycle):
        new_end = (today() + timedelta(days=30)).isoformat()

        res = client.put(f"{BASE}/{open_cycle.id}/end-date", json={"endDate": new_end}, headers=headers)

        assert res.status_code == 200
        assert res.json()["endDate"] == new_end

    def test_shortens_the_end_date_to_a_day_that_is_not_past(self, client, headers, open_cycle):
        new_end = today().isoformat()

        res = client.put(f"{BASE}/{open_cycle.id}/end-date", json={"endDate": new_end}, headers=headers)

        assert res.status_code == 200
        assert res.json()["endDate"] == new_end

    def test_the_same_end_date_is_not_an_error(self, client, headers, open_cycle):
        res = client.put(
            f"{BASE}/{open_cycle.id}/end-date", json={"endDate": open_cycle.end_date.isoformat()}, headers=headers
        )

        assert res.status_code == 200

    def test_refuses_an_end_date_in_the_past(self, client, headers, open_cycle):
        past = (today() - timedelta(days=1)).isoformat()

        res = client.put(f"{BASE}/{open_cycle.id}/end-date", json={"endDate": past}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "END_DATE_IN_PAST"

    def test_refuses_an_end_date_before_the_start_date(self, client, headers, open_cycle):
        before_start = (today() - timedelta(days=11)).isoformat()

        res = client.put(f"{BASE}/{open_cycle.id}/end-date", json={"endDate": before_start}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "INVALID_DATE_RANGE"

    @pytest.mark.parametrize("status", [CycleStatus.draft, CycleStatus.closed, CycleStatus.finalized])
    def test_only_an_open_cycle_can_change_its_end_date(self, client, headers, make_cycle, status):
        cycle = make_cycle(status=status)

        res = client.put(f"{BASE}/{cycle.id}/end-date", json={"endDate": "2026-12-31"}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_INVALID_STATUS"

    def test_returns_404_for_an_unknown_cycle(self, client, headers):
        res = client.put(f"{BASE}/999999/end-date", json={"endDate": "2026-12-31"}, headers=headers)

        assert res.status_code == 404

    @pytest.mark.parametrize("body", [{}, {"endDate": None}, {"endDate": "tomorrow"}, {"endDate": 20261231}])
    def test_rejects_an_invalid_body(self, client, headers, open_cycle, body):
        res = client.put(f"{BASE}/{open_cycle.id}/end-date", json=body, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"


class TestDelete:
    def test_deletes_a_draft_cycle(self, client, headers, make_cycle):
        cycle = make_cycle()

        res = client.delete(f"{BASE}/{cycle.id}", headers=headers)

        assert res.status_code == 204
        assert res.content == b""
        assert client.get(f"{BASE}/{cycle.id}", headers=headers).status_code == 404

    def test_frees_the_year_and_type_for_a_new_cycle(self, client, headers, make_cycle):
        cycle = make_cycle(year=2026)
        client.delete(f"{BASE}/{cycle.id}", headers=headers)

        res = client.post(BASE, json=NEW_CYCLE, headers=headers)

        assert res.status_code == 201

    @pytest.mark.parametrize("status", [CycleStatus.open, CycleStatus.closed, CycleStatus.finalized])
    def test_only_a_draft_can_be_deleted(self, client, headers, make_cycle, status):
        cycle = make_cycle(status=status)

        res = client.delete(f"{BASE}/{cycle.id}", headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_NOT_DRAFT"
        assert client.get(f"{BASE}/{cycle.id}", headers=headers).status_code == 200

    def test_returns_404_for_an_unknown_cycle(self, client, headers):
        res = client.delete(f"{BASE}/999999", headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "CYCLE_NOT_FOUND"


class TestReadiness:
    def test_a_lone_draft_is_ready(self, client, headers, make_cycle, give_everyone_a_department):
        cycle = make_cycle()
        give_everyone_a_department()

        body = client.get(f"{BASE}/{cycle.id}/readiness", headers=headers).json()

        assert body == {"ready": True, "problems": []}

    def test_reports_that_another_cycle_is_open(self, client, headers, make_cycle, give_everyone_a_department):
        make_cycle(year=2025, status=CycleStatus.open)
        cycle = make_cycle(year=2026)
        give_everyone_a_department()

        body = client.get(f"{BASE}/{cycle.id}/readiness", headers=headers).json()

        assert body["ready"] is False
        assert [p["code"] for p in body["problems"]] == ["ANOTHER_CYCLE_OPEN"]
        assert body["problems"][0]["message"]

    @pytest.mark.parametrize("status", [CycleStatus.open, CycleStatus.closed, CycleStatus.finalized])
    def test_only_a_draft_can_be_checked(self, client, headers, make_cycle, status):
        cycle = make_cycle(status=status)

        res = client.get(f"{BASE}/{cycle.id}/readiness", headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_INVALID_STATUS"

    def test_returns_404_for_an_unknown_cycle(self, client, headers):
        assert client.get(f"{BASE}/999999/readiness", headers=headers).status_code == 404


class TestLifecycle:
    """draft -> open -> closed -> finalized, with closed -> open (reopen) as the one way back."""

    def test_follows_the_whole_path(self, client, headers, make_cycle, give_templates, give_everyone_a_department):
        cycle = make_cycle()
        give_templates(cycle)
        give_everyone_a_department()

        assert client.post(f"{BASE}/{cycle.id}/open", headers=headers).json()["status"] == "open"
        assert client.post(f"{BASE}/{cycle.id}/close", headers=headers).json()["status"] == "closed"
        assert client.post(f"{BASE}/{cycle.id}/reopen", headers=headers).json()["status"] == "open"
        assert client.post(f"{BASE}/{cycle.id}/close", headers=headers).json()["status"] == "closed"
        assert client.post(f"{BASE}/{cycle.id}/finalize", headers=headers).json()["status"] == "finalized"

    def test_an_opened_cycle_becomes_the_current_one(
        self, client, headers, make_cycle, employee, give_templates, give_everyone_a_department
    ):
        cycle = make_cycle()
        give_templates(cycle)
        give_everyone_a_department()
        client.post(f"{BASE}/{cycle.id}/open", headers=headers)

        res = client.get(f"{BASE}/current", headers=bearer(employee))

        assert res.json()["id"] == cycle.id

    def test_a_closed_cycle_is_no_longer_current(self, client, headers, make_cycle, employee):
        cycle = make_cycle(status=CycleStatus.open)
        client.post(f"{BASE}/{cycle.id}/close", headers=headers)

        assert client.get(f"{BASE}/current", headers=bearer(employee)).status_code == 404

    def test_finalize_returns_the_cycle_with_a_warnings_list(self, client, headers, make_cycle):
        cycle = make_cycle(status=CycleStatus.closed)

        body = client.post(f"{BASE}/{cycle.id}/finalize", headers=headers).json()

        assert body["status"] == "finalized"
        assert body["id"] == cycle.id
        assert body["warnings"] == []

    # ---- only one cycle can be open

    def test_refuses_to_open_while_another_cycle_is_open(self, client, headers, make_cycle):
        make_cycle(year=2025, status=CycleStatus.open)
        cycle = make_cycle(year=2026)

        res = client.post(f"{BASE}/{cycle.id}/open", headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "ANOTHER_CYCLE_OPEN"

    def test_refuses_to_reopen_while_another_cycle_is_open(self, client, headers, make_cycle):
        make_cycle(year=2026, status=CycleStatus.open)
        closed = make_cycle(year=2025, status=CycleStatus.closed)

        res = client.post(f"{BASE}/{closed.id}/reopen", headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "ANOTHER_CYCLE_OPEN"

    def test_allows_opening_again_after_the_open_cycle_is_closed(
        self, client, headers, make_cycle, give_templates, give_everyone_a_department
    ):
        first = make_cycle(year=2025, status=CycleStatus.open)
        second = make_cycle(year=2026)
        give_templates(second)
        give_everyone_a_department()
        client.post(f"{BASE}/{first.id}/close", headers=headers)

        res = client.post(f"{BASE}/{second.id}/open", headers=headers)

        assert res.status_code == 200

    # ---- every move that is not allowed

    @pytest.mark.parametrize(
        ("action", "status"),
        [
            ("open", CycleStatus.open),
            ("open", CycleStatus.closed),
            ("open", CycleStatus.finalized),
            ("close", CycleStatus.draft),
            ("close", CycleStatus.closed),
            ("close", CycleStatus.finalized),
            ("reopen", CycleStatus.draft),
            ("reopen", CycleStatus.open),
            ("reopen", CycleStatus.finalized),
            ("finalize", CycleStatus.draft),
            ("finalize", CycleStatus.open),
            ("finalize", CycleStatus.finalized),
        ],
    )
    def test_refuses_a_move_the_status_does_not_allow(self, client, headers, make_cycle, action, status):
        cycle = make_cycle(status=status)

        res = client.post(f"{BASE}/{cycle.id}/{action}", headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "CYCLE_INVALID_STATUS"
        assert client.get(f"{BASE}/{cycle.id}", headers=headers).json()["status"] == status.value

    @pytest.mark.parametrize("action", ["open", "close", "reopen", "finalize"])
    def test_returns_404_for_an_unknown_cycle(self, client, headers, action):
        res = client.post(f"{BASE}/999999/{action}", headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "CYCLE_NOT_FOUND"

    def test_a_finalized_cycle_stays_read_only(self, client, headers, make_cycle):
        cycle = make_cycle(status=CycleStatus.finalized)

        responses = [
            client.patch(f"{BASE}/{cycle.id}", json={"name": "X"}, headers=headers),
            client.put(f"{BASE}/{cycle.id}/end-date", json={"endDate": "2026-12-31"}, headers=headers),
            client.delete(f"{BASE}/{cycle.id}", headers=headers),
        ]

        assert [r.status_code for r in responses] == [409, 409, 409]
