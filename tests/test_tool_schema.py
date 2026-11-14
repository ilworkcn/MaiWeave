"""检查原生 SDK 注册后的工具定义及嵌套引用。"""

import json
from copy import deepcopy

from maiweave.core.models import ChatArrangement, PlanInput
from maiweave.interfaces.feature_tools import parameters
from maiweave.plugin import create_plugin


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def test_all_registered_tool_references_resolve():
    for component in create_plugin().get_components():
        if component["type"] != "TOOL":
            continue
        root = json.loads(json.dumps(component["metadata"]["parameters_raw"]))
        for ref in references(root):
            assert ref.startswith("#/"), (component["name"], ref)
            target = root
            for key in ref[2:].split("/"):
                key = key.replace("~1", "/").replace("~0", "~")
                assert key in target, (component["name"], ref, key)
                target = target[key]
            assert isinstance(target, dict)


def test_arrangement_patch_definitions_live_at_tool_root():
    raw = ChatArrangement.model_json_schema()
    original = deepcopy(raw)
    schema = parameters({"arrangement": raw, "plan": PlanInput.model_json_schema()}, ["arrangement"])
    assert raw == original
    assert "$defs" not in schema["properties"]["arrangement"]
    assert schema["$defs"]["ChatPlanUpdate"]["properties"]["patch"]["$ref"] == "#/$defs/PlanPatch"
    assert schema["properties"]["arrangement"]["properties"]["plan_updates"]["items"]["$ref"] == "#/$defs/ChatPlanUpdate"
    assert "start" in schema["$defs"]["PlanPatch"]["properties"]
    assert schema["required"] == ["arrangement"]
    assert schema["additionalProperties"] is False


def test_plain_parameters_keep_existing_shape():
    assert parameters({"action": {"type": "string"}}, ["action"]) == {
        "type": "object", "properties": {"action": {"type": "string"}},
        "required": ["action"], "additionalProperties": False}
