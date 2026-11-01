"""用量统计、连续活动、语境唤醒和资料工具的联动。"""

import json
from datetime import timedelta

import pytest

from maiweave.usage import generate, summary
from test_plugin import plugin
from test_sleep_policy import sleep_plan


async def test_usage_records_host_tokens_and_survives_reload(plugin):
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "内容", "model_name": "模型A",
        "prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125, "prompt_cache_hit_tokens": 40}
    await generate(plugin.ctx, plugin.store, plugin.config, "梦境", "提示")
    plugin.ctx.llm.generate.return_value = {"success": False, "error": "模型繁忙"}
    with pytest.raises(RuntimeError, match="模型繁忙"):
        await generate(plugin.ctx, plugin.store, plugin.config, "自主活动", "提示")
    plugin.ctx.llm.generate.side_effect = RuntimeError("连接中断")
    with pytest.raises(RuntimeError, match="连接中断"):
        await generate(plugin.ctx, plugin.store, plugin.config, "目标分解", "提示")
    result = await plugin.ui_usage(1)
    assert result["totals"]["calls"] == 7
    assert result["totals"]["total"] == 125
    assert result["totals"]["cached"] == 40
    assert result["totals"]["missing_calls"] == 6
    assert result["totals"]["success_rate"] == 14.3
    assert (await plugin.ui_usage(7, "梦境"))["totals"]["calls"] == 1
    await plugin.on_unload()
    await plugin.on_load()
    assert (await plugin.ui_usage(30))["totals"]["total"] == 125


async def test_usage_period_uses_local_calendar_days(plugin):
    now = plugin.now()
    with plugin.store.db:
        for days in (0, 6, 7, 29, 30):
            plugin.store.put("model_usage", {"id": str(days), "at": (now - timedelta(days=days)).isoformat(),
                "feature": "梦境", "status": "成功", "prompt_tokens": 2, "completion_tokens": 3,
                "total_tokens": 5, "prompt_cache_hit_tokens": 0, "seconds": 1})
    assert summary(plugin.store, now, 1)["totals"]["calls"] == 1
    assert summary(plugin.store, now, 7)["totals"]["calls"] == 2
    assert summary(plugin.store, now, 30)["totals"]["calls"] == 4


@pytest.mark.parametrize("text", ["我已经不难过了", "我不再难过", "并不难过", "之前难过，现在已经好了"])
async def test_recovered_mood_keeps_sleep(plugin, text):
    now, plan = sleep_plan(plugin)
    assert not plugin.living.wake_for_message(text, "chat-a", now)
    assert plugin.store.get("plan", plan["id"])["status"] == "active"


async def test_chat_extends_wake_and_goodnight_releases_it(plugin):
    person = plugin.anniversaries.save_profile("亲密对象", "chat-a", "u1")
    with plugin.store.db:
        plugin.store.put("profile", {**person, "intimacy": 3, "intimacy_mode": "manual", "relationship": "恋人"})
    now, _ = sleep_plan(plugin)
    assert plugin.living.wake_for_message("陪陪我", "chat-a", now, "u1")
    assert plugin.living.wake_for_message("今天发生了一件事", "chat-a", now + timedelta(minutes=40), "u1")
    assert plugin.living.awake_until(now + timedelta(minutes=65)) == now + timedelta(minutes=160)
    assert not plugin.living.wake_for_message("普通消息", "chat-a", now + timedelta(minutes=60), "u2")
    assert plugin.living.wake_for_message("先聊到这里，晚安", "chat-a", now + timedelta(minutes=70), "u1")
    assert plugin.living.awake_until(now + timedelta(minutes=70)) is None


async def test_planner_wake_tool_uses_current_session(plugin):
    person = plugin.anniversaries.save_profile("亲密对象", "a", "u")
    with plugin.store.db:
        plugin.store.put("profile", {**person, "intimacy": 3, "intimacy_mode": "manual", "relationship": "恋人"})
    now, plan = sleep_plan(plugin)
    plugin.now = lambda: now
    result = await plugin.wake("用户说今天独自承受了很多，希望有人陪伴", stream_id="a", user_id="u")
    assert result["content"]["awake"]
    assert plugin.store.get("plan", plan["id"])["status"] == "cancelled"
    assert plugin.store.get("runtime", "awakening")["user_id"] == "u"


async def test_conversation_personal_data_round_trip(plugin):
    plugin.ctx.llm.generate.return_value = {"success": True, "response": '{"conflicts":[],"level":1,"relationship":"朋友","reason":"测试关系"}'}
    person = await plugin.personal("profile", name="小明", notes="喜欢散步", stream_id="a", user_id="u")
    person_id = person["content"]["result"]["id"]
    created = await plugin.personal("save_date", title="生日", calendar="lunar", month=8, day=3, stream_id="a", user_id="u")
    date_id = created["content"]["result"]["id"]
    assert created["content"]["result"]["profile_id"] == person_id
    changed = await plugin.personal("save_date", anniversary_id=date_id, month=8, day=5, calendar="lunar", stream_id="a", user_id="u")
    assert changed["content"]["result"]["day"] == 5
    assert (await plugin.ui_anniversaries())["anniversaries"][0]["day"] == 5
    other = await plugin.personal("delete_date", anniversary_id=date_id, stream_id="b", user_id="u")
    assert other["success"] is False
    await plugin.personal("delete_date", anniversary_id=date_id, stream_id="a", user_id="u")
    assert plugin.store.all("anniversary") == []


async def test_personal_date_requires_explicit_month_and_day(plugin):
    result = await plugin.personal("save_date", stream_id="a", user_id="u")
    assert result["success"] is False
    assert plugin.store.all("profile") == []


async def test_finished_activity_triggers_next_plan_inside_interval(plugin):
    now = plugin.now()
    plugin.config.life.schedule_enabled = False
    plugin.config.life.autonomous = True
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "title": "读书", "kind": "leisure", "minutes": 10, "location": "住处", "motivation": "想读书"})}
    await plugin.living.choose_activity(now)
    await plugin.living.choose_activity(now + timedelta(minutes=1))
    assert plugin.ctx.llm.generate.await_count == 1
    plugin.engine.advance(now + timedelta(minutes=11))
    await plugin.living.choose_activity(now + timedelta(minutes=11))
    assert plugin.ctx.llm.generate.await_count == 2


async def test_unchanged_failed_planning_waits_for_interval(plugin):
    now = plugin.now().replace(hour=12)
    plugin.config.life.autonomous = True
    plugin.ctx.llm.generate.return_value = {"success": False, "error": "模型繁忙"}
    with pytest.raises(RuntimeError):
        await plugin.living.choose_activity(now)
    await plugin.living.choose_activity(now + timedelta(minutes=1))
    assert plugin.ctx.llm.generate.await_count == 3


async def test_next_day_continuation_uses_saved_subject_and_progress(plugin):
    now = plugin.now().replace(hour=12)
    plugin.config.life.autonomous = True
    with plugin.store.db:
        plugin.store.put("activity_memory", {"id": "book", "session_id": "a", "activity": "阅读", "subject": "海边的故事",
            "next_action": "从第三章继续", "details": {"进度": "第二章"}, "updated_at": (now - timedelta(days=1)).isoformat(),
            "experience_id": "old", "kind": "leisure", "location": "住处", "completed": False})
    await plugin.living.choose_activity(now)
    plan = next(p for p in plugin.store.all("plan") if p["kind"] == "leisure")
    assert plan["continuation_id"] == "book"
    assert "海边的故事" in plan["title"] and "第三章" in plan["motivation"]
    plugin.ctx.llm.generate.assert_not_awaited()
    plugin.engine.advance(now + timedelta(minutes=31))
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "activity": "读小说", "subject": "模型的另一种称呼", "summary": "读完了第三章", "details": {"进度": "第三章"},
        "enjoyment": 3, "next_action": "从第四章继续", "completed": True})}
    await plugin.continuity.reflect(plan["id"], now + timedelta(minutes=31))
    records = plugin.store.all("activity_memory")
    assert len(records) == 1
    latest = plugin.store.get("activity_memory", "book")
    assert latest["subject"] == "海边的故事"
    assert latest["completed"] is True
    plugin.engine.revise_experience(latest["experience_id"], {"进度": "第四章"}, "读到第四章", "用户更正阅读进度", "a", now + timedelta(minutes=32))
    corrected = plugin.store.get("activity_memory", "book")
    assert corrected["details"]["进度"] == "第四章"
    assert "第四章" in corrected["next_action"]
    assert corrected["completed"] is True
    assert (await plugin.ui_continuity())["continuations"][0]["progress_status"] == "已完成"


async def test_editor_reads_saved_form_values(plugin):
    person = await plugin.ui_save_profile("小明", "a", notes="喜欢散步")
    result = await plugin.ui_editor_records("profile", "小明")
    assert result["records"][0]["notes"] == "喜欢散步"
    await plugin.ui_save_profile("小明", "a", notes="喜欢读书", profile_id=person["id"])
    assert (await plugin.ui_editor_records("profile"))["records"][0]["notes"] == "喜欢读书"


async def test_zero_provider_usage_is_unreported(plugin):
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "实际生成内容",
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "prompt_cache_hit_tokens": 0}
    await generate(plugin.ctx, plugin.store, plugin.config, "资料核对", "核对资料")
    result = await plugin.ui_usage(1)
    assert result["totals"]["missing_calls"] == 1
    assert result["recent"][0]["usage_label"] == "用量未返回"
    row = plugin.store.all("model_usage")[0]
    row.update(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    with plugin.store.db:
        plugin.store.put("model_usage", row)
    assert (await plugin.ui_usage(1))["totals"]["missing_calls"] == 1
