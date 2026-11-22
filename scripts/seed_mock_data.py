"""Fill the database with mock data to try the app in a browser.

Run from the backend folder, after the migrations:

    .venv/Scripts/python scripts/seed_mock_data.py

It can run again safely: anything that already exists (same email, name or year) is left as it is.
All mock people have an @example.test address and share the password below. Mock data only, never
for a real deployment with real people.
"""

import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Criterion,
    Cycle,
    CycleStatus,
    CycleType,
    Department,
    Question,
    QuestionKind,
    ReviewType,
    Role,
    Template,
    TemplateQuestion,
    User,
)
from app.security import hash_password  # noqa: E402
from app.services import cycles as cycle_service  # noqa: E402
from app.services import departments as department_service  # noqa: E402
from app.services import question_bank, templates  # noqa: E402

MOCK_PASSWORD = "MockData2026!"

# name -> parent name (None for a top-level department)
DEPARTMENTS: dict[str, str | None] = {
    "Engineering": None,
    "Backend": "Engineering",
    "Frontend": "Engineering",
    "Quality assurance": "Engineering",
    "Product": None,
    "Sales": None,
    "Inside sales": "Sales",
    "People & HR": None,
    "Operations": None,
    "Customer support": "Operations",
    "Finance": None,
}


@dataclass
class Person:
    name: str
    email: str
    role: Role
    department: str
    manager: str | None = None  # email of the manager
    active: bool = True


PEOPLE = [
    Person("Lina Haddad", "lina.haddad@example.test", Role.admin, "People & HR"),
    Person("Karim Aziz", "karim.aziz@example.test", Role.admin, "People & HR"),
    Person("Omar Nasser", "omar.nasser@example.test", Role.manager, "Backend"),
    Person("Rania Khoury", "rania.khoury@example.test", Role.manager, "Frontend"),
    Person("Yousef Saleh", "yousef.saleh@example.test", Role.manager, "Sales"),
    Person("Dana Mansour", "dana.mansour@example.test", Role.manager, "Customer support"),
    Person("Sara Odeh", "sara.odeh@example.test", Role.employee, "Backend", "omar.nasser@example.test"),
    Person("Ahmad Taha", "ahmad.taha@example.test", Role.employee, "Backend", "omar.nasser@example.test"),
    Person("Maya Darwish", "maya.darwish@example.test", Role.employee, "Quality assurance", "omar.nasser@example.test"),
    Person("Ziad Hamdan", "ziad.hamdan@example.test", Role.employee, "Frontend", "rania.khoury@example.test"),
    Person("Noor Khalil", "noor.khalil@example.test", Role.employee, "Frontend", "rania.khoury@example.test"),
    Person("Tariq Fares", "tariq.fares@example.test", Role.employee, "Inside sales", "yousef.saleh@example.test"),
    Person("Hiba Yassin", "hiba.yassin@example.test", Role.employee, "Inside sales", "yousef.saleh@example.test"),
    Person("Faris Jaber", "faris.jaber@example.test", Role.employee, "Customer support", "dana.mansour@example.test"),
    Person("Leen Barakat", "leen.barakat@example.test", Role.employee, "Customer support", "dana.mansour@example.test"),
    Person("Basel Qasem", "basel.qasem@example.test", Role.employee, "Product"),
    Person("Rami Sabbagh", "rami.sabbagh@example.test", Role.employee, "Finance", active=False),
]

R, T = QuestionKind.rating, QuestionKind.text

# criterion -> (description, [(question text, kind)])
QUESTION_BANK: dict[str, tuple[str | None, list[tuple[str, QuestionKind]]]] = {
    "Communication": (
        "Shares information clearly and listens to others.",
        [
            ("Explains ideas clearly, in writing and in person.", R),
            ("Listens to others and responds to what they say.", R),
            ("Keeps the right people informed without being asked.", R),
            ("What is one thing they could communicate better?", T),
        ],
    ),
    "Teamwork": (
        "Works well with others and helps the team succeed.",
        [
            ("Helps teammates when they are stuck.", R),
            ("Gives and accepts feedback in a constructive way.", R),
            ("Shares credit and takes part in team decisions.", R),
            ("Describe a time they made the team stronger.", T),
        ],
    ),
    "Delivery": (
        "Gets the right work done on time and to a good standard.",
        [
            ("Meets deadlines or raises risks early.", R),
            ("Produces work of a consistently high quality.", R),
            ("Plans work and breaks it into clear steps.", R),
            ("Which result this period are they most proud of?", T),
        ],
    ),
    "Technical skill": (
        "Knows the tools and methods the role needs, and keeps learning them.",
        [
            ("Has the technical knowledge the role needs.", R),
            ("Solves problems in a simple, maintainable way.", R),
            ("Keeps their skills up to date.", R),
        ],
    ),
    "Leadership": (
        "Gives direction and supports the people around them.",
        [
            ("Gives clear direction and priorities.", R),
            ("Supports my growth and gives me useful feedback.", R),
            ("Is fair and treats everyone with respect.", R),
            ("What should this manager keep doing?", T),
            ("What should this manager do differently?", T),
        ],
    ),
    "Growth": (
        "Learns from experience and works toward their goals.",
        [
            ("Learns from mistakes and changes their approach.", R),
            ("Takes on new challenges willingly.", R),
            ("What would you like to learn or achieve next year?", T),
        ],
    ),
    # No questions yet, so the bank shows a criterion that can still be deleted.
    "Innovation": ("Suggests and tries new ideas.", []),
}

# Questions left out of every form and then archived, to show archived questions in the bank.
ARCHIVED = [
    ("Communication", "Writes clear emails.", R),
    ("Delivery", "Works overtime when needed.", R),
]

# review type -> [(question text, weight)]. Weight is ignored for written-answer questions.
FORMS: dict[ReviewType, list[tuple[str, int]]] = {
    ReviewType.self_: [
        ("Explains ideas clearly, in writing and in person.", 2),
        ("Keeps the right people informed without being asked.", 1),
        ("Helps teammates when they are stuck.", 1),
        ("Meets deadlines or raises risks early.", 3),
        ("Produces work of a consistently high quality.", 2),
        ("Which result this period are they most proud of?", 1),
        ("Learns from mistakes and changes their approach.", 1),
        ("What would you like to learn or achieve next year?", 1),
    ],
    ReviewType.manager: [
        ("Explains ideas clearly, in writing and in person.", 2),
        ("Listens to others and responds to what they say.", 1),
        ("What is one thing they could communicate better?", 1),
        ("Helps teammates when they are stuck.", 1),
        ("Gives and accepts feedback in a constructive way.", 2),
        ("Meets deadlines or raises risks early.", 3),
        ("Produces work of a consistently high quality.", 3),
        ("Plans work and breaks it into clear steps.", 1),
        ("Has the technical knowledge the role needs.", 2),
        ("Solves problems in a simple, maintainable way.", 1),
        ("Takes on new challenges willingly.", 1),
    ],
    ReviewType.peer: [
        ("Explains ideas clearly, in writing and in person.", 1),
        ("Listens to others and responds to what they say.", 1),
        ("Helps teammates when they are stuck.", 2),
        ("Shares credit and takes part in team decisions.", 1),
        ("Describe a time they made the team stronger.", 1),
    ],
    ReviewType.upward: [
        ("Gives clear direction and priorities.", 2),
        ("Supports my growth and gives me useful feedback.", 2),
        ("Is fair and treats everyone with respect.", 1),
        ("What should this manager keep doing?", 1),
        ("What should this manager do differently?", 1),
    ],
}


@dataclass
class CyclePlan:
    name: str
    type: CycleType
    year: int
    start: date
    end: date
    status: CycleStatus
    forms: tuple[ReviewType, ...]  # which forms get questions


CYCLES = [
    CyclePlan("Annual review 2025", CycleType.annual, 2025, date(2025, 1, 1), date(2025, 12, 31),
              CycleStatus.closed, tuple(ReviewType)),
    CyclePlan("Annual review 2026", CycleType.annual, 2026, date(2026, 1, 1), date(2026, 12, 31),
              CycleStatus.open, tuple(ReviewType)),
    # Draft, so its forms can be edited. The upward form stays empty on purpose.
    CyclePlan("Mid-year review 2027", CycleType.midyear, 2027, date(2027, 1, 1), date(2027, 6, 30),
              CycleStatus.draft, (ReviewType.self_, ReviewType.manager, ReviewType.peer)),
]


# ---------------------------------------------------------------------------- steps


def seed_departments(db: Session, actor_id: int) -> dict[str, Department]:
    by_name: dict[str, Department] = {}
    for name, parent_name in DEPARTMENTS.items():
        parent_id = by_name[parent_name].id if parent_name else None
        same_parent = Department.parent_id.is_(None) if parent_id is None else Department.parent_id == parent_id
        existing = db.scalars(select(Department).where(Department.name == name, same_parent)).first()
        if existing is None:
            existing = department_service.create_department(db, actor_id, name, parent_id)
            print(f"  department  {name}")
        by_name[name] = existing
    return by_name


def seed_people(db: Session, departments: dict[str, Department]) -> dict[str, User]:
    password_hash = hash_password(MOCK_PASSWORD)
    by_email: dict[str, User] = {}
    for person in PEOPLE:
        user = db.scalars(select(User).where(User.email == person.email)).first()
        if user is None:
            user = User(
                name=person.name,
                email=person.email,
                password_hash=password_hash,
                role=person.role,
                department_id=departments[person.department].id,
                manager_id=by_email[person.manager].id if person.manager else None,
                is_active=person.active,
                email_verified_at=datetime.now().replace(microsecond=0),
                must_change_password=False,
            )
            db.add(user)
            db.flush()
            print(f"  person      {person.name} ({person.role.value})")
        by_email[person.email] = user
    db.commit()
    return by_email


def seed_question_bank(db: Session, actor_id: int) -> dict[str, Question]:
    by_text: dict[str, Question] = {}
    for criterion_name, (description, questions) in QUESTION_BANK.items():
        key = question_bank.name_key(criterion_name)
        criterion = db.scalars(select(Criterion).where(Criterion.name_key == key)).first()
        if criterion is None:
            criterion = question_bank.create_criterion(db, actor_id, criterion_name, description)
            print(f"  criterion   {criterion_name}")
        for text, kind in questions:
            by_text[text] = _question(db, actor_id, criterion, text, kind)
    return by_text


def _question(db: Session, actor_id: int, criterion: Criterion, text: str, kind: QuestionKind) -> Question:
    question = db.scalars(
        select(Question).where(Question.criterion_id == criterion.id, Question.text == text)
    ).first()
    if question is None:
        question = question_bank.create_question(db, actor_id, criterion.id, text, kind).question
    return question


def seed_cycles(db: Session, actor_id: int, questions: dict[str, Question]) -> None:
    for plan in CYCLES:
        cycle = db.scalars(select(Cycle).where(Cycle.year == plan.year, Cycle.type == plan.type)).first()
        if cycle is None:
            data = cycle_service.NewCycle(plan.name, plan.type, plan.year, plan.start, plan.end)
            cycle = cycle_service.create_cycle(db, actor_id, data)
            print(f"  cycle       {plan.name}")
        if cycle.status != CycleStatus.draft:
            continue  # already moved on by an earlier run

        forms = _forms_of(db, cycle)
        for review_type in plan.forms:
            template = forms[review_type]
            in_form = set(db.scalars(select(TemplateQuestion.question_id).where(
                TemplateQuestion.template_id == template.id)))
            for text, weight in FORMS[review_type]:
                question = questions[text]
                if question.id not in in_form:
                    templates.add_question(
                        db, actor_id, template.id, question.id,
                        weight if question.kind == QuestionKind.rating else None,
                    )
        _move_to(db, actor_id, cycle, plan.status)


def _forms_of(db: Session, cycle: Cycle) -> dict[ReviewType, Template]:
    rows = list(db.scalars(select(Template).where(Template.cycle_id == cycle.id)))
    if not rows:
        # Until cycles.py creates a new cycle's forms itself, make them here.
        rows = templates.create_default_templates(db, cycle)
        db.commit()
    return {row.review_type: row for row in rows}


def _move_to(db: Session, actor_id: int, cycle: Cycle, status: CycleStatus) -> None:
    if status == CycleStatus.draft:
        return
    cycle_service.open_cycle(db, actor_id, cycle.id)
    print(f"  opened      {cycle.name}")
    if status in (CycleStatus.closed, CycleStatus.finalized):
        cycle_service.close_cycle(db, actor_id, cycle.id)
        print(f"  closed      {cycle.name}")


def seed_archived(db: Session, actor_id: int) -> None:
    for criterion_name, text, kind in ARCHIVED:
        criterion = db.scalars(
            select(Criterion).where(Criterion.name_key == question_bank.name_key(criterion_name))
        ).one()
        question = _question(db, actor_id, criterion, text, kind)
        if question.archived_at is None:
            question_bank.archive_question(db, actor_id, question.id)
            print(f"  archived    {text}")


def main() -> None:
    with SessionLocal() as db:
        print("Adding mock data:")
        actor = db.scalars(select(User).where(User.role.in_([Role.admin, Role.super_admin])).order_by(User.id)).first()
        departments = seed_departments(db, actor.id if actor else 0)
        people = seed_people(db, departments)
        actor_id = actor.id if actor else people["lina.haddad@example.test"].id
        questions = seed_question_bank(db, actor_id)
        seed_cycles(db, actor_id, questions)
        seed_archived(db, actor_id)
        print(f"Done. Mock people sign in with their @example.test address and the password in {Path(__file__).name}.")


if __name__ == "__main__":
    main()
