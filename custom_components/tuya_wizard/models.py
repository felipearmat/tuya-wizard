from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_SAFE_TEMPLATE_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


class TuyaCredentials(BaseModel):
    access_id: str
    access_secret: str
    region: str = "us"


class DpInfo(BaseModel):
    id: str
    name: str
    tuya_type: str  # "bool", "int", "float", "enum", "string", "raw"
    platform: str  # "switch", "number", "select", "sensor"
    select_options: dict[str, str] = Field(default_factory=dict)
    min_value: float = 0.0
    max_value: float = 100.0
    step_size: float = 1.0
    is_passive: bool = False


class DeviceInfo(BaseModel):
    device_id: str
    name: str
    slug: str
    ip: Optional[str] = None
    local_key: str
    protocol_version: str = "3.5"
    category: str = ""
    dps: list[DpInfo] = Field(default_factory=list)
    online: bool = True
    onvif_password: str = ""


class DpEntry(BaseModel):
    id: str
    label: str


class CardTemplate(BaseModel):
    id: str
    name: str
    source: str = "custom"  # "builtin" | "custom"
    category: str = ""
    main_dps: list[DpEntry] = Field(default_factory=list)
    advanced_dps: list[DpEntry] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        # id is used both as a filename under /config/www and as a JS custom
        # element tag name — restrict it so it can never break out of either.
        if not _SAFE_TEMPLATE_ID_RE.match(value):
            raise ValueError(
                "id must be 1-64 lowercase letters, digits, '-' or '_'"
            )
        return value


class DiscoverRequest(BaseModel):
    credentials: TuyaCredentials


class ProvisionRequest(BaseModel):
    credentials: TuyaCredentials
    devices: list[DeviceInfo]
    default_local_password: Optional[str] = None


class RemoveRequest(BaseModel):
    device_id: str


class CardTemplateSaveRequest(BaseModel):
    template: CardTemplate


class CardTemplateDeleteRequest(BaseModel):
    template_id: str


class StepResult(BaseModel):
    step: str
    status: str  # "ok" | "error" | "skip"
    detail: str = ""
