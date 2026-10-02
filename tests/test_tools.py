import pytest

from app.tools import mocks


@pytest.mark.parametrize(
    ("document_id", "expected"),
    [
        ("1712345678", {"verified": True, "confidence": 0.95}),
        ("0923456789", {"verified": True, "confidence": 0.72}),
        ("1708765432", {"verified": False, "confidence": 0.2}),
        ("1700000001", {"verified": True, "confidence": 0.9}),  # válido, fuera del registro mock
        ("12345", {"verified": False, "confidence": 0.0}),  # formato inválido
        ("9912345678", {"verified": False, "confidence": 0.0}),  # provincia inexistente
    ],
)
def test_verify_identity(document_id, expected):
    assert mocks.verify_identity(document_id) == expected


def test_verify_identity_timeout():
    with pytest.raises(mocks.ToolTimeoutError):
        mocks.verify_identity("9999999999")


def test_verify_biometrics_unknown_token():
    with pytest.raises(mocks.ToolError):
        mocks.verify_biometrics("0923456789", "TOKEN-INVENTADO")


def test_check_risk_lists_clean_prospect():
    assert mocks.check_risk_lists("Juan Perez", "1712345678") == {"risk_level": "low", "matches": []}


def test_check_risk_lists_document_match_is_high():
    result = mocks.check_risk_lists("Pedro Gomez", "1705555555")
    assert result["risk_level"] == "high"
    assert result["matches"][0]["matched_on"] == "document_id"


def test_check_risk_lists_name_only_match_is_medium_and_ignores_accents():
    result = mocks.check_risk_lists("Luis  Méndoza", "1712345678")
    assert result["risk_level"] == "medium"
    assert result["matches"][0]["matched_on"] == "name_only"


def test_prepare_documentation_adds_edd_documents_for_medium_risk(policies):
    low = mocks.prepare_documentation("cuenta_ahorros", {"risk_level": "low"}, policies)
    medium = mocks.prepare_documentation("cuenta_ahorros", {"risk_level": "medium"}, policies)
    codes = [d["code"] for d in medium["required_documents"]]
    assert len(medium["required_documents"]) == len(low["required_documents"]) + 2
    assert "ORIGEN_FONDOS" in codes and medium["enhanced_due_diligence"] is True


def test_prepare_documentation_unknown_product(policies):
    with pytest.raises(mocks.ToolError):
        mocks.prepare_documentation("hipoteca", {"risk_level": "low"}, policies)
