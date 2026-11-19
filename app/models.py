import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# DATETIME(3) on MySQL, like the existing tables. Values are stored as naive UTC.
Timestamp = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(enum.StrEnum):
    employee = "employee"
    manager = "manager"
    admin = "admin"
    super_admin = "super_admin"


class AuthTokenType(enum.StrEnum):
    verify_email = "verify_email"
    reset_password = "reset_password"


class CycleType(enum.StrEnum):
    annual = "annual"
    midyear = "midyear"


class CycleStatus(enum.StrEnum):
    draft = "draft"
    open = "open"
    closed = "closed"
    finalized = "finalized"


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (Index("departments_parent_id_idx", "parent_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "departments.id",
            name="departments_parent_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utcnow, onupdate=utcnow
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role", values_callable=lambda e: [m.value for m in e]),
        default=Role.employee,
        server_default=Role.employee.value,
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "departments.id",
            name="users_department_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        )
    )
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="users_manager_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    email_verified_at: Mapped[datetime | None] = mapped_column(Timestamp)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utcnow, onupdate=utcnow
    )

    department: Mapped[Department | None] = relationship(lazy="joined")
    manager: Mapped["User | None"] = relationship(
        remote_side="User.id", lazy="joined", join_depth=1
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("refresh_tokens_user_id_idx", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="refresh_tokens_user_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        )
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (Index("auth_tokens_user_id_type_idx", "user_id", "type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="auth_tokens_user_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        )
    )
    type: Mapped[AuthTokenType] = mapped_column(
        Enum(
            AuthTokenType,
            name="auth_token_type",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    used_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (
        UniqueConstraint("year", "type", name="cycles_year_type_key"),
        # open_marker is 1 for an open cycle and NULL for any other. NULLs never collide in a
        # unique index, so the database allows many non-open cycles but only one open cycle.
        UniqueConstraint("open_marker", name="cycles_one_open_key"),
        CheckConstraint("start_date <= end_date", name="cycles_dates_check"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[CycleType] = mapped_column(
        Enum(
            CycleType, name="cycle_type", values_callable=lambda e: [m.value for m in e]
        )
    )
    year: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[CycleStatus] = mapped_column(
        Enum(
            CycleStatus,
            name="cycle_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=CycleStatus.draft,
        server_default=CycleStatus.draft.value,
    )
    open_marker: Mapped[int | None] = mapped_column(
        Integer, Computed("CASE WHEN status = 'open' THEN 1 END", persisted=True)
    )


# ------------------------------------------------- question bank and templates (clusters 5, 6)


class QuestionKind(enum.StrEnum):
    rating = "rating"
    text = "text"


class ReviewType(enum.StrEnum):
    self_ = "self"
    manager = "manager"
    peer = "peer"
    upward = "upward"


class Criterion(Base):
    __tablename__ = "criteria"
    __table_args__ = (UniqueConstraint("name_key", name="criteria_name_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # The name in lower case with all spaces removed. The unique index on it enforces Q1.
    # Lower-casing can make a name up to 3 times longer (one "ß" becomes "ss"), so it has 360.
    name_key: Mapped[str] = mapped_column(String(360))
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utcnow, onupdate=utcnow
    )


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (Index("questions_criterion_id_idx", "criterion_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    criterion_id: Mapped[int] = mapped_column(
        ForeignKey(
            "criteria.id",
            name="questions_criterion_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        )
    )
    text: Mapped[str] = mapped_column(String(500))
    kind: Mapped[QuestionKind] = mapped_column(
        Enum(
            QuestionKind,
            name="question_kind",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    # Set when the question is archived (Q9). NULL means it is not archived.
    archived_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utcnow, onupdate=utcnow
    )

    criterion: Mapped[Criterion] = relationship(lazy="joined")


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (
        # T1: a cycle owns at most one template for each review type.
        UniqueConstraint(
            "cycle_id", "review_type", name="templates_cycle_id_review_type_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ON DELETE CASCADE: deleting a draft cycle deletes its templates (CYC-8).
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey(
            "cycles.id",
            name="templates_cycle_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        )
    )
    review_type: Mapped[ReviewType] = mapped_column(
        Enum(
            ReviewType,
            name="review_type",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    name: Mapped[str] = mapped_column(String(150))
    # T7: the template this one's questions were last copied from.
    copied_from_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "templates.id",
            name="templates_copied_from_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        )
    )
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utcnow, onupdate=utcnow
    )

    cycle: Mapped[Cycle] = relationship(lazy="joined")


class TemplateQuestion(Base):
    __tablename__ = "template_questions"
    __table_args__ = (
        Index("template_questions_question_id_idx", "question_id"),
        CheckConstraint(
            "weight IS NULL OR weight >= 1", name="template_questions_weight_check"
        ),
        CheckConstraint("position >= 1", name="template_questions_position_check"),
    )

    # The primary key enforces T3: a question appears at most once in a template.
    template_id: Mapped[int] = mapped_column(
        ForeignKey(
            "templates.id",
            name="template_questions_template_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey(
            "questions.id",
            name="template_questions_question_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    # 1, 2, 3 and so on inside the template, with no gaps (T4).
    position: Mapped[int] = mapped_column(Integer)
    # A whole number, 1 or more, for a rating question. NULL for a text question (T5).
    weight: Mapped[int | None] = mapped_column(Integer)

    question: Mapped[Question] = relationship(lazy="joined")

    # ------------------------------------------------- participants and assignments (cluster 7)


class AssignmentStatus(enum.StrEnum):
    pending = "pending"
    draft = "draft"
    submitted = "submitted"


class CycleParticipant(Base):
    """One person in one cycle, with the department and manager they had when it opened (C6)."""

    __tablename__ = "cycle_participants"
    __table_args__ = (
        Index("cycle_participants_user_id_idx", "user_id"),
        Index("cycle_participants_department_id_idx", "department_id", "cycle_id"),
        Index("cycle_participants_manager_id_idx", "manager_id", "cycle_id"),
    )

    cycle_id: Mapped[int] = mapped_column(
        ForeignKey(
            "cycles.id",
            name="cycle_participants_cycle_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="cycle_participants_user_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    # Snapshot values. They do not follow later changes to the user.
    department_id: Mapped[int] = mapped_column(
        ForeignKey(
            "departments.id",
            name="cycle_participants_department_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        )
    )
    # There is no foreign key to cycle_participants for the manager. The service checks that the
    # manager is a participant too. The key to users only keeps the id real.
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="cycle_participants_manager_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        )
    )
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Assignment(Base):
    """One review to write: the reviewer writes about the reviewee, in one cycle, of one type."""

    __tablename__ = "assignments"
    __table_args__ = (
        # P3: the same review is never assigned twice.
        UniqueConstraint(
            "cycle_id",
            "reviewer_id",
            "reviewee_id",
            "review_type",
            name="assignments_cycle_reviewer_reviewee_type_key",
        ),
        ForeignKeyConstraint(
            ["cycle_id", "reviewer_id"],
            ["cycle_participants.cycle_id", "cycle_participants.user_id"],
            name="assignments_reviewer_fkey",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cycle_id", "reviewee_id"],
            ["cycle_participants.cycle_id", "cycle_participants.user_id"],
            name="assignments_reviewee_fkey",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        # The cycle must have a template of this type. There is no template_id column.
        ForeignKeyConstraint(
            ["cycle_id", "review_type"],
            ["templates.cycle_id", "templates.review_type"],
            name="assignments_template_fkey",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "(review_type = 'self' AND reviewer_id = reviewee_id)"
            " OR (review_type <> 'self' AND reviewer_id <> reviewee_id)",
            name="assignments_people_check",
        ),
        CheckConstraint(
            "(status = 'submitted' AND submitted_at IS NOT NULL) OR (status <> 'submitted' AND submitted_at IS NULL)",
            name="assignments_submitted_at_check",
        ),
        Index("assignments_reviewee_idx", "cycle_id", "reviewee_id", "review_type"),
        Index("assignments_type_status_idx", "cycle_id", "review_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[int] = mapped_column(Integer)
    reviewee_id: Mapped[int] = mapped_column(Integer)
    review_type: Mapped[ReviewType] = mapped_column(
        Enum(
            ReviewType,
            name="review_type",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(
            AssignmentStatus,
            name="assignment_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=AssignmentStatus.pending,
        server_default=AssignmentStatus.pending.value,
    )
    # Set exactly when the status is submitted (W8).
    submitted_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Answer(Base):
    """The saved answer to one question of one assignment. Cluster 8 writes and reads it."""

    __tablename__ = "answers"
    __table_args__ = (
        Index("answers_question_id_idx", "question_id"),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="answers_rating_check",
        ),
    )

    # The database removes the answers when their assignment is removed.
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey(
            "assignments.id",
            name="answers_assignment_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey(
            "questions.id",
            name="answers_question_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utcnow, onupdate=utcnow
    )
