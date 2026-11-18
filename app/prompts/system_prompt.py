"""Builds startup rules/clinic facts and adds the relevant saved state for each turn."""

import json
from datetime import datetime
from pathlib import Path

from app.core.clinic_config import ClinicConfig, Weekday
from app.schemas.appointment import TIME_PERIODS
from app.schemas.conversation import ConversationState

_TEMPLATE_PATH = Path(__file__).with_name("system_prompt.md")
_PLACEHOLDER = "{clinic_facts}"


def build_system_prompt(config: ClinicConfig) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace(_PLACEHOLDER, _render_facts(config))


def build_turn_prompt(system_prompt: str, state: ConversationState, *, now: datetime) -> str:
    """Append saved preferences and the request's current time in the clinic timezone."""
    context = {
        "current_time": {
            "timezone": str(now.tzinfo),
            "local_datetime": now.isoformat(),
            "today": now.date().isoformat(),
        },
        "time_periods": {
            name: {
                "start_time": start.isoformat(),
                "end_time_exclusive": end.isoformat() if end is not None else None,
            }
            for name, (start, end) in TIME_PERIODS.items()
        },
        "current_workflow_state": {
            "stage": state.stage,
            "service_id": state.service_id,
            "dentist_preference": state.dentist_preference.model_dump(mode="json"),
            "date_preference": (
                state.date_preference.model_dump(mode="json")
                if state.date_preference is not None
                else None
            ),
            "time_preference": (
                state.time_preference.model_dump(mode="json")
                if state.time_preference is not None
                else None
            ),
        },
    }
    return f"{system_prompt}\n\n## Current backend workflow state\n{json.dumps(context)}"


def _render_facts(config: ClinicConfig) -> str:
    """Spell the facts out as plain text, so the model never has to resolve ids
    or infer that a missing day means closed."""
    dentist_names = {d.id: d.name for d in config.dentists}

    lines = [
        f"Clinic: {config.name}",
        f"Address: {config.address}",
        f"Phone: {config.phone}",
        "",
        "Opening hours:",
    ]
    for day in Weekday:
        hours = config.opening_hours.get(day)
        when = f"{hours.open:%H:%M}-{hours.close:%H:%M}" if hours else "closed"
        lines.append(f"- {day.value.capitalize()}: {when}")

    lines += ["", "Dentists:"]
    lines += [f"- {d.name}: {d.specialty}" for d in config.dentists]

    lines += ["", "Services:"]
    for s in config.services:
        dentists = ", ".join(dentist_names[i] for i in s.dentist_ids)
        lines.append(
            f"- {s.name} ({s.duration_minutes} min): {s.description} Dentists: {dentists}."
        )

    lines += ["", "Internal service IDs (never show these identifiers to patients):"]
    lines += [f"- {s.id}: {s.name}" for s in config.services]

    lines += ["", "Internal dentist IDs (never show these identifiers to patients):"]
    lines += [f"- {d.id}: {d.name}" for d in config.dentists]

    if config.policies:
        lines += ["", "Policies:"]
        lines += [f"- {k.replace('_', ' ').capitalize()}: {v}" for k, v in config.policies.items()]

    lines += [
        "",
        "Emergency contacts:",
        f"- Clinic emergency phone: {config.emergency.clinic_phone}",
        f"- Public medical emergency number: {config.emergency.public_emergency_number}",
    ]
    return "\n".join(lines)
