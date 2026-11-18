from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.dependencies import DbSession, require_roles
from app.models import Role, User
from app.schemas.question_bank import MAX_ID, CriterionRef
from app.schemas.templates import (
    TemplateAddQuestion,
    TemplateCopyFrom,
    TemplateCriterionOrder,
    TemplateDetail,
    TemplateOut,
    TemplateQuestionOrder,
    TemplateQuestionOut,
    TemplateQuestionWeight,
    TemplateRename,
    TemplateReplaceQuestion,
    TemplateSection,
)
from app.services import templates

# T11: only admins manage templates. The super admin is not allowed.
router = APIRouter(prefix="/api", dependencies=[Depends(require_roles(Role.admin))])

CycleId = Annotated[int, Path(ge=1, le=MAX_ID)]
TemplateId = Annotated[int, Path(ge=1, le=MAX_ID)]
QuestionId = Annotated[int, Path(ge=1, le=MAX_ID)]
CriterionId = Annotated[int, Path(ge=1, le=MAX_ID)]
Admin = Annotated[User, Depends(require_roles(Role.admin))]


def _to_detail(detail: templates.TemplateDetail) -> TemplateDetail:
    template = detail.template
    return TemplateDetail(
        id=template.id,
        cycle_id=template.cycle_id,
        review_type=template.review_type,
        name=template.name,
        question_count=sum(len(section.links) for section in detail.sections),
        cycle_status=template.cycle.status,
        editable=detail.editable,
        copied_from_id=template.copied_from_id,
        sections=[
            TemplateSection(
                criterion=CriterionRef(id=section.criterion.id, name=section.criterion.name),
                questions=[
                    TemplateQuestionOut(
                        question_id=link.question_id,
                        position=link.position,
                        text=link.question.text,
                        kind=link.question.kind,
                        weight=link.weight,
                        archived=link.question.archived_at is not None,
                    )
                    for link in section.links
                ],
            )
            for section in detail.sections
        ],
    )


# ------------------------------------------------------------------ reads


@router.get("/cycles/{cycle_id}/templates")
def list_templates(db: DbSession, cycle_id: CycleId) -> list[TemplateOut]:
    return [
        TemplateOut(
            id=row.template.id,
            cycle_id=row.template.cycle_id,
            review_type=row.template.review_type,
            name=row.template.name,
            question_count=row.question_count,
        )
        for row in templates.list_templates(db, cycle_id)
    ]


@router.get("/templates/{template_id}")
def get_template(db: DbSession, template_id: TemplateId) -> TemplateDetail:
    return _to_detail(templates.get_template(db, template_id))


# ----------------------------------------------------------------- writes
# Every write answers with the whole template, because one change can renumber its questions.


@router.patch("/templates/{template_id}")
def rename_template(db: DbSession, template_id: TemplateId, body: TemplateRename) -> TemplateDetail:
    return _to_detail(templates.rename_template(db, template_id, body.name))


@router.post("/templates/{template_id}/questions", status_code=status.HTTP_201_CREATED)
def add_question(db: DbSession, actor: Admin, template_id: TemplateId, body: TemplateAddQuestion) -> TemplateDetail:
    return _to_detail(templates.add_question(db, actor.id, template_id, body.question_id, body.weight))


@router.delete("/templates/{template_id}/questions/{question_id}")
def remove_question(db: DbSession, template_id: TemplateId, question_id: QuestionId) -> TemplateDetail:
    return _to_detail(templates.remove_question(db, template_id, question_id))


@router.patch("/templates/{template_id}/questions/{question_id}")
def change_weight(
    db: DbSession, template_id: TemplateId, question_id: QuestionId, body: TemplateQuestionWeight
) -> TemplateDetail:
    return _to_detail(templates.change_weight(db, template_id, question_id, body.weight))


@router.post("/templates/{template_id}/questions/{question_id}/replace")
def replace_question(
    db: DbSession, actor: Admin, template_id: TemplateId, question_id: QuestionId, body: TemplateReplaceQuestion
) -> TemplateDetail:
    return _to_detail(templates.replace_question(db, actor.id, template_id, question_id, body.question_id))


@router.put("/templates/{template_id}/criteria/{criterion_id}/question-order")
def reorder_questions(
    db: DbSession, template_id: TemplateId, criterion_id: CriterionId, body: TemplateQuestionOrder
) -> TemplateDetail:
    return _to_detail(templates.reorder_questions(db, template_id, criterion_id, body.question_ids))


@router.put("/templates/{template_id}/criterion-order")
def reorder_criteria(db: DbSession, template_id: TemplateId, body: TemplateCriterionOrder) -> TemplateDetail:
    return _to_detail(templates.reorder_criteria(db, template_id, body.criterion_ids))


@router.post("/templates/{template_id}/copy-from")
def copy_from(db: DbSession, actor: Admin, template_id: TemplateId, body: TemplateCopyFrom) -> TemplateDetail:
    return _to_detail(templates.copy_from(db, actor.id, template_id, body.source_template_id))
