"""Builds the system prompt: fixed rules (system_prompt.md) + clinic facts from the config."""

from pathlib import Path

from app.core.clinic_config import ClinicConfig, Weekday

_TEMPLATE_PATH = Path(__file__).with_name("system_prompt.md")
_PLACEHOLDER = "{clinic_facts}"


def build_system_prompt(config: ClinicConfig) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace(_PLACEHOLDER, _render_facts(config))


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
