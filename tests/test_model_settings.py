"""功能模型选择、配置展示和实际用量记录。"""
import pytest
from test_plugin import plugin
from maiweave.config import MODEL_FEATURES
from maiweave.integrations.usage import generate, _once, json_payload
from pydantic import BaseModel


class JsonPayloadSample(BaseModel):
    arrangements: list
    promises: list


@pytest.mark.parametrize("text", [
    '```json\n{"arrangements": [], "promises": []}\n```',
    '{\n  "arrangements": [],\n  "promises": []\n}\n```\n',
    '问题原因：模型输出带了代码块。\n{"arrangements": [], "promises": []}',
    '```json {"arrangements": [], "promises": []} ```',
])
def test_json_payload_extracts_structured_model_output(text):
    value = JsonPayloadSample.model_validate_json(json_payload(text))
    assert value.arrangements == [] and value.promises == []


def test_json_payload_supports_array_top_level():
    assert json_payload('说明\n[{"title": "阅读"}]\n结束') == '[{"title": "阅读"}]'


@pytest.mark.parametrize("feature,key", MODEL_FEATURES.items())
async def test_feature_override_and_default(plugin, feature, key):
    plugin.config.life.model_task = "planner"
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "完成", "model": "实际模型", "total_tokens": 12}
    await generate(plugin.ctx, plugin.store, plugin.config, feature, "测试")
    assert plugin.ctx.llm.generate.call_args.kwargs["task_name"] == "planner"
    setattr(plugin.config.models, key, "replyer")
    await generate(plugin.ctx, plugin.store, plugin.config, feature, "测试")
    assert plugin.ctx.llm.generate.call_args.kwargs["task_name"] == "replyer"
    rows = plugin.store.all("model_usage")
    assert [row["task"] for row in rows] == ["planner", "replyer"]
    assert all(row["feature"] == feature and row["model"] == "实际模型" for row in rows)
    other = next(name for name, setting in MODEL_FEATURES.items() if setting != key)
    await generate(plugin.ctx, plugin.store, plugin.config, other, "测试")
    assert plugin.ctx.llm.generate.call_args.kwargs["task_name"] == "planner"


async def test_failed_call_records_selected_task(plugin):
    plugin.config.models.reading = "replyer"
    plugin.ctx.llm.generate.side_effect = RuntimeError("服务调用失败")
    with pytest.raises(RuntimeError, match="服务调用失败"):
        await _once(plugin.ctx, plugin.store, plugin.config, "书籍阅读", "测试")
    row = plugin.store.all("model_usage")[0]
    assert row["task"] == "replyer" and row["status"] == "失败"


async def test_tabs_and_model_selection_preserve_config(plugin):
    old = plugin.config.model_dump()
    old.pop("models")
    old["life"]["model_task"] = "custom_default"
    old["retention"]["usage_days"] = 60
    normalized, _ = plugin.normalize_plugin_config(old)
    assert normalized["retention"]["usage_days"] == 60
    assert all(value == "跟随默认" for value in normalized["models"].values())
    plugin.set_plugin_config(normalized)
    plugin.config.models.reading = "custom_reading"
    schema = plugin.get_webui_config_schema()
    tabs = {tab["id"]: tab for tab in schema["layout"]["tabs"]}
    assert len(tabs) == 7
    assert "retention" in tabs["advanced"]["sections"]
    assert "retention" not in tabs["connections"]["sections"]
    assert all(schema["sections"][key]["collapsed"] for key in tabs["advanced"]["sections"])
    shown = [key for tab in tabs.values() for key in tab["sections"]]
    assert len(shown) == len(set(shown)) and set(shown) == set(schema["sections"])
    paths = [(section["name"], key) for section in schema["sections"].values() for key in section["fields"]]
    assert len(paths) == len(set(paths))
    from maiweave.integrations.diary_images import CHARACTER_FIELDS
    assert set(paths) == {(section, key) for section, fields in plugin.config.model_dump().items() for key in fields
                          if not (section == "diary" and key in CHARACTER_FIELDS)}
    fields = schema["sections"]["model_expression"]["fields"]
    assert "custom_reading" in fields["reading"]["choices"]
    assert "当前使用：custom_reading" in fields["reading"]["hint"]
    assert "当前使用：custom_default" in fields["diary"]["hint"]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_interrupted_model_call_records_reason(plugin, cancelled):
    import asyncio
    error = asyncio.CancelledError() if cancelled else TimeoutError()
    plugin.ctx.llm.generate.side_effect = error
    with pytest.raises(type(error)):
        await _once(plugin.ctx, plugin.store, plugin.config, "生活日记", "测试")
    row = plugin.store.all("model_usage")[-1]
    assert row["status"] == "失败"
    assert row["error"] == ("模型请求已取消" if cancelled else "模型请求超时")


async def test_image_configuration_and_model_schema_are_independent(plugin):
    plugin.config.life.model_task = "planner"
    plugin.config.models.diary = "replyer"
    plugin.config.diary.image_model = "local-image"
    first = plugin.get_webui_config_schema()
    second = plugin.get_webui_config_schema()
    assert first == second
    assert first["sections"]["image_service"]["fields"]["image_api_key"]["ui_type"] == "password"
    assert "当前使用：replyer" in first["sections"]["model_expression"]["fields"]["diary"]["hint"]
    await _once(plugin.ctx, plugin.store, plugin.config, "生活日记", "写日记")
    assert plugin.ctx.llm.generate.call_args.kwargs["task_name"] == "replyer"
    assert plugin.config.diary.image_model == "local-image"
