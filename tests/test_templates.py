# ruff: noqa: F811
# The fixtures are imported by name, and each test takes them as arguments.
import pytest
from sqlalchemy import select

from app.models import CycleStatus, Template, TemplateQuestion
from app.services import templates as template_service
from tests.bank_helpers import (  # noqa: F401
    admin,
    auth,
    bank,
    make_cycle,
    order,
    positions,
    super_admin,
    template_ids,
)
from tests.conftest import bearer

TEMPLATES = "/api/templates"


def error(res) -> dict:
    return res.json()["error"]


@pytest.fixture
def form(bank, make_cycle):
    """A manager template in a draft cycle with two criteria: C1, C2 (Communication), T1, T2 (Teamwork)."""

    class Form:
        cycle = make_cycle()
        communication = bank.criterion("Communication")
        teamwork = bank.criterion("Teamwork")
        c1 = bank.question(communication, "C1.")
        c2 = bank.question(communication, "C2.")
        t1 = bank.question(teamwork, "T1.")
        t2 = bank.question(teamwork, "T2 as free text.", kind="text")
        id = bank.use_in(cycle, c1, c2, t1, t2)
        url = f"{TEMPLATES}/{id}"

    return Form


class TestWhoMayUseTemplates:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/cycles/1/templates"),
            ("get", f"{TEMPLATES}/1"),
            ("patch", f"{TEMPLATES}/1"),
            ("post", f"{TEMPLATES}/1/questions"),
            ("delete", f"{TEMPLATES}/1/questions/1"),
            ("patch", f"{TEMPLATES}/1/questions/1"),
            ("post", f"{TEMPLATES}/1/questions/1/replace"),
            ("put", f"{TEMPLATES}/1/criteria/1/question-order"),
            ("put", f"{TEMPLATES}/1/criterion-order"),
            ("post", f"{TEMPLATES}/1/copy-from"),
        ],
    )
    def test_only_admins(self, client, employee, super_admin, method, path):
        call = getattr(client, method)

        assert call(path).status_code == 401
        assert call(path, headers=bearer(employee)).status_code == 403
        # T11: the super admin cannot manage templates.
        assert call(path, headers=bearer(super_admin)).status_code == 403


class TestListTemplates:
    def test_lists_the_four_templates_of_a_cycle(self, client, auth, bank, make_cycle):
        cycle = make_cycle()
        question = bank.question(bank.criterion())
        bank.use_in(cycle, question, review_type="peer")

        res = client.get(f"/api/cycles/{cycle.id}/templates", headers=auth)

        assert res.status_code == 200
        assert [(item["reviewType"], item["name"], item["questionCount"]) for item in res.json()] == [
            ("self", "Self review", 0),
            ("manager", "Manager review", 0),
            ("peer", "Peer review", 1),
            ("upward", "Upward review", 0),
        ]
        assert {item["cycleId"] for item in res.json()} == {cycle.id}

    def test_lists_only_the_templates_of_that_cycle(self, client, auth, make_cycle):
        first, second = make_cycle(), make_cycle()

        ids = {item["id"] for item in client.get(f"/api/cycles/{first.id}/templates", headers=auth).json()}
        other = {item["id"] for item in client.get(f"/api/cycles/{second.id}/templates", headers=auth).json()}

        assert len(ids) == 4
        assert ids.isdisjoint(other)

    def test_unknown_cycle_is_404(self, client, auth):
        res = client.get("/api/cycles/999/templates", headers=auth)

        assert res.status_code == 404
        assert error(res)["code"] == "CYCLE_NOT_FOUND"


class TestViewTemplate:
    def test_shows_the_questions_grouped_by_criterion_in_order(self, client, auth, form):
        res = client.get(form.url, headers=auth)

        assert res.status_code == 200
        assert res.json() == {
            "id": form.id,
            "cycleId": form.cycle.id,
            "reviewType": "manager",
            "name": "Manager review",
            "questionCount": 4,
            "cycleStatus": "draft",
            "editable": True,
            "copiedFromId": None,
            "sections": [
                {
                    "criterion": {"id": form.communication, "name": "Communication"},
                    "questions": [
                        {"questionId": form.c1, "position": 1, "text": "C1.", "kind": "rating", "weight": 1, "archived": False},
                        {"questionId": form.c2, "position": 2, "text": "C2.", "kind": "rating", "weight": 1, "archived": False},
                    ],
                },
                {
                    "criterion": {"id": form.teamwork, "name": "Teamwork"},
                    "questions": [
                        {"questionId": form.t1, "position": 3, "text": "T1.", "kind": "rating", "weight": 1, "archived": False},
                        {
                            "questionId": form.t2,
                            "position": 4,
                            "text": "T2 as free text.",
                            "kind": "text",
                            "weight": None,
                            "archived": False,
                        },
                    ],
                },
            ],
        }

    def test_a_template_of_an_opened_cycle_can_still_be_read(self, client, auth, bank, form):
        bank.set_status(form.cycle, CycleStatus.finalized)

        body = client.get(form.url, headers=auth).json()

        assert (body["cycleStatus"], body["editable"]) == ("finalized", False)
        assert order(body) == [form.c1, form.c2, form.t1, form.t2]

    def test_unknown_template_is_404(self, client, auth):
        res = client.get(f"{TEMPLATES}/999", headers=auth)

        assert res.status_code == 404
        assert error(res)["code"] == "TEMPLATE_NOT_FOUND"


class TestOnlyDraftCyclesCanChange:
    """T2: every change is refused once the cycle is open, closed or finalized."""

    @pytest.mark.parametrize("status", [CycleStatus.open, CycleStatus.closed, CycleStatus.finalized])
    def test_every_write_is_refused(self, client, auth, bank, form, make_cycle, status):
        spare = bank.question(form.communication, "Spare.")
        source = bank.template(make_cycle())
        bank.set_status(form.cycle, status)
        writes = [
            ("patch", form.url, {"name": "Renamed"}),
            ("post", f"{form.url}/questions", {"questionId": spare}),
            ("delete", f"{form.url}/questions/{form.c1}", None),
            ("patch", f"{form.url}/questions/{form.c1}", {"weight": 3}),
            ("post", f"{form.url}/questions/{form.c1}/replace", {"questionId": spare}),
            ("put", f"{form.url}/criteria/{form.communication}/question-order", {"questionIds": [form.c2, form.c1]}),
            ("put", f"{form.url}/criterion-order", {"criterionIds": [form.teamwork, form.communication]}),
            ("post", f"{form.url}/copy-from", {"sourceTemplateId": source}),
        ]

        for method, url, body in writes:
            res = client.request(method, url, headers=auth, json=body)
            assert res.status_code == 409, (method, url)
            assert error(res)["code"] == "TEMPLATE_LOCKED"

        after = bank.get_template(form.id)
        assert after["name"] == "Manager review"
        assert order(after) == [form.c1, form.c2, form.t1, form.t2]


class TestRenameTemplate:
    def test_renames(self, client, auth, form):
        res = client.patch(form.url, headers=auth, json={"name": "  Manager form 2026 "})

        assert res.status_code == 200
        assert res.json()["name"] == "Manager form 2026"

    @pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 151}, {"name": "Ok", "reviewType": "peer"}])
    def test_validates_the_body(self, client, auth, form, body):
        assert client.patch(form.url, headers=auth, json=body).status_code == 400

    def test_unknown_template_is_404(self, client, auth):
        assert client.patch(f"{TEMPLATES}/999", headers=auth, json={"name": "Any"}).status_code == 404


class TestAddQuestion:
    def test_goes_to_the_end_of_its_criterions_section(self, client, auth, bank, form):
        c3 = bank.question(form.communication, "C3.")

        res = bank.add(form.id, c3)

        assert res.status_code == 201
        assert order(res.json()) == [form.c1, form.c2, c3, form.t1, form.t2]
        assert positions(res.json()) == [1, 2, 3, 4, 5]

    def test_a_new_criterion_starts_a_section_at_the_end(self, client, auth, bank, form):
        ownership = bank.criterion("Ownership")
        o1 = bank.question(ownership, "O1.")

        body = bank.add(form.id, o1).json()

        assert order(body) == [form.c1, form.c2, form.t1, form.t2, o1]
        assert [section["criterion"]["name"] for section in body["sections"]] == ["Communication", "Teamwork", "Ownership"]

    def test_a_rating_question_gets_weight_1_unless_one_is_sent(self, client, auth, bank, form):
        default = bank.question(form.communication, "Default weight.")
        heavy = bank.question(form.communication, "Heavy.")

        bank.add(form.id, default)
        body = bank.add(form.id, heavy, weight=5).json()

        weights = {q["questionId"]: q["weight"] for s in body["sections"] for q in s["questions"]}
        assert (weights[default], weights[heavy]) == (1, 5)

    def test_a_text_question_has_no_weight(self, client, auth, bank, form):
        text = bank.question(form.communication, "Free text.", kind="text")

        refused = bank.add(form.id, text, weight=2)
        accepted = bank.add(form.id, text)

        assert refused.status_code == 400
        assert error(refused)["code"] == "TEXT_QUESTION_HAS_NO_WEIGHT"
        assert accepted.status_code == 201
        weights = {q["questionId"]: q["weight"] for s in accepted.json()["sections"] for q in s["questions"]}
        assert weights[text] is None

    def test_a_question_appears_at_most_once(self, client, auth, bank, form):
        res = bank.add(form.id, form.c1)

        assert res.status_code == 409
        assert error(res)["code"] == "QUESTION_ALREADY_IN_TEMPLATE"
        assert order(bank.get_template(form.id)) == [form.c1, form.c2, form.t1, form.t2]

    def test_the_same_question_can_be_in_other_templates(self, client, auth, bank, form):
        peer = bank.template(form.cycle, "peer")

        assert bank.add(peer, form.c1).status_code == 201

    def test_adding_an_archived_question_unarchives_it(self, client, auth, bank, form):
        old = bank.question(form.communication, "Old.")
        client.post(f"/api/questions/{old}/archive", headers=auth)

        assert bank.add(form.id, old).status_code == 201
        assert client.get(f"/api/questions/{old}", headers=auth).json()["archived"] is False

    def test_refuses_an_unknown_question(self, client, auth, form):
        res = client.post(f"{form.url}/questions", headers=auth, json={"questionId": 999})

        assert res.status_code == 400
        assert error(res)["code"] == "QUESTION_NOT_FOUND"

    @pytest.mark.parametrize(
        "body", [{}, {"questionId": "1"}, {"questionId": 1, "weight": 0}, {"questionId": 1, "weight": "2"}, {"questionId": 1, "weight": 1.5}]
    )
    def test_validates_the_body(self, client, auth, form, body):
        assert client.post(f"{form.url}/questions", headers=auth, json=body).status_code == 400


class TestRemoveQuestion:
    def test_removing_closes_the_gap(self, client, auth, form):
        res = client.delete(f"{form.url}/questions/{form.c2}", headers=auth)

        assert res.status_code == 200
        assert order(res.json()) == [form.c1, form.t1, form.t2]
        assert positions(res.json()) == [1, 2, 3]

    def test_removing_the_last_question_of_a_criterion_removes_its_section(self, client, auth, form):
        client.delete(f"{form.url}/questions/{form.c1}", headers=auth)
        body = client.delete(f"{form.url}/questions/{form.c2}", headers=auth).json()

        assert [section["criterion"]["name"] for section in body["sections"]] == ["Teamwork"]
        assert positions(body) == [1, 2]

    def test_the_question_stays_in_the_bank(self, client, auth, form):
        client.delete(f"{form.url}/questions/{form.c1}", headers=auth)

        assert client.get(f"/api/questions/{form.c1}", headers=auth).status_code == 200

    def test_a_question_that_is_not_in_the_template_is_404(self, client, auth, bank, form):
        other = bank.question(form.communication, "Not in the form.")

        res = client.delete(f"{form.url}/questions/{other}", headers=auth)

        assert res.status_code == 404
        assert error(res)["code"] == "QUESTION_NOT_IN_TEMPLATE"


class TestReorderQuestions:
    def _put(self, client, auth, form, criterion_id, ids):
        return client.put(
            f"{form.url}/criteria/{criterion_id}/question-order", headers=auth, json={"questionIds": ids}
        )

    def test_changes_the_order_inside_one_section_only(self, client, auth, bank, form):
        c3 = bank.question(form.communication, "C3.")
        bank.add(form.id, c3)

        res = self._put(client, auth, form, form.communication, [c3, form.c1, form.c2])

        assert res.status_code == 200
        assert order(res.json()) == [c3, form.c1, form.c2, form.t1, form.t2]
        assert positions(res.json()) == [1, 2, 3, 4, 5]

    def test_a_later_section_keeps_its_numbers(self, client, auth, form):
        body = self._put(client, auth, form, form.teamwork, [form.t2, form.t1]).json()

        assert order(body) == [form.c1, form.c2, form.t2, form.t1]

    @pytest.mark.parametrize("pick", ["missing one", "extra one", "from another section"])
    def test_needs_exactly_the_questions_of_that_section(self, client, auth, bank, form, pick):
        ids = {
            "missing one": [form.c1],
            "extra one": [form.c1, form.c2, bank.question(form.communication, "Not in the form.")],
            "from another section": [form.c1, form.t1],
        }[pick]

        res = self._put(client, auth, form, form.communication, ids)

        assert res.status_code == 400
        assert error(res)["code"] == "ORDER_MISMATCH"

    def test_refuses_a_repeated_id(self, client, auth, form):
        res = self._put(client, auth, form, form.communication, [form.c1, form.c1])

        assert res.status_code == 400
        assert error(res)["code"] == "VALIDATION_ERROR"

    def test_a_criterion_with_no_question_in_the_template_is_404(self, client, auth, bank, form):
        ownership = bank.criterion("Ownership")

        res = self._put(client, auth, form, ownership, [form.c1])

        assert res.status_code == 404
        assert error(res)["code"] == "CRITERION_NOT_IN_TEMPLATE"


class TestReorderCriteria:
    def _put(self, client, auth, form, ids):
        return client.put(f"{form.url}/criterion-order", headers=auth, json={"criterionIds": ids})

    def test_moves_whole_sections_and_renumbers(self, client, auth, form):
        res = self._put(client, auth, form, [form.teamwork, form.communication])

        assert res.status_code == 200
        assert order(res.json()) == [form.t1, form.t2, form.c1, form.c2]
        assert positions(res.json()) == [1, 2, 3, 4]

    def test_a_question_added_later_still_joins_its_section(self, client, auth, bank, form):
        self._put(client, auth, form, [form.teamwork, form.communication])
        t3 = bank.question(form.teamwork, "T3.")

        assert order(bank.add(form.id, t3).json()) == [form.t1, form.t2, t3, form.c1, form.c2]

    @pytest.mark.parametrize("pick", ["missing one", "unknown one"])
    def test_needs_exactly_the_criteria_of_the_template(self, client, auth, bank, form, pick):
        ids = {
            "missing one": [form.teamwork],
            "unknown one": [form.teamwork, form.communication, bank.criterion("Ownership")],
        }[pick]

        res = self._put(client, auth, form, ids)

        assert res.status_code == 400
        assert error(res)["code"] == "ORDER_MISMATCH"


class TestChangeWeight:
    def test_changes_the_weight_of_a_rating_question(self, client, auth, form):
        res = client.patch(f"{form.url}/questions/{form.c2}", headers=auth, json={"weight": 3})

        assert res.status_code == 200
        weights = {q["questionId"]: q["weight"] for s in res.json()["sections"] for q in s["questions"]}
        assert weights == {form.c1: 1, form.c2: 3, form.t1: 1, form.t2: None}

    def test_a_weight_belongs_to_one_template_only(self, client, auth, bank, form):
        peer = bank.template(form.cycle, "peer")
        bank.add(peer, form.c1)

        client.patch(f"{form.url}/questions/{form.c1}", headers=auth, json={"weight": 4})

        assert bank.get_template(peer)["sections"][0]["questions"][0]["weight"] == 1

    def test_a_text_question_has_no_weight(self, client, auth, form):
        res = client.patch(f"{form.url}/questions/{form.t2}", headers=auth, json={"weight": 2})

        assert res.status_code == 400
        assert error(res)["code"] == "TEXT_QUESTION_HAS_NO_WEIGHT"

    @pytest.mark.parametrize("weight", [0, -1, 1.5, "2", None])
    def test_the_weight_is_a_whole_number_1_or_more(self, client, auth, form, weight):
        res = client.patch(f"{form.url}/questions/{form.c1}", headers=auth, json={"weight": weight})

        assert res.status_code == 400

    def test_a_question_that_is_not_in_the_template_is_404(self, client, auth, bank, form):
        other = bank.question(form.communication, "Not in the form.")

        assert client.patch(f"{form.url}/questions/{other}", headers=auth, json={"weight": 2}).status_code == 404


class TestReplaceQuestion:
    def _replace(self, client, auth, form, old, new):
        return client.post(f"{form.url}/questions/{old}/replace", headers=auth, json={"questionId": new})

    def test_keeps_the_place_and_the_weight(self, client, auth, bank, form):
        client.patch(f"{form.url}/questions/{form.c1}", headers=auth, json={"weight": 4})
        reworded = bank.question(form.communication, "C1 reworded.")

        res = self._replace(client, auth, form, form.c1, reworded)

        assert res.status_code == 200
        assert order(res.json()) == [reworded, form.c2, form.t1, form.t2]
        assert res.json()["sections"][0]["questions"][0] == {
            "questionId": reworded,
            "position": 1,
            "text": "C1 reworded.",
            "kind": "rating",
            "weight": 4,
            "archived": False,
        }

    def test_a_replacement_of_another_criterion_moves_to_the_end_of_that_section(self, client, auth, bank, form):
        t3 = bank.question(form.teamwork, "T3.")

        body = self._replace(client, auth, form, form.c1, t3).json()

        assert order(body) == [form.c2, form.t1, form.t2, t3]
        assert positions(body) == [1, 2, 3, 4]

    def test_works_as_the_swap_after_copying_a_question(self, client, auth, form):
        copy = client.post(f"/api/questions/{form.c1}/copy", headers=auth, json={"text": "C1, clearer."}).json()["id"]

        assert order(self._replace(client, auth, form, form.c1, copy).json())[0] == copy

    def test_the_replacement_must_be_the_same_kind(self, client, auth, bank, form):
        text = bank.question(form.communication, "Free text.", kind="text")

        res = self._replace(client, auth, form, form.c1, text)

        assert res.status_code == 400
        assert error(res)["code"] == "QUESTION_KIND_MISMATCH"

    def test_the_replacement_must_not_already_be_in_the_template(self, client, auth, form):
        res = self._replace(client, auth, form, form.c1, form.c2)

        assert res.status_code == 409
        assert error(res)["code"] == "QUESTION_ALREADY_IN_TEMPLATE"

    def test_a_question_cannot_replace_itself(self, client, auth, form):
        res = self._replace(client, auth, form, form.c1, form.c1)

        assert res.status_code == 400
        assert error(res)["code"] == "SAME_QUESTION"

    def test_refuses_an_unknown_replacement(self, client, auth, form):
        res = self._replace(client, auth, form, form.c1, 999)

        assert res.status_code == 400
        assert error(res)["code"] == "QUESTION_NOT_FOUND"

    def test_the_question_to_replace_must_be_in_the_template(self, client, auth, bank, form):
        other = bank.question(form.communication, "Not in the form.")
        another = bank.question(form.communication, "Another.")

        assert self._replace(client, auth, form, other, another).status_code == 404

    def test_an_archived_replacement_is_unarchived(self, client, auth, bank, form):
        old = bank.question(form.communication, "Old.")
        client.post(f"/api/questions/{old}/archive", headers=auth)

        self._replace(client, auth, form, form.c1, old)

        assert client.get(f"/api/questions/{old}", headers=auth).json()["archived"] is False


class TestCopyFromAnotherCycle:
    def _copy(self, client, auth, target: int, source: int):
        return client.post(f"{TEMPLATES}/{target}/copy-from", headers=auth, json={"sourceTemplateId": source})

    def test_replaces_the_questions_order_and_weights(self, client, auth, bank, form, make_cycle):
        client.patch(f"{form.url}/questions/{form.c2}", headers=auth, json={"weight": 3})
        client.put(f"{form.url}/criterion-order", headers=auth, json={"criterionIds": [form.teamwork, form.communication]})
        target = bank.template(make_cycle())
        bank.add(target, bank.question(form.communication, "Will be replaced."))

        res = self._copy(client, auth, target, form.id)

        assert res.status_code == 200
        body = res.json()
        assert body["copiedFromId"] == form.id
        assert order(body) == [form.t1, form.t2, form.c1, form.c2]
        weights = {q["questionId"]: q["weight"] for s in body["sections"] for q in s["questions"]}
        assert weights == {form.t1: 1, form.t2: None, form.c1: 1, form.c2: 3}

    def test_the_source_is_left_unchanged_and_the_copy_is_independent(self, client, auth, bank, form, make_cycle):
        target = bank.template(make_cycle())
        self._copy(client, auth, target, form.id)

        client.delete(f"{TEMPLATES}/{target}/questions/{form.c1}", headers=auth)

        assert order(bank.get_template(form.id)) == [form.c1, form.c2, form.t1, form.t2]

    def test_the_source_may_belong_to_an_opened_cycle(self, client, auth, bank, form, make_cycle):
        bank.set_status(form.cycle, CycleStatus.finalized)
        target = bank.template(make_cycle())

        assert self._copy(client, auth, target, form.id).status_code == 200

    def test_keeps_the_review_type_of_its_source(self, client, auth, bank, form, make_cycle):
        peer = bank.template(make_cycle(), "peer")

        res = self._copy(client, auth, peer, form.id)

        assert res.status_code == 400
        assert error(res)["code"] == "REVIEW_TYPE_MISMATCH"

    def test_the_source_must_belong_to_another_cycle(self, client, auth, form):
        res = self._copy(client, auth, form.id, form.id)

        assert res.status_code == 400
        assert error(res)["code"] == "SAME_CYCLE"

    def test_refuses_an_unknown_source(self, client, auth, form):
        res = self._copy(client, auth, form.id, 999)

        assert res.status_code == 400
        assert error(res)["code"] == "SOURCE_TEMPLATE_NOT_FOUND"

    def test_copied_archived_questions_are_unarchived(self, client, auth, bank, form, make_cycle):
        client.post(f"/api/questions/{form.c1}/archive", headers=auth)
        target = bank.template(make_cycle())

        self._copy(client, auth, target, form.id)

        assert client.get(f"/api/questions/{form.c1}", headers=auth).json()["archived"] is False


class TestStepsForTheCycleCluster:
    """What the cycle steps will call: create, copy, delete and the readiness check (T9, CYC-2, CYC-8, C3)."""

    def test_a_new_cycle_gets_four_empty_templates(self, db, make_cycle):
        cycle = make_cycle()

        rows = db.scalars(select(Template).where(Template.cycle_id == cycle.id)).all()

        assert sorted(row.review_type.value for row in rows) == ["manager", "peer", "self", "upward"]
        assert db.scalar(select(TemplateQuestion).limit(1)) is None

    def test_a_cycle_cannot_get_two_templates_of_one_review_type(self, db, make_cycle):
        from sqlalchemy.exc import IntegrityError

        cycle = make_cycle()

        with pytest.raises(IntegrityError):
            template_service.create_default_templates(db, cycle)
        db.rollback()

    def test_copying_a_cycle_copies_its_templates(self, db, admin, client, auth, bank, form, make_cycle):
        client.patch(form.url, headers=auth, json={"name": "Manager form"})
        client.patch(f"{form.url}/questions/{form.c2}", headers=auth, json={"weight": 3})
        from datetime import date

        from app.models import Cycle, CycleType

        new_cycle = Cycle(
            name="Annual 2040", type=CycleType.annual, year=2040, start_date=date(2040, 1, 1), end_date=date(2040, 12, 31)
        )
        db.add(new_cycle)
        db.flush()

        template_service.copy_cycle_templates(db, admin.id, form.cycle, new_cycle)
        db.commit()

        copy = bank.get_template(template_ids(db, new_cycle)["manager"])
        assert (copy["name"], copy["copiedFromId"], copy["editable"]) == ("Manager form", form.id, True)
        assert order(copy) == [form.c1, form.c2, form.t1, form.t2]
        assert copy["sections"][0]["questions"][1]["weight"] == 3
        assert len(template_ids(db, new_cycle)) == 4

    def test_deleting_a_cycles_templates_removes_their_question_links(self, db, bank, form):
        template_service.delete_cycle_templates(db, form.cycle)
        db.commit()

        assert template_ids(db, form.cycle) == {}
        assert db.scalar(select(TemplateQuestion).limit(1)) is None
        # The questions themselves stay in the bank and are unused again.
        assert bank.client.delete(f"/api/questions/{form.c1}", headers=bank.headers).status_code == 204

    def test_readiness_lists_what_each_template_still_needs(self, db, bank, make_cycle):
        cycle = make_cycle()
        criterion = bank.criterion()
        bank.use_in(cycle, bank.question(criterion, "Rated."), review_type="self")
        bank.use_in(cycle, bank.question(criterion, "Only text.", kind="text"), review_type="peer")
        db.delete(db.get(Template, template_ids(db, cycle)["upward"]))
        db.commit()

        problems = template_service.readiness_problems(db, cycle)

        assert [(problem.code, problem.message) for problem in problems] == [
            ("TEMPLATE_EMPTY", "The manager template has no questions."),
            ("TEMPLATE_NO_RATING_QUESTION", "The peer template has no rating question."),
            ("TEMPLATE_MISSING", "The cycle has no upward template."),
        ]

    def test_a_cycle_with_a_rating_question_in_every_template_is_ready(self, db, bank, make_cycle):
        cycle = make_cycle()
        question = bank.question(bank.criterion())
        for review_type in ("self", "manager", "peer", "upward"):
            bank.use_in(cycle, question, review_type=review_type)

        assert template_service.readiness_problems(db, cycle) == []
