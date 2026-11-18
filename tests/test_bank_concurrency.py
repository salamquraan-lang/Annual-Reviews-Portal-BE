# ruff: noqa: F811, B023
# The fixtures are imported by name, and each test takes them as arguments.
# B023: the lambdas use the loop's variables, but each one runs inside the same round, so that is safe.
"""Requests that arrive at the same moment. They need MySQL, because SQLite ignores row locks.

Each test sends its requests together many times, because one try is not enough to see a race.
After every round, every template must still follow T3, T4 and T5.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine
from app.main import create_app
from app.models import CycleStatus
from app.services import cycles as cycle_service
from app.services import question_bank as bank_service
from tests.bank_helpers import admin, auth, bank, make_cycle  # noqa: F401
from tests.test_bank_edge_cases import broken_rules

pytestmark = pytest.mark.skipif(
    not engine.url.drivername.startswith("mysql"), reason="row locks need MySQL; SQLite ignores them"
)

ROUNDS = 8
QUESTIONS = "/api/questions"
TEMPLATES = "/api/templates"


def together(*calls):
    """Run the calls at the same moment, each with its own client. Returns the status codes in order."""
    barrier = Barrier(len(calls))

    def worker(call):
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            barrier.wait()
            return call(client).status_code

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return tuple(pool.map(worker, calls))


def check(db, codes, allowed):
    db.rollback()
    assert codes in allowed, codes
    assert broken_rules(db) == []


def test_deleting_a_criterion_while_a_question_is_created_in_it(bank, auth, db):
    for round_number in range(ROUNDS):
        criterion = bank.criterion(f"Criterion {round_number}")
        codes = together(
            lambda c: c.delete(f"/api/criteria/{criterion}", headers=auth),
            lambda c: c.post(QUESTIONS, headers=auth, json={"criterionId": criterion, "text": "T", "kind": "text"}),
        )
        # Either the delete wins and the question is refused, or the question wins and the delete is refused.
        check(db, codes, {(204, 400), (409, 201)})


def test_deleting_a_question_while_it_is_added_to_a_template(bank, auth, make_cycle, db):
    for _ in range(ROUNDS):
        question = bank.question(bank.criterion(f"C {_}"))
        template = bank.template(make_cycle())
        codes = together(
            lambda c: c.delete(f"{QUESTIONS}/{question}", headers=auth),
            lambda c: c.post(f"{TEMPLATES}/{template}/questions", headers=auth, json={"questionId": question}),
        )
        check(db, codes, {(204, 400), (409, 201)})


def test_moving_a_question_into_a_criterion_while_it_is_deleted(bank, auth, db):
    for round_number in range(ROUNDS):
        question = bank.question(bank.criterion(f"From {round_number}"))
        target = bank.criterion(f"To {round_number}")
        codes = together(
            lambda c: c.patch(f"{QUESTIONS}/{question}", headers=auth, json={"criterionId": target}),
            lambda c: c.delete(f"/api/criteria/{target}", headers=auth),
        )
        check(db, codes, {(200, 409), (400, 204)})


@pytest.mark.parametrize(("start", "end", "weight"), [("rating", "text", 5), ("text", "rating", None)])
def test_changing_the_kind_while_the_question_is_added(bank, auth, make_cycle, db, start, end, weight):
    """A text question never ends up with a weight, and a rating question never without one (T5)."""
    for round_number in range(ROUNDS):
        question = bank.question(bank.criterion(f"C {round_number}"), kind=start)
        template = bank.template(make_cycle())
        body = {"questionId": question} | ({"weight": weight} if weight else {})
        codes = together(
            lambda c: c.patch(f"{QUESTIONS}/{question}", headers=auth, json={"kind": end}),
            lambda c: c.post(f"{TEMPLATES}/{template}/questions", headers=auth, json=body),
        )
        check(db, codes, {(200, 201), (200, 400)})


def test_four_questions_added_to_one_template_at_once(bank, auth, make_cycle, db):
    for round_number in range(ROUNDS):
        first, second = bank.criterion(f"A {round_number}"), bank.criterion(f"B {round_number}")
        questions = [bank.question(first), bank.question(second), bank.question(first), bank.question(second, kind="text")]
        template = bank.template(make_cycle())
        codes = together(
            *[
                lambda c, q=question: c.post(f"{TEMPLATES}/{template}/questions", headers=auth, json={"questionId": q})
                for question in questions
            ]
        )
        check(db, codes, {(201, 201, 201, 201)})


def test_the_same_question_added_twice_at_once(bank, auth, make_cycle, db):
    for round_number in range(ROUNDS):
        question = bank.question(bank.criterion(f"C {round_number}"))
        template = bank.template(make_cycle())
        add = lambda c: c.post(f"{TEMPLATES}/{template}/questions", headers=auth, json={"questionId": question})
        codes = together(add, add)
        check(db, codes, {(201, 409), (409, 201)})


def test_moving_reordering_removing_and_adding_at_once(bank, auth, make_cycle, db):
    for round_number in range(ROUNDS):
        first, second = bank.criterion(f"A {round_number}"), bank.criterion(f"B {round_number}")
        q = [bank.question(first) for _ in range(4)] + [bank.question(second) for _ in range(2)]
        extra = bank.question(first)
        template = bank.use_in(make_cycle(), *q)
        url = f"{TEMPLATES}/{template}"
        codes = together(
            lambda c: c.patch(f"{QUESTIONS}/{q[0]}", headers=auth, json={"criterionId": second}),
            lambda c: c.put(f"{url}/criteria/{first}/question-order", headers=auth, json={"questionIds": q[3:0:-1]}),
            lambda c: c.delete(f"{url}/questions/{q[4]}", headers=auth),
            lambda c: c.post(f"{url}/questions", headers=auth, json={"questionId": extra}),
        )
        # The reorder is a mismatch if the extra question joined that section first.
        check(db, codes, {(200, 200, 200, 201), (200, 400, 200, 201)})


def test_two_questions_change_criterion_at_once(bank, auth, make_cycle, db):
    for round_number in range(ROUNDS):
        first, second = bank.criterion(f"A {round_number}"), bank.criterion(f"B {round_number}")
        q = [bank.question(first) for _ in range(3)] + [bank.question(second)]
        bank.use_in(make_cycle(), *q)
        codes = together(
            lambda c: c.patch(f"{QUESTIONS}/{q[0]}", headers=auth, json={"criterionId": second}),
            lambda c: c.patch(f"{QUESTIONS}/{q[1]}", headers=auth, json={"criterionId": second, "kind": "text"}),
        )
        check(db, codes, {(200, 200)})


def test_two_templates_copied_from_each_other_at_once(bank, auth, make_cycle, db):
    """Each copy locks both cycles in the same order, so the two requests never wait for each other."""
    for round_number in range(ROUNDS):
        criterion = bank.criterion(f"C {round_number}")
        a = bank.use_in(make_cycle(), *[bank.question(criterion) for _ in range(3)])
        b = bank.use_in(make_cycle(), *[bank.question(criterion) for _ in range(3)])
        codes = together(
            lambda c: c.post(f"{TEMPLATES}/{b}/copy-from", headers=auth, json={"sourceTemplateId": a}),
            lambda c: c.post(f"{TEMPLATES}/{a}/copy-from", headers=auth, json={"sourceTemplateId": b}),
        )
        check(db, codes, {(200, 200)})


def test_a_question_deleted_while_its_template_is_copied(bank, auth, make_cycle, db):
    for round_number in range(ROUNDS):
        criterion = bank.criterion(f"C {round_number}")
        q = [bank.question(criterion) for _ in range(3)]
        source = bank.use_in(make_cycle(), *q)
        target = bank.template(make_cycle())

        def remove_then_delete(c):
            c.delete(f"{TEMPLATES}/{source}/questions/{q[1]}", headers=auth)
            return c.delete(f"{QUESTIONS}/{q[1]}", headers=auth)

        codes = together(
            lambda c: c.post(f"{TEMPLATES}/{target}/copy-from", headers=auth, json={"sourceTemplateId": source}),
            remove_then_delete,
        )
        # The delete is refused if the copy got the question first.
        check(db, codes, {(200, 204), (200, 409)})


def test_renaming_and_deleting_a_criterion_while_its_question_is_added(bank, auth, make_cycle, db):
    for round_number in range(ROUNDS):
        criterion = bank.criterion(f"C {round_number}")
        question = bank.question(criterion)
        template = bank.template(make_cycle())
        codes = together(
            lambda c: c.patch(f"/api/criteria/{criterion}", headers=auth, json={"name": f"Renamed {round_number}"}),
            lambda c: c.delete(f"/api/criteria/{criterion}", headers=auth),
            lambda c: c.post(f"{TEMPLATES}/{template}/questions", headers=auth, json={"questionId": question}),
        )
        check(db, codes, {(200, 409, 201)})


def test_two_criteria_swap_names_while_a_third_takes_one(bank, auth, db):
    for round_number in range(ROUNDS):
        left, right = bank.criterion(f"Left {round_number}"), bank.criterion(f"Right {round_number}")
        codes = together(
            lambda c: c.patch(f"/api/criteria/{left}", headers=auth, json={"name": f"Right {round_number}"}),
            lambda c: c.patch(f"/api/criteria/{right}", headers=auth, json={"name": f"Left {round_number}"}),
            lambda c: c.post("/api/criteria", headers=auth, json={"name": f"left{round_number}"}),
        )
        check(db, codes, {(409, 409, 409)})


def test_a_question_added_to_a_draft_while_a_cycle_opens_is_not_archived(
    bank, auth, admin, make_cycle, db, monkeypatch
):
    """QB-14 runs while a cycle opens. A question that a draft starts using at that moment stays active."""
    # Until cycles.py calls archive_unused_questions itself, its stub is replaced here.
    # The stand-in takes any extra arguments, so it still fits once the stub also takes actor_id.
    monkeypatch.setattr(
        cycle_service,
        "_archive_unused_questions",
        lambda session, cycle, *_: bank_service.archive_unused_questions(session, cycle, admin.id),
    )
    criterion = bank.criterion()
    old, new = bank.question(criterion, "Old."), bank.question(criterion, "New.")
    first = make_cycle()
    bank.use_in(first, old)
    bank.set_status(first, CycleStatus.closed)
    for _ in range(3):
        cycle = make_cycle()
        bank.use_in(cycle, new)
        bank.set_status(cycle, CycleStatus.closed)
    opening = make_cycle()
    bank.use_in(opening, new)
    draft = bank.template(make_cycle())

    for _ in range(ROUNDS):
        db.execute(text("UPDATE cycles SET status = 'draft' WHERE id = :id"), {"id": opening.id})
        db.execute(text("DELETE FROM template_questions WHERE template_id = :id"), {"id": draft})
        db.execute(text("UPDATE questions SET archived_at = NULL"))
        db.commit()

        codes = together(
            lambda c: c.post(f"/api/cycles/{opening.id}/open", headers=auth),
            lambda c: c.post(f"{TEMPLATES}/{draft}/questions", headers=auth, json={"questionId": old}),
        )

        check(db, codes, {(200, 201)})
        assert db.execute(text("SELECT archived_at FROM questions WHERE id = :id"), {"id": old}).scalar() is None
