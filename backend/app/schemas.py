from importlib.resources import files
from functools import lru_cache
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from .config import MODELS


@lru_cache
def timezone_aliases():
    aliases = {}
    for line in files("tzdata.zoneinfo").joinpath("tzdata.zi").read_text().splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "L":
            aliases[fields[2]] = fields[1]
    return aliases


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Research(Strict):
    topic: str = Field(min_length=3, max_length=2000)
    depth: Literal["standard", "deep"] = "standard"
    model: str = MODELS[0]

    @field_validator("model")
    @classmethod
    def model_allowed(cls, v):
        if v not in MODELS:
            raise ValueError("Only platform free models are allowed")
        return v


class Rename(Strict):
    title: str = Field(min_length=1, max_length=200)


class Conversation(Rename):
    title: str = "New chat"
    report_id: UUID | None = None


class Message(Strict):
    content: str = Field(min_length=1, max_length=6000)
    model: str = MODELS[0]
    _model = field_validator("model")(Research.model_allowed.__func__)


class Schedule(Research):
    title: str = Field(min_length=1, max_length=200)
    frequency: Literal["once", "daily", "weekly", "monthly"]
    timezone: str = "UTC"
    start_at: datetime
    end_at: datetime | None = None
    weekdays: list[int] = Field(default_factory=lambda: [1], min_length=1, max_length=7)
    paused: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, v):
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone")
        aliases = timezone_aliases()
        for _ in range(10):
            if v not in aliases:
                break
            v = aliases[v]
        return v

    @field_validator("weekdays")
    @classmethod
    def valid_days(cls, v):
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("Weekdays must be 1 through 7")
        return sorted(set(v))

    @model_validator(mode="after")
    def dates(self):
        if self.start_at.tzinfo is None or (self.end_at and self.end_at.tzinfo is None):
            raise ValueError("Dates require a timezone offset")
        if self.end_at and self.end_at < self.start_at:
            raise ValueError("End must follow start")
        return self


class RedTeam(Strict):
    suites: list[Literal["jailbreak", "xpia", "crescendo", "authority"]] = Field(
        default_factory=lambda: ["xpia"], min_length=1, max_length=4
    )
    max_tests: int = Field(default=4, ge=1, le=12)
    model: str = MODELS[0]
    target: Literal["platform", "custom"] = "platform"
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    authorized: bool = False

    @model_validator(mode="after")
    def target_fields(self):
        if self.target == "platform" and self.model not in MODELS:
            raise ValueError("Unknown platform model")
        if self.target == "custom" and (not self.authorized or not self.base_url or not self.api_key or not self.model):
            raise ValueError("Custom target requires URL, key, model, and authorization acknowledgement")
        return self


class UserUpdate(Strict):
    @field_validator("suspended", "features", mode="before")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("This field cannot be null")
        return value

    suspended: bool | None = None
    daily_limit: int | None = Field(default=None, ge=0, le=10000000)
    features: dict[Literal["research", "chat", "redteam", "schedule"], bool] | None = None


class SettingsUpdate(Strict):
    default_daily_limit: int = Field(ge=0, le=10000000)
    search_monthly_limit: int = Field(default=1000, ge=0, le=1000)
