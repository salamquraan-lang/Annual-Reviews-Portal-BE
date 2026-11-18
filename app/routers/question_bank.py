from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.dependencies import DbSession, require_roles
from app.models import Criterion, Question, Role, User
from app.schemas.question_bank import (
    MAX_ID,
    CriterionCreate,
    CriterionDetail,
    CriterionListResponse,
    CriterionOut,
    CriterionQuestion,
    CriterionRef,
    CriterionUpdate,
    CycleRef,
    QuestionCopy,
    QuestionCreate,
    QuestionDetail,
    QuestionListItem,
    QuestionListQuery,
    QuestionListResponse,
    QuestionUpdate,
    QuestionUsage,
)
from app.services import question_bank

# Q7: only admins manage criteria and questions. The super admin is not allowed.
criteria_router = APIRouter(prefix="/api/criteria", dependencies=[Depends(require_roles(Role.admin))])
questions_router = APIRouter(prefix="/api/questions", dependencies=[Depends(require_roles(Role.admin))])
router = APIRouter()

CriterionId = Annotated[int, Path(ge=1, le=MAX_ID)]
QuestionId = Annotated[int, Path(ge=1, le=MAX_ID)]
Admin = Annotated[User, Depends(require_roles(Role.admin))]


def _criterion_out(criterion: Criterion, question_count: int) -> CriterionOut:
    return CriterionOut(
        id=criterion.id, name=criterion.name, description=criterion.description, question_count=question_count
    )


def _question_fields(question: Question) -> dict:
    return {
        "id": question.id,
        "criterion": CriterionRef(id=question.criterion.id, name=question.criterion.name),
        "text": question.text,
        "kind": question.kind,
        "archived": question.archived_at is not None,
        "archived_at": question.archived_at,
    }


def _cycle_ref(cycle: question_bank.CycleRef | None) -> CycleRef | None:
    return CycleRef(id=cycle.id, name=cycle.name, status=cycle.status) if cycle else None


def _question_detail(detail: question_bank.QuestionDetail) -> QuestionDetail:
    return QuestionDetail(
        **_question_fields(detail.question),
        locked=detail.locked,
        usage=[
            QuestionUsage(
                template_id=item.template_id,
                template_name=item.template_name,
                review_type=item.review_type,
                cycle=_cycle_ref(item.cycle),
            )
            for item in detail.usage
        ],
    )


# ---------------------------------------------------------------- criteria


@criteria_router.get("")
def list_criteria(db: DbSession) -> CriterionListResponse:
    rows = question_bank.list_criteria(db)
    return CriterionListResponse(items=[_criterion_out(row.criterion, row.question_count) for row in rows])


@criteria_router.post("", status_code=status.HTTP_201_CREATED)
def create_criterion(db: DbSession, actor: Admin, body: CriterionCreate, response: Response) -> CriterionOut:
    criterion = question_bank.create_criterion(db, actor.id, body.name, body.description)
    response.headers["Location"] = f"/api/criteria/{criterion.id}"
    return _criterion_out(criterion, 0)


@criteria_router.get("/{criterion_id}")
def get_criterion(db: DbSession, criterion_id: CriterionId) -> CriterionDetail:
    criterion, questions = question_bank.get_criterion(db, criterion_id)
    return CriterionDetail(
        **_criterion_out(criterion, len(questions)).model_dump(),
        questions=[
            CriterionQuestion(
                id=question.id, text=question.text, kind=question.kind, archived=question.archived_at is not None
            )
            for question in questions
        ],
    )


@criteria_router.patch("/{criterion_id}")
def update_criterion(db: DbSession, actor: Admin, criterion_id: CriterionId, body: CriterionUpdate) -> CriterionDetail:
    # exclude_unset keeps only the fields the client sent.
    question_bank.update_criterion(db, actor.id, criterion_id, body.model_dump(exclude_unset=True))
    return get_criterion(db, criterion_id)


@criteria_router.delete("/{criterion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_criterion(db: DbSession, actor: Admin, criterion_id: CriterionId) -> Response:
    question_bank.delete_criterion(db, actor.id, criterion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- questions


@questions_router.get("")
def list_questions(db: DbSession, query: Annotated[QuestionListQuery, Query()]) -> QuestionListResponse:
    result = question_bank.list_questions(db, **query.model_dump())
    return QuestionListResponse(
        items=[
            QuestionListItem(
                **_question_fields(row.question), used=row.used, last_used_cycle=_cycle_ref(row.last_used_cycle)
            )
            for row in result.items
        ],
        page=query.page,
        page_size=query.page_size,
        total=result.total,
    )


@questions_router.post("", status_code=status.HTTP_201_CREATED)
def create_question(db: DbSession, actor: Admin, body: QuestionCreate, response: Response) -> QuestionDetail:
    detail = question_bank.create_question(db, actor.id, body.criterion_id, body.text, body.kind)
    response.headers["Location"] = f"/api/questions/{detail.question.id}"
    return _question_detail(detail)


@questions_router.get("/{question_id}")
def get_question(db: DbSession, question_id: QuestionId) -> QuestionDetail:
    return _question_detail(question_bank.get_question(db, question_id))


@questions_router.patch("/{question_id}")
def update_question(db: DbSession, actor: Admin, question_id: QuestionId, body: QuestionUpdate) -> QuestionDetail:
    changes = body.model_dump(exclude_unset=True)
    return _question_detail(question_bank.update_question(db, actor.id, question_id, changes))


@questions_router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(db: DbSession, actor: Admin, question_id: QuestionId) -> Response:
    question_bank.delete_question(db, actor.id, question_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@questions_router.post("/{question_id}/copy", status_code=status.HTTP_201_CREATED)
def copy_question(
    db: DbSession, actor: Admin, question_id: QuestionId, response: Response, body: QuestionCopy | None = None
) -> QuestionDetail:
    changes = body.model_dump(exclude_unset=True) if body else {}
    detail = question_bank.copy_question(db, actor.id, question_id, changes)
    response.headers["Location"] = f"/api/questions/{detail.question.id}"
    return _question_detail(detail)


@questions_router.post("/{question_id}/archive")
def archive_question(db: DbSession, actor: Admin, question_id: QuestionId) -> QuestionDetail:
    return _question_detail(question_bank.archive_question(db, actor.id, question_id))


@questions_router.post("/{question_id}/unarchive")
def unarchive_question(db: DbSession, actor: Admin, question_id: QuestionId) -> QuestionDetail:
    return _question_detail(question_bank.unarchive_question(db, actor.id, question_id))


router.include_router(criteria_router)
router.include_router(questions_router)
