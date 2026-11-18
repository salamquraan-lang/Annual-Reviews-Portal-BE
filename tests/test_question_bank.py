# ruff: noqa: F811
# The fixtures are imported by name, and each test takes them as arguments.
import pytest

from app.models import CycleStatus, Question
from app.services import question_bank
from tests.bank_helpers import admin, auth, bank, make_cycle, order, super_admin  # noqa: F401
from tests.conftest import bearer

CRITERIA = "/api/criteria"
QUESTIONS = "/api/questions"


def error(res) -> dict:
    return res.json()["error"]


class TestWhoMayUseTheQuestionBank:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", CRITERIA),
            ("post", CRITERIA),
            ("get", f"{CRITERIA}/1"),
            ("patch", f"{CRITERIA}/1"),
            ("delete", f"{CRITERIA}/1"),
            ("get", QUESTIONS),
            ("post", QUESTIONS),
            ("get", f"{QUESTIONS}/1"),
            ("patch", f"{QUESTIONS}/1"),
            ("delete", f"{QUESTIONS}/1"),
            ("post", f"{QUESTIONS}/1/copy"),
            ("post", f"{QUESTIONS}/1/archive"),
            ("post", f"{QUESTIONS}/1/unarchive"),
        ],
    )
    def test_only_admins(self, client, employee, super_admin, method, path):
        call = getattr(client, method)

        assert call(path).status_code == 401
        assert call(path, headers=bearer(employee)).status_code == 403
        # Q7: the super admin cannot manage the question bank.
        assert call(path, headers=bearer(super_admin)).status_code == 403


class TestCreateCriterion:
    def test_creates_a_criterion(self, client, auth):
        res = client.post(CRITERIA, headers=auth, json={"name": "  Communication ", "description": " Shares clearly. "})

        assert res.status_code == 201
        body = res.json()
        assert body == {
            "id": body["id"],
            "name": "Communication",
            "description": "Shares clearly.",
            "questionCount": 0,
        }
        assert res.headers["location"] == f"/api/criteria/{body['id']}"

    def test_the_description_is_optional(self, client, auth):
        res = client.post(CRITERIA, headers=auth, json={"name": "Ownership"})

        assert res.status_code == 201
        assert res.json()["description"] is None

    @pytest.mark.parametrize("name", ["Team Work", "teamwork", "TEAM  WORK", " team work "])
    def test_names_are_unique_ignoring_case_and_spaces(self, client, auth, bank, name):
        bank.criterion("Teamwork")

        res = client.post(CRITERIA, headers=auth, json={"name": name})

        assert res.status_code == 409
        assert error(res)["code"] == "CRITERION_NAME_TAKEN"

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({}, "This field is required."),
            ({"name": "   "}, "Name is required."),
            ({"name": "x" * 121}, "Name must be at most 120 characters."),
            ({"name": "Ok", "description": "x" * 501}, "Description must be at most 500 characters."),
        ],
    )
    def test_validates_the_body(self, client, auth, body, message):
        res = client.post(CRITERIA, headers=auth, json=body)

        assert res.status_code == 400
        assert error(res)["details"][0]["message"] == message

    def test_refuses_unknown_fields(self, client, auth):
        res = client.post(CRITERIA, headers=auth, json={"name": "Ok", "reviewType": "peer"})

        assert res.status_code == 400


class TestReadCriteria:
    def test_lists_criteria_by_name_with_their_question_counts(self, client, auth, bank):
        teamwork = bank.criterion("Teamwork")
        communication = bank.criterion("Communication")
        bank.question(teamwork, "Helps teammates.")
        bank.question(teamwork, "Works with other teams.")

        res = client.get(CRITERIA, headers=auth)

        assert res.status_code == 200
        assert [(item["id"], item["questionCount"]) for item in res.json()["items"]] == [
            (communication, 0),
            (teamwork, 2),
        ]

    def test_views_one_criterion_with_its_questions(self, client, auth, bank):
        criterion = bank.criterion("Teamwork", "Works well with others.")
        first = bank.question(criterion, "Helps teammates.")
        second = bank.question(criterion, "What should they keep doing?", kind="text")
        client.post(f"{QUESTIONS}/{second}/archive", headers=auth)

        res = client.get(f"{CRITERIA}/{criterion}", headers=auth)

        assert res.status_code == 200
        assert res.json() == {
            "id": criterion,
            "name": "Teamwork",
            "description": "Works well with others.",
            "questionCount": 2,
            "questions": [
                {"id": first, "text": "Helps teammates.", "kind": "rating", "archived": False},
                {"id": second, "text": "What should they keep doing?", "kind": "text", "archived": True},
            ],
        }

    def test_unknown_criterion_is_404(self, client, auth):
        res = client.get(f"{CRITERIA}/999", headers=auth)

        assert res.status_code == 404
        assert error(res)["code"] == "CRITERION_NOT_FOUND"


class TestEditCriterion:
    def test_changes_only_the_fields_that_are_sent(self, client, auth, bank):
        criterion = bank.criterion("Teamwork", "Old text.")

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"name": "Collaboration"})

        assert res.status_code == 200
        assert res.json()["name"] == "Collaboration"
        assert res.json()["description"] == "Old text."

    def test_null_clears_the_description(self, client, auth, bank):
        criterion = bank.criterion("Teamwork", "Old text.")

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"description": None})

        assert res.status_code == 200
        assert res.json()["description"] is None

    def test_may_change_the_case_of_its_own_name(self, client, auth, bank):
        criterion = bank.criterion("teamwork")

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"name": "Team Work"})

        assert res.status_code == 200
        assert res.json()["name"] == "Team Work"

    def test_refuses_the_name_of_another_criterion(self, client, auth, bank):
        bank.criterion("Communication")
        criterion = bank.criterion("Teamwork")

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"name": "communication"})

        assert res.status_code == 409
        assert error(res)["code"] == "CRITERION_NAME_TAKEN"

    @pytest.mark.parametrize("body", [{}, {"name": None}, {"name": ""}])
    def test_validates_the_body(self, client, auth, bank, body):
        criterion = bank.criterion()

        assert client.patch(f"{CRITERIA}/{criterion}", headers=auth, json=body).status_code == 400

    @pytest.mark.parametrize("status", [CycleStatus.open, CycleStatus.closed, CycleStatus.finalized])
    def test_cannot_be_edited_once_an_opened_cycle_uses_it(self, client, auth, bank, make_cycle, status):
        criterion = bank.criterion("Teamwork")
        cycle = make_cycle()
        bank.use_in(cycle, bank.question(criterion))
        bank.set_status(cycle, status)

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"name": "Collaboration"})

        assert res.status_code == 409
        assert error(res)["code"] == "CRITERION_LOCKED"

    def test_can_be_edited_while_only_draft_cycles_use_it(self, client, auth, bank, make_cycle):
        criterion = bank.criterion("Teamwork")
        bank.use_in(make_cycle(), bank.question(criterion))

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"name": "Collaboration"})

        assert res.status_code == 200

    def test_sending_the_same_values_is_not_an_edit(self, client, auth, bank, make_cycle):
        criterion = bank.criterion("Teamwork")
        cycle = make_cycle()
        bank.use_in(cycle, bank.question(criterion))
        bank.set_status(cycle, CycleStatus.closed)

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"name": "Teamwork"})

        assert res.status_code == 200


class TestDeleteCriterion:
    def test_deletes_a_criterion_without_questions(self, client, auth, bank):
        criterion = bank.criterion()

        assert client.delete(f"{CRITERIA}/{criterion}", headers=auth).status_code == 204
        assert client.get(f"{CRITERIA}/{criterion}", headers=auth).status_code == 404

    def test_refuses_a_criterion_that_has_questions(self, client, auth, bank):
        criterion = bank.criterion()
        bank.question(criterion)

        res = client.delete(f"{CRITERIA}/{criterion}", headers=auth)

        assert res.status_code == 409
        assert error(res)["code"] == "CRITERION_HAS_QUESTIONS"

    def test_its_name_is_free_again(self, client, auth, bank):
        client.delete(f"{CRITERIA}/{bank.criterion('Teamwork')}", headers=auth)

        assert client.post(CRITERIA, headers=auth, json={"name": "Teamwork"}).status_code == 201


class TestCreateQuestion:
    def test_creates_a_question(self, client, auth, bank):
        criterion = bank.criterion("Communication")

        res = client.post(
            QUESTIONS, headers=auth, json={"criterionId": criterion, "text": " Explains ideas clearly. ", "kind": "rating"}
        )

        assert res.status_code == 201
        body = res.json()
        assert body == {
            "id": body["id"],
            "criterion": {"id": criterion, "name": "Communication"},
            "text": "Explains ideas clearly.",
            "kind": "rating",
            "archived": False,
            "archivedAt": None,
            "locked": False,
            "usage": [],
        }
        assert res.headers["location"] == f"/api/questions/{body['id']}"

    def test_refuses_an_unknown_criterion(self, client, auth):
        res = client.post(QUESTIONS, headers=auth, json={"criterionId": 999, "text": "Any.", "kind": "text"})

        assert res.status_code == 400
        assert error(res)["code"] == "CRITERION_NOT_FOUND"

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"kind": "scale"}, "kind"),
            ({"text": "  "}, "text"),
            ({"text": "x" * 501}, "text"),
            ({"criterionId": "1"}, "criterionId"),
            ({"criterionId": 0}, "criterionId"),
        ],
    )
    def test_validates_the_body(self, client, auth, bank, change, field):
        body = {"criterionId": bank.criterion(), "text": "Any.", "kind": "rating"} | change

        res = client.post(QUESTIONS, headers=auth, json=body)

        assert res.status_code == 400
        assert error(res)["details"][0]["field"] == field


class TestListQuestions:
    def test_lists_questions_in_pages(self, client, auth, bank):
        criterion = bank.criterion()
        ids = [bank.question(criterion, f"Question {n}.") for n in range(5)]

        res = client.get(QUESTIONS, headers=auth, params={"page": 2, "pageSize": 2})

        assert res.status_code == 200
        body = res.json()
        assert [item["id"] for item in body["items"]] == ids[2:4]
        assert (body["page"], body["pageSize"], body["total"]) == (2, 2, 5)

    def test_filters_by_criterion_and_kind(self, client, auth, bank):
        communication = bank.criterion("Communication")
        teamwork = bank.criterion("Teamwork")
        bank.question(communication, "Rated.")
        wanted = bank.question(communication, "Free text.", kind="text")
        bank.question(teamwork, "Other text.", kind="text")

        res = client.get(QUESTIONS, headers=auth, params={"criterionId": communication, "kind": "text"})

        assert [item["id"] for item in res.json()["items"]] == [wanted]

    def test_filters_by_used_or_unused(self, client, auth, bank, make_cycle):
        criterion = bank.criterion()
        used = bank.question(criterion, "Used.")
        unused = bank.question(criterion, "Unused.")
        bank.use_in(make_cycle(), used)

        def ids(value: str) -> list[int]:
            return [item["id"] for item in client.get(QUESTIONS, headers=auth, params={"used": value}).json()["items"]]

        assert ids("true") == [used]
        assert ids("false") == [unused]

    def test_searches_the_text_ignoring_case(self, client, auth, bank):
        criterion = bank.criterion()
        wanted = bank.question(criterion, "Meets 100% of deadlines.")
        bank.question(criterion, "Meets 100 deadlines.")

        def ids(search: str) -> list[int]:
            res = client.get(QUESTIONS, headers=auth, params={"search": search})
            return [item["id"] for item in res.json()["items"]]

        assert len(ids("DEADLINES")) == 2
        # "%" is searched as a plain character, not as a wildcard.
        assert ids("100%") == [wanted]

    def test_hides_archived_questions_unless_asked(self, client, auth, bank):
        criterion = bank.criterion()
        kept = bank.question(criterion, "Kept.")
        archived = bank.question(criterion, "Old.")
        client.post(f"{QUESTIONS}/{archived}/archive", headers=auth)

        default = client.get(QUESTIONS, headers=auth).json()
        with_archived = client.get(QUESTIONS, headers=auth, params={"showArchived": "true"}).json()

        assert [item["id"] for item in default["items"]] == [kept]
        assert [(item["id"], item["archived"]) for item in with_archived["items"]] == [(kept, False), (archived, True)]

    def test_each_row_shows_the_last_cycle_that_used_it(self, client, auth, bank, make_cycle):
        criterion = bank.criterion()
        question = bank.question(criterion, "Used twice.")
        never = bank.question(criterion, "Never used.")
        older = make_cycle(year=2024)
        newer = make_cycle(year=2025)
        bank.use_in(newer, question)
        bank.use_in(older, question)
        bank.set_status(older, CycleStatus.finalized)

        rows = {item["id"]: item for item in client.get(QUESTIONS, headers=auth).json()["items"]}

        assert rows[question]["used"] is True
        assert rows[question]["lastUsedCycle"] == {"id": newer.id, "name": "Annual 2025", "status": "draft"}
        assert rows[never]["used"] is False
        assert rows[never]["lastUsedCycle"] is None

    @pytest.mark.parametrize("params", [{"page": 0}, {"pageSize": 101}, {"kind": "scale"}, {"sort": "text"}])
    def test_validates_the_query(self, client, auth, params):
        assert client.get(QUESTIONS, headers=auth, params=params).status_code == 400


class TestViewQuestion:
    def test_shows_where_the_question_is_used(self, client, auth, bank, make_cycle):
        question = bank.question(bank.criterion())
        draft = make_cycle(year=2026)
        closed = make_cycle(year=2025)
        draft_template = bank.use_in(draft, question, review_type="peer")
        closed_template = bank.use_in(closed, question, review_type="self")
        bank.set_status(closed, CycleStatus.closed)

        res = client.get(f"{QUESTIONS}/{question}", headers=auth)

        assert res.status_code == 200
        assert res.json()["locked"] is True
        assert res.json()["usage"] == [
            {
                "templateId": draft_template,
                "templateName": "Peer review",
                "reviewType": "peer",
                "cycle": {"id": draft.id, "name": "Annual 2026", "status": "draft"},
            },
            {
                "templateId": closed_template,
                "templateName": "Self review",
                "reviewType": "self",
                "cycle": {"id": closed.id, "name": "Annual 2025", "status": "closed"},
            },
        ]

    def test_unknown_question_is_404(self, client, auth):
        res = client.get(f"{QUESTIONS}/999", headers=auth)

        assert res.status_code == 404
        assert error(res)["code"] == "QUESTION_NOT_FOUND"


class TestEditQuestion:
    def test_changes_only_the_fields_that_are_sent(self, client, auth, bank):
        criterion = bank.criterion()
        question = bank.question(criterion, "Old text.")

        res = client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"text": "New text."})

        assert res.status_code == 200
        assert (res.json()["text"], res.json()["kind"], res.json()["criterion"]["id"]) == ("New text.", "rating", criterion)

    def test_moves_a_question_to_another_criterion(self, client, auth, bank):
        question = bank.question(bank.criterion("Communication"))
        teamwork = bank.criterion("Teamwork")

        res = client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"criterionId": teamwork})

        assert res.json()["criterion"] == {"id": teamwork, "name": "Teamwork"}

    def test_refuses_an_unknown_criterion(self, client, auth, bank):
        question = bank.question(bank.criterion())

        res = client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"criterionId": 999})

        assert res.status_code == 400
        assert error(res)["code"] == "CRITERION_NOT_FOUND"

    @pytest.mark.parametrize("body", [{}, {"text": None}, {"kind": None}, {"criterionId": None}, {"text": ""}])
    def test_validates_the_body(self, client, auth, bank, body):
        question = bank.question(bank.criterion())

        assert client.patch(f"{QUESTIONS}/{question}", headers=auth, json=body).status_code == 400

    @pytest.mark.parametrize("status", [CycleStatus.open, CycleStatus.closed, CycleStatus.finalized])
    def test_cannot_be_edited_once_an_opened_cycle_uses_it(self, client, auth, bank, make_cycle, status):
        question = bank.question(bank.criterion(), "Original.")
        cycle = make_cycle()
        bank.use_in(cycle, question)
        bank.set_status(cycle, status)

        res = client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"text": "Changed."})

        assert res.status_code == 409
        assert error(res)["code"] == "QUESTION_LOCKED"
        assert client.get(f"{QUESTIONS}/{question}", headers=auth).json()["text"] == "Original."

    def test_an_edit_shows_in_every_draft_template_that_uses_it(self, client, auth, bank, make_cycle):
        question = bank.question(bank.criterion(), "Original.")
        first = bank.use_in(make_cycle(), question)
        second = bank.use_in(make_cycle(), question, review_type="peer")

        client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"text": "Changed."})

        for template_id in (first, second):
            assert bank.get_template(template_id)["sections"][0]["questions"][0]["text"] == "Changed."

    def test_a_new_criterion_moves_it_to_the_end_of_that_section_in_draft_templates(
        self, client, auth, bank, make_cycle
    ):
        communication = bank.criterion("Communication")
        teamwork = bank.criterion("Teamwork")
        c1 = bank.question(communication, "C1.")
        moving = bank.question(communication, "Moving.")
        c3 = bank.question(communication, "C3.")
        t1 = bank.question(teamwork, "T1.")
        template_id = bank.use_in(make_cycle(), c1, moving, c3, t1)

        client.patch(f"{QUESTIONS}/{moving}", headers=auth, json={"criterionId": teamwork})

        template = bank.get_template(template_id)
        assert order(template) == [c1, c3, t1, moving]
        assert [section["criterion"]["name"] for section in template["sections"]] == ["Communication", "Teamwork"]

    def test_a_new_kind_changes_the_weight_in_draft_templates(self, client, auth, bank, make_cycle):
        question = bank.question(bank.criterion(), kind="rating")
        template_id = bank.template(make_cycle())
        bank.add(template_id, question, weight=4)

        def weight():
            return bank.get_template(template_id)["sections"][0]["questions"][0]["weight"]

        client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"kind": "text"})
        assert weight() is None
        client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"kind": "rating"})
        assert weight() == 1


class TestCopyQuestion:
    def test_makes_a_new_question_with_the_same_content(self, client, auth, bank):
        criterion = bank.criterion()
        source = bank.question(criterion, "Original.", kind="text")

        res = client.post(f"{QUESTIONS}/{source}/copy", headers=auth)

        assert res.status_code == 201
        body = res.json()
        assert body["id"] != source
        assert (body["text"], body["kind"], body["criterion"]["id"]) == ("Original.", "text", criterion)
        assert res.headers["location"] == f"/api/questions/{body['id']}"

    def test_fields_in_the_body_replace_the_copied_ones(self, client, auth, bank):
        source = bank.question(bank.criterion("Communication"), "Original.")
        teamwork = bank.criterion("Teamwork")

        res = client.post(
            f"{QUESTIONS}/{source}/copy", headers=auth, json={"text": "Reworded.", "criterionId": teamwork}
        )

        assert res.status_code == 201
        assert (res.json()["text"], res.json()["kind"], res.json()["criterion"]["id"]) == ("Reworded.", "rating", teamwork)

    def test_a_locked_question_can_be_copied_and_the_copy_is_free(self, client, auth, bank, make_cycle):
        source = bank.question(bank.criterion(), "Original.")
        cycle = make_cycle()
        bank.use_in(cycle, source)
        bank.set_status(cycle, CycleStatus.finalized)
        client.post(f"{QUESTIONS}/{source}/archive", headers=auth)

        copy = client.post(f"{QUESTIONS}/{source}/copy", headers=auth).json()

        assert (copy["locked"], copy["archived"], copy["usage"]) == (False, False, [])
        assert client.patch(f"{QUESTIONS}/{copy['id']}", headers=auth, json={"text": "Changed."}).status_code == 200

    def test_unknown_question_is_404(self, client, auth):
        assert client.post(f"{QUESTIONS}/999/copy", headers=auth).status_code == 404


class TestDeleteQuestion:
    def test_deletes_a_question_that_no_template_uses(self, client, auth, bank):
        question = bank.question(bank.criterion())

        assert client.delete(f"{QUESTIONS}/{question}", headers=auth).status_code == 204
        assert client.get(f"{QUESTIONS}/{question}", headers=auth).status_code == 404

    def test_refuses_a_question_that_a_template_uses_even_in_a_draft_cycle(self, client, auth, bank, make_cycle):
        question = bank.question(bank.criterion())
        bank.use_in(make_cycle(), question)

        res = client.delete(f"{QUESTIONS}/{question}", headers=auth)

        assert res.status_code == 409
        assert error(res)["code"] == "QUESTION_IN_USE"

    def test_can_be_deleted_after_it_is_removed_from_the_template(self, client, auth, bank, make_cycle):
        question = bank.question(bank.criterion())
        template_id = bank.use_in(make_cycle(), question)
        client.delete(f"/api/templates/{template_id}/questions/{question}", headers=auth)

        assert client.delete(f"{QUESTIONS}/{question}", headers=auth).status_code == 204


class TestArchiveByHand:
    def test_archives_and_unarchives(self, client, auth, bank):
        question = bank.question(bank.criterion())

        archived = client.post(f"{QUESTIONS}/{question}/archive", headers=auth)
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        assert archived.json()["archivedAt"].endswith("Z")

        restored = client.post(f"{QUESTIONS}/{question}/unarchive", headers=auth)
        assert restored.status_code == 200
        assert (restored.json()["archived"], restored.json()["archivedAt"]) == (False, None)

    def test_archiving_twice_keeps_the_first_time(self, client, auth, bank):
        question = bank.question(bank.criterion())
        first = client.post(f"{QUESTIONS}/{question}/archive", headers=auth).json()["archivedAt"]

        again = client.post(f"{QUESTIONS}/{question}/archive", headers=auth)

        assert again.status_code == 200
        assert again.json()["archivedAt"] == first

    def test_is_allowed_even_when_an_opened_cycle_uses_the_question(self, client, auth, bank, make_cycle):
        question = bank.question(bank.criterion())
        cycle = make_cycle()
        template_id = bank.use_in(cycle, question)
        bank.set_status(cycle, CycleStatus.open)

        res = client.post(f"{QUESTIONS}/{question}/archive", headers=auth)

        assert res.status_code == 200
        # Q10: it stays in every template that uses it.
        assert order(bank.get_template(template_id)) == [question]
        assert client.post(f"{QUESTIONS}/{question}/unarchive", headers=auth).status_code == 200

    def test_unknown_question_is_404(self, client, auth):
        assert client.post(f"{QUESTIONS}/999/archive", headers=auth).status_code == 404
        assert client.post(f"{QUESTIONS}/999/unarchive", headers=auth).status_code == 404


class TestArchiveWhenACycleOpens:
    """QB-14. The step runs inside "open a cycle", so these tests call it the way that step will."""

    def _open(self, db, admin, bank, cycle) -> list[int]:
        bank.set_status(cycle, CycleStatus.open)
        archived = question_bank.archive_unused_questions(db, cycle, admin.id)
        db.commit()
        return archived

    def _is_archived(self, db, question_id: int) -> bool:
        db.expire_all()
        return db.get(Question, question_id).archived_at is not None

    def test_archives_a_question_that_the_last_3_opened_cycles_do_not_use(self, db, admin, bank, make_cycle):
        criterion = bank.criterion()
        old = bank.question(criterion, "Used long ago.")
        current = bank.question(criterion, "Still used.")
        first = make_cycle(year=2021)
        bank.use_in(first, old, current)
        bank.set_status(first, CycleStatus.finalized)
        for year in (2022, 2023):
            cycle = make_cycle(year=year)
            bank.use_in(cycle, current)
            bank.set_status(cycle, CycleStatus.finalized)
        opening = make_cycle(year=2024)
        bank.use_in(opening, current)

        archived = self._open(db, admin, bank, opening)

        assert archived == [old]
        assert self._is_archived(db, old)
        assert not self._is_archived(db, current)

    def test_the_cycle_being_opened_counts_as_one_of_the_3(self, db, admin, bank, make_cycle):
        question = bank.question(bank.criterion())
        first = make_cycle(year=2021)
        bank.use_in(first, question)
        bank.set_status(first, CycleStatus.finalized)
        second = make_cycle(year=2022)
        bank.set_status(second, CycleStatus.finalized)
        # 2021 is still one of the last 3 opened cycles: 2021, 2022 and the one opening now.
        third = make_cycle(year=2023)
        assert self._open(db, admin, bank, third) == []
        bank.set_status(third, CycleStatus.finalized)

        # One cycle later it is not.
        assert self._open(db, admin, bank, make_cycle(year=2024)) == [question]

    def test_a_question_in_a_draft_cycle_is_kept(self, db, admin, bank, make_cycle):
        question = bank.question(bank.criterion())
        first = make_cycle(year=2021)
        bank.use_in(first, question)
        bank.set_status(first, CycleStatus.finalized)
        for year in (2022, 2023):
            bank.set_status(make_cycle(year=year), CycleStatus.finalized)
        bank.use_in(make_cycle(year=2030), question)

        assert self._open(db, admin, bank, make_cycle(year=2024)) == []
        assert not self._is_archived(db, question)

    def test_a_question_that_was_never_used_is_never_archived(self, db, admin, bank, make_cycle):
        never_used = bank.question(bank.criterion())
        for year in (2021, 2022, 2023):
            bank.set_status(make_cycle(year=year), CycleStatus.finalized)

        assert self._open(db, admin, bank, make_cycle(year=2024)) == []
        assert not self._is_archived(db, never_used)

    def test_annual_and_midyear_cycles_both_count(self, db, admin, bank, make_cycle):
        question = bank.question(bank.criterion())
        first = make_cycle(year=2023)
        bank.use_in(first, question)
        bank.set_status(first, CycleStatus.finalized)
        midyear = make_cycle(year=2024, type="midyear", name="Midyear 2024")
        bank.set_status(midyear, CycleStatus.finalized)
        annual = make_cycle(year=2024)
        bank.set_status(annual, CycleStatus.finalized)

        assert self._open(db, admin, bank, make_cycle(year=2025)) == [question]

    def test_does_not_archive_twice(self, db, admin, bank, make_cycle, client, auth):
        question = bank.question(bank.criterion())
        first = make_cycle(year=2021)
        bank.use_in(first, question)
        bank.set_status(first, CycleStatus.finalized)
        for year in (2022, 2023):
            bank.set_status(make_cycle(year=year), CycleStatus.finalized)
        client.post(f"{QUESTIONS}/{question}/archive", headers=auth)

        assert self._open(db, admin, bank, make_cycle(year=2024)) == []
