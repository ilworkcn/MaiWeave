"""验证聊天预算、分类开关以及生活功能的接续。"""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from maibot_sdk.config import rebuild_plugin_config_data

from maiweave.core.models import ExperienceInput, PlanInput
from test_plugin import plugin
from test_books import upload, answer, begin_reading


def snapshot(text):
    return json.loads(text.split("\n")[-1])


async def test_group_and_private_presets_use_native_chat_type(plugin):
    plugin.ctx.chat.get_all_streams.return_value = [{"stream_id": "group", "group_id": "123"}, {"stream_id": "private", "group_id": ""}]
    for stream, mode, budget in (("group", "精简", 2400), ("private", "均衡", 4800)):
        await plugin.planner_context(session_id=stream, items=[])
        report = plugin.chat_context.recent[0]
        assert (report["mode"], report["budget"]) == (mode, budget)
    plugin.ctx.chat.get_all_streams.assert_awaited_once()


@pytest.mark.parametrize("mode", [1, 2, 3])
@pytest.mark.parametrize("budget", [1200, 2400, 4800])
async def test_total_budget_includes_rules_and_large_records(plugin, mode, budget):
    plugin.config.context.private_mode = mode
    plugin.config.context.private_chars = budget
    now = plugin.now()
    for i in range(15):
        plugin.engine.record_experience(ExperienceInput(summary=f"记录{i}" + "很长的感想" * 200,
            occurred_at=now.isoformat(), source="reported", evidence="用户陈述", details={"过程": "生活细节" * 800}), "a", now)
    plugin.engine.create_plan(PlanInput(title="很长的活动标题" * 15, start=now.isoformat(),
        end=(now + timedelta(hours=1)).isoformat(), location="很长的地点" * 100), "a", now)
    text = plugin.context_text("a", text="今天做了什么，明天有什么安排？")
    assert len(text) <= budget
    data = snapshot(text)
    assert {"sleep_policy", "state", "current", "now"} <= data.keys()
    report = plugin.chat_context.recent[0]
    assert report["chars"] == report["base_chars"] + sum(row["chars"] for row in report["sections"])
    assert report["chars"] == len(text)


async def test_minimal_ordinary_chat_and_requested_schedule(plugin):
    plugin.chat_context.kinds["group"] = "group"
    now = plugin.now()
    plan = plugin.engine.create_plan(PlanInput(title="去图书馆", start=(now+timedelta(hours=1)).isoformat(),
        end=(now+timedelta(hours=2)).isoformat()), "", now)
    assert "plans" not in snapshot(plugin.context_text("group", text="哈哈真有趣"))
    assert snapshot(plugin.context_text("group", text="下个日程安排是什么？"))["plans"][0]["id"] == plan["id"]
    plugin.config.context.schedule = False
    assert "plans" not in snapshot(plugin.context_text("group", text="下个日程安排是什么？"))
    changed = await plugin.change_plan(plan["id"], "update", {"title": "换成散步"}, stream_id="group")
    assert changed["success"]


async def test_disabled_categories_preserve_sleep_and_pending_confirmation(plugin):
    for key in ("schedule", "people", "reading", "continuity", "experiences", "sharing", "environment", "dreams"):
        setattr(plugin.config.context, key, False)
    now = plugin.now()
    plugin.engine.create_plan(PlanInput(title="睡觉", kind="sleep", start=now.isoformat(),
        end=(now+timedelta(hours=2)).isoformat()), "", now)
    with plugin.store.db:
        plugin.store.put("review", {"id": "review-1", "session_id": "a", "status": "待确认", "action": "保存资料", "conflicts": ["生日不同"]})
    text = plugin.context_text("a", text="生日和读书计划")
    data = snapshot(text)
    assert data["sleep_policy"]["sleeping"]
    assert data["pending_changes"][0]["id"] == "review-1"
    assert "maiweave_wake" in text and "maiweave_confirm_change" in text
    assert set(data) == {"now", "timezone", "state", "current", "sleep_policy", "pending_changes"}
    assert (await plugin.sleep_before_send({"session_id": "a"}))["action"] == "abort"


async def test_named_book_is_shared_by_planner_and_reply_in_group(plugin):
    book = await upload(plugin, '# 第一章\n风吹过窗前的银杏树。', '银杏书.md')
    now, plan = await begin_reading(plugin, book)
    answer(plugin, '风吹过窗前的银杏树。')
    await plugin.books.process(now)
    end = now + timedelta(minutes=16)
    plugin.engine.advance(end)
    await plugin.books.process(end)
    plugin.now = lambda: end + timedelta(seconds=1)
    plugin.chat_context.kinds['group'] = 'group'
    planner = await plugin.planner_context(session_id='group', items=[{'item_type':'UserMessageItem','parts':[{'type':'text','text':'聊聊《银杏书》？'}]}])
    reply = await plugin.reply_context(session_id='group', extra_prompt='')
    for text in (planner['modified_kwargs']['items'][-1]['parts'][0]['text'], reply['modified_kwargs']['extra_prompt']):
        assert len(text) <= 2400
        assert snapshot(text)['bookshelf']['notes'][0]['book_id'] == book['id']
    plugin.config.context.reading = False
    assert 'bookshelf' not in snapshot(plugin.context_text('group', book['id']))
    assert plugin.books.notes(book['id'])['items']


async def test_feature_switches_hide_saved_information(plugin):
    plugin.config.context.private_mode = 3
    plugin.config.proactive.enabled = False
    now = plugin.now()
    with plugin.store.db:
        plugin.store.put('dream', {'id':'dream','session_id':'a','status':'已生成','content':'旧梦境'})
        plugin.store.put('share_receipt', {'id':'receipt','session':'a','sent_at':now.isoformat(),'topic':'旧分享'})
    plugin.config.dreams.enabled = False
    data = snapshot(plugin.context_text('a', text='昨晚的梦'))
    assert 'dreams' not in data and 'shared_topics' not in data
    plugin.config.dreams.enabled = True
    plugin.config.proactive.enabled = True
    data = snapshot(plugin.context_text('a', text='昨晚的梦'))
    assert data['dreams'][0]['content'] == '旧梦境'
    assert data['shared_topics'][0]['id'] == 'receipt'


async def test_context_usage_is_bounded_and_exposes_no_message_text(plugin):
    for i in range(15):
        plugin.context_text('a', text=f'私人原话{i}', stage='回复')
    report = (await plugin.ui_usage())['chat_context']
    assert len(report) == 10
    assert report[0]['stage'] == '回复'
    assert '私人原话' not in json.dumps(report, ensure_ascii=False)


async def test_config_upgrade_preserves_choices(plugin):
    defaults = plugin.get_default_config()
    old = deepcopy(defaults)
    del old['context']
    old['plugin']['config_version'] = '0.9.0'
    old['proactive']['daily_limit'] = 9
    upgraded = rebuild_plugin_config_data(defaults, old)
    assert upgraded['context'] == defaults['context']
    assert upgraded['proactive']['daily_limit'] == 9
    assert upgraded['plugin']['config_version'] == '0.11.5'
    upgraded['context']['group_mode'] = '丰富'
    assert rebuild_plugin_config_data(defaults, upgraded)['context']['group_mode'] == '丰富'


async def test_repeated_planner_hook_replaces_previous_life_context(plugin):
    first = await plugin.planner_context(session_id="a", items=[{"item_type":"UserMessageItem", "parts":[{"type":"text", "text":"下个安排是什么"}]}])
    second = await plugin.planner_context(**first["modified_kwargs"])
    items = second["modified_kwargs"]["items"]
    assert len(items) == 2
    assert items[0]["parts"][0]["text"] == "下个安排是什么"
    assert plugin.chat_context.topics["a"] == "下个安排是什么"


async def test_small_budget_keeps_conflict_id_with_large_details(plugin):
    plugin.config.context.private_chars = 1200
    with plugin.store.db:
        plugin.store.put("review", {"id":"review-1", "session_id":"a", "status":"待确认", "action":"save_profile",
            "conflicts":["生日冲突" * 200, "姓名冲突" * 200]})
    text = plugin.context_text("a", text="确认修改资料")
    assert len(text) <= 1200
    assert snapshot(text)["pending_changes"][0]["id"] == "review-1"


async def test_usage_table_and_configuration_bind_to_context_controls(plugin):
    from pathlib import Path
    schema = plugin.get_webui_config_schema()
    section = schema["sections"]["context"]
    assert section["fields"]["group_mode"]["min"] == 1
    assert section["fields"]["group_mode"]["max"] == 3
    assert section["fields"]["private_mode"]["ui_type"] == "slider"
    assert any(tab["sections"] == ["context"] for tab in schema["layout"]["tabs"])
    html = (Path(__file__).parents[1] / "webui/editor.html").read_text(encoding="utf-8")
    metadata = json.loads(html.split("const PAGE_METADATA=", 1)[1].split(";\n", 1)[0])
    table = next(block for block in metadata["usage"]["view"]["content"]
                 if block.get("type") == "table" and block["value"]["field"] == "chat_context")
    plugin.context_text("a")
    row = (await plugin.ui_usage())["chat_context"][0]
    assert all(column["field"] in row for column in table["columns"])


async def test_minimal_group_context_prioritizes_current_speaker(plugin):
    from test_person_presence import message
    for user, text in (("first", "我去忙了"), ("second", "我刚下班了")):
        incoming = message("group", text, user=user)
        incoming["message_info"]["group_info"] = {"group_id": "123"}
        await plugin.management_message(incoming)
    context = snapshot(plugin.context_text("group"))
    assert [row["user_id"] for row in context["person_states"]] == ["second"]
    assert context["person_states"][0]["statement"] == "空闲"


async def test_received_topic_survives_planner_time_and_reminder(plugin):
    from test_person_presence import message
    incoming = message("group", "聊聊今天的读书感想", user="reader")
    incoming["message_info"]["group_info"] = {"group_id": "123"}
    await plugin.management_message(incoming)
    items = [{"item_type": "UserMessageItem", "parts": [{"type": "text", "text": text}]} for text in
             ("聊聊今天的读书感想", "时间：2026-11-09 10:26:00", "请判断是否需要回应")]
    await plugin.planner_context(session_id="group", items=items)
    assert plugin.chat_context.topics["group"] == "聊聊今天的读书感想"
    await plugin.management_message(message("group", "明天有哪些安排", user="reader"))
    await plugin.planner_context(session_id="group", items=items)
    assert plugin.chat_context.topics["group"] == "明天有哪些安排"


def test_text_modes_upgrade_to_slider_positions():
    from maiweave.config import ContextConfig
    for label, position in (("精简", 1), ("均衡", 2), ("丰富", 3)):
        config = ContextConfig(group_mode=label, private_mode=label)
        assert config.group_mode == config.private_mode == position
