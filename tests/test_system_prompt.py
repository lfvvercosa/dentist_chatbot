import pytest

from app.core.clinic_config import load_clinic_config
from app.prompts.system_prompt import build_system_prompt


@pytest.fixture(scope="module")
def config():
    return load_clinic_config()


@pytest.fixture(scope="module")
def prompt(config) -> str:
    return build_system_prompt(config)


def _line_starting_with(prompt: str, prefix: str) -> str:
    return next(line for line in prompt.splitlines() if line.startswith(prefix))


def test_placeholder_is_filled(prompt):
    assert "{clinic_facts}" not in prompt
    assert "Clinic: Big Smile Clinic" in prompt


def test_every_configured_fact_reaches_the_prompt(config, prompt):
    facts = [config.address, config.phone, config.emergency.clinic_phone]
    facts += [config.emergency.public_emergency_number]
    facts += [d.name for d in config.dentists] + [s.name for s in config.services]
    facts += list(config.policies.values())

    missing = [fact for fact in facts if fact not in prompt]
    assert missing == []


def test_opening_hours_include_closed_days(prompt):
    assert "- Saturday: 08:00-12:00" in prompt
    assert "- Sunday: closed" in prompt


def test_services_show_dentist_names_not_ids(prompt):
    line = _line_starting_with(prompt, "- Root canal evaluation")

    assert line.endswith("Dentists: Dr. Bruno Lima.")
    assert "bruno" not in line  # the raw id is resolved to a name
