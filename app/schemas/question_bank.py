import unicodedata
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.models import CycleStatus, QuestionKind, ReviewType
from app.schemas.departments import UtcDateTime


def clean_text(value: str, *, keep: str = "") -> str:
    """Remove control characters (such as the NUL character), except the ones in "keep".

    Text that is not valid Unicode cannot be stored, so it is refused here with a clear message.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("The text contains characters that cannot be saved.") from None
    return "".join(char for char in value if char in keep or unicodedata.category(char) != "Cc")


def is_blank(value: str) -> bool:
    """True if nothing in the text can be seen: it is empty or holds only spaces and invisible characters."""
    return all(unicodedata.category(char)[0] in "CZ" for char in value)


def _check_criterion_name(value: str) -> str:
    value = clean_text(" ".join(value.split()))
    if is_blank(value):
        raise ValueError("Name is required.")
    if len(value) > 120:
        raise ValueError("Name must be at most 120 characters.")
    return value


def _check_description(value: str | None) -> str | None:
    if value is None:
        return None
    value = clean_text(value, keep="\n\t").strip()
    if len(value) > 500:
        raise ValueError("Description must be at most 500 characters.")
    return None if is_blank(value) else value


def _check_question_text(value: str) -> str:
    value = clean_text(value, keep="\n\t").strip()
    if is_blank(value):
        raise ValueError("Text is required.")
    if len(value) > 500:
        raise ValueError("Text must be at most 500 characters.")
    return value


CriterionName = Annotated[str, AfterValidator(_check_criterion_name)]
Description = Annotated[str | None, AfterValidator(_check_description)]
QuestionText = Annotated[str, AfterValidator(_check_question_text)]
# The largest id an INT column can hold. A larger number can never be a real id.
MAX_ID = 2_147_483_647
# Request bodies must send a real number, not a number inside a string.
BodyId = Annotated[StrictInt, Field(ge=1, le=MAX_ID)]


# ---------------------------------------------------------------- requests


class CriterionCreate(BaseModel):
    """Body of QB-1."""

    model_config = ConfigDict(extra="forbid")

    name: CriterionName
    description: Description = None


class CriterionUpdate(BaseModel):
    """Body of QB-4. Only the fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    name: CriterionName | None = None
    # A description may be cleared with null. A name may not.
    description: Description = None

    @field_validator("name", mode="before")
    @classmethod
    def _not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("This field cannot be empty.")
        return value

    @model_validator(mode="after")
    def _check_sent_fields(self) -> "CriterionUpdate":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        return self


class QuestionCreate(BaseModel):
    """Body of QB-6."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: BodyId = Field(alias="criterionId")
    text: QuestionText
    kind: QuestionKind


class QuestionUpdate(BaseModel):
    """Body of QB-9. Only the fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: BodyId | None = Field(default=None, alias="criterionId")
    text: QuestionText | None = None
    kind: QuestionKind | None = None

    @field_validator("criterion_id", "text", "kind", mode="before")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Runs only for fields that were sent. No field of a question can be empty.
        if value is None:
            raise ValueError("This field cannot be empty.")
        return value

    @model_validator(mode="after")
    def _check_sent_fields(self) -> "QuestionUpdate":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        return self


class QuestionCopy(QuestionUpdate):
    """Body of QB-10. Every field is optional: a field that is sent replaces the copied one."""

    @model_validator(mode="after")
    def _check_sent_fields(self) -> "QuestionCopy":
        return self


class QuestionListQuery(BaseModel):
    """Query string of QB-7."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    criterion_id: int | None = Field(default=None, alias="criterionId", ge=1, le=MAX_ID)
    kind: QuestionKind | None = None
    used: bool | None = None
    show_archived: bool = Field(default=False, alias="showArchived")
    search: str | None = Field(default=None, max_length=100)
    # The upper limit keeps the row offset inside what MySQL accepts.
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")


# --------------------------------------------------------------- responses


class CriterionRef(BaseModel):
    id: int
    name: str


class CycleRef(BaseModel):
    id: int
    name: str
    status: CycleStatus


class CriterionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    description: str | None
    question_count: int = Field(serialization_alias="questionCount")


class CriterionQuestion(BaseModel):
    id: int
    text: str
    kind: QuestionKind
    archived: bool


class CriterionDetail(CriterionOut):
    questions: list[CriterionQuestion]


class QuestionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    criterion: CriterionRef
    text: str
    kind: QuestionKind
    archived: bool
    archived_at: UtcDateTime | None = Field(serialization_alias="archivedAt")


class QuestionListItem(QuestionOut):
    used: bool
    # The newest cycle with a template that uses the question. Null if no template uses it.
    last_used_cycle: CycleRef | None = Field(serialization_alias="lastUsedCycle")


class QuestionListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[QuestionListItem]
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total: int


class QuestionUsage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    template_id: int = Field(serialization_alias="templateId")
    template_name: str = Field(serialization_alias="templateName")
    review_type: ReviewType = Field(serialization_alias="reviewType")
    cycle: CycleRef


class QuestionDetail(QuestionOut):
    # True when a cycle that was opened uses the question, so it cannot be edited (Q4).
    locked: bool
    usage: list[QuestionUsage]


class CriterionListResponse(BaseModel):
    items: list[CriterionOut]
