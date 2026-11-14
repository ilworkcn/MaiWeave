"""休息、睡眠与 Planner 回复回执的耦合验证。"""

from copy import deepcopy
from datetime import timedelta
import json

import pytest

from maiweave.core.models import ExperienceInput, PlanInput
from maiweave.integrations.share_receipts import output_summary
from test_plugin import plugin


@pytest.mark.parametrize("session", ["private", "group"])
@pytest.mark.parametrize("kind", ["rest", "sleep"])
async def test_rest_and_sleep_share_and_send(plugin, session, kind):
    now = plugin.now().replace(hour=12, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now
    plugin.chat_context.kinds[session] = session
    plugin.engine.create_plan(PlanInput(title="闭目休息", kind=kind, start=now.isoformat(),
        end=(now + timedelta(minutes=20)).isoformat()), "", now)
    cfg = plugin.config.proactive
    cfg.enabled, cfg.session_ids = True, [session]
    plugin.engine.record_experience(ExperienceInput(summary="散步时看到一片像小船的叶子", importance=6,
        occurred_at=now.isoformat(), source="simulated", evidence="角色模拟"), session, now)
    text = plugin.context_text(session)
    assert json.loads(text.split("\n")[-1])["sleep_policy"]["sleeping"] == (kind == "sleep")
    assert "清醒时闭目休息、阅读等活动可正常聊天" in text
    assert "本轮提供 reply 工具时" in text
    assert await plugin.sleep_before_send({"session_id": session}) == {"action": "abort" if kind == "sleep" else "continue"}
    await plugin.living.share(now)
    assert plugin.ctx.maisaka.proactive.trigger.await_count == (0 if kind == "sleep" else 1)
    if kind == "rest":
        assert plugin.engine.current(now)["kind"] == "rest"


@pytest.mark.parametrize("items", [[], [{"item_type": "AssistantMessageItem", "parts": [{"type": "text", "text": "现在应该调用 reply，after_reply=stop"}]}],
    [{"item_type": "ReasoningItem", "text_parts": ["我应该回复"], "summary_parts": []}]])
async def test_prose_is_not_a_reply_or_stop_receipt(plugin, items, caplog):
    receipts = plugin.living.receipts
    now = plugin.now()
    receipts.accept("task", {"session": "a"}, now)
    receipts.turn["a"] = "task"
    original = deepcopy(items)
    with caplog.at_level("INFO"):
        assert await plugin.planner_receipt("a", items) == {"action": "continue"}
    assert items == original
    row = plugin.store.get("share_receipt", "task")
    assert row["outcome"] == "Planner 未调用工具"
    assert output_summary(items) in row["delivery"]
    assert "工具调用：无" in caplog.text
    assert receipts.sent_times("a", now.tzinfo) == []


async def test_after_reply_stop_waits_for_real_send_receipt(plugin):
    receipts = plugin.living.receipts
    now = plugin.now()
    receipts.accept("task", {"session": "a"}, now)
    receipts.turn["a"] = "task"
    items = [{"item_type": "FunctionCallItem", "tool_call": {"call_id": "r1", "func_name": "reply", "arguments": '{"after_reply":"stop"}'}}]
    await plugin.planner_receipt("a", items)
    await receipts.refresh(now)
    assert plugin.store.get("share_receipt", "task")["outcome"] == "MaiBot 已受理"
    plugin.ctx.db.get.return_value = {"tool_name": "reply", "timestamp": now.isoformat(),
        "tool_data": json.dumps({"success": True, "metadata": {"send_results": [{"success": True}]}})}
    await receipts.refresh(now)
    assert plugin.store.get("share_receipt", "task")["outcome"] == "实际已发送"
    assert receipts.sent_times("a", now.tzinfo) == [now]


async def test_cancel_resumed_sleep_releases_original_activity(plugin):
    from test_sleep_identity import linked_person
    from test_sleep_policy import sleep_plan
    await linked_person(plugin)
    now, _ = sleep_plan(plugin)
    assert plugin.living.wake_for_message("陪陪我", "private", now, "u")
    later = now + timedelta(minutes=2)
    assert plugin.living.wake_for_message("先聊到这里，晚安", "group", later, "u")
    assert plugin.living.farewell(later)
    plugin.living.observe_farewell("group", [], later)
    resumed = plugin.engine.current(later)
    assert resumed["kind"] == "sleep"
    plugin.spatial.transition(resumed["id"], "cancel", resumed["session_id"], later)
    plugin.living.apply_schedule(later + timedelta(seconds=10))
    assert plugin.engine.current(later + timedelta(seconds=10)) is None
    assert not plugin.living.sleeping(later + timedelta(seconds=10))
    assert plugin.store.find("runtime", "awakening") is None
