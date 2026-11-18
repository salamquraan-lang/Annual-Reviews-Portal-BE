from dataclasses import dataclass, field
from typing import TypedDict

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError, bad_request
from app.models import (
    Criterion,
    Cycle,
    CycleStatus,
    Question,
    QuestionKind,
    Template,
    TemplateQuestion,
    utcnow,
)
from app.services import templates

# Error codes. Message text lives next to the line that raises it.
CRITERION_NOT_FOUND = "CRITERION_NOT_FOUND"
CRITERION_NAME_TAKEN = "CRITERION_NAME_TAKEN"
CRITERION_LOCKED = "CRITERION_LOCKED"
CRITERION_HAS_QUESTIONS = "CRITERION_HAS_QUESTIONS"
QUESTION_NOT_FOUND = "QUESTION_NOT_FOUND"
QUESTION_LOCKED = "QUESTION_LOCKED"
QUESTION_IN_USE = "QUESTION_IN_USE"

# Q4: an opened cycle is one that is open, closed or finalized.
OPENED = (CycleStatus.open, CycleStatus.closed, CycleStatus.finalized)

# Q12: a question is archived when none of the last 3 opened cycles uses it.
ARCHIVE_AFTER_CYCLES = 3
AUTO_ARCHIVE_REASON = "unused for 3 cycles"


class CriterionChanges(TypedDict, total=False):
    """The fields of an update. A missing key means the field was not sent."""

    name: str
    description: str | None


class QuestionChanges(TypedDict, total=False):
    text: str
    criterion_id: int
    kind: QuestionKind


@dataclass
class CriterionRow:
    criterion: Criterion
    question_count: int


@dataclass
class CycleRef:
    id: int
    name: str
    status: CycleStatus


@dataclass
class QuestionRow:
    question: Question
    used: bool
    last_used_cycle: CycleRef | None


@dataclass
class QuestionPage:
    items: list[QuestionRow]
    total: int


@dataclass
class Usage:
    template_id: int
    template_name: str
    review_type: str
    cycle: CycleRef


@dataclass
class QuestionDetail:
    question: Question
    locked: bool
    usage: list[Usage] = field(default_factory=list)


# ---------------------------------------------------------------- helpers


def _criterion_not_found() -> AppError:
    return AppError(404, CRITERION_NOT_FOUND, "Criterion not found.")


def _question_not_found() -> AppError:
    return AppError(404, QUESTION_NOT_FOUND, "Question not found.")


def _name_taken() -> AppError:
    return AppError(409, CRITERION_NAME_TAKEN, "A criterion with this name already exists.")


def name_key(name: str) -> str:
    """Q1: spaces and letter case are ignored when two criterion names are compared."""
    return "".join(name.split()).casefold()


def _name_is_taken(db: Session, name: str, exclude_id: int | None = None) -> bool:
    conditions = [Criterion.name_key == name_key(name)]
    if exclude_id is not None:
        conditions.append(Criterion.id != exclude_id)
    return db.scalar(select(select(Criterion.id).where(*conditions).exists())) is True


def _flush_criterion(db: Session) -> None:
    """The check before this gives a friendly early answer. The unique index is the real guard,
    so a race between two requests ends here as the same 409."""
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _name_taken() from None


# Row locks follow the order in services/templates.py: cycles, then questions, then criteria.


def _lock_criterion(db: Session, criterion_id: int) -> Criterion:
    criterion = db.scalar(
        select(Criterion)
        .where(Criterion.id == criterion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if criterion is None:
        raise _criterion_not_found()
    return criterion


def _lock_question(db: Session, question_id: int) -> Question:
    question = db.scalar(
        select(Question)
        .where(Question.id == question_id)
        # "of" keeps the lock on the question row. Without it the joined criterion row is locked too.
        .with_for_update(of=Question)
        .execution_options(populate_existing=True)
    )
    if question is None:
        raise _question_not_found()
    return question


def _cycles_using(*conditions) -> Select:
    """The ids of the cycles with a template that uses a question that matches the conditions."""
    return (
        select(Template.cycle_id)
        .join(TemplateQuestion, TemplateQuestion.template_id == Template.id)
        .join(Question, Question.id == TemplateQuestion.question_id)
        .where(*conditions)
        .distinct()
    )


def _lock_question_for_edit(db: Session, question_id: int) -> Question:
    """Lock the question and every cycle that uses it.

    With the cycles locked, none of them can be opened while the question is edited (Q4), and
    their templates stay still while the edit is carried into them (Q5).
    """
    return templates.lock_after_cycles(
        db, _cycles_using(Question.id == question_id), lambda: _lock_question(db, question_id)
    )


def _lock_criterion_for_edit(db: Session, criterion_id: int) -> Criterion:
    """Lock the criterion and every cycle that uses one of its questions (Q4)."""
    return templates.lock_after_cycles(
        db, _cycles_using(Question.criterion_id == criterion_id), lambda: _lock_criterion(db, criterion_id)
    )


def _is_used(db: Session, question_id: int) -> bool:
    return (
        db.scalar(select(select(TemplateQuestion.question_id).where(TemplateQuestion.question_id == question_id).exists()))
        is True
    )


def _in_opened_cycle(db: Session, *conditions) -> bool:
    """True if a template of an opened cycle uses a question that matches the conditions."""
    return (
        db.scalar(
            select(
                select(TemplateQuestion.question_id)
                .join(Question, Question.id == TemplateQuestion.question_id)
                .join(Template, Template.id == TemplateQuestion.template_id)
                .join(Cycle, Cycle.id == Template.cycle_id)
                .where(Cycle.status.in_(OPENED), *conditions)
                .exists()
            )
        )
        is True
    )


def _last_used_cycles(db: Session, question_ids: list[int]) -> dict[int, CycleRef]:
    """For each question, the newest cycle with a template that uses it."""
    if not question_ids:
        return {}
    rows = db.execute(
        select(TemplateQuestion.question_id, Cycle)
        .join(Template, Template.id == TemplateQuestion.template_id)
        .join(Cycle, Cycle.id == Template.cycle_id)
        .where(TemplateQuestion.question_id.in_(question_ids))
        .order_by(Cycle.year.desc(), Cycle.start_date.desc(), Cycle.id.desc())
    ).all()
    newest: dict[int, CycleRef] = {}
    for question_id, cycle in rows:
        newest.setdefault(question_id, CycleRef(cycle.id, cycle.name, cycle.status))
    return newest


def _require_criterion(db: Session, criterion_id: int) -> Criterion:
    """A criterion named in a request body. A missing one is a 400, like a missing parent department.

    It gets a shared lock, so it cannot be deleted before the question that points at it is saved.
    """
    criterion = db.scalar(
        select(Criterion)
        .where(Criterion.id == criterion_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if criterion is None:
        raise bad_request(CRITERION_NOT_FOUND, "Criterion not found.")
    return criterion


# ---------------------------------------------------------------- criteria


def list_criteria(db: Session) -> list[CriterionRow]:
    rows = db.execute(
        select(Criterion, func.count(Question.id))
        .outerjoin(Question, Question.criterion_id == Criterion.id)
        .group_by(Criterion.id)
        .order_by(Criterion.name, Criterion.id)
    ).all()
    return [CriterionRow(criterion, count) for criterion, count in rows]


def get_criterion(db: Session, criterion_id: int) -> tuple[Criterion, list[Question]]:
    criterion = db.get(Criterion, criterion_id)
    if criterion is None:
        raise _criterion_not_found()
    questions = db.scalars(
        select(Question).where(Question.criterion_id == criterion_id).order_by(Question.id)
    ).all()
    return criterion, list(questions)


def create_criterion(db: Session, actor_id: int, name: str, description: str | None) -> Criterion:
    if _name_is_taken(db, name):
        raise _name_taken()

    criterion = Criterion(name=name, name_key=name_key(name), description=description)
    db.add(criterion)
    _flush_criterion(db)
    _audit(db, actor_id, "create", "criteria", criterion.id, {"name": name, "description": description})
    db.commit()
    return criterion


def update_criterion(db: Session, actor_id: int, criterion_id: int, changes: CriterionChanges) -> Criterion:
    criterion = _lock_criterion_for_edit(db, criterion_id)

    new = {key: value for key, value in changes.items() if getattr(criterion, key) != value}
    if not new:
        return criterion
    # Q4: a criterion that a template of an opened cycle uses cannot be edited.
    if _in_opened_cycle(db, Question.criterion_id == criterion_id):
        raise AppError(
            409,
            CRITERION_LOCKED,
            "A cycle that was opened uses this criterion, so it cannot be edited. Create a new criterion.",
        )
    if "name" in new and _name_is_taken(db, new["name"], exclude_id=criterion_id):
        raise _name_taken()

    old = {key: getattr(criterion, key) for key in new}
    for key, value in new.items():
        setattr(criterion, key, value)
    criterion.name_key = name_key(criterion.name)
    _flush_criterion(db)
    _audit(db, actor_id, "update", "criteria", criterion.id, {"from": old, "to": new})
    db.commit()
    return criterion


def delete_criterion(db: Session, actor_id: int, criterion_id: int) -> None:
    criterion = _lock_criterion(db, criterion_id)
    # Q6: a criterion can be deleted only if it has no questions.
    count = db.scalar(select(func.count()).select_from(Question).where(Question.criterion_id == criterion_id)) or 0
    if count:
        raise AppError(
            409, CRITERION_HAS_QUESTIONS, f"The criterion still has {count} question(s). Delete or move them first."
        )

    details = {"name": criterion.name, "description": criterion.description}
    db.delete(criterion)
    db.flush()
    _audit(db, actor_id, "delete", "criteria", criterion_id, details)
    db.commit()


# --------------------------------------------------------------- questions


def list_questions(
    db: Session,
    *,
    criterion_id: int | None = None,
    kind: QuestionKind | None = None,
    used: bool | None = None,
    show_archived: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> QuestionPage:
    is_used = select(TemplateQuestion.question_id).where(TemplateQuestion.question_id == Question.id).exists()

    conditions = []
    if criterion_id is not None:
        conditions.append(Question.criterion_id == criterion_id)
    if kind is not None:
        conditions.append(Question.kind == kind)
    if used is not None:
        conditions.append(is_used if used else ~is_used)
    if not show_archived:
        # Q10: lists hide archived questions unless "show archived" is on.
        conditions.append(Question.archived_at.is_(None))
    if search:
        conditions.append(func.lower(Question.text).contains(search.lower(), autoescape=True))

    total = db.scalar(select(func.count()).select_from(Question).where(*conditions)) or 0
    rows = db.execute(
        select(Question, is_used)
        .where(*conditions)
        .order_by(Question.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    last_used = _last_used_cycles(db, [question.id for question, _ in rows])
    items = [QuestionRow(question, bool(row_used), last_used.get(question.id)) for question, row_used in rows]
    return QuestionPage(items=items, total=total)


def get_question(db: Session, question_id: int) -> QuestionDetail:
    question = db.get(Question, question_id)
    if question is None:
        raise _question_not_found()
    return _detail(db, question)


def _detail(db: Session, question: Question) -> QuestionDetail:
    rows = db.execute(
        select(Template, Cycle)
        .join(TemplateQuestion, TemplateQuestion.template_id == Template.id)
        .join(Cycle, Cycle.id == Template.cycle_id)
        .where(TemplateQuestion.question_id == question.id)
        .order_by(Cycle.year.desc(), Cycle.start_date.desc(), Cycle.id.desc(), Template.id)
    ).all()
    usage = [
        Usage(
            template_id=template.id,
            template_name=template.name,
            review_type=template.review_type.value,
            cycle=CycleRef(cycle.id, cycle.name, cycle.status),
        )
        for template, cycle in rows
    ]
    locked = any(item.cycle.status in OPENED for item in usage)
    return QuestionDetail(question=question, locked=locked, usage=usage)


def create_question(db: Session, actor_id: int, criterion_id: int, text: str, kind: QuestionKind) -> QuestionDetail:
    _require_criterion(db, criterion_id)

    question = Question(criterion_id=criterion_id, text=text, kind=kind)
    db.add(question)
    db.flush()
    _audit(
        db,
        actor_id,
        "create",
        "questions",
        question.id,
        {"criterion_id": criterion_id, "text": text, "kind": kind.value},
    )
    db.commit()
    return _detail(db, question)


def update_question(db: Session, actor_id: int, question_id: int, changes: QuestionChanges) -> QuestionDetail:
    question = _lock_question_for_edit(db, question_id)

    new = {key: value for key, value in changes.items() if getattr(question, key) != value}
    if not new:
        return _detail(db, question)
    # Q4: a question that a template of an opened cycle uses cannot be edited.
    if _in_opened_cycle(db, Question.id == question_id):
        raise AppError(
            409,
            QUESTION_LOCKED,
            "A cycle that was opened uses this question, so it cannot be edited. Copy it and change the copy.",
        )
    if "criterion_id" in new:
        _require_criterion(db, new["criterion_id"])

    old = {key: getattr(question, key) for key in new}
    for key, value in new.items():
        setattr(question, key, value)
    db.flush()
    db.refresh(question)
    if "criterion_id" in new or "kind" in new:
        # Q5: the change shows in every draft template that uses the question.
        templates.sync_question_change(db, question, criterion_changed="criterion_id" in new)
    _audit(db, actor_id, "update", "questions", question.id, {"from": _plain(old), "to": _plain(new)})
    db.commit()
    return _detail(db, question)


def copy_question(db: Session, actor_id: int, question_id: int, changes: QuestionChanges) -> QuestionDetail:
    """QB-10: a new question that starts as a copy. Fields sent in the request replace the copied ones."""
    source = db.get(Question, question_id)
    if source is None:
        raise _question_not_found()
    criterion_id = changes.get("criterion_id", source.criterion_id)
    try:
        _require_criterion(db, criterion_id)
    except AppError:
        if "criterion_id" in changes:
            raise
        # The source's own criterion is gone, so the source was deleted a moment ago.
        raise _question_not_found() from None

    copy = Question(
        criterion_id=criterion_id,
        text=changes.get("text", source.text),
        kind=changes.get("kind", source.kind),
    )
    db.add(copy)
    db.flush()
    _audit(
        db,
        actor_id,
        "create",
        "questions",
        copy.id,
        {"criterion_id": copy.criterion_id, "text": copy.text, "kind": copy.kind.value, "copied_from_id": source.id},
    )
    db.commit()
    return _detail(db, copy)


def delete_question(db: Session, actor_id: int, question_id: int) -> None:
    question = _lock_question(db, question_id)
    # Q6: a question can be deleted only if no template uses it.
    if _is_used(db, question_id):
        raise AppError(409, QUESTION_IN_USE, "A template uses this question, so it cannot be deleted.")

    details = {"criterion_id": question.criterion_id, "text": question.text, "kind": question.kind.value}
    db.execute(delete(Question).where(Question.id == question_id))
    db.flush()
    _audit(db, actor_id, "delete", "questions", question_id, details)
    db.commit()


def archive_question(db: Session, actor_id: int, question_id: int) -> QuestionDetail:
    """QB-12. Archiving is not an edit, so it works even when an opened cycle uses the question (Q11)."""
    question = _lock_question(db, question_id)
    if question.archived_at is None:
        question.archived_at = utcnow()
        db.flush()
        _audit(db, actor_id, "archive", "questions", question.id)
        db.commit()
    return _detail(db, question)


def unarchive_question(db: Session, actor_id: int, question_id: int) -> QuestionDetail:
    question = _lock_question(db, question_id)
    if question.archived_at is not None:
        question.archived_at = None
        db.flush()
        _audit(db, actor_id, "unarchive", "questions", question.id)
        db.commit()
    return _detail(db, question)


# ------------------------------------------------- called when a cycle opens


def archive_unused_questions(db: Session, cycle: Cycle, actor_id: int) -> list[int]:
    """QB-14: archive the questions that Q12 names. Returns the ids it archived.

    It runs inside the caller's transaction while the cycle is being opened, and never commits.
    A question is archived if it was used before, no template of the last 3 opened cycles uses it
    (the cycle being opened counts as one), and no draft cycle's template uses it. A question that
    was never used is left alone (Q13).
    """
    earlier = db.scalars(
        select(Cycle.id)
        .where(Cycle.status.in_(OPENED), Cycle.id != cycle.id)
        .order_by(Cycle.year.desc(), Cycle.start_date.desc(), Cycle.id.desc())
        .limit(ARCHIVE_AFTER_CYCLES - 1)
    ).all()
    recent_ids = [cycle.id, *earlier]

    def used_in(*conditions) -> set[int]:
        return set(
            db.scalars(
                select(TemplateQuestion.question_id)
                .join(Template, Template.id == TemplateQuestion.template_id)
                .join(Cycle, Cycle.id == Template.cycle_id)
                .where(*conditions)
                .distinct()
            ).all()
        )

    used_before = used_in(Cycle.status.in_(OPENED) | (Cycle.id == cycle.id))
    def still_needed() -> set[int]:
        return used_in(Cycle.id.in_(recent_ids)) | used_in(Cycle.status == CycleStatus.draft, Cycle.id != cycle.id)

    candidates = used_before - still_needed()
    if not candidates:
        return []

    # Lock the candidates, then check again. A locked question cannot be added to a template,
    # so a question that another request put into a draft a moment ago is not archived.
    locked = templates.lock_questions(db, candidates)
    needed_now = still_needed()
    questions = [
        question
        for question_id, question in sorted(locked.items())
        if question.archived_at is None and question_id not in needed_now
    ]
    now = utcnow()
    for question in questions:
        question.archived_at = now
        # Q8: one audit row per question, under the admin who opened the cycle.
        _audit(
            db,
            actor_id,
            "archive",
            "questions",
            question.id,
            {"reason": AUTO_ARCHIVE_REASON, "cycle_id": cycle.id},
        )
    db.flush()
    return [question.id for question in questions]


# ------------------------------------------------------------------ hooks


def _plain(values: dict) -> dict:
    """Turn enums into plain values, so an audit row can store them as JSON."""
    return {key: value.value if isinstance(value, QuestionKind) else value for key, value in values.items()}


def _audit(
    db: Session, actor_id: int, action: str, table: str, record_id: int, details: dict | None = None
) -> None:
    # TODO(Q8): write the audit_log row here, inside the caller's transaction (before commit).
    # The audit log is cluster 11. Same stub as in services/departments.py and services/cycles.py.
    return None
