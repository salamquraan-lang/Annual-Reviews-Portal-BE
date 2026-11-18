from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.errors import AppError, bad_request
from app.models import (
    Criterion,
    Cycle,
    CycleStatus,
    Question,
    QuestionKind,
    ReviewType,
    Template,
    TemplateQuestion,
)

# Error codes. Message text lives next to the line that raises it.
TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
CYCLE_NOT_FOUND = "CYCLE_NOT_FOUND"
TEMPLATE_LOCKED = "TEMPLATE_LOCKED"
QUESTION_NOT_FOUND = "QUESTION_NOT_FOUND"
QUESTION_ALREADY_IN_TEMPLATE = "QUESTION_ALREADY_IN_TEMPLATE"
QUESTION_NOT_IN_TEMPLATE = "QUESTION_NOT_IN_TEMPLATE"
CRITERION_NOT_IN_TEMPLATE = "CRITERION_NOT_IN_TEMPLATE"
TEXT_QUESTION_HAS_NO_WEIGHT = "TEXT_QUESTION_HAS_NO_WEIGHT"
ORDER_MISMATCH = "ORDER_MISMATCH"
KIND_MISMATCH = "QUESTION_KIND_MISMATCH"
SAME_QUESTION = "SAME_QUESTION"
SOURCE_TEMPLATE_NOT_FOUND = "SOURCE_TEMPLATE_NOT_FOUND"
REVIEW_TYPE_MISMATCH = "REVIEW_TYPE_MISMATCH"
SAME_CYCLE = "SAME_CYCLE"
TRY_AGAIN = "TRY_AGAIN"

# How often lock_after_cycles starts over before it gives up.
LOCK_ATTEMPTS = 5

# Readiness problems, for the check before a cycle opens (C3, T6).
TEMPLATE_MISSING = "TEMPLATE_MISSING"
TEMPLATE_EMPTY = "TEMPLATE_EMPTY"
TEMPLATE_NO_RATING_QUESTION = "TEMPLATE_NO_RATING_QUESTION"

DEFAULT_WEIGHT = 1
DEFAULT_NAMES = {
    ReviewType.self_: "Self review",
    ReviewType.manager: "Manager review",
    ReviewType.peer: "Peer review",
    ReviewType.upward: "Upward review",
}


@dataclass
class TemplateRow:
    template: Template
    question_count: int


@dataclass
class Section:
    """The questions of one criterion, next to each other in the form (T4)."""

    criterion: Criterion
    links: list[TemplateQuestion]


@dataclass
class TemplateDetail:
    template: Template
    sections: list[Section]

    @property
    def editable(self) -> bool:
        return self.template.cycle.status == CycleStatus.draft


@dataclass
class TemplateProblem:
    code: str
    message: str


# ---------------------------------------------------------------- helpers


def _not_found() -> AppError:
    return AppError(404, TEMPLATE_NOT_FOUND, "Template not found.")


def _links(db: Session, template_id: int) -> list[TemplateQuestion]:
    return list(
        db.scalars(
            select(TemplateQuestion)
            .where(TemplateQuestion.template_id == template_id)
            .order_by(TemplateQuestion.position, TemplateQuestion.question_id)
            .execution_options(populate_existing=True)
        ).all()
    )


def _sections(links: list[TemplateQuestion]) -> list[Section]:
    """Group the questions by criterion, in the order each criterion first appears."""
    by_criterion: dict[int, Section] = {}
    for link in links:
        criterion = link.question.criterion
        by_criterion.setdefault(criterion.id, Section(criterion, [])).links.append(link)
    return list(by_criterion.values())


def _renumber(sections: list[Section]) -> None:
    """T4: number the questions 1, 2, 3 with no gaps, keeping each criterion's questions together."""
    position = 0
    for section in sections:
        for link in section.links:
            position += 1
            link.position = position


def _place_at_end_of_its_section(sections: list[Section], link: TemplateQuestion) -> None:
    """T4: a question goes to the end of its criterion's section, or starts a new section at the end."""
    criterion = link.question.criterion
    for section in sections:
        if section.criterion.id == criterion.id:
            section.links.append(link)
            return
    sections.append(Section(criterion, [link]))


# Row locks. Two requests at the same moment must not break a rule, so every write locks the rows
# its checks depend on, and reads them after the lock. Locks are always taken in this order:
# cycles, then questions, then criteria, each by id. One fixed order means two requests can never
# wait for each other. The reads after a lock need READ COMMITTED (app/database.py).


def lock_cycles(db: Session, cycle_ids: Iterable[int]) -> dict[int, Cycle]:
    """Lock cycle rows until the transaction ends. A locked cycle cannot be opened or deleted,
    and its templates cannot be changed by another request."""
    ids = sorted(set(cycle_ids))
    if not ids:
        return {}
    cycles = db.scalars(
        select(Cycle)
        .where(Cycle.id.in_(ids))
        .order_by(Cycle.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {cycle.id: cycle for cycle in cycles}


def lock_questions(db: Session, question_ids: Iterable[int]) -> dict[int, Question]:
    """Lock questions that are about to be put into a template, and their criteria.

    While they are locked, another request cannot delete or edit them. The criteria get a shared
    lock: they can still be read and used, but not renamed or deleted.
    """
    ids = sorted(set(question_ids))
    if not ids:
        return {}
    questions = db.scalars(
        select(Question)
        .where(Question.id.in_(ids))
        .order_by(Question.id)
        # "of" keeps the lock on the question rows. Without it the joined criteria rows are locked too.
        .with_for_update(of=Question)
        .execution_options(populate_existing=True)
    ).all()
    criterion_ids = sorted({question.criterion_id for question in questions})
    if criterion_ids:
        db.scalars(
            select(Criterion.id).where(Criterion.id.in_(criterion_ids)).order_by(Criterion.id).with_for_update(read=True)
        ).all()
    return {question.id: question for question in questions}


def lock_after_cycles[Row](db: Session, cycles_in_use: Select, lock_row: Callable[[], Row]) -> Row:
    """Lock the cycles that use a question or a criterion, then the row itself.

    The cycles come first because of the lock order. A template can start using the row between
    the two steps, so the cycles are read again after the row is locked. From then on no new
    template can use it. If a new cycle did appear, everything is released and tried again.
    """
    for _ in range(LOCK_ATTEMPTS):
        cycle_ids = set(db.scalars(cycles_in_use).all())
        lock_cycles(db, cycle_ids)
        row = lock_row()
        if set(db.scalars(cycles_in_use).all()) <= cycle_ids:
            return row
        db.rollback()
    raise AppError(409, TRY_AGAIN, "Other changes are being saved at the same time. Try again.")


def _load_for_change(db: Session, template_id: int, also_lock_cycle_id: int | None = None) -> Template:
    """Load a template for a change, with its cycle's row locked until the transaction ends.

    Locking the cycle makes the draft check safe against a cycle that is being opened at the same
    moment, and makes two changes to the same template run one after the other.
    """
    cycle_id = db.scalar(select(Template.cycle_id).where(Template.id == template_id))
    if cycle_id is None:
        raise _not_found()
    cycle = lock_cycles(db, {cycle_id, also_lock_cycle_id or cycle_id}).get(cycle_id)
    # Read the template after the lock, so it shows what other requests have saved.
    template = db.get(Template, template_id, populate_existing=True)
    if cycle is None or template is None:
        raise _not_found()
    # T2: a template can be changed only while its cycle is a draft.
    if cycle.status != CycleStatus.draft:
        raise AppError(
            409,
            TEMPLATE_LOCKED,
            f"A template can be changed only while its cycle is a draft. This cycle is {cycle.status.value}.",
        )
    return template


def _question_for_body(db: Session, question_id: int) -> Question:
    """A question named in a request body, locked. A missing one is a 400."""
    question = lock_questions(db, [question_id]).get(question_id)
    if question is None:
        raise bad_request(QUESTION_NOT_FOUND, "Question not found.")
    return question


def _unarchive(db: Session, actor_id: int, question: Question, template: Template) -> None:
    # Q10: adding an archived question to a template unarchives it. Q8: that writes an audit row.
    if question.archived_at is not None:
        question.archived_at = None
        _audit(db, actor_id, "unarchive", "questions", question.id, {"added_to_template_id": template.id})


def _find(links: list[TemplateQuestion], question_id: int) -> TemplateQuestion:
    for link in links:
        if link.question_id == question_id:
            return link
    raise AppError(404, QUESTION_NOT_IN_TEMPLATE, "This question is not in the template.")


def _check_weight(question: Question, weight: int | None) -> int | None:
    """T5: only rating questions carry a weight. The default is 1."""
    if question.kind == QuestionKind.text:
        if weight is not None:
            raise bad_request(TEXT_QUESTION_HAS_NO_WEIGHT, "A text question has no weight.")
        return None
    return DEFAULT_WEIGHT if weight is None else weight


def _detail(db: Session, template: Template) -> TemplateDetail:
    return TemplateDetail(template=template, sections=_sections(_links(db, template.id)))


# ------------------------------------------------------------------ reads


def list_templates(db: Session, cycle_id: int) -> list[TemplateRow]:
    if db.get(Cycle, cycle_id) is None:
        raise AppError(404, CYCLE_NOT_FOUND, "Cycle not found.")
    question_count = (
        select(func.count())
        .select_from(TemplateQuestion)
        .where(TemplateQuestion.template_id == Template.id)
        .scalar_subquery()
    )
    rows = db.execute(select(Template, question_count).where(Template.cycle_id == cycle_id)).all()
    order = list(ReviewType)
    rows = sorted(rows, key=lambda row: order.index(row[0].review_type))
    return [TemplateRow(template, count) for template, count in rows]


def get_template(db: Session, template_id: int) -> TemplateDetail:
    template = db.get(Template, template_id)
    if template is None:
        raise _not_found()
    return _detail(db, template)


# ----------------------------------------------------------------- writes
# T10: edits to a draft template are not written to the audit log.


def rename_template(db: Session, template_id: int, name: str) -> TemplateDetail:
    template = _load_for_change(db, template_id)
    template.name = name
    db.commit()
    return _detail(db, template)


def add_question(
    db: Session, actor_id: int, template_id: int, question_id: int, weight: int | None
) -> TemplateDetail:
    template = _load_for_change(db, template_id)
    question = _question_for_body(db, question_id)
    links = _links(db, template.id)
    # T3: a question appears at most once in a template.
    if any(link.question_id == question_id for link in links):
        raise AppError(409, QUESTION_ALREADY_IN_TEMPLATE, "This question is already in the template.")

    link = TemplateQuestion(
        template_id=template.id,
        question_id=question.id,
        position=len(links) + 1,
        weight=_check_weight(question, weight),
    )
    link.question = question
    db.add(link)
    sections = _sections(links)
    _place_at_end_of_its_section(sections, link)
    _renumber(sections)
    _unarchive(db, actor_id, question, template)
    db.commit()
    return _detail(db, template)


def remove_question(db: Session, template_id: int, question_id: int) -> TemplateDetail:
    template = _load_for_change(db, template_id)
    links = _links(db, template.id)
    link = _find(links, question_id)

    links.remove(link)
    db.delete(link)
    # Removing a question closes the gap (T4).
    _renumber(_sections(links))
    db.commit()
    return _detail(db, template)


def reorder_questions(db: Session, template_id: int, criterion_id: int, question_ids: list[int]) -> TemplateDetail:
    """TPL-6: the new order of the questions inside one criterion's section."""
    template = _load_for_change(db, template_id)
    sections = _sections(_links(db, template.id))
    section = next((s for s in sections if s.criterion.id == criterion_id), None)
    if section is None:
        raise AppError(404, CRITERION_NOT_IN_TEMPLATE, "The template has no question of this criterion.")
    by_id = {link.question_id: link for link in section.links}
    if len(question_ids) != len(by_id) or set(question_ids) != set(by_id):
        raise bad_request(
            ORDER_MISMATCH, "Send every question of this criterion's section exactly once, in the new order."
        )

    section.links = [by_id[question_id] for question_id in question_ids]
    _renumber(sections)
    db.commit()
    return _detail(db, template)


def reorder_criteria(db: Session, template_id: int, criterion_ids: list[int]) -> TemplateDetail:
    """TPL-7: the new order of the criterion sections."""
    template = _load_for_change(db, template_id)
    sections = _sections(_links(db, template.id))
    by_id = {section.criterion.id: section for section in sections}
    if len(criterion_ids) != len(by_id) or set(criterion_ids) != set(by_id):
        raise bad_request(ORDER_MISMATCH, "Send every criterion of this template exactly once, in the new order.")

    _renumber([by_id[criterion_id] for criterion_id in criterion_ids])
    db.commit()
    return _detail(db, template)


def change_weight(db: Session, template_id: int, question_id: int, weight: int) -> TemplateDetail:
    template = _load_for_change(db, template_id)
    link = _find(_links(db, template.id), question_id)
    if link.question.kind == QuestionKind.text:
        raise bad_request(TEXT_QUESTION_HAS_NO_WEIGHT, "A text question has no weight.")

    link.weight = weight
    db.commit()
    return _detail(db, template)


def replace_question(
    db: Session, actor_id: int, template_id: int, question_id: int, new_question_id: int
) -> TemplateDetail:
    """TPL-9: swap a question for another one, keeping its place and weight (T8)."""
    template = _load_for_change(db, template_id)
    links = _links(db, template.id)
    old_link = _find(links, question_id)
    if new_question_id == question_id:
        raise bad_request(SAME_QUESTION, "The replacement must be a different question.")
    new_question = _question_for_body(db, new_question_id)
    if any(link.question_id == new_question_id for link in links):
        raise AppError(409, QUESTION_ALREADY_IN_TEMPLATE, "The replacement question is already in the template.")
    if new_question.kind != old_link.question.kind:
        raise bad_request(
            KIND_MISMATCH,
            f"The replacement must be a {old_link.question.kind.value} question, like the one it replaces.",
        )

    new_link = TemplateQuestion(
        template_id=template.id, question_id=new_question.id, position=old_link.position, weight=old_link.weight
    )
    new_link.question = new_question
    same_criterion = new_question.criterion_id == old_link.question.criterion_id
    index = links.index(old_link)
    links.remove(old_link)
    db.delete(old_link)
    db.add(new_link)
    if same_criterion:
        links.insert(index, new_link)
        sections = _sections(links)
    else:
        # A different criterion: it moves to the end of that criterion's section.
        sections = _sections(links)
        _place_at_end_of_its_section(sections, new_link)
    _renumber(sections)
    _unarchive(db, actor_id, new_question, template)
    db.commit()
    return _detail(db, template)


def copy_from(db: Session, actor_id: int, template_id: int, source_template_id: int) -> TemplateDetail:
    """TPL-10: replace this template's questions, order and weights with a copy of another cycle's (T7)."""
    # The source's cycle is locked too, so the source cannot change while it is being copied.
    source_cycle_id = db.scalar(select(Template.cycle_id).where(Template.id == source_template_id))
    template = _load_for_change(db, template_id, also_lock_cycle_id=source_cycle_id)
    source = db.get(Template, source_template_id, populate_existing=True)
    if source is None:
        raise bad_request(SOURCE_TEMPLATE_NOT_FOUND, "Source template not found.")
    if source.cycle_id == template.cycle_id:
        raise bad_request(SAME_CYCLE, "The source template must belong to another cycle.")
    if source.review_type != template.review_type:
        raise bad_request(
            REVIEW_TYPE_MISMATCH,
            f"The source must be a {template.review_type.value} template, like the one it is copied into.",
        )

    _copy_links(db, actor_id, source, template)
    db.commit()
    return _detail(db, template)


def _copy_links(db: Session, actor_id: int, source: Template, target: Template) -> None:
    """The caller holds the lock on both cycles, so neither template changes during the copy."""
    links = _links(db, source.id)
    lock_questions(db, [link.question_id for link in links])
    db.execute(delete(TemplateQuestion).where(TemplateQuestion.template_id == target.id))
    for link in links:
        db.add(
            TemplateQuestion(
                template_id=target.id, question_id=link.question_id, position=link.position, weight=link.weight
            )
        )
        _unarchive(db, actor_id, link.question, target)
    target.copied_from_id = source.id
    db.flush()


# ------------------------------------------- called when a question is edited


def sync_question_change(db: Session, question: Question, *, criterion_changed: bool) -> None:
    """Q5: after a question's criterion or kind changed, fix every template that uses it.

    Only draft templates can be affected, because a question in an opened cycle cannot be edited.
    The question moves to the end of its new criterion's section, and its weight follows its kind.
    It runs inside the caller's transaction and never commits. The caller holds the lock on every
    cycle that uses the question (lock_after_cycles), so no other request changes these templates.
    """
    template_ids = db.scalars(
        select(TemplateQuestion.template_id).where(TemplateQuestion.question_id == question.id)
    ).all()
    for template_id in template_ids:
        links = _links(db, template_id)
        link = _find(links, question.id)
        if question.kind == QuestionKind.text:
            link.weight = None
        elif link.weight is None:
            link.weight = DEFAULT_WEIGHT

        if criterion_changed:
            links.remove(link)
            sections = _sections(links)
            _place_at_end_of_its_section(sections, link)
            _renumber(sections)
    db.flush()


# ------------------------------------------------ called by the cycle steps
# Each one runs inside the caller's transaction and never commits.


def create_default_templates(db: Session, cycle: Cycle) -> list[Template]:
    """T9: a new cycle gets its four templates, empty."""
    created = [
        Template(cycle_id=cycle.id, review_type=review_type, name=DEFAULT_NAMES[review_type])
        for review_type in ReviewType
    ]
    db.add_all(created)
    db.flush()
    return created


def copy_cycle_templates(db: Session, actor_id: int, source: Cycle, cycle: Cycle) -> list[Template]:
    """CYC-2, T9: a copied cycle gets the source's four templates with their questions, order and weights."""
    source_templates = {
        template.review_type: template
        for template in db.scalars(select(Template).where(Template.cycle_id == source.id)).all()
    }
    created = []
    for review_type in ReviewType:
        original = source_templates.get(review_type)
        template = Template(
            cycle_id=cycle.id,
            review_type=review_type,
            name=original.name if original else DEFAULT_NAMES[review_type],
        )
        db.add(template)
        db.flush()
        if original is not None:
            _copy_links(db, actor_id, original, template)
        created.append(template)
    return created


def delete_cycle_templates(db: Session, cycle: Cycle) -> None:
    """CYC-8: delete a draft cycle's templates and their question links.

    MySQL also does this through ON DELETE CASCADE when the cycle row is deleted.
    """
    template_ids = select(Template.id).where(Template.cycle_id == cycle.id)
    db.execute(delete(TemplateQuestion).where(TemplateQuestion.template_id.in_(template_ids)))
    db.execute(delete(Template).where(Template.cycle_id == cycle.id))
    db.flush()


def readiness_problems(db: Session, cycle: Cycle) -> list[TemplateProblem]:
    """C3, T6: what stops the cycle from opening, as far as its templates go.

    The check that the score weights add up to 100 (R7) belongs to cluster 9.
    """
    counts = {
        review_type: (total, ratings)
        for review_type, total, ratings in db.execute(
            select(
                Template.review_type,
                func.count(TemplateQuestion.question_id),
                # Only rating questions carry a weight, so this counts the rating questions.
                func.count(TemplateQuestion.weight),
            )
            .outerjoin(TemplateQuestion, TemplateQuestion.template_id == Template.id)
            .where(Template.cycle_id == cycle.id)
            .group_by(Template.id, Template.review_type)
        ).all()
    }
    problems = []
    for review_type in ReviewType:
        label = review_type.value
        if review_type not in counts:
            problems.append(TemplateProblem(TEMPLATE_MISSING, f"The cycle has no {label} template."))
            continue
        total, ratings = counts[review_type]
        if total == 0:
            problems.append(TemplateProblem(TEMPLATE_EMPTY, f"The {label} template has no questions."))
        elif ratings == 0:
            problems.append(
                TemplateProblem(TEMPLATE_NO_RATING_QUESTION, f"The {label} template has no rating question.")
            )
    return problems


# ------------------------------------------------------------------ hooks


def _audit(
    db: Session, actor_id: int, action: str, table: str, record_id: int, details: dict | None = None
) -> None:
    # TODO(Q8): write the audit_log row here, inside the caller's transaction (before commit).
    # The audit log is cluster 11. Same stub as in services/question_bank.py.
    return None
