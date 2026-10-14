from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExamState(StrEnum):
    UNPUBLISHED = "UNPUBLISHED"
    NOT_STARTED = "NOT_STARTED"
    AVAILABLE = "AVAILABLE"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class TaskState(StrEnum):
    DISCOVERED = "DISCOVERED"
    ANSWERING = "ANSWERING"
    READY = "READY"
    SAVING = "SAVING"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    SUBMITTED = "SUBMITTED"
    DRY_RUN = "DRY_RUN"
    NEEDS_USER = "NEEDS_USER"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class Target(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    course_id: str
    class_id: str
    cpi: str

    @field_validator("course_id", "class_id", "cpi")
    @classmethod
    def numeric(cls, v):
        if not v.isascii() or not v.isdigit():
            raise ValueError("课程 course_id / class_id / cpi 必须是非空数字")
        return v

    @property
    def key(self):
        return f"{self.course_id}:{self.class_id}:{self.cpi}"


class Exam(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exam_id: str
    name: str
    target: Target
    state: ExamState
    discovered_at: datetime = Field(default_factory=utcnow)
    starts_at: datetime | None = None
    deadline: datetime | None = None
    duration_seconds: int | None = Field(default=None, gt=0)
    entry_url: str = Field(default="", repr=False, exclude=True)

    @field_validator("starts_at", "deadline")
    @classmethod
    def aware(cls, v):
        if v is not None and v.tzinfo is None:
            raise ValueError("考试时间必须包含时区")
        return v


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    number: int = Field(gt=0)
    type: Literal["single_choice", "multiple_choice"]
    question: str = Field(min_length=1)
    options: dict[str, str]
    existing_answer: list[str] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_options(self):
        if len(self.options) < 2 or any(
            len(k) != 1 or not k.isascii() or not k.isupper() or not v.strip()
            for k, v in self.options.items()
        ):
            raise ValueError("选项标识/文本异常")
        if len(self.existing_answer) != len(set(self.existing_answer)) or not set(
            self.existing_answer
        ).issubset(self.options):
            raise ValueError("已有答案不属于选项")
        if self.type == "single_choice" and len(self.existing_answer) > 1:
            raise ValueError("单选题已有多个答案")
        return self

    def ai_payload(self):
        return self.model_dump(include={"question_id", "type", "question", "options"})


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question_id: str
    answer: list[str]
    uncertain: bool


class AutoExamError(Exception):
    """Messages must be safe for logs: never include response bodies or credentials."""


class NeedsUser(AutoExamError):
    pass


class CompatibilityError(AutoExamError):
    pass


class DeadlineError(AutoExamError):
    pass


class ProviderError(AutoExamError):
    pass


class RateLimited(AutoExamError):
    def __init__(self, seconds=60):
        self.seconds = min(max(float(seconds), 30), 300)
        super().__init__("平台限流，暂停轮询")


class SubmissionUnknown(AutoExamError):
    pass
