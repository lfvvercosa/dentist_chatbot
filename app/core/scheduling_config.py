"""Explicit demo search settings; clinic hours/services remain in clinic_config."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SCHEDULING_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scheduling.yaml"


class SchedulingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    search_horizon_days: int = Field(ge=1, le=90)
    slot_spacing_minutes: int = Field(ge=1, le=1440)
    max_results: int = Field(ge=1, le=20)


def load_scheduling_config(path: Path = DEFAULT_SCHEDULING_CONFIG_PATH) -> SchedulingConfig:
    return SchedulingConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
