"""自然问候与作息、人物、群聊、额度和原生回执的耦合。"""
import json
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from maiweave.core.models import PlanInput
from test_plugin import plugin


async def prepare(plugin, kind="晚安", group=False):
    now = (plugin.now() + timedelta(days=1)).replace(hour=22 if kind == "晚安" else 7, minute=45 if kind == "晚安" else 5, second=0, microsecond=0)
    plugin.now = lambda: now
    plugin.config.proactive.enabled = True
    plugin.config.proactive.natural_greetings = True
    plugin.config.life.autonomous = True
    plugin.ctx.chat.get_all_streams.return_value = [{"stream_id": "a", "platform": "qq", "group_id": "g" if group else "", "user_id": "u"}]
    profile = plugin.anniversaries.save_profile("朋友", "a", "u", person_id="qq:u")
    with plugin.store.db:
        plugin.store.put("profile", {**profile, "intimacy": 2, "intimacy_mode": "manual"})
    plugin.ctx.db.get.return_value = [{"timestamp": (now-timedelta(minutes=10)).isoformat(), "user_id": "u", "user_nickname": "朋友",
                                     "processed_plain_text": "今天终于把那件事办好了", "is_mentioned": group}]
    if kind == "早安":
        end = now.replace(minute=0)
        start = end-timedelta(hours=8)
        plugin.engine.create_plan(PlanInput(title="夜间睡眠",kind="sleep",start=start.isoformat(),end=end.isoformat()), "", start)
        plugin.engine.advance(now)
    return now


@pytest.mark.parametrize("kind", ["早安", "晚安"])
async def test_natural_greetings_use_planner_and_persist_once(plugin, kind):
    now = await prepare(plugin, kind)
    plugin.config.proactive.mode = "自然交流"
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_awaited_once()
    receipt, = plugin.store.all("share_receipt")
    assert receipt["mode"] == "自然交流" and receipt["greeting_kind"] == kind
    assert plugin.living.receipts.sent_times("a", now.tzinfo) == []
    plugin.living.receipts.turn["a"] = receipt["id"]
    plugin.living.receipts.observe("a", [{"item_type":"FunctionCallItem", "tool_call":{"call_id":"stop-id", "func_name":"stop"}}])
    plugin.ctx.db.get.return_value = {"tool_name":"stop", "tool_data":json.dumps({"success":True}), "timestamp":now.isoformat()}
    await plugin.living.receipts.refresh(now)
    assert plugin.store.get("share_receipt",receipt["id"])["outcome"] == "Planner 选择沉默"
    plugin.ctx.db.get.return_value = [{"timestamp":now.isoformat(),"user_id":"u","user_nickname":"朋友","processed_plain_text":"今天天气很好"}]
    await plugin.on_unload()
    await plugin.on_load()
    await plugin.living.share(now+timedelta(minutes=10))
    plugin.ctx.maisaka.proactive.trigger.assert_awaited_once()


@pytest.mark.parametrize("case", ["开关关闭", "全天候", "陌生人物", "旧消息", "已说晚安", "额度", "冷却", "正在陪伴"])
async def test_greeting_gates(plugin, case):
    now = await prepare(plugin)
    if case == "开关关闭": plugin.config.proactive.natural_greetings = False
    if case == "全天候": plugin.config.life.schedule_enabled = False
    if case == "陌生人物":
        with plugin.store.db:
            p=plugin.store.all("profile")[0];plugin.store.put("profile",{**p,"intimacy":0})
    if case == "旧消息": plugin.ctx.db.get.return_value[0]["timestamp"]=(now-timedelta(days=2)).isoformat()
    if case == "已说晚安": plugin.ctx.db.get.return_value[0]["processed_plain_text"]="晚安，睡个好觉"
    if case == "额度": plugin.config.proactive.daily_limit=0
    if case == "冷却":
        with plugin.store.db:
            plugin.store.put("share_receipt",{"id":"sent","session":"a","created":now.isoformat(),"outcome":"实际已发送","sent_at":now.isoformat()})
    if case == "正在陪伴":
        with plugin.store.db:
            plugin.store.put("runtime",{"id":"awakening","until":(now+timedelta(hours=1)).isoformat()})
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()


@pytest.mark.parametrize("kind,mentioned,age,expected", [("早安",True,10,False),("晚安",False,10,False),("晚安",True,40,False),("晚安",True,10,True)])
async def test_group_only_says_goodnight_after_interaction(plugin,kind,mentioned,age,expected):
    now=await prepare(plugin,kind,True)
    plugin.ctx.db.get.return_value[0].update(is_mentioned=mentioned,timestamp=(now-timedelta(minutes=age)).isoformat())
    await plugin.living.share(now)
    assert bool(plugin.ctx.maisaka.proactive.trigger.await_count) == expected


async def test_bedtime_and_send_gate_are_preserved(plugin):
    now=await prepare(plugin)
    await plugin.living.share(now)
    bedtime=now.replace(hour=23,minute=0)
    plugin.engine.create_plan(PlanInput(title="按时睡觉",kind="sleep",start=bedtime.isoformat(),end=(bedtime+timedelta(hours=8)).isoformat()),"",now)
    plugin.now=lambda:bedtime
    plugin.engine.advance(bedtime)
    assert plugin.living.greetings.window(bedtime) is None
    assert await plugin.sleep_before_send({"session_id":"a"}) == {"action":"abort"}
    await plugin.living.share(bedtime)
    plugin.ctx.maisaka.proactive.trigger.assert_awaited_once()


async def test_actual_sent_greeting_counts_toward_other_shares(plugin):
    now=await prepare(plugin)
    await plugin.living.share(now)
    row=plugin.store.all("share_receipt")[0]
    plugin.living.receipts.turn["a"]=row["id"]
    plugin.living.receipts.observe("a",[{"item_type":"FunctionCallItem","tool_call":{"call_id":"reply-id","func_name":"reply"}}])
    async def get(table, **kwargs):
        if table == "ToolRecord":
            return {"tool_name":"reply","timestamp":now.isoformat(),"tool_data":json.dumps({"success":True,"metadata":{"sent_message_ids":["sent-msg"]}})}
        return {"timestamp":now.isoformat()}
    plugin.ctx.db.get=AsyncMock(side_effect=get)
    await plugin.living.receipts.refresh(now)
    assert plugin.living.receipts.sent_times("a",now.tzinfo)==[now]
    plugin.config.proactive.start_hour=plugin.config.proactive.end_hour=0
    plugin.config.proactive.natural_greetings=False
    await plugin.living.share(now+timedelta(minutes=1))
    assert "上次实际发送" in plugin.living.share_decisions[0]["reason"]


async def test_expired_window_short_nap_and_fixed_evening_plan(plugin):
    now=await prepare(plugin,"早安")
    assert plugin.living.greetings.window(now+timedelta(hours=1)) is None
    with plugin.store.db:
        p=plugin.store.all("plan")[0];plugin.store.put("plan",{**p,"elapsed_seconds":1800})
    assert plugin.living.greetings.window(now) is None
    evening=now.replace(hour=22,minute=45)
    plugin.engine.create_plan(PlanInput(title="约好的电影",start=evening.isoformat(),end=(evening+timedelta(hours=2)).isoformat()),"a",evening)
    assert plugin.living.greetings.window(evening) is None


async def test_greeting_request_failure_can_retry(plugin):
    now=await prepare(plugin)
    plugin.ctx.maisaka.proactive.trigger.return_value={"success":False,"error":"请求失败"}
    with pytest.raises(RuntimeError): await plugin.living.share(now)
    assert plugin.store.all("share_receipt")[0]["outcome"] == "请求失败"
    plugin.config.proactive.retry_minutes = 1
    plugin.ctx.maisaka.proactive.trigger.return_value={"success":True,"task_id":"retry"}
    await plugin.living.share(now + timedelta(minutes=1))
    assert plugin.store.get("share_receipt","retry")["greeting_kind"]=="晚安"


async def test_explicit_sleep_time_takes_priority_over_habit(plugin):
    now=await prepare(plugin)
    start=now+timedelta(hours=2)
    plugin.engine.create_plan(PlanInput(title="今晚晚些休息",kind="sleep",start=start.isoformat(),end=(start+timedelta(hours=8)).isoformat()),"",now)
    assert plugin.living.greetings.window(now) is None
    window=plugin.living.greetings.window(start-timedelta(minutes=10))
    assert window["kind"]=="晚安" and window["until"]==start


async def test_wakeup_after_interrupted_sleep(plugin):
    now=await prepare(plugin,"早安")
    with plugin.store.db:
        plan=plugin.store.all("plan")[0]
        plugin.store.put("plan",{**plan,"elapsed_seconds":3600})
        plugin.store.put("plan",{**plan,"id":"earlier-sleep","status":"cancelled","elapsed_seconds":4*3600})
    assert plugin.living.greetings.window(now)["kind"]=="早安"


async def test_conversation_greeting_and_person_deduplication(plugin):
    now=await prepare(plugin)
    window=plugin.living.greetings.window(now)
    plugin.ctx.db.get.return_value[0]["processed_plain_text"]="今天聊得开心，晚安"
    assert await plugin.living.greetings.candidate("a",now,window) is None
    plugin.ctx.db.get.return_value[0]["processed_plain_text"]="今天聊得开心"
    with plugin.store.db:
        plugin.store.put("share_receipt",{"id":"other-stream","session":"another","created":now.isoformat(),
            "outcome":"实际已发送","sent_at":now.isoformat(),"greeting_key":f"qq:u:{now.date().isoformat()}:晚安"})
    assert await plugin.living.greetings.candidate("a",now,window) is None


async def test_important_experience_keeps_priority_in_normal_hours(plugin):
    from maiweave.core.models import ExperienceInput
    now=await prepare(plugin)
    plugin.config.proactive.start_hour=plugin.config.proactive.end_hour=0
    plugin.engine.record_experience(ExperienceInput(summary="完成了长期目标",importance=9,score_reason="重要进展",
        source="observed",evidence="已确认完成",occurred_at=now.isoformat()),"a",now)
    await plugin.living.share(now)
    row=plugin.store.all("share_receipt")[0]
    assert "greeting_key" not in row
    assert "经历分享" in row["reason"] and "9/10" in row["score"]


async def test_sleep_statement_is_not_a_completed_goodnight(plugin):
    now=await prepare(plugin)
    plugin.ctx.db.get.return_value[0]["processed_plain_text"]="我去睡了"
    candidate=await plugin.living.greetings.candidate("a",now,plugin.living.greetings.window(now))
    assert candidate["kind"]=="晚安"
    assert "对方刚说要休息" in candidate["intent"]
