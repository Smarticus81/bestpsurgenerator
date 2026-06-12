"""Pydantic request/response models for the PSUR demo service."""
from datetime import date
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from server.specs import INPUT_NAMES


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def _start_before_end(self) -> "Period":
        if self.start > self.end:
            raise ValueError(
                f"period.start ({self.start}) must not be after period.end ({self.end})"
            )
        return self


class TableInput(BaseModel):
    """Editable content for a table input. Row structure is validated
    separately against the locked column spec (server/specs.py)."""
    model_config = ConfigDict(extra="forbid")

    rows: List[Dict[str, Any]] = Field(..., min_length=1)


class JsonInput(BaseModel):
    """Editable content for a JSON input. Key structure is validated
    separately against the locked template (server/specs.py)."""
    model_config = ConfigDict(extra="forbid")

    value: Dict[str, Any]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Period
    inputs: Dict[str, Union[TableInput, JsonInput]] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def _known_input_names(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        unknown = sorted(set(v.keys()) - set(INPUT_NAMES))
        if unknown:
            raise ValueError(
                f"unknown input(s): {', '.join(unknown)} — "
                f"allowed inputs: {', '.join(INPUT_NAMES)}"
            )
        return v


class RunCreated(BaseModel):
    run_id: str


class RunStatus(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "failed"]
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    device_name: Optional[str] = None
    report_type: Optional[str] = None
    error: Optional[str] = None
    validation: Optional[Dict[str, Any]] = None


class ArtifactInfo(BaseModel):
    name: str
    content_type: str
    size_bytes: int


class ArtifactList(BaseModel):
    run_id: str
    status: str
    artifacts: List[ArtifactInfo]
