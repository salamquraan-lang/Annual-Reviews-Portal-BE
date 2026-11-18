# ruff: noqa: F811
# The fixtures are imported by name, and each test takes them as arguments.
"""Unusual input and long random runs for the question bank and the templates."""

import random

import pytest
from sqlalchemy import text

from app.errors import AppError
from app.models import TemplateQuestion
from app.services import question_bank as bank_service
from app.services import templates as template_service
from tests.bank_helpers import admin, auth, bank, make_cycle  # noqa: F401

CRITERIA = "/api/criteria"
QUESTIONS = "/api/questions"
TEMPLATES = "/api/templates"
JSON = {"Content-Type": "application/json"}

# A name with no visible character: zero-width space, no-break space, a control character.
INVISIBLE = ["\u200b", " ", "\u200b ‍", "\u0000", "⁠﻿"]
# "x" + half of a surrogate pair + "y". JSON can carry it, but it is not valid Unicode.
BROKEN_UNICODE = b'"x\\ud800y"'


def error(res) -> dict:
    return res.json()["error"]


def broken_rules(db) -> list[str]:
    """Check every template in the database against T3, T4 and T5. Returns what is wrong."""
    rows = db.execute(
        text(
            "SELECT tq.template_id, tq.question_id, tq.position, tq.weight, q.kind, q.criterion_id "
            "FROM template_questions tq JOIN questions q ON q.id = tq.question_id "
            "ORDER BY tq.template_id, tq.position, tq.question_id"
        )
    ).all()
    by_template: dict[int, list] = {}
    for row in rows:
        by_template.setdefault(row.template_id, []).append(row)

    problems = []
    for template_id, links in by_template.items():
        found = [link.position for link in links]
        if found != list(range(1, len(links) + 1)):
            problems.append(f"template {template_id}: positions are {found}")
        seen: set[int] = set()
        last = None
        for link in links:
            if link.criterion_id != last:
                if link.criterion_id in seen:
                    problems.append(f"template {template_id}: criterion {link.criterion_id} is in two places")
                seen.add(link.criterion_id)
                last = link.criterion_id
            if (link.weight is None) != (link.kind == "text"):
                problems.append(f"template {template_id}: {link.kind} question has weight {link.weight}")
    return problems


class TestUnusualText:
    @pytest.mark.parametrize("letter", ["ß", "ﬃ"])
    def test_a_name_that_gets_longer_in_lower_case_is_saved(self, client, auth, letter):
        """One sharp s becomes "ss" in lower case, and one "ffi" sign becomes three letters."""
        name = letter * 120

        res = client.post(CRITERIA, headers=auth, json={"name": name})
        again = client.post(CRITERIA, headers=auth, json={"name": name})

        assert res.status_code == 201
        assert res.json()["name"] == name
        assert again.status_code == 409

    def test_a_criterion_can_be_renamed_to_such_a_name(self, client, auth, bank):
        criterion = bank.criterion("Short")

        res = client.patch(f"{CRITERIA}/{criterion}", headers=auth, json={"name": "ß" * 120})

        assert res.status_code == 200

    def test_emoji_and_other_alphabets_are_kept(self, client, auth, bank):
        name = "Team \U0001f600 عمل"
        question = "هل يتواصل بوضوح؟ \U0001f44d"
        criterion = bank.criterion(name)

        question_id = bank.question(criterion, question)

        assert client.get(f"{CRITERIA}/{criterion}", headers=auth).json()["name"] == name
        assert client.get(f"{QUESTIONS}/{question_id}", headers=auth).json()["text"] == question

    def test_the_longest_texts_of_emoji_are_saved(self, client, auth, bank, make_cycle):
        criterion = bank.criterion("\U0001f600" * 120, description="\U0001f600" * 500)
        question = bank.question(criterion, "\U0001f600" * 500)
        template = bank.template(make_cycle())

        res = client.patch(f"{TEMPLATES}/{template}", headers=auth, json={"name": "\U0001f600" * 150})

        assert res.status_code == 200
        assert len(client.get(f"{QUESTIONS}/{question}", headers=auth).json()["text"]) == 500

    def test_control_characters_are_removed(self, client, auth, bank):
        criterion = client.post(CRITERIA, headers=auth, json={"name": "Team\u0000 work\u0007", "description": "a\u0000b"})
        question = client.post(
            QUESTIONS,
            headers=auth,
            json={"criterionId": criterion.json()["id"], "text": "First line\u0000\nSecond\tline", "kind": "text"},
        )

        assert criterion.json()["name"] == "Team work"
        assert criterion.json()["description"] == "ab"
        assert question.json()["text"] == "First line\nSecond\tline"

    @pytest.mark.parametrize("value", INVISIBLE)
    def test_a_criterion_name_needs_a_visible_character(self, client, auth, value):
        res = client.post(CRITERIA, headers=auth, json={"name": value})

        assert res.status_code == 400
        assert error(res)["details"][0]["message"] == "Name is required."

    @pytest.mark.parametrize("value", INVISIBLE)
    def test_a_question_text_needs_a_visible_character(self, client, auth, bank, value):
        res = client.post(QUESTIONS, headers=auth, json={"criterionId": bank.criterion(), "text": value, "kind": "text"})

        assert res.status_code == 400
        assert error(res)["details"][0]["message"] == "Text is required."

    @pytest.mark.parametrize("value", INVISIBLE)
    def test_a_template_name_needs_a_visible_character(self, client, auth, bank, make_cycle, value):
        res = client.patch(f"{TEMPLATES}/{bank.template(make_cycle())}", headers=auth, json={"name": value})

        assert res.status_code == 400
        assert error(res)["details"][0]["message"] == "Name is required."

    def test_an_invisible_description_is_saved_as_no_description(self, client, auth):
        res = client.post(CRITERIA, headers=auth, json={"name": "Ownership", "description": "\u200b  "})

        assert res.status_code == 201
        assert res.json()["description"] is None

    def test_text_that_is_not_valid_unicode_is_refused(self, client, auth, bank, make_cycle):
        criterion = bank.criterion()
        template = bank.template(make_cycle())
        headers = auth | JSON
        question_body = b'{"criterionId": %d, "kind": "text", "text": %s}' % (criterion, BROKEN_UNICODE)

        answers = [
            client.post(CRITERIA, headers=headers, content=b'{"name": %s}' % BROKEN_UNICODE),
            client.post(CRITERIA, headers=headers, content=b'{"name": "Ok", "description": %s}' % BROKEN_UNICODE),
            client.patch(f"{CRITERIA}/{criterion}", headers=headers, content=b'{"name": %s}' % BROKEN_UNICODE),
            client.post(QUESTIONS, headers=headers, content=question_body),
            client.patch(f"{TEMPLATES}/{template}", headers=headers, content=b'{"name": %s}' % BROKEN_UNICODE),
        ]

        for res in answers:
            assert res.status_code == 400
            assert error(res)["details"][0]["message"] == "The text contains characters that cannot be saved."

    @pytest.mark.parametrize("search", ["%", "_", "\\", "'", "\U0001f600", "' OR 1=1 --", "%%__"])
    def test_search_treats_every_character_as_plain_text(self, client, auth, bank, search):
        criterion = bank.criterion()
        bank.question(criterion, "Plain words only.")
        marked = bank.question(criterion, f"Has {search} inside.")

        res = client.get(QUESTIONS, headers=auth, params={"search": search})

        assert res.status_code == 200
        assert [item["id"] for item in res.json()["items"]] == [marked]


class TestHugeNumbers:
    @pytest.mark.parametrize("page", [1_000_001, 10**18, 10**30])
    def test_a_page_number_that_is_too_large_is_refused(self, client, auth, page):
        res = client.get(QUESTIONS, headers=auth, params={"page": page})

        assert res.status_code == 400
        assert error(res)["details"][0]["field"] == "page"

    def test_the_last_allowed_page_is_empty(self, client, auth, bank):
        bank.question(bank.criterion())

        res = client.get(QUESTIONS, headers=auth, params={"page": 1_000_000, "pageSize": 100})

        assert res.status_code == 200
        assert res.json()["items"] == []
        assert res.json()["total"] == 1

    @pytest.mark.parametrize("number", [2**31, 2**63, 10**30])
    def test_an_id_too_large_to_exist_is_refused(self, client, auth, bank, make_cycle, number):
        criterion = bank.criterion()
        question = bank.question(criterion)
        template = bank.use_in(make_cycle(), question)

        answers = [
            client.get(f"{CRITERIA}/{number}", headers=auth),
            client.patch(f"{CRITERIA}/{number}", headers=auth, json={"name": "New"}),
            client.delete(f"{CRITERIA}/{number}", headers=auth),
            client.get(f"{QUESTIONS}/{number}", headers=auth),
            client.patch(f"{QUESTIONS}/{number}", headers=auth, json={"text": "New"}),
            client.delete(f"{QUESTIONS}/{number}", headers=auth),
            client.post(f"{QUESTIONS}/{number}/copy", headers=auth),
            client.post(f"{QUESTIONS}/{number}/archive", headers=auth),
            client.post(f"{QUESTIONS}/{number}/unarchive", headers=auth),
            client.get(QUESTIONS, headers=auth, params={"criterionId": number}),
            client.post(QUESTIONS, headers=auth, json={"criterionId": number, "text": "T", "kind": "text"}),
            client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"criterionId": number}),
            client.get(f"/api/cycles/{number}/templates", headers=auth),
            client.get(f"{TEMPLATES}/{number}", headers=auth),
            client.patch(f"{TEMPLATES}/{number}", headers=auth, json={"name": "New"}),
            client.post(f"{TEMPLATES}/{template}/questions", headers=auth, json={"questionId": number}),
            client.delete(f"{TEMPLATES}/{template}/questions/{number}", headers=auth),
            client.patch(f"{TEMPLATES}/{template}/questions/{number}", headers=auth, json={"weight": 2}),
            client.post(f"{TEMPLATES}/{template}/questions/{question}/replace", headers=auth, json={"questionId": number}),
            client.put(f"{TEMPLATES}/{template}/criteria/{number}/question-order", headers=auth, json={"questionIds": [1]}),
            client.put(f"{TEMPLATES}/{template}/criterion-order", headers=auth, json={"criterionIds": [number]}),
            client.post(f"{TEMPLATES}/{template}/copy-from", headers=auth, json={"sourceTemplateId": number}),
        ]

        for res in answers:
            assert res.status_code == 400, res.request.url
            assert error(res)["code"] == "VALIDATION_ERROR"

    def test_the_largest_possible_id_is_simply_not_found(self, client, auth):
        assert client.get(f"{CRITERIA}/{2**31 - 1}", headers=auth).status_code == 404
        assert client.get(f"{QUESTIONS}/{2**31 - 1}", headers=auth).status_code == 404
        assert client.get(f"{TEMPLATES}/{2**31 - 1}", headers=auth).status_code == 404

    def test_a_very_long_order_list_is_answered_without_an_error(self, client, auth, bank, make_cycle):
        template = bank.use_in(make_cycle(), bank.question(bank.criterion()))

        res = client.put(
            f"{TEMPLATES}/{template}/criterion-order", headers=auth, json={"criterionIds": list(range(1, 5001))}
        )

        assert res.status_code == 400
        assert error(res)["code"] == "ORDER_MISMATCH"


class TestRandomRuns:
    """Many random steps in a row. After every step, every template must still follow the rules."""

    @pytest.mark.parametrize("seed", range(12))
    def test_the_templates_stay_in_order(self, client, auth, bank, make_cycle, db, seed):
        rnd = random.Random(seed)
        criteria = [bank.criterion(f"Criterion {n}") for n in range(3)]
        questions = [
            bank.question(rnd.choice(criteria), f"Question {n}.", rnd.choice(["rating", "rating", "text"]))
            for n in range(9)
        ]
        first, second = make_cycle(), make_cycle()
        pairs = [(bank.template(first, kind), bank.template(second, kind)) for kind in ("manager", "peer")]
        templates = [template for pair in pairs for template in pair]

        def step():
            template, question, other = rnd.choice(templates), rnd.choice(questions), rnd.choice(questions)
            url = f"{TEMPLATES}/{template}"
            pick = rnd.randrange(12)
            if pick < 3:
                return client.post(f"{url}/questions", headers=auth, json={"questionId": question})
            if pick == 3:
                return client.delete(f"{url}/questions/{question}", headers=auth)
            if pick == 4:
                return client.patch(f"{QUESTIONS}/{question}", headers=auth, json={"criterionId": rnd.choice(criteria)})
            if pick == 5:
                body = {"kind": rnd.choice(["rating", "text"])}
                return client.patch(f"{QUESTIONS}/{question}", headers=auth, json=body)
            if pick in (6, 7):
                sections = client.get(url, headers=auth).json()["sections"]
                if not sections:
                    return None
                if pick == 6:
                    ids = [section["criterion"]["id"] for section in sections]
                    rnd.shuffle(ids)
                    return client.put(f"{url}/criterion-order", headers=auth, json={"criterionIds": ids})
                section = rnd.choice(sections)
                ids = [item["questionId"] for item in section["questions"]]
                rnd.shuffle(ids)
                path = f"{url}/criteria/{section['criterion']['id']}/question-order"
                return client.put(path, headers=auth, json={"questionIds": ids})
            if pick == 8:
                return client.post(f"{url}/questions/{question}/replace", headers=auth, json={"questionId": other})
            if pick == 9:
                target, source = rnd.sample(rnd.choice(pairs), 2)
                return client.post(f"{TEMPLATES}/{target}/copy-from", headers=auth, json={"sourceTemplateId": source})
            if pick == 10:
                body = {"weight": rnd.randint(1, 9)}
                return client.patch(f"{url}/questions/{question}", headers=auth, json=body)
            return client.post(f"{QUESTIONS}/{question}/{rnd.choice(['archive', 'unarchive'])}", headers=auth)

        for number in range(80):
            res = step()
            if res is None:
                continue
            assert res.status_code in (200, 201, 400, 404, 409), f"step {number}: {res.status_code} {res.text}"
            assert broken_rules(db) == [], f"step {number}"

        for template in templates:
            body = client.get(f"{TEMPLATES}/{template}", headers=auth).json()
            shown = [item for section in body["sections"] for item in section["questions"]]
            assert [item["position"] for item in shown] == list(range(1, len(shown) + 1))
            assert body["questionCount"] == len(shown)
            # A question in a draft template is never left archived by the steps above, unless
            # an admin archived it by hand afterwards. Either way the answer must say which.
            assert all(isinstance(item["archived"], bool) for item in shown)


class TestStartingOverWhenACycleAppears:
    """lock_after_cycles reads the cycles, locks them, locks the row and reads the cycles again."""

    def _let_another_template_take_it(self, monkeypatch, db, spare_templates):
        """Each time the question is locked, one more template starts using it."""
        real = bank_service._lock_question
        calls = []

        def lock(session, question_id):
            row = real(session, question_id)
            calls.append(question_id)
            if spare_templates:
                link = TemplateQuestion(
                    template_id=spare_templates.pop(), question_id=question_id, position=1, weight=1
                )
                session.add(link)
                session.commit()
            return row

        monkeypatch.setattr(bank_service, "_lock_question", lock)
        return calls

    def test_it_starts_over_when_another_cycle_starts_using_the_question(
        self, db, admin, bank, make_cycle, monkeypatch
    ):
        question = bank.question(bank.criterion())
        spare = [bank.template(make_cycle())]
        calls = self._let_another_template_take_it(monkeypatch, db, spare)

        detail = bank_service.update_question(db, admin.id, question, {"text": "New text."})

        assert len(calls) == 2
        assert detail.question.text == "New text."

    def test_it_gives_up_after_five_tries(self, db, admin, bank, make_cycle, monkeypatch):
        question = bank.question(bank.criterion())
        spare = [bank.template(make_cycle()) for _ in range(template_service.LOCK_ATTEMPTS)]
        calls = self._let_another_template_take_it(monkeypatch, db, spare)

        with pytest.raises(AppError) as caught:
            bank_service.update_question(db, admin.id, question, {"text": "New text."})

        assert caught.value.status == 409
        assert caught.value.code == "TRY_AGAIN"
        assert len(calls) == template_service.LOCK_ATTEMPTS
