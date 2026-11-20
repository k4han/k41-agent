import json
from functools import lru_cache
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


NotificationEvent = Literal["background_failure", "rate_limit", "ask_user", "browser_handoff", "interruption_timeout"]
EVENTS = ["background_failure", "rate_limit", "ask_user", "browser_handoff", "interruption_timeout"]
CHANNELS = ("telegram", "discord", "zalo")


@lru_cache(maxsize=1)
def messages() -> dict[str, str]:
    return json.loads(files(__package__).joinpath("messages.vi.json").read_text(encoding="utf-8"))


class NotificationSettings(BaseModel):
    timeout_minutes: int = Field(default=30, ge=1, le=2**31 - 1, strict=True)
    dashboard_notify_delay_seconds: int = Field(default=60, ge=0, le=2**31 - 1, strict=True)
    fallback_prompt: str = Field(default_factory=lambda: messages()["fallback"], max_length=16000)
    dashboard_base_url: str = Field(default="", max_length=2048)
    recipient_identity_ids: list[int] = Field(default_factory=list)
    events: list[NotificationEvent] = Field(default_factory=lambda: list(EVENTS))

    @field_validator("dashboard_base_url")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        try:
            url = urlsplit(value)
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                    or url.path or any(c.isspace() or c in "\\?#" for c in value)):
                raise ValueError
            if url.port == 0:
                raise ValueError
        except ValueError:
            raise ValueError("Enter a Dashboard HTTP or HTTPS origin.") from None
        return value

    @model_validator(mode="after")
    def validate_destination(self):
        self.recipient_identity_ids = sorted(set(self.recipient_identity_ids))
        if self.recipient_identity_ids and not self.dashboard_base_url:
            raise ValueError("A Dashboard URL is required to send notifications.")
        self.fallback_prompt = self.fallback_prompt.strip() or messages()["fallback"]
        return self


def load_settings(config) -> NotificationSettings:
    defaults = NotificationSettings().model_dump()
    return NotificationSettings.model_validate({
        key: config.get(f"notifications.{key}", value) for key, value in defaults.items()
    })
