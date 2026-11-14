"""真实 SDK 配置字段和页面 API 绑定测试。"""

import inspect
import json
import pytest
from datetime import timedelta
from pathlib import Path

from maiweave.config import MaiWeaveConfig
from maiweave.plugin import create_plugin
from test_plugin import plugin


async def test_energy_and_mood_share_sample_times(plugin):
    now = plugin.now()
    with plugin.store.db:
        for i in range(144):
            at = now - timedelta(minutes=10 * (143 - i))
            key = at.strftime("%Y-%m-%dT%H:") + f"{at.minute // 10 * 10:02d}"
            plugin.store.put("state_history", {"id": key, "at": at.isoformat(), "energy": i / 2, "mood": 50 - i})
    overview = await plugin.ui_overview()
    points = overview["energy_trend"]
    assert len(points) == 13 and len(overview["history"]) == 144
    assert [p["energy"] for p in points] == sorted(p["energy"] for p in points)
    assert points[0]["energy"] == 0 and points[-1]["energy"] == 71.5
    assert all(p["mood"] == 50 - 2 * p["energy"] for p in points)


async def test_share_card_names_channels_and_dates_last_decision(plugin):
    now = plugin.now()
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["private", "group", "missing"]
    plugin.ctx.chat.get_all_streams.return_value = [
        {"stream_id": "private", "platform": "qq", "user_nickname": "小明", "user_id": "123"},
        {"stream_id": "group", "platform": "qq", "group_name": "读书群", "group_id": "456"}]
    plugin.next_share_check = now + timedelta(minutes=7)
    plugin.living.share_decisions.append({"created": (now - timedelta(hours=1)).isoformat(),
        "at": "05:00", "session": "全部", "outcome": "跳过", "reason": "允许交流的时段为 06:00 至 23:00",
        "mode": "自然交流", "score": "本轮未筛选经历"})
    data = await plugin.ui_overview()
    rows = {r["session"]: r for r in data["share_summary"]}
    assert rows["private"]["session_name"] == "小明"
    assert rows["private"]["session_detail"] == "QQ · 私聊 · 账号 123"
    assert rows["group"]["session_name"] == "读书群"
    assert rows["group"]["session_detail"] == "QQ · 群聊 · 群号 456"
    assert rows["missing"]["session_name"] == "未找到聊天资料"
    assert rows["private"]["last_at"] == (now - timedelta(hours=1)).isoformat()
    assert data["share_decisions"][0]["session_name"] == "全部聊天"
    assert data["share_settings"]["next_check"] == plugin.next_share_check.isoformat().replace("T", " ")[:19]
    plugin.config.proactive.enabled = False
    assert (await plugin.ui_overview())["share_settings"]["next_check"] == "主动交流已关闭"


def test_config_labels_and_controls_are_chinese():
    instance = create_plugin()
    instance.set_plugin_config(instance.get_default_config())
    sections = instance.get_webui_config_schema()["sections"]
    for section in sections.values():
        for field in section["fields"].values():
            assert any("\u4e00" <= c <= "\u9fff" for c in field["label"])
    assert sections["life"]["fields"]["enabled"]["ui_type"] == "switch"
    threshold = sections["proactive"]["fields"]["share_threshold"]
    assert threshold["choices"] == list(range(1, 11)) and threshold["ui_type"] == "select"
    assert instance.config.proactive.share_threshold == 5
    assert sections["planning"]["fields"]["tick_seconds"]["ui_type"] == "number"
    assert sections["profile"]["fields"]["interests"]["ui_type"] == "list"


def test_display_groups_preserve_config_paths_and_values():
    instance = create_plugin()
    config = instance.get_default_config()
    config["life"].update(timezone="Asia/Tokyo", interests=["画画"], background="住在海边")
    instance.set_plugin_config(config)
    schema = instance.get_webui_config_schema()
    paths = [(section["name"], name) for section in schema["sections"].values() for name in section["fields"]]
    assert len(paths) == len(set(paths))
    assert set(paths) == {(section, name) for section, fields in config.items() for name in fields}
    for section in schema["sections"].values():
        for name, field in section["fields"].items():
            assert field["hint"]
            assert name in config[section["name"]]
    profile = schema["sections"]["profile"]
    config[profile["name"]]["background"] = "住在山间"
    normalized, _ = instance.normalize_plugin_config(config)
    assert normalized["life"]["background"] == "住在山间"
    assert normalized["life"]["interests"] == ["画画"]
    assert normalized["life"]["timezone"] == "Asia/Tokyo"
    assert schema["sections"]["memory"]["fields"]["session_cookie"]["ui_type"] == "password"


def test_old_config_keeps_values_and_loads_new_defaults():
    config = MaiWeaveConfig.model_validate({"plugin": {"config_version": "0.1.0"}, "life": {"interests": ["画画"], "autonomous": False}})
    assert config.life.interests == ["画画"] and not config.life.autonomous
    assert config.world.city == "大庆" and config.rhythm.habits_enabled and config.dreams.enabled


def test_all_page_bindings_match_real_registered_api():
    instance = create_plugin()
    instance.set_plugin_config(instance.get_default_config())
    components = {c["name"]: c for c in instance.get_components() if c["type"] == "API"}
    raw = (Path(__file__).parents[1] / "webui.json").read_bytes()
    assert len(raw) <= 128 * 1024
    pages = json.loads(raw)["pages"]
    assert len(pages) == 10
    for page in pages:
        for binding in [*page["queries"].values(), *page["actions"].values()]:
            assert "required" not in binding
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
                  plugin.ui_dreams, plugin.ui_habits, plugin.ui_records, plugin.ui_continuity, plugin.ui_anniversaries):
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


def test_merged_pages_keep_action_selections_and_data_sources():
    """分组后，查询引用、详情窗口和按钮选择仍指向页面内组件。"""
    import re
    root = Path(__file__).parents[1]
    pages = json.loads((root / "webui.json").read_text(encoding="utf-8"))["pages"]
    metadata = json.loads(re.search(r"const PAGE_METADATA=(.*);", (root / "webui/editor.html").read_text(encoding="utf-8")).group(1))
    assert {p["id"] for p in pages} == {"overview", "schedule", "world", "space", "records", "continuity", "anniversaries", "usage", "diary", "books"}
    def objects(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from objects(item)
        elif isinstance(value, list):
            for item in value:
                yield from objects(item)
    for page in pages:
        meta = metadata.get(page["id"], {})
        for category in meta.get("categories", []):
            alias = {"experience": "experience"}.get(category, category)
            if category not in {"profile", "anniversary"}:
                assert alias in page["actions"], (page["id"], category)
        view = meta.get("view") or page
        content = list(objects(view.get("content", [])))
        selections = {item["selection"] for item in content if "selection" in item}
        inputs = {item["name"] for item in content if item.get("type") in {"input", "date", "select", "switch"}}
        dialogs = {item["name"] for item in content if item.get("type") == "dialog"}
        for item in objects(view):
            if item.get("scope") == "selection":
                assert item["source"] in selections, (page["id"], item)
            elif item.get("scope") == "input":
                assert item["source"] in inputs
            elif isinstance(item.get("source"), str):
                assert item["source"] in view["queries"], (page["id"], item)
        for item in content:
            if item.get("type") == "button":
                assert item["action"] in view["actions"]
            if item.get("detail"):
                assert item["detail"] in dialogs


def test_new_routine_webui_does_not_require_existing_id():
    pages = json.loads((Path(__file__).parents[1] / "webui.json").read_text(encoding="utf-8"))["pages"]
    page = next(page for page in pages if page["id"] == "schedule")
    assert page["actions"]["routine"]["parameters"]["rule_id"]["required"] is False


async def test_goal_stage_shows_running_plan(plugin):
    with plugin.store.db:
        plugin.store.put("goal", {"id": "goal", "title": "读书", "status": "active",
            "steps": [{"id": "step", "status": "pending"}]})
        plugin.store.put("plan", {"id": "plan", "goal_step_id": "step", "status": "active"})
    assert (await plugin.ui_continuity())["steps"][0]["status_label"] == "进行中"
    with plugin.store.db:
        plugin.store.put("plan", {"id": "plan", "goal_step_id": "step", "status": "cancelled"})
    assert (await plugin.ui_continuity())["steps"][0]["status_label"] == "未开始"


async def test_operation_progress_tracks_job_outcome(plugin):
    plugin.store.put("job", {"id": "search", "label": "联网阅读", "status": "进行中", "error": ""})
    assert not (await plugin.ui_operation_status(task_id="search"))["done"]
    plugin.store.put("job", {"id": "search", "label": "联网阅读", "status": "失败", "error": "服务连接失败"})
    result = await plugin.ui_operation_status(task_id="search")
    assert result["done"] and result["error"] == "服务连接失败"
    plugin.store.put("job", {"id": "search", "label": "联网阅读", "status": "已完成", "error": ""})
    assert (await plugin.ui_operation_status(task_id="search"))["message"] == "联网阅读：已完成"


async def test_diary_progress_matches_current_writing_plan(plugin):
    from datetime import timedelta
    start = plugin.now()
    plugin.store.put("plan", {"id": "writing", "start": start.isoformat(), "status": "active"})
    plugin.store.put("diary", {"id": "old", "plan_id": "previous", "status": "已生成"})
    assert not (await plugin.ui_operation_status(plan_id="writing"))["done"]
    plugin.store.put("diary", {"id": "new", "plan_id": "writing", "status": "生成中"})
    assert (await plugin.ui_operation_status(plan_id="writing"))["message"] == "模型正在写日记…"
    plugin.store.put("diary", {"id": "new", "plan_id": "writing", "status": "已生成"})
    assert (await plugin.ui_operation_status(plan_id="writing"))["done"]
    plugin.store.put("plan", {"id": "later", "start": (start + timedelta(hours=1)).isoformat(), "status": "planned"})
    result = await plugin.ui_operation_status(plan_id="later")
    assert result["done"] and "开始时间" in result["message"]


async def test_planning_reason_survives_status_checks(plugin):
    now = plugin.now().replace(hour=12, minute=0)
    plugin.config.life.autonomous = True
    state = plugin.engine.state()
    with plugin.store.db:
        plugin.store.put("state", {**state, "hunger": 90})
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"title": "吃晚饭", "kind": "meal", "minutes": 25, "location": "住处", "motivation": "有些饿了"})}
    await plugin.plan_life(now)
    result = await plugin.ui_overview()
    decision, = result["planning_history"]
    assert decision["source"] == "身体需要 · 用餐"
    assert decision["reason"] == "有些饿了"
    assert decision["minutes"] == 25
    plugin.engine.advance(now + timedelta(minutes=1))
    await plugin.plan_life(now + timedelta(minutes=1))
    waiting = await plugin.ui_overview()
    assert "预计" in waiting["planning_status"]
    assert waiting["planning_history"] == [decision]

    async def failed(at):
        raise ValueError("地点搜索失败")

    await plugin.run_background("自主生活", failed)
    failure = await plugin.ui_overview()
    assert "地点搜索失败" in failure["planning_failure"]
    assert failure["planning_history"] == [decision]


@pytest.mark.parametrize("script", ["webui_details.mjs", "webui_reading.mjs"])
def test_nested_details_and_pagination_buttons(script):
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if node is None:
        pytest.skip("页面脚本测试需要 Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name(script))],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("hours", [0, 15])
async def test_recent_changes_use_original_history_across_midnight(plugin, hours):
    now = plugin.now().replace(hour=hours, minute=30, second=0, microsecond=0)
    plugin.now = lambda: now
    with plugin.store.db:
        for row in plugin.store.all("state_history"):
            plugin.store.delete("state_history", row["id"])
        plugin.store.put("state", {**plugin.engine.state(), "energy": 71.6, "mood": -2.3})
        for minutes, energy, mood in [(130, 99, 99), (110, 83.8, -10.1), (10, 73, -3), (-10, 0, 0)]:
            plugin.store.put("state_history", {"id": str(minutes), "at": (now-timedelta(minutes=minutes)).isoformat(), "energy": energy, "mood": mood})
    before = plugin.store.all("state_history")
    for day in (-1, 0, 1):
        data = await plugin.ui_dashboard(day)
        assert data["recent_changes"] == {"since": (now-timedelta(minutes=110)).isoformat(), "energy": -12, "mood": 8}
    assert plugin.store.all("state_history") == before


async def test_recent_changes_wait_for_a_recent_record(plugin):
    now = plugin.now()
    plugin.now = lambda: now
    with plugin.store.db:
        for row in plugin.store.all("state_history"):
            plugin.store.delete("state_history", row["id"])
        plugin.store.put("state_history", {"id": "old", "at": (now-timedelta(days=1)).isoformat(), "energy": 50, "mood": 0})
    assert (await plugin.ui_dashboard())["recent_changes"] is None
    with plugin.store.db:
        plugin.store.put("state_history", {"id": "recent", "at": (now-timedelta(minutes=10)).isoformat(),
            "energy": plugin.engine.state()["energy"], "mood": plugin.engine.state()["mood"]})
    changes = (await plugin.ui_dashboard())["recent_changes"]
    assert changes["energy"] == changes["mood"] == 0
