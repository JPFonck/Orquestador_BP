import json

import pytest

from app.tools.registry import AGENT_TOOL_ALLOWLIST, ToolRegistry


@pytest.fixture
def registry(policies) -> ToolRegistry:
    return ToolRegistry(policies)


@pytest.mark.parametrize("agent", list(AGENT_TOOL_ALLOWLIST))
def test_definitions_only_include_allowed_tools(registry, agent):
    names = {d["name"] for d in registry.definitions_for(agent)}
    assert names == set(AGENT_TOOL_ALLOWLIST[agent])


def test_tool_definitions_are_strict(registry):
    definition = next(
        d for d in registry.definitions_for("documentation_agent") if d["name"] == "prepare_documentation"
    )
    schema = definition["input_schema"]
    assert definition["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["properties"]["client_data"]["additionalProperties"] is False
    assert "$ref" not in json.dumps(schema)


def test_unauthorized_tool_is_blocked_and_audited(registry):
    execution = registry.execute("identity_agent", "toolu_1", "check_risk_lists", {"name": "x", "document_id": "1"})
    assert execution.record.allowed is False
    assert execution.result_block["is_error"] is True
    assert execution.result_block["tool_use_id"] == "toolu_1"
    assert "no autorizada" in execution.result_block["content"]


def test_response_agent_has_no_tools(registry):
    execution = registry.execute("response_agent", "toolu_1", "verify_identity", {"document_id": "1712345678"})
    assert not execution.ok


def test_invalid_parameters_return_error(registry):
    execution = registry.execute("identity_agent", "toolu_1", "verify_identity", {"doc": "1712345678"})
    assert execution.record.allowed is True
    assert execution.result_block["is_error"] is True


def test_tool_failure_is_returned_as_error_result(registry):
    execution = registry.execute("identity_agent", "toolu_1", "verify_identity", {"document_id": "9999999999"})
    assert execution.result_block["is_error"] is True
    assert "timeout" in execution.record.error


def test_successful_execution(registry):
    execution = registry.execute("identity_agent", "toolu_1", "verify_identity", {"document_id": "1712345678"})
    assert execution.ok
    assert json.loads(execution.result_block["content"]) == {"verified": True, "confidence": 0.95}
