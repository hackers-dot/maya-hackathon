import hashlib
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

Severity = Literal["info", "low", "medium", "high", "critical"]


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class Evidence(BaseModel):
    description: str
    data: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: AwareDatetime
    record_id: str | None = None  # EvidenceStore record holding the full (redacted) request/response

    @field_validator("captured_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.utcoffset() != timedelta(0):
            raise ValueError("captured_at must be UTC")
        return v

    @model_validator(mode="after")
    def _hash_matches(self):
        if sha256(self.data) != self.sha256:
            raise ValueError("sha256 does not match data")
        return self

    @classmethod
    def capture(cls, description: str, data: str, record_id: str | None = None) -> "Evidence":
        return cls(description=description, data=data, sha256=sha256(data),
                   captured_at=datetime.now(timezone.utc), record_id=record_id)


class Finding(BaseModel):
    id: str = Field(min_length=1)
    check: str = Field(min_length=1)
    target: str = Field(min_length=1)
    location: str = Field(min_length=1)
    severity: Severity
    evidence: list[Evidence] = Field(min_length=1)
    plain_explanation: str = Field(min_length=1)
    technical_explanation: str = Field(min_length=1)
    demo: bool = False
