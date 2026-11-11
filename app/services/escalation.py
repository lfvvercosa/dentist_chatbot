"""Select configured guidance from an LLM decision; no symptom/keyword matching."""

from pathlib import Path
from string import Formatter

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from app.core.clinic_config import Emergency
from app.llm.client import EscalationLevel, ResponseLanguage

TEMPLATES_PATH = Path(__file__).resolve().parents[2] / "config" / "escalation.yaml"


class LocalizedGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pt: str
    en: str

    @field_validator("pt", "en")
    @classmethod
    def require_contacts(cls, value: str) -> str:
        fields = {name for _, name, _, _ in Formatter().parse(value) if name is not None}
        if fields != {"clinic_phone", "public_emergency_number"}:
            raise ValueError("guidance must reference both configured emergency contacts only")
        return value


class EscalationTemplates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    urgent: LocalizedGuidance
    emergency: LocalizedGuidance


class EscalationService:
    def __init__(self, contacts: Emergency, templates_path: Path = TEMPLATES_PATH):
        templates = EscalationTemplates.model_validate(
            yaml.safe_load(templates_path.read_text(encoding="utf-8"))
        )
        # Render once at startup, so invalid templates fail before handling requests.
        self._replies = {
            (level, language): text.format(**contacts.model_dump())
            for level, translations in templates.model_dump().items()
            for language, text in translations.items()
        }

    def reply(self, level: EscalationLevel, language: ResponseLanguage) -> str | None:
        if level == "none":
            return None
        return self._replies[level, language]
