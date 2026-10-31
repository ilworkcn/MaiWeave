"""全天候模式和关键词唤醒的实际状态转换。"""

import json
from datetime import timedelta

import pytest

from maiweave.models import PlanInput
from test_plugin import plugin


def sleep_plan(plugin, autonomous=True):
    now = (plugin.now() + timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
    plan = plugin.spatial.schedule(PlanInput(title="睡觉", kind="sleep", location="住处",
        start=now.isoformat(), end=(now + timedelta(hours=8)).isoformat()), "", now, autonomous=autonomous)
    plugin.engine.advance(now)
    return now, plan


async def test_all_day_mode_cancels_autonomous_sleep_and_does_not_reschedule(plugin):
    now, plan = sleep_plan(plugin)
    plugin.config.life.schedule_enabled = False
    plugin.config.life.autonomous = True
    plugin.living.apply_schedule(now + timedelta(minutes=20))
    assert plugin.store.get("plan", plan["id"])["status"] == "cancelled"
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "title": "读书", "kind": "leisure", "minutes": 20, "location": "住处", "motivation": "继续阅读"})}
    await plugin.living.choose_activity(now + timedelta(minutes=21))
    assert all(p["kind"] != "sleep" for p in plugin.store.all("plan") if p["status"] in {"active", "scheduled"})


async def test_all_day_preserves_explicit_sleep_plan(plugin):
    now, plan = sleep_plan(plugin, autonomous=False)
    plugin.config.life.schedule_enabled = False
    plugin.living.apply_schedule(now)
    assert plugin.store.get("plan", plan["id"])["status"] == "active"


async def test_keyword_wakes_extends_and_returns_to_sleep(plugin):
    now, plan = sleep_plan(plugin)
    plugin.config.life.autonomous = True
    when = now + timedelta(minutes=20)
    plugin.now = lambda: when
    result = await plugin.management_message({"processed_plain_text": "今天心情不好，陪陪我", "session_id": "chat-a"})
    assert result == {"action": "continue"}
    assert plugin.store.get("plan", plan["id"])["status"] == "cancelled"
    assert plugin.store.get("plan", plan["id"])["elapsed_seconds"] == 20 * 60
    await plugin.living.choose_activity(when + timedelta(minutes=1))
    assert plugin.engine.current(when + timedelta(minutes=1)) is None
    assert plugin.living.wake_for_message("还是难过", "chat-a", when + timedelta(minutes=50))
    assert plugin.living.awake_until(when + timedelta(minutes=61)) == when + timedelta(minutes=110)
    await plugin.on_unload()
    await plugin.on_load()
    assert plugin.living.awake_until(when + timedelta(minutes=61)) == when + timedelta(minutes=110)
    await plugin.living.choose_activity(when + timedelta(minutes=111))
    assert any(p["kind"] == "sleep" and p["status"] in {"active", "scheduled"} for p in plugin.store.all("plan"))


@pytest.mark.parametrize("mode", ["disabled", "all_day", "no_wake", "empty", "other_word", "command"])
async def test_non_waking_messages_preserve_sleep(plugin, mode):
    now, plan = sleep_plan(plugin)
    text = "心情不好"
    if mode == "disabled":
        plugin.config.life.enabled = False
    elif mode == "all_day":
        plugin.config.life.schedule_enabled = False
    elif mode == "no_wake":
        plugin.config.life.wake_enabled = False
    elif mode == "empty":
        plugin.config.life.wake_keywords = ["", " "]
    elif mode == "other_word":
        plugin.config.life.wake_keywords = ["请醒来"]
    else:
        text = "/其他插件 心情不好"
    assert not plugin.living.wake_for_message(text, "chat-a", now)
    assert plugin.store.get("plan", plan["id"])["status"] == "active"


async def test_custom_keyword(plugin):
    now, _ = sleep_plan(plugin)
    plugin.config.life.wake_keywords = ["请醒来"]
    assert plugin.living.wake_for_message("请醒来，我想聊天", "chat-a", now)
