"""功能模型选择、配置展示和实际用量记录。"""
import pytest
from test_plugin import plugin
from maiweave.config import MODEL_FEATURES
from maiweave.integrations.usage import generate, _once


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
    assert set(paths) == {(section, key) for section, fields in plugin.config.model_dump().items() for key in fields}
    fields = schema["sections"]["model_expression"]["fields"]
    assert "custom_reading" in fields["reading"]["choices"]
    assert "当前使用：custom_reading" in fields["reading"]["hint"]
    assert "当前使用：custom_default" in fields["diary"]["hint"]
