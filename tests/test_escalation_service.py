import pytest
import yaml
from pydantic import ValidationError

from app.core.clinic_config import Emergency
from app.services.escalation import TEMPLATES_PATH, EscalationService


@pytest.mark.parametrize("level", ["urgent", "emergency"])
@pytest.mark.parametrize("language", ["pt", "en"])
def test_guidance_uses_injected_contacts_and_prioritizes_emergency_number(level, language):
    service = EscalationService(
        Emergency(clinic_phone="CLINIC-TEST", public_emergency_number="PUBLIC-TEST")
    )
    reply = service.reply(level, language)
    assert "CLINIC-TEST" in reply and "PUBLIC-TEST" in reply
    assert "{" not in reply
    if level == "emergency":
        assert reply.index("PUBLIC-TEST") < reply.index("CLINIC-TEST")


def test_no_escalation_leaves_normal_response_to_the_llm(escalation_service):
    assert escalation_service.reply("none", "pt") is None


def test_template_without_required_emergency_contact_fails_at_startup(tmp_path):
    data = yaml.safe_load(TEMPLATES_PATH.read_text())
    data["urgent"]["pt"] = "Call {clinic_phone} only"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="both configured emergency contacts"):
        EscalationService(Emergency(clinic_phone="CLINIC", public_emergency_number="PUBLIC"), path)
