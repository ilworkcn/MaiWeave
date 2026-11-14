"""告别回复、分段发送与睡眠恢复的联动。"""
from datetime import timedelta
import json
import pytest
from maiweave.core.models import PlanInput
from test_plugin import plugin


def prepare(plugin, hour=23):
    now = plugin.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now
    plugin.engine.create_plan(PlanInput(title="睡觉", kind="sleep", start=now.isoformat(),
        end=(now + timedelta(hours=2)).isoformat(), location="住处"), "", now)
    plugin.engine.advance(now)
    assert plugin.living.wake("private", now, "测试陪伴", "u", require_intimacy=False)
    assert plugin.living.wake_for_message("晚安，去睡吧", "private", now, "u")
    return now


def calls(*names):
    return [{"item_type": "FunctionCallItem", "tool_call": {"call_id": str(i), "func_name": name,
            "arguments": '{"after_reply":"stop"}'}} for i, name in enumerate(names)]


@pytest.mark.parametrize("hour", [13, 23])
@pytest.mark.parametrize("success", [True, False])
async def test_segments_finish_before_sleep(plugin, hour, success):
    now = prepare(plugin, hour)
    await plugin.planner_receipt("private", calls("reply", "send_emoji"))
    assert plugin.living.proactive_pause(now)["reason"] == "farewell"
    assert plugin.living.greetings.window(now) is None
    assert await plugin.sleep_before_send({"session_id": "other"}) == {"action": "abort"}
    for _ in range(3):
        assert await plugin.sleep_before_send({"session_id": "private"}) == {"action": "continue"}
        await plugin.schedule_after_send({"session_id": "private"}, sent=True)
        await plugin.living.refresh_farewell(now)
        assert not plugin.living.sleeping(now)
    async def receipt(table, filters, **kwargs):
        if filters["tool_id"] == "0":
            return {"tool_name": "reply", "tool_data": json.dumps({"success": success})}
    plugin.ctx.db.get.side_effect = receipt
    await plugin.living.refresh_farewell(now)
    assert not plugin.living.sleeping(now)
    plugin.ctx.db.get.side_effect = None
    async def complete(table, filters, **kwargs):
        return {"tool_name": "reply" if filters["tool_id"] == "0" else "send_emoji", "tool_data": json.dumps({"success": success})}
    plugin.ctx.db.get.side_effect = complete
    await plugin.living.refresh_farewell(now)
    assert plugin.living.quiet_sleep(now)
    assert plugin.engine.current(now)["kind"] == "sleep"
    assert await plugin.sleep_before_send({"session_id": "private"}) == {"action": "abort"}


@pytest.mark.parametrize("outcome", ["silent", "stop", "wait", "timeout"])
async def test_silence_and_expiry_resume_sleep(plugin, outcome):
    now = prepare(plugin)
    if outcome == "silent":
        await plugin.planner_receipt("private", [])
    elif outcome == "timeout":
        now += timedelta(minutes=5)
        plugin.living.apply_schedule(now)
    else:
        await plugin.planner_receipt("private", calls(outcome))
        plugin.ctx.db.get.return_value = {"tool_name": outcome, "tool_data": '{"success":true}'}
        await plugin.living.refresh_farewell(now)
    assert plugin.living.quiet_sleep(now)


@pytest.mark.parametrize("budget", [1200, 2400, 4800])
async def test_context_budget_and_session(plugin, budget):
    now = prepare(plugin)
    plugin.config.context.private_chars = budget
    plugin.config.context.group_chars = budget
    for session in ["private", "group"]:
        plugin.chat_context.kinds[session] = session
        text = plugin.context_text(session)
        assert len(text) <= budget
        assert ("after_reply=stop；本轮发送完毕" in text) == (session == "private")
    await plugin.planner_receipt("group", [])
    assert plugin.living.farewell(now)


async def test_followup_reopens_conversation_and_old_receipt_is_ignored(plugin):
    now = prepare(plugin)
    await plugin.planner_receipt("private", calls("reply"))
    assert plugin.living.wake_for_message("等等，还有件事想聊", "private", now, "u")
    assert plugin.living.farewell(now) is None
    plugin.ctx.db.get.return_value = {"tool_name": "reply"}
    await plugin.living.refresh_farewell(now)
    assert plugin.living.awake_until(now) > now + timedelta(minutes=5)


async def test_restart_all_day_and_expired_nap(plugin):
    now = prepare(plugin, 13)
    await plugin.on_unload()
    await plugin.on_load()
    assert plugin.living.farewell(now)
    plugin.config.life.schedule_enabled = False
    plugin.living.apply_schedule(now)
    assert not plugin.living.farewell(now)
    assert await plugin.sleep_before_send({"session_id": "other"}) == {"action": "continue"}
    assert plugin.living.greetings.window(now) is None
    plugin.config.life.schedule_enabled = True
    assert not plugin.living.farewell(now)


async def test_nap_end_and_no_duplicate_greeting(plugin):
    now = prepare(plugin, 13)
    row = plugin.store.get("runtime", "awakening")
    with plugin.store.db:
        plugin.store.put("runtime", {**row, "sleep_end": now.isoformat()})
    await plugin.planner_receipt("private", [])
    assert not plugin.living.sleeping(now)
    assert plugin.engine.current(now) is None


async def test_proactive_pauses_in_both_modes(plugin):
    now = prepare(plugin)
    plugin.config.proactive.enabled = True
    plugin.config.proactive.start_hour = plugin.config.proactive.end_hour = 0
    for mode in ["自然交流", "主动表达"]:
        plugin.config.proactive.mode = mode
        await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    plugin.ctx.send.text.assert_not_awaited()


async def test_readonly_status_during_farewell(plugin):
    now = prepare(plugin)
    before = list(plugin.store.db.iterdump())
    status = await plugin.get_life_status()
    assert status["quiet"] is False and status["effective_sleeping"] is False
    assert (await plugin.evaluate_behavior("conversation"))["decision"] == "allow"
    proactive = await plugin.evaluate_behavior("proactive")
    assert proactive["decision"] == "defer" and proactive["reason"] == "farewell"
    assert proactive["until"] == (now + timedelta(minutes=5)).isoformat()
    assert list(plugin.store.db.iterdump()) == before
