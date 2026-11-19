import pytest

from app.models import Department, Role
from tests.conftest import bearer, error_code

BASE = "/api/departments"


@pytest.fixture
def headers(super_admin):
    return bearer(super_admin)


@pytest.fixture
def admin_headers(admin):
    return bearer(admin)


class TestAccess:
    """Admins and super admins read. Only the super admin writes."""

    def test_refuses_a_request_without_a_token(self, client):
        assert client.get(BASE).status_code == 401

    @pytest.mark.parametrize("role", [Role.employee, Role.manager])
    def test_refuses_employees_and_managers_even_for_reads(self, client, make_user, role):
        user = make_user(role=role)

        assert client.get(BASE, headers=bearer(user)).status_code == 403

    def test_lets_an_admin_read(self, client, admin_headers):
        assert client.get(BASE, headers=admin_headers).status_code == 200

    def test_refuses_an_admin_on_every_write(self, client, admin_headers, make_department):
        department = make_department()
        other = make_department("Other")

        responses = [
            client.post(BASE, json={"name": "New"}, headers=admin_headers),
            client.patch(f"{BASE}/{department.id}", json={"name": "New"}, headers=admin_headers),
            client.put(f"{BASE}/{department.id}/parent", json={"parentId": None}, headers=admin_headers),
            client.post(
                f"{BASE}/{department.id}/move-users", json={"targetDepartmentId": other.id}, headers=admin_headers
            ),
        ]

        assert [r.status_code for r in responses] == [403, 403, 403, 403]


class TestList:
    def test_returns_departments_with_paging_fields(self, client, headers, make_department):
        make_department("Engineering")

        body = client.get(BASE, headers=headers).json()

        assert body["page"] == 1
        assert body["pageSize"] == 20
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Engineering"
        assert set(body["items"][0]) == {"id", "name", "parentId", "isActive", "createdAt", "updatedAt"}

    def test_sorts_by_name(self, client, headers, make_department):
        make_department("Sales")
        make_department("Design")
        make_department("Marketing")

        names = [d["name"] for d in client.get(BASE, headers=headers).json()["items"]]

        assert names == ["Design", "Marketing", "Sales"]

    def test_filters_by_active_state(self, client, headers, make_department):
        make_department("On")
        make_department("Off", is_active=False)

        active = client.get(BASE, params={"isActive": "true"}, headers=headers).json()
        inactive = client.get(BASE, params={"isActive": "false"}, headers=headers).json()

        assert [d["name"] for d in active["items"]] == ["On"]
        assert [d["name"] for d in inactive["items"]] == ["Off"]

    def test_filters_by_parent(self, client, headers, make_department):
        root = make_department("Root")
        make_department("Child", parent=root)
        make_department("Other root")

        body = client.get(BASE, params={"parentId": root.id}, headers=headers).json()

        assert [d["name"] for d in body["items"]] == ["Child"]

    def test_filters_top_level_only(self, client, headers, make_department):
        root = make_department("Root")
        make_department("Child", parent=root)

        body = client.get(BASE, params={"topLevel": "true"}, headers=headers).json()

        assert [d["name"] for d in body["items"]] == ["Root"]

    def test_refuses_parent_id_together_with_top_level(self, client, headers, make_department):
        root = make_department("Root")

        res = client.get(BASE, params={"parentId": root.id, "topLevel": "true"}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"

    def test_searches_by_part_of_the_name_ignoring_case(self, client, headers, make_department):
        make_department("Human Resources")
        make_department("Finance")

        body = client.get(BASE, params={"search": "resour"}, headers=headers).json()

        assert [d["name"] for d in body["items"]] == ["Human Resources"]

    def test_treats_percent_in_search_as_plain_text(self, client, headers, make_department):
        make_department("Finance")

        body = client.get(BASE, params={"search": "%"}, headers=headers).json()

        assert body["total"] == 0

    def test_pages_the_results(self, client, headers, make_department):
        for name in ("A", "B", "C"):
            make_department(name)

        body = client.get(BASE, params={"page": 2, "pageSize": 2}, headers=headers).json()

        assert body["total"] == 3
        assert [d["name"] for d in body["items"]] == ["C"]

    @pytest.mark.parametrize(
        "params",
        [{"page": 0}, {"pageSize": 0}, {"pageSize": 101}, {"parentId": 0}, {"isActive": "maybe"}, {"unknown": 1}],
    )
    def test_rejects_bad_query_values(self, client, headers, params):
        res = client.get(BASE, params=params, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"


class TestTree:
    def test_returns_an_empty_list_when_there_are_no_departments(self, client, headers):
        assert client.get(f"{BASE}/tree", headers=headers).json() == []

    def test_nests_children_under_their_parent(self, client, headers, make_department):
        root = make_department("Root")
        child = make_department("Child", parent=root)
        make_department("Grandchild", parent=child)

        tree = client.get(f"{BASE}/tree", headers=headers).json()

        assert len(tree) == 1
        assert tree[0]["name"] == "Root"
        assert tree[0]["children"][0]["name"] == "Child"
        assert tree[0]["children"][0]["children"][0]["name"] == "Grandchild"
        assert tree[0]["children"][0]["children"][0]["children"] == []

    def test_includes_inactive_departments_with_their_state(self, client, headers, make_department):
        make_department("Gone", is_active=False)

        tree = client.get(f"{BASE}/tree", headers=headers).json()

        assert tree == [{"id": tree[0]["id"], "name": "Gone", "isActive": False, "children": []}]


class TestGet:
    def test_returns_the_department_with_children_and_active_user_count(
        self, client, headers, make_department, make_user
    ):
        root = make_department("Root")
        make_department("Child", parent=root)
        make_user(email="a@example.test", department_id=root.id)
        make_user(email="b@example.test", department_id=root.id)
        make_user(email="c@example.test", department_id=root.id, is_active=False)

        body = client.get(f"{BASE}/{root.id}", headers=headers).json()

        assert body["name"] == "Root"
        assert body["parentId"] is None
        assert body["activeUserCount"] == 2
        assert [c["name"] for c in body["children"]] == ["Child"]
        assert set(body["children"][0]) == {"id", "name", "isActive"}

    def test_returns_404_for_an_unknown_department(self, client, headers):
        res = client.get(f"{BASE}/999999", headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "DEPARTMENT_NOT_FOUND"

    @pytest.mark.parametrize("bad_id", ["0", "-1", "abc"])
    def test_rejects_a_bad_id(self, client, headers, bad_id):
        assert client.get(f"{BASE}/{bad_id}", headers=headers).status_code == 400

    def test_formats_timestamps_as_utc_with_a_z(self, client, headers, make_department):
        department = make_department()

        body = client.get(f"{BASE}/{department.id}", headers=headers).json()

        assert body["createdAt"].endswith("Z")
        assert body["updatedAt"].endswith("Z")


class TestCreate:
    def test_creates_a_top_level_department(self, client, headers):
        res = client.post(BASE, json={"name": "Engineering"}, headers=headers)

        assert res.status_code == 201
        body = res.json()
        assert body["name"] == "Engineering"
        assert body["parentId"] is None
        assert body["isActive"] is True
        assert res.headers["Location"] == f"/api/departments/{body['id']}"

    def test_creates_a_child_department(self, client, headers, make_department):
        root = make_department("Root")

        res = client.post(BASE, json={"name": "Child", "parentId": root.id}, headers=headers)

        assert res.status_code == 201
        assert res.json()["parentId"] == root.id

    def test_trims_the_name(self, client, headers):
        res = client.post(BASE, json={"name": "  Sales  "}, headers=headers)

        assert res.json()["name"] == "Sales"

    def test_refuses_a_duplicate_name_under_the_same_parent(self, client, headers, make_department):
        make_department("Sales")

        res = client.post(BASE, json={"name": "Sales"}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_NAME_TAKEN"

    def test_allows_the_same_name_under_a_different_parent(self, client, headers, make_department):
        a = make_department("A")
        b = make_department("B")
        make_department("Support", parent=a)

        res = client.post(BASE, json={"name": "Support", "parentId": b.id}, headers=headers)

        assert res.status_code == 201

    def test_allows_reusing_the_name_of_an_inactive_department(self, client, headers, make_department):
        make_department("Sales", is_active=False)

        res = client.post(BASE, json={"name": "Sales"}, headers=headers)

        assert res.status_code == 201

    def test_refuses_an_unknown_parent(self, client, headers):
        res = client.post(BASE, json={"name": "X", "parentId": 999999}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "PARENT_NOT_FOUND"

    def test_refuses_an_inactive_parent(self, client, headers, make_department):
        parent = make_department("Old", is_active=False)

        res = client.post(BASE, json={"name": "X", "parentId": parent.id}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_INACTIVE"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": ""},
            {"name": "   "},
            {"name": "x" * 121},
            {"name": None},
            {"name": 5},
            {"name": "X", "parentId": 0},
            {"name": "X", "parentId": "1"},
            {"name": "X", "parentId": 1.0},
            {"name": "X", "unknown": 1},
        ],
    )
    def test_rejects_an_invalid_body(self, client, headers, body):
        res = client.post(BASE, json=body, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"

    def test_accepts_a_name_of_exactly_120_characters(self, client, headers):
        assert client.post(BASE, json={"name": "x" * 120}, headers=headers).status_code == 201


class TestUpdate:
    def test_renames_a_department(self, client, headers, make_department):
        department = make_department("Old")

        res = client.patch(f"{BASE}/{department.id}", json={"name": "New"}, headers=headers)

        assert res.status_code == 200
        assert res.json()["name"] == "New"

    def test_renaming_to_the_same_name_changes_nothing(self, client, headers, make_department):
        department = make_department("Same")

        res = client.patch(f"{BASE}/{department.id}", json={"name": "Same"}, headers=headers)

        assert res.status_code == 200

    def test_refuses_a_name_that_a_sibling_already_has(self, client, headers, make_department):
        make_department("Taken")
        department = make_department("Mine")

        res = client.patch(f"{BASE}/{department.id}", json={"name": "Taken"}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_NAME_TAKEN"

    def test_deactivates_a_department(self, client, headers, make_department):
        department = make_department()

        res = client.patch(f"{BASE}/{department.id}", json={"isActive": False}, headers=headers)

        assert res.status_code == 200
        assert res.json()["isActive"] is False

    def test_reactivates_a_department(self, client, headers, make_department):
        department = make_department(is_active=False)

        res = client.patch(f"{BASE}/{department.id}", json={"isActive": True}, headers=headers)

        assert res.status_code == 200
        assert res.json()["isActive"] is True

    def test_setting_the_state_it_already_has_is_not_an_error(self, client, headers, make_department):
        on = make_department("On")
        off = make_department("Off", is_active=False)

        assert client.patch(f"{BASE}/{on.id}", json={"isActive": True}, headers=headers).status_code == 200
        assert client.patch(f"{BASE}/{off.id}", json={"isActive": False}, headers=headers).status_code == 200

    def test_refuses_to_deactivate_a_department_with_active_users(
        self, client, headers, make_department, make_user
    ):
        department = make_department()
        make_user(department_id=department.id)

        res = client.patch(f"{BASE}/{department.id}", json={"isActive": False}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_HAS_ACTIVE_USERS"

    def test_inactive_users_do_not_block_deactivation(self, client, headers, make_department, make_user):
        department = make_department()
        make_user(department_id=department.id, is_active=False)

        res = client.patch(f"{BASE}/{department.id}", json={"isActive": False}, headers=headers)

        assert res.status_code == 200

    def test_refuses_to_deactivate_a_department_with_active_children(self, client, headers, make_department):
        parent = make_department("Parent")
        make_department("Child", parent=parent)

        res = client.patch(f"{BASE}/{parent.id}", json={"isActive": False}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_HAS_ACTIVE_CHILDREN"

    def test_inactive_children_do_not_block_deactivation(self, client, headers, make_department):
        parent = make_department("Parent")
        make_department("Child", parent=parent, is_active=False)

        res = client.patch(f"{BASE}/{parent.id}", json={"isActive": False}, headers=headers)

        assert res.status_code == 200

    def test_refuses_to_reactivate_under_an_inactive_parent(self, client, headers, make_department):
        parent = make_department("Parent", is_active=False)
        child = make_department("Child", parent=parent, is_active=False)

        res = client.patch(f"{BASE}/{child.id}", json={"isActive": True}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_PARENT_INACTIVE"

    def test_refuses_to_reactivate_when_an_active_sibling_took_the_name(self, client, headers, make_department):
        old = make_department("Sales", is_active=False)
        make_department("Sales")

        res = client.patch(f"{BASE}/{old.id}", json={"isActive": True}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_NAME_TAKEN"

    def test_renames_and_deactivates_in_one_request(self, client, headers, make_department):
        department = make_department("Old")

        res = client.patch(f"{BASE}/{department.id}", json={"name": "New", "isActive": False}, headers=headers)

        assert res.status_code == 200
        assert res.json()["name"] == "New"
        assert res.json()["isActive"] is False

    def test_renames_and_reactivates_in_one_request(self, client, headers, make_department):
        make_department("Sales")
        old = make_department("Old", is_active=False)

        res = client.patch(f"{BASE}/{old.id}", json={"name": "Sales 2", "isActive": True}, headers=headers)

        assert res.status_code == 200
        assert res.json()["name"] == "Sales 2"
        assert res.json()["isActive"] is True

    def test_reactivation_checks_the_new_name_not_the_old_one(self, client, headers, make_department):
        make_department("Sales")
        old = make_department("Sales", is_active=False)

        res = client.patch(f"{BASE}/{old.id}", json={"name": "Sales Old", "isActive": True}, headers=headers)

        assert res.status_code == 200

    def test_saves_nothing_when_one_part_of_the_request_fails(
        self, client, headers, db, make_department, make_user
    ):
        department = make_department("Keep")
        make_user(department_id=department.id)

        res = client.patch(f"{BASE}/{department.id}", json={"name": "Lost", "isActive": False}, headers=headers)

        assert res.status_code == 409
        db.expire_all()
        saved = db.get(Department, department.id)
        assert saved.name == "Keep"
        assert saved.is_active is True

    def test_returns_404_for_an_unknown_department(self, client, headers):
        res = client.patch(f"{BASE}/999999", json={"name": "X"}, headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "DEPARTMENT_NOT_FOUND"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": None},
            {"isActive": None},
            {"name": ""},
            {"name": "x" * 121},
            {"isActive": "yes"},
            {"isActive": "true"},
            {"isActive": 1},
            {"unknown": 1},
        ],
    )
    def test_rejects_an_invalid_body(self, client, headers, make_department, body):
        department = make_department()

        res = client.patch(f"{BASE}/{department.id}", json=body, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"

    def test_the_old_deactivate_and_reactivate_routes_are_gone(self, client, headers, make_department):
        department = make_department()

        assert client.post(f"{BASE}/{department.id}/deactivate", headers=headers).status_code == 404
        assert client.post(f"{BASE}/{department.id}/reactivate", headers=headers).status_code == 404


class TestChangeParent:
    def test_moves_a_department_under_a_new_parent(self, client, headers, make_department):
        a = make_department("A")
        b = make_department("B")

        res = client.put(f"{BASE}/{b.id}/parent", json={"parentId": a.id}, headers=headers)

        assert res.status_code == 200
        assert res.json()["parentId"] == a.id

    def test_moves_a_department_to_the_top_level_with_null(self, client, headers, make_department):
        root = make_department("Root")
        child = make_department("Child", parent=root)

        res = client.put(f"{BASE}/{child.id}/parent", json={"parentId": None}, headers=headers)

        assert res.status_code == 200
        assert res.json()["parentId"] is None

    def test_keeps_the_children_of_the_moved_department(self, client, headers, make_department):
        a = make_department("A")
        b = make_department("B")
        make_department("B child", parent=b)

        client.put(f"{BASE}/{b.id}/parent", json={"parentId": a.id}, headers=headers)
        tree = client.get(f"{BASE}/tree", headers=headers).json()

        assert tree[0]["name"] == "A"
        assert tree[0]["children"][0]["name"] == "B"
        assert tree[0]["children"][0]["children"][0]["name"] == "B child"

    def test_the_same_parent_is_not_an_error(self, client, headers, make_department):
        root = make_department("Root")
        child = make_department("Child", parent=root)

        res = client.put(f"{BASE}/{child.id}/parent", json={"parentId": root.id}, headers=headers)

        assert res.status_code == 200

    def test_refuses_to_move_a_department_under_itself(self, client, headers, make_department):
        department = make_department()

        res = client.put(f"{BASE}/{department.id}/parent", json={"parentId": department.id}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_LOOP"

    def test_refuses_to_move_a_department_under_its_child(self, client, headers, make_department):
        parent = make_department("Parent")
        child = make_department("Child", parent=parent)

        res = client.put(f"{BASE}/{parent.id}/parent", json={"parentId": child.id}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_LOOP"

    def test_refuses_to_move_a_department_under_a_deep_descendant(self, client, headers, make_department):
        a = make_department("A")
        b = make_department("B", parent=a)
        c = make_department("C", parent=b)

        res = client.put(f"{BASE}/{a.id}/parent", json={"parentId": c.id}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_LOOP"

    def test_refuses_an_unknown_parent(self, client, headers, make_department):
        department = make_department()

        res = client.put(f"{BASE}/{department.id}/parent", json={"parentId": 999999}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "PARENT_NOT_FOUND"

    def test_refuses_an_inactive_parent(self, client, headers, make_department):
        parent = make_department("Old", is_active=False)
        department = make_department("Mine")

        res = client.put(f"{BASE}/{department.id}/parent", json={"parentId": parent.id}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_INACTIVE"

    def test_refuses_a_move_that_clashes_with_a_name_under_the_new_parent(self, client, headers, make_department):
        a = make_department("A")
        make_department("Support", parent=a)
        other = make_department("Support")

        res = client.put(f"{BASE}/{other.id}/parent", json={"parentId": a.id}, headers=headers)

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_NAME_TAKEN"

    def test_returns_404_for_an_unknown_department(self, client, headers):
        res = client.put(f"{BASE}/999999/parent", json={"parentId": None}, headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "DEPARTMENT_NOT_FOUND"

    @pytest.mark.parametrize("body", [{}, {"parentId": 0}, {"parentId": "1"}, {"parentId": 1.0}, {"parentId": 1, "x": 2}])
    def test_rejects_an_invalid_body(self, client, headers, make_department, body):
        department = make_department()

        res = client.put(f"{BASE}/{department.id}/parent", json=body, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"


class TestMoveUsers:
    def test_moves_the_active_users_and_returns_the_count(self, client, headers, db, make_department, make_user):
        source = make_department("Source")
        target = make_department("Target")
        first = make_user(email="a@example.test", department_id=source.id)
        second = make_user(email="b@example.test", department_id=source.id)

        res = client.post(
            f"{BASE}/{source.id}/move-users", json={"targetDepartmentId": target.id}, headers=headers
        )

        assert res.status_code == 200
        assert res.json() == {"movedCount": 2}
        db.expire_all()
        assert db.get(type(first), first.id).department_id == target.id
        assert db.get(type(second), second.id).department_id == target.id

    def test_leaves_inactive_users_where_they_are(self, client, headers, db, make_department, make_user):
        source = make_department("Source")
        target = make_department("Target")
        active = make_user(email="a@example.test", department_id=source.id)
        inactive = make_user(email="b@example.test", department_id=source.id, is_active=False)

        res = client.post(
            f"{BASE}/{source.id}/move-users", json={"targetDepartmentId": target.id}, headers=headers
        )

        assert res.json() == {"movedCount": 1}
        db.expire_all()
        assert db.get(type(active), active.id).department_id == target.id
        assert db.get(type(inactive), inactive.id).department_id == source.id

    def test_returns_zero_when_there_is_nobody_to_move(self, client, headers, make_department):
        source = make_department("Source")
        target = make_department("Target")

        res = client.post(
            f"{BASE}/{source.id}/move-users", json={"targetDepartmentId": target.id}, headers=headers
        )

        assert res.status_code == 200
        assert res.json() == {"movedCount": 0}

    def test_lets_the_emptied_department_be_deactivated(self, client, headers, make_department, make_user):
        source = make_department("Source")
        target = make_department("Target")
        make_user(department_id=source.id)

        client.post(f"{BASE}/{source.id}/move-users", json={"targetDepartmentId": target.id}, headers=headers)
        res = client.patch(f"{BASE}/{source.id}", json={"isActive": False}, headers=headers)

        assert res.status_code == 200

    def test_refuses_the_same_department_as_target(self, client, headers, make_department):
        department = make_department()

        res = client.post(
            f"{BASE}/{department.id}/move-users", json={"targetDepartmentId": department.id}, headers=headers
        )

        assert res.status_code == 400
        assert error_code(res) == "SAME_DEPARTMENT"

    def test_refuses_an_unknown_target(self, client, headers, make_department):
        source = make_department()

        res = client.post(f"{BASE}/{source.id}/move-users", json={"targetDepartmentId": 999999}, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "TARGET_NOT_FOUND"

    def test_refuses_an_inactive_target(self, client, headers, make_department):
        source = make_department("Source")
        target = make_department("Target", is_active=False)

        res = client.post(
            f"{BASE}/{source.id}/move-users", json={"targetDepartmentId": target.id}, headers=headers
        )

        assert res.status_code == 409
        assert error_code(res) == "DEPARTMENT_INACTIVE"

    def test_returns_404_for_an_unknown_source(self, client, headers, make_department):
        target = make_department()

        res = client.post(f"{BASE}/999999/move-users", json={"targetDepartmentId": target.id}, headers=headers)

        assert res.status_code == 404
        assert error_code(res) == "DEPARTMENT_NOT_FOUND"

    @pytest.mark.parametrize(
        "body", [{}, {"targetDepartmentId": 0}, {"targetDepartmentId": "2"}, {"targetDepartmentId": None}]
    )
    def test_rejects_an_invalid_body(self, client, headers, make_department, body):
        source = make_department()

        res = client.post(f"{BASE}/{source.id}/move-users", json=body, headers=headers)

        assert res.status_code == 400
        assert error_code(res) == "VALIDATION_ERROR"
