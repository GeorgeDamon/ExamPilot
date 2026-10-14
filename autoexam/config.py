from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Target


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class App(Strict):
    name: str = "ExamPilot"
    timezone: str = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def zone(cls, v):
        ZoneInfo(v)
        return v


WEEKDAYS = {
    v: i
    for i, v in enumerate(
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )
}


class Schedule(Strict):
    weekday: str = "wednesday"
    start_time: time = time(7, 59)
    monitor_start: time = time(8)
    monitor_end: time = time(10)

    @model_validator(mode="after")
    def valid(self):
        if self.weekday not in WEEKDAYS:
            raise ValueError("weekday 无效")
        if not self.start_time <= self.monitor_start < self.monitor_end:
            raise ValueError("时间必须 start_time <= monitor_start < monitor_end，同一天")
        return self


class Monitor(Strict):
    interval_seconds: float = Field(default=30, ge=10, le=300)
    target_course_id: str = ""
    target_class_id: str = ""
    target_cpi: str = ""
    check_on_startup: bool = True
    max_consecutive_failures: int = Field(default=5, ge=1, le=10)

    def target(self):
        return Target(
            course_id=self.target_course_id, class_id=self.target_class_id, cpi=self.target_cpi
        )


class DeepSeek(Strict):
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    thinking: bool = False
    batch_size: int = Field(default=20, ge=1, le=100)
    max_concurrency: int = Field(default=3, ge=1, le=5)
    connect_timeout_seconds: float = Field(default=5, gt=0, le=30)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=3)
    max_tokens: int = Field(default=4096, ge=256, le=16384)

    @field_validator("base_url")
    @classmethod
    def official_only(cls, v):
        if v.rstrip("/") not in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
            raise ValueError("API Key 只能发送至 DeepSeek 官方 API")
        return v.rstrip("/")


class ExamConfig(Strict):
    single_choice: bool = True
    multiple_choice: bool = True
    auto_answer: bool = True
    auto_submit: bool = True
    dry_run: bool = True
    reserve_seconds: float = Field(default=20, ge=10, le=120)
    uncertain_second_pass: bool = True
    max_questions: int = Field(default=200, ge=1, le=500)


class Notification(Strict):
    enabled: bool = True
    macos: bool = True
    mobile: bool = False


class Logging(Strict):
    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def valid(cls, v):
        if v not in {"INFO", "WARNING", "ERROR"}:
            raise ValueError("禁止调试级别，以免 HTTP 凭据被记录")
        return v


class Platform(Strict):
    backend: str = "live"
    headless: bool = True
    timeout_seconds: float = Field(default=12, ge=2, le=30)
    start_labels: list[str] = ["开始考试", "进入考试", "继续考试"]
    submit_labels: list[str] = ["交卷", "提交试卷"]
    confirm_labels: list[str] = ["确定", "确认提交"]
    runtime_dir: str = "runtime"

    @field_validator("backend")
    @classmethod
    def backend_valid(cls, v):
        if v not in {"live", "mock"}:
            raise ValueError("backend 必须 live 或 mock")
        return v


class Config(Strict):
    app: App = Field(default_factory=App)
    schedule: Schedule = Field(default_factory=Schedule)
    monitor: Monitor = Field(default_factory=Monitor)
    deepseek: DeepSeek = Field(default_factory=DeepSeek)
    exam: ExamConfig = Field(default_factory=ExamConfig)
    notification: Notification = Field(default_factory=Notification)
    logging: Logging = Field(default_factory=Logging)
    platform: Platform = Field(default_factory=Platform)


def load_config(path: Path):
    return Config.model_validate(yaml.safe_load(path.read_text("utf-8")) or {})
