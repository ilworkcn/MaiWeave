"""人物近期状态、群私聊上下文与睡前日程衔接。"""
import json
from datetime import timedelta
import pytest
from test_plugin import plugin
from test_sleep_identity import linked_person


def message(stream, text, user="u", platform="qq"):
    return {"session_id": stream, "platform": platform, "processed_plain_text": text,
            "message_info": {"user_info": {"user_id": user}}}


@pytest.mark.parametrize("first,second", [("private", "group"), ("group", "private")])
async def test_statement_then_activity_and_context(plugin, first, second):
    now = plugin.now()
    plugin.now = lambda: now
    await plugin.management_message(message(first, "我去睡了"))
    assert plugin.presence.context(second, now) == []
    now += timedelta(minutes=5)
    await plugin.management_message(message(second, "今天聊什么"))
    record = plugin.presence.context(second, now)[0]
    assert record["statement"] == "准备睡觉"
    assert record["activity"] == "此后仍在发言"
    assert not record["same_chat"]
    assert record["source"] == "reported" and record["activity_source"] == "observed"
    prompt = await plugin.planner_context(session_id=second, items=[])
    assert "此后仍在发言" in prompt["modified_kwargs"]["items"][0]["parts"][0]["text"]
    reply = await plugin.reply_context(session_id=second, extra_prompt="")
    assert "准备睡觉" in reply["modified_kwargs"]["extra_prompt"]
    await plugin.on_unload()
    await plugin.on_load()
    assert plugin.presence.context(second, now)[0]["statement"] == "准备睡觉"
    assert plugin.presence.context(second, now + timedelta(minutes=31)) == []
    now += timedelta(hours=13)
    await plugin.management_message(message(second, "早上好"))
    assert plugin.presence.context(second, now) == []


@pytest.mark.parametrize("text", ["他说我去睡了", "我不去睡了", "昨天我去睡了", "/maiweave", "我去睡了吗？"])
async def test_other_meanings_and_commands(plugin, text):
    await plugin.identify_person("private", "u", "qq")
    plugin.presence.receive("private", "u", text, plugin.now())
    assert plugin.store.all("person_state") == []


async def test_identity_and_updated_state(plugin):
    await plugin.management_message(message("private", "我去睡了"))
    await plugin.management_message(message("group", "你好", "other"))
    await plugin.management_message(message("telegram", "你好", platform="telegram"))
    assert plugin.presence.context("group", plugin.now()) == []
    assert plugin.presence.context("telegram", plugin.now()) == []
    await plugin.management_message(message("group", "我起床了"))
    assert plugin.presence.context("group", plugin.now())[0]["statement"] == "空闲"
    result = await plugin.person_state("忙碌", stream_id="group", user_id="u", platform="qq")
    assert result["success"]
    assert plugin.presence.context("group", plugin.now())[0]["statement"] == "忙碌"


async def test_person_sleep_is_separate_from_bot_sleep(plugin):
    await linked_person(plugin)
    from test_sleep_policy import sleep_plan
    now, _ = sleep_plan(plugin)
    plugin.now = lambda: now
    await plugin.management_message(message("private", "我去睡了"))
    now += timedelta(minutes=2)
    await plugin.management_message(message("group", "今晚真热闹"))
    assert plugin.presence.context("group", now)[0]["activity"] == "此后仍在发言"
    assert await plugin.sleep_before_send({"session_id": "group"}) == {"action": "abort"}
    await plugin.management_message(message("group", "陪陪我"))
    assert await plugin.sleep_before_send({"session_id": "private"}) == {"action": "continue"}
    await plugin.management_message(message("private", "我去睡了"))
    assert await plugin.sleep_before_send({"session_id": "group"}) == {"action": "abort"}


@pytest.mark.parametrize("enabled", [True, False])
async def test_activity_finishes_before_bedtime(plugin, enabled):
    plugin.config.life.autonomous = True
    plugin.config.life.schedule_enabled = enabled
    now = plugin.now().replace(hour=22, minute=30, second=0, microsecond=0)
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "title": "听音乐", "kind": "leisure", "minutes": 90, "location": "住处", "motivation": "想听新专辑"})}
    await plugin.living.choose_activity(now)
    plan = next(p for p in plugin.store.all("plan") if p["kind"] == "leisure")
    assert plugin.engine.time(plan["end"]) == now + timedelta(minutes=30 if enabled else 90)
    if enabled:
        bedtime = now + timedelta(minutes=30)
        plugin.engine.advance(bedtime)
        await plugin.living.choose_activity(bedtime)
        plugin.engine.advance(bedtime)
        assert plugin.living.sleeping(bedtime)
        plugin.now = lambda: bedtime
        for stream in ["private", "group"]:
            assert await plugin.sleep_before_send({"session_id": stream}) == {"action": "abort"}


async def test_return_home_before_sleep(plugin):
    plugin.config.life.autonomous = True
    now = plugin.now().replace(hour=22, minute=40, second=0, microsecond=0)
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "location": "公园"})
    await plugin.living.choose_activity(now)
    rest = next(p for p in plugin.store.all("plan") if p["kind"] == "rest")
    assert rest["location"] == "住处"
    assert plugin.engine.time(rest["end"]) == now.replace(hour=23, minute=0)
    assert rest["travel_id"]
    plugin.ctx.llm.generate.assert_not_awaited()


async def test_return_arrives_exactly_at_bedtime(plugin):
    plugin.config.life.autonomous = True
    now = plugin.now().replace(hour=22, minute=45, second=0, microsecond=0)
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "location": "公园"})
    await plugin.living.choose_activity(now)
    sleep = next(p for p in plugin.store.all("plan") if p["kind"] == "sleep")
    assert plugin.engine.time(sleep["start"]) == now.replace(hour=23, minute=0)
    assert plugin.engine.time(sleep["end"]).hour == 7
    plugin.engine.advance(now.replace(hour=23, minute=0))
    assert plugin.living.sleeping(now.replace(hour=23, minute=0))


@pytest.mark.parametrize("stream", ["private", "group"])
async def test_sleep_starts_while_reply_is_being_generated(plugin, stream):
    from maiweave.core.models import PlanInput
    now = plugin.now().replace(hour=22, minute=59, second=0, microsecond=0)
    plugin.now = lambda: now
    plugin.spatial.schedule(PlanInput(title="睡觉", kind="sleep", location="住处",
        start=(now + timedelta(minutes=1)).isoformat(), end=(now + timedelta(hours=8)).isoformat()), "", now)
    before = await plugin.planner_context(session_id=stream, items=[])
    snapshot = json.loads(before["modified_kwargs"]["items"][0]["parts"][0]["text"].split("\n")[-1])
    assert not snapshot["sleep_policy"]["sleeping"]
    now += timedelta(minutes=2)
    assert await plugin.sleep_before_send({"session_id": stream}) == {"action": "abort"}
