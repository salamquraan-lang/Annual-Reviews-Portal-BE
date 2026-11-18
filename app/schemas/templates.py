from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt

from app.models import CycleStatus, QuestionKind, ReviewType
from app.schemas.question_bank import BodyId, CriterionRef, clean_text, is_blank


def _check_template_name(value: str) -> str:
    value = clean_text(" ".join(value.split()))
    if is_blank(value):
        raise ValueError("Name is required.")
    if len(value) > 150:
        raise ValueError("Name must be at most 150 characters.")
    return value


def _no_repeats(value: list[int]) -> list[int]:
    if len(set(value)) != len(value):
        raise ValueError("An id may appear only once.")
    return value


TemplateName = Annotated[str, AfterValidator(_check_template_name)]
# T5: a whole number, 1 or more.
Weight = Annotated[StrictInt, Field(ge=1, le=1000)]
IdList = Annotated[list[BodyId], Field(min_length=1), AfterValidator(_no_repeats)]


# ---------------------------------------------------------------- requests


class TemplateRename(BaseModel):
    """Body of TPL-3."""

    model_config = ConfigDict(extra="forbid")

    name: TemplateName


class TemplateAddQuestion(BaseModel):
    """Body of TPL-4. The weight is for rating questions only. Left out, it is 1."""

    model_config = ConfigDict(extra="forbid")

    question_id: BodyId = Field(alias="questionId")
    weight: Weight | None = None


class TemplateQuestionWeight(BaseModel):
    """Body of TPL-8."""

    model_config = ConfigDict(extra="forbid")

    weight: Weight


class TemplateQuestionOrder(BaseModel):
    """Body of TPL-6: every question of one criterion's section, in the new order."""

    model_config = ConfigDict(extra="forbid")

    question_ids: IdList = Field(alias="questionIds")


class TemplateCriterionOrder(BaseModel):
    """Body of TPL-7: every criterion of the template, in the new order."""

    model_config = ConfigDict(extra="forbid")

    criterion_ids: IdList = Field(alias="criterionIds")


class TemplateReplaceQuestion(BaseModel):
    """Body of TPL-9: the question that takes the place of the one in the URL."""

    model_config = ConfigDict(extra="forbid")

    question_id: BodyId = Field(alias="questionId")


class TemplateCopyFrom(BaseModel):
    """Body of TPL-10."""

    model_config = ConfigDict(extra="forbid")

    source_template_id: BodyId = Field(alias="sourceTemplateId")


# --------------------------------------------------------------- responses


class TemplateOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    cycle_id: int = Field(serialization_alias="cycleId")
    review_type: ReviewType = Field(serialization_alias="reviewType")
    name: str
    question_count: int = Field(serialization_alias="questionCount")


class TemplateQuestionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question_id: int = Field(serialization_alias="questionId")
    position: int
    text: str
    kind: QuestionKind
    # Null for a text question.
    weight: int | None
    archived: bool


class TemplateSection(BaseModel):
    criterion: CriterionRef
    questions: list[TemplateQuestionOut]


class TemplateDetail(TemplateOut):
    cycle_status: CycleStatus = Field(serialization_alias="cycleStatus")
    # True while the cycle is a draft (T2).
    editable: bool
    copied_from_id: int | None = Field(serialization_alias="copiedFromId")
    sections: list[TemplateSection]
