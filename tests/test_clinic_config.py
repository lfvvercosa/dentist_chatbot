from datetime import time

import pytest
from pydantic import ValidationError

from app.core.clinic_config import ClinicConfig, Weekday, load_clinic_config


@pytest.fixture
def data() -> dict:
    """Smallest valid config. Each test breaks one thing."""
    return {
        "name": "Test Clinic",
        "address": "Somewhere 1",
        "phone": "+55 11 0000-0000",
        "timezone": "America/Sao_Paulo",
        "opening_hours": {"monday": {"open": "08:00", "close": "18:00"}},
        "dentists": [{"id": "ana", "name": "Dr. Ana", "specialty": "General"}],
        "services": [
            {
                "id": "cleaning",
                "name": "Cleaning",
                "description": "Cleaning.",
                "duration_minutes": 45,
                "dentist_ids": ["ana"],
            }
        ],
        "emergency": {"clinic_phone": "+55 11 0000-0001", "public_emergency_number": "192"},
    }


def test_shipped_config_is_valid():
    config = load_clinic_config()

    assert config.name == "Big Smile Clinic"
    assert config.opening_hours[Weekday.SATURDAY].close == time(12, 0)
    assert Weekday.SUNDAY not in config.opening_hours  # closed


def test_minimal_config_is_valid(data):
    ClinicConfig.model_validate(data)


def test_rejects_service_with_unknown_dentist(data):
    data["services"][0]["dentist_ids"] = ["ana", "ghost"]

    with pytest.raises(ValidationError, match="unknown dentists"):
        ClinicConfig.model_validate(data)


def test_rejects_duplicate_ids(data):
    data["dentists"].append({"id": "ana", "name": "Another Ana", "specialty": "General"})

    with pytest.raises(ValidationError, match="duplicate dentist ids"):
        ClinicConfig.model_validate(data)


def test_rejects_unknown_key(data):
    data["servics"] = []  # typo

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ClinicConfig.model_validate(data)


def test_rejects_unknown_timezone(data):
    data["timezone"] = "Mars/Olympus"

    with pytest.raises(ValidationError, match="unknown timezone"):
        ClinicConfig.model_validate(data)


def test_rejects_close_before_open(data):
    data["opening_hours"]["monday"] = {"open": "18:00", "close": "08:00"}

    with pytest.raises(ValidationError, match="open must be before close"):
        ClinicConfig.model_validate(data)


def test_rejects_non_positive_duration(data):
    data["services"][0]["duration_minutes"] = 0

    with pytest.raises(ValidationError):
        ClinicConfig.model_validate(data)


def test_rejects_unquoted_yaml_time(tmp_path):
    path = tmp_path / "clinic.yaml"
    path.write_text("opening_hours:\n  monday: {open: 08:00, close: 18:00}\n")

    with pytest.raises(ValidationError, match="times must be quoted"):
        load_clinic_config(path)
