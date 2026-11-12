from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import TypedDict

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError, bad_request
from app.models import Cycle, CycleStatus, CycleType, utcnow

# Error codes. Message text lives next to the line that raises it.
NOT_FOUND = "CYCLE_NOT_FOUND"
NO_OPEN_CYCLE = "NO_OPEN_CYCLE"
NOT_DRAFT = "CYCLE_NOT_DRAFT"
INVALID_STATUS = "CYCLE_INVALID_STATUS"
NOT_READY = "CYCLE_NOT_READY"
ANOTHER_OPEN = "ANOTHER_CYCLE_OPEN"
YEAR_TYPE_TAKEN = "CYCLE_YEAR_TYPE_TAKEN"
INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
END_DATE_IN_PAST = "END_DATE_IN_PAST"
BANDS_INCOMPLETE = "REWARD_BANDS_INCOMPLETE"

MYSQL_DUPLICATE_ENTRY = 1062

ONE_OPEN_KEY = "cycles_one_open_key"
YEAR_TYPE_KEY = "cycles_year_type_key"


@dataclass
class NewCycle:
    name: str
    type: CycleType
    year: int
    start_date: date
    end_date: date


class CycleChanges(TypedDict, total=False):
    """The fields of an update. A missing key means the field was not sent."""

    name: str
    type: CycleType
    year: int
    start_date: date
    end_date: date


@dataclass
class CyclePage:
    items: list[Cycle]
    total: int


@dataclass
class Problem:
    code: str
    message: str


@dataclass
class FinalizeWarning:
    code: str
    user_id: int
    message: str


@dataclass
class FinalizeResult:
    cycle: Cycle
    warnings: list[FinalizeWarning] = field(default_factory=list)


# ---------------------------------------------------------------- helpers


def _not_found() -> AppError:
    return AppError(404, NOT_FOUND, "Cycle not found.")


def _year_type_taken_error() -> AppError:
    return AppError(409, YEAR_TYPE_TAKEN, "This year already has a cycle of this type.")


def _another_open_error() -> AppError:
    return AppError(409, ANOTHER_OPEN, "Another cycle is already open. Close it first.")


def _lock_cycle(db: Session, cycle_id: int) -> Cycle:
    """Load the cycle and lock its row until the transaction ends.

    Locking comes first, so every plain read after it sees the latest committed data.
    """
    cycle = db.scalar(
        select(Cycle).where(Cycle.id == cycle_id).with_for_update().execution_options(populate_existing=True)
    )
    if cycle is None:
        raise _not_found()
    return cycle


def _require_status(cycle: Cycle, *allowed: CycleStatus) -> None:
    if cycle.status not in allowed:
        needed = " or ".join(status.value for status in allowed)
        raise AppError(
            409, INVALID_STATUS, f"The cycle is {cycle.status.value}. This action needs it to be {needed}."
        )


def _require_draft(cycle: Cycle) -> None:
    # C2 and C9: only a draft can be edited or deleted. A finalized cycle is read-only for good.
    if cycle.status != CycleStatus.draft:
        raise AppError(409, NOT_DRAFT, f"Only a draft cycle can be changed. This cycle is {cycle.status.value}.")


def _year_type_taken(db: Session, year: int, cycle_type: CycleType, exclude_id: int | None = None) -> bool:
    conditions = [Cycle.year == year, Cycle.type == cycle_type]
    if exclude_id is not None:
        conditions.append(Cycle.id != exclude_id)
    return db.scalar(select(select(Cycle.id).where(*conditions).exists())) is True


def _other_open_cycle_exists(db: Session, exclude_id: int | None = None) -> bool:
    conditions = [Cycle.status == CycleStatus.open]
    if exclude_id is not None:
        conditions.append(Cycle.id != exclude_id)
    return db.scalar(select(select(Cycle.id).where(*conditions).exists())) is True


def _require_no_other_open(db: Session, cycle: Cycle) -> None:
    if _other_open_cycle_exists(db, exclude_id=cycle.id):
        raise _another_open_error()


def _duplicate_error(exc: IntegrityError) -> AppError | None:
    """Map a MySQL duplicate-key error to the matching 409. Anything else returns None."""
    orig = exc.orig
    if orig is None or len(orig.args) < 2 or orig.args[0] != MYSQL_DUPLICATE_ENTRY:
        return None
    message = str(orig.args[1])
    if ONE_OPEN_KEY in message:
        return _another_open_error()
    if YEAR_TYPE_KEY in message:
        return _year_type_taken_error()
    return None


def _flush(db: Session) -> None:
    """Send pending changes to the database.

    The checks before this give a friendly early answer. The unique indexes are the real guard,
    so a race between two requests ends here as the same 409.
    """
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        error = _duplicate_error(exc)
        if error is not None:
            raise error from None
        raise


def _plain(values: dict) -> dict:
    """Turn dates and enums into plain values, so an audit row can store them as JSON."""
    return {
        key: value.isoformat() if isinstance(value, date) else value.value if isinstance(value, Enum) else value
        for key, value in values.items()
    }


# ------------------------------------------------------------------ reads


def list_cycles(
    db: Session,
    *,
    status: CycleStatus | None = None,
    year: int | None = None,
    type: CycleType | None = None,  # noqa: A002
    page: int = 1,
    page_size: int = 20,
) -> CyclePage:
    conditions = []
    if status is not None:
        conditions.append(Cycle.status == status)
    if year is not None:
        conditions.append(Cycle.year == year)
    if type is not None:
        conditions.append(Cycle.type == type)

    total = db.scalar(select(func.count()).select_from(Cycle).where(*conditions)) or 0
    items = db.scalars(
        select(Cycle)
        .where(*conditions)
        .order_by(Cycle.year.desc(), Cycle.start_date.desc(), Cycle.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return CyclePage(items=list(items), total=total)


def get_cycle(db: Session, cycle_id: int) -> Cycle:
    cycle = db.get(Cycle, cycle_id)
    if cycle is None:
        raise _not_found()
    return cycle


def get_open_cycle(db: Session) -> Cycle:
    cycle = db.scalar(select(Cycle).where(Cycle.status == CycleStatus.open))
    if cycle is None:
        raise AppError(404, NO_OPEN_CYCLE, "No cycle is open right now.")
    return cycle


def check_readiness(db: Session, cycle_id: int) -> list[Problem]:
    cycle = get_cycle(db, cycle_id)
    _require_status(cycle, CycleStatus.draft)
    problems = []
    # The start date is never after the end date. The database refuses such a row (C3).
    if _other_open_cycle_exists(db, exclude_id=cycle.id):
        problems.append(Problem(ANOTHER_OPEN, "Another cycle is already open. Close it first."))
    problems.extend(_template_problems(db, cycle))
    return problems


# ----------------------------------------------------------------- writes


def create_cycle(db: Session, actor_id: int, data: NewCycle) -> Cycle:
    if _year_type_taken(db, data.year, data.type):
        raise _year_type_taken_error()

    cycle = Cycle(
        name=data.name,
        type=data.type,
        year=data.year,
        start_date=data.start_date,
        end_date=data.end_date,
        status=CycleStatus.draft,
    )
    db.add(cycle)
    _flush(db)
    _create_default_templates(db, cycle)
    _audit(db, actor_id, "create", cycle.id, _plain(asdict(data)))
    db.commit()
    return cycle


def copy_cycle(db: Session, actor_id: int, source_id: int, data: NewCycle) -> Cycle:
    source = _lock_cycle(db, source_id)
    if _year_type_taken(db, data.year, data.type):
        raise _year_type_taken_error()

    cycle = Cycle(
        name=data.name,
        type=data.type,
        year=data.year,
        start_date=data.start_date,
        end_date=data.end_date,
        status=CycleStatus.draft,
    )
    db.add(cycle)
    _flush(db)
    _copy_contents(db, source, cycle)
    _audit(db, actor_id, "create", cycle.id, _plain(asdict(data)) | {"copied_from_id": source.id})
    db.commit()
    return cycle


def update_cycle(db: Session, actor_id: int, cycle_id: int, changes: CycleChanges) -> Cycle:
    cycle = _lock_cycle(db, cycle_id)
    _require_draft(cycle)

    new = {key: value for key, value in changes.items() if getattr(cycle, key) != value}
    if not new:
        return cycle

    start_date = new.get("start_date", cycle.start_date)
    end_date = new.get("end_date", cycle.end_date)
    if start_date > end_date:
        raise bad_request(INVALID_DATE_RANGE, "Start date must not be after end date.")
    if ("year" in new or "type" in new) and _year_type_taken(
        db, new.get("year", cycle.year), new.get("type", cycle.type), exclude_id=cycle.id
    ):
        raise _year_type_taken_error()

    old = {key: getattr(cycle, key) for key in new}
    for key, value in new.items():
        setattr(cycle, key, value)
    _flush(db)
    _audit(db, actor_id, "update", cycle.id, {"from": _plain(old), "to": _plain(new)})
    db.commit()
    return cycle


def change_end_date(db: Session, actor_id: int, cycle_id: int, end_date: date) -> Cycle:
    cycle = _lock_cycle(db, cycle_id)
    _require_status(cycle, CycleStatus.open)
    if end_date == cycle.end_date:
        return cycle
    if end_date < cycle.start_date:
        raise bad_request(INVALID_DATE_RANGE, "The end date must not be before the start date.")
    if end_date < utcnow().date():
        raise bad_request(END_DATE_IN_PAST, "The end date cannot be in the past.")

    old_end_date = cycle.end_date
    cycle.end_date = end_date
    _flush(db)
    _audit(db, actor_id, "change_end_date", cycle.id, {"from": old_end_date.isoformat(), "to": end_date.isoformat()})
    db.commit()
    return cycle


def delete_cycle(db: Session, actor_id: int, cycle_id: int) -> None:
    cycle = _lock_cycle(db, cycle_id)
    _require_draft(cycle)

    details = _plain({"name": cycle.name, "type": cycle.type, "year": cycle.year})
    _delete_templates(db, cycle)
    db.delete(cycle)
    _flush(db)
    _audit(db, actor_id, "delete", cycle_id, details)
    db.commit()


def open_cycle(db: Session, actor_id: int, cycle_id: int) -> Cycle:
    cycle = _lock_cycle(db, cycle_id)
    _require_status(cycle, CycleStatus.draft)
    _require_no_other_open(db, cycle)
    problems = _template_problems(db, cycle)
    if problems:
        raise AppError(
            409,
            NOT_READY,
            "The cycle is not ready to open.",
            details=[{"code": problem.code, "message": problem.message} for problem in problems],
        )

    # Change the status first. If another request opened a cycle at the same moment,
    # the unique index refuses it here, before any slow work starts.
    cycle.status = CycleStatus.open
    _flush(db)
    _take_snapshot_and_assign(db, cycle)
    _archive_unused_questions(db, cycle)
    _audit(db, actor_id, "open", cycle.id)
    db.commit()
    return cycle


def close_cycle(db: Session, actor_id: int, cycle_id: int) -> Cycle:
    cycle = _lock_cycle(db, cycle_id)
    _require_status(cycle, CycleStatus.open)

    cycle.status = CycleStatus.closed
    _flush(db)
    _calculate_scores(db, cycle)
    _audit(db, actor_id, "close", cycle.id)
    db.commit()
    return cycle


def reopen_cycle(db: Session, actor_id: int, cycle_id: int) -> Cycle:
    cycle = _lock_cycle(db, cycle_id)
    _require_status(cycle, CycleStatus.closed)
    _require_no_other_open(db, cycle)

    cycle.status = CycleStatus.open
    _flush(db)
    _discard_results(db, cycle)
    _audit(db, actor_id, "reopen", cycle.id)
    db.commit()
    return cycle


def finalize_cycle(db: Session, actor_id: int, cycle_id: int) -> FinalizeResult:
    cycle = _lock_cycle(db, cycle_id)
    _require_status(cycle, CycleStatus.closed)
    problems = _reward_band_problems(db, cycle)
    if problems:
        raise AppError(
            409,
            BANDS_INCOMPLETE,
            "The reward bands must cover every score from 1.00 to 5.00 with no gap and no overlap.",
            details=[{"code": problem.code, "message": problem.message} for problem in problems],
        )

    cycle.status = CycleStatus.finalized
    _flush(db)
    warnings = _create_reward_proposals(db, cycle)
    _audit(db, actor_id, "finalize", cycle.id)
    db.commit()
    return FinalizeResult(cycle=cycle, warnings=warnings)


# ------------------------------------------------------------------ hooks
# Steps that belong to other clusters. Each one runs inside the caller's transaction, before its
# commit, and must never commit. Each stays a stub until its cluster exists. The list of all
# hooks is at the end of the file.


def _audit(db: Session, actor_id: int, action: str, cycle_id: int, details: dict | None = None) -> None:
    # TODO(C12): write the audit row here, inside the caller's transaction (before commit).
    # Do not merge this branch until this is done. Same stub as in services/departments.py.
    return None


def _create_default_templates(db: Session, cycle: Cycle) -> None:
    # TODO(T9, S8): create the cycle's four empty templates (self, manager, peer, upward)
    # and its default score weights. Needs clusters 6 and 9.
    return None


def _copy_contents(db: Session, source: Cycle, cycle: Cycle) -> None:
    # TODO(CYC-2, S8): copy the source's four templates with their questions, order and weights,
    # its reward rules and its score weights into the new cycle. Participants are not copied.
    # Needs clusters 6, 9 and 10.
    return None


def _delete_templates(db: Session, cycle: Cycle) -> None:
    # TODO(CYC-8): delete the draft cycle's templates and their question links.
    # A foreign key with ON DELETE CASCADE can do this instead. Needs cluster 6.
    return None


def _template_problems(db: Session, cycle: Cycle) -> list[Problem]:
    # TODO(C3, T6, R7): return one Problem for each of these that fails: a template is missing,
    # a template has no question, a template has no rating question, the weights of a group do
    # not add up to 100. Until then a cycle with no questions can be opened.
    # Needs clusters 6 and 9.
    return []


def _take_snapshot_and_assign(db: Session, cycle: Cycle) -> None:
    # TODO(C6, P1, PART-1): make every active user a participant with a snapshot of their
    # department and manager, and create the self, manager and upward assignments.
    # Needs cluster 7.
    return None


def _archive_unused_questions(db: Session, cycle: Cycle) -> None:
    # TODO(Q12, Q8, QB-14): archive the questions that no template of the last 3 opened cycles
    # uses, with one audit row per question. Needs cluster 5.
    return None


def _calculate_scores(db: Session, cycle: Cycle) -> None:
    # TODO(SCR-3, S6): calculate and store every participant's scores. Needs cluster 9.
    return None


def _discard_results(db: Session, cycle: Cycle) -> None:
    # TODO(C8): delete the cycle's calculated results, including any HR adjustment.
    # Needs cluster 9.
    return None


def _reward_band_problems(db: Session, cycle: Cycle) -> list[Problem]:
    # TODO(RW2): return a Problem for each gap or overlap in the cycle's reward bands over
    # 1.00 to 5.00. Needs cluster 10.
    return []


def _create_reward_proposals(db: Session, cycle: Cycle) -> list[FinalizeWarning]:
    # TODO(RW4, S13): create one reward proposal for every participant who has a result, and
    # return a warning for each participant who has none. Needs clusters 9 and 10.
    return []


# Hooks that are still stubs, and the cluster each one waits for:
#   _audit                      C12            audit log
#   _create_default_templates   T9, S8         clusters 6 and 9
#   _copy_contents              CYC-2, S8      clusters 6, 9 and 10
#   _delete_templates           CYC-8          cluster 6
#   _template_problems          C3, T6, R7     clusters 6 and 9
#   _take_snapshot_and_assign   C6, P1         cluster 7
#   _archive_unused_questions   Q12, Q8        cluster 5
#   _calculate_scores           SCR-3, S6      cluster 9
#   _discard_results            C8             cluster 9
#   _reward_band_problems       RW2            cluster 10
#   _create_reward_proposals    RW4, S13       clusters 9 and 10
