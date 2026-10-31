"""真实 SDK 配置字段和页面 API 绑定测试。"""

import inspect
import json
from pathlib import Path

from maiweave.config import MaiWeaveConfig
from maiweave.plugin import create_plugin
from test_plugin import plugin


def test_config_labels_and_controls_are_chinese():
    instance = create_plugin()
    instance.set_plugin_config(instance.get_default_config())
    sections = instance.get_webui_config_schema()["sections"]
    for section in sections.values():
        for field in section["fields"].values():
            assert any("\u4e00" <= c <= "\u9fff" for c in field["label"])
    assert sections["life"]["fields"]["enabled"]["ui_type"] == "switch"
    assert sections["life"]["fields"]["tick_seconds"]["ui_type"] == "number"
    assert sections["life"]["fields"]["interests"]["ui_type"] == "list"


def test_old_config_keeps_values_and_loads_new_defaults():
    config = MaiWeaveConfig.model_validate({"plugin": {"config_version": "0.1.0"}, "life": {"interests": ["画画"], "autonomous": False}})
    assert config.life.interests == ["画画"] and not config.life.autonomous
    assert config.world.city == "" and config.rhythm.habits_enabled and config.dreams.enabled


def test_all_page_bindings_match_real_registered_api():
    instance = create_plugin()
    instance.set_plugin_config(instance.get_default_config())
    components = {c["name"]: c for c in instance.get_components() if c["type"] == "API"}
    raw = (Path(__file__).parents[1] / "webui.json").read_bytes()
    assert len(raw) <= 128 * 1024
    pages = json.loads(raw)["pages"]
    assert len(pages) == 9
    for page in pages:
        for binding in [*page["queries"].values(), *page["actions"].values()]:
            component = components[binding["api"]]
            assert component["metadata"]["version"] == binding["version"]
            method = getattr(instance, component["metadata"]["handler_name"])
            parameters = inspect.signature(method).parameters
            declared = binding.get("parameters", {})
            assert declared.keys() <= parameters.keys()
            required = {name for name, param in parameters.items() if param.default == inspect.Parameter.empty}
            assert required <= declared.keys()


async def test_page_queries_do_not_mutate_life(plugin):
    before = plugin.store.db.total_changes
    for query in (plugin.ui_overview, plugin.ui_schedule, plugin.ui_world, plugin.ui_space,
                  plugin.ui_dreams, plugin.ui_habits, plugin.ui_records, plugin.ui_continuity):
        result = await query()
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert plugin.store.db.total_changes == before


async def test_page_actions_persist_then_queries_show_results(plugin):
    rule = await plugin.ui_save_routine(title="晚间阅读", anchor_date="2027-01-01", start_time="20:00", duration_minutes=30)
    schedule = await plugin.ui_schedule()
    assert schedule["rules"][0]["frequency_label"] == "按周"
    await plugin.ui_routine_action(rule["id"], "cancel")
    assert (await plugin.ui_schedule())["rules"][0]["status_label"] == "已取消"
    place = await plugin.ui_save_place("咖啡馆", False, "角色生活地点")
    await plugin.ui_save_route("楼下街道", place["id"], 8)
    assert any(p["name"] == "咖啡馆" for p in (await plugin.ui_space())["places"])
