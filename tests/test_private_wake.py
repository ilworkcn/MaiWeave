"""普通私聊按轮唤醒与作息、关键词及发送状态的联动。"""
from datetime import timedelta
from unittest.mock import Mock
import json
import pytest
from maiweave.core.models import PlanInput
from test_plugin import plugin


def prepare(plugin, hour=23, probability=100):
    now=plugin.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    plugin.now=lambda:now
    plugin.config.life.private_wake_probability=probability
    plugin.config.life.wake_minutes=10
    plugin.engine.create_plan(PlanInput(title="睡觉",kind="sleep",start=now.isoformat(),end=(now+timedelta(hours=2)).isoformat()),"",now)
    plugin.engine.advance(now)
    return now


async def receive(plugin,text="今天看到一只小猫",group=False):
    info={"user_info":{"user_id":"u"}}
    if group:
        info["group_info"]={"group_id":"g"}
    return await plugin.management_message({"session_id":"private", "platform":"qq", "message_info":info,"processed_plain_text":text})


async def turn(plugin):
    return await plugin.planner_context(session_id="private",items=[])


@pytest.mark.parametrize("hour",[14,23])
async def test_wakes_once_and_reuses_extension_and_resume(plugin,monkeypatch,hour):
    now=prepare(plugin,hour)
    draw=Mock(return_value=0.2)
    monkeypatch.setattr("maiweave.life.living.random.random",draw)
    await receive(plugin)
    await receive(plugin,"还有一只橘猫")
    assert plugin.living.quiet_sleep(now)
    result=await turn(plugin)
    policy=json.loads(result["modified_kwargs"]["items"][-1]["parts"][0]["text"].split("\n")[-1])["sleep_policy"]
    assert not policy["quiet"] and policy["awake_until"]
    assert plugin.living.awake_until(now)==now+timedelta(minutes=10)
    assert (await plugin.sleep_before_send({"session_id":"private"}))["action"]=="continue"
    await turn(plugin)
    assert draw.call_count==1
    now+=timedelta(minutes=5)
    plugin.now=lambda:now
    await receive(plugin,"它跟着我走了两步")
    assert plugin.living.awake_until(now)==now+timedelta(minutes=10)
    now+=timedelta(minutes=10)
    plugin.now=lambda:now
    assert plugin.living.quiet_sleep(now)
    plugin.living.apply_schedule(now)
    assert plugin.engine.current(now)["kind"]=="sleep"
    assert draw.call_count==1


@pytest.mark.parametrize("mode",["zero","group","all_day","no_wake","no_nap_quiet","disabled","command"])
async def test_ordinary_wake_scope(plugin,monkeypatch,mode):
    now=prepare(plugin,14)
    if mode=="zero":plugin.config.life.private_wake_probability=0
    if mode=="all_day":plugin.config.life.schedule_enabled=False
    if mode=="no_wake":plugin.config.life.wake_enabled=False
    if mode=="no_nap_quiet":plugin.config.life.nap_quiet=False
    if mode=="disabled":plugin.config.life.enabled=False
    draw=Mock(return_value=0)
    monkeypatch.setattr("maiweave.life.living.random.random",draw)
    await receive(plugin,"/help" if mode=="command" else "今天怎么样",group=mode=="group")
    await turn(plugin)
    assert plugin.store.find("runtime","awakening") is None
    draw.assert_not_called()


async def test_failed_draw_waits_for_next_received_round(plugin,monkeypatch):
    now=prepare(plugin,probability=10)
    draw=Mock(side_effect=[0.1,0.09])
    monkeypatch.setattr("maiweave.life.living.random.random",draw)
    await receive(plugin)
    await receive(plugin,"第二条")
    await turn(plugin)
    await turn(plugin)
    assert draw.call_count==1 and plugin.living.quiet_sleep(now)
    await receive(plugin,"下一轮聊天")
    await plugin.reply_context(session_id="private",extra_prompt="")
    assert draw.call_count==1
    await turn(plugin)
    assert draw.call_count==2 and not plugin.living.quiet_sleep(now)


async def test_goodnight_does_not_draw_again(plugin,monkeypatch):
    now=prepare(plugin)
    draw=Mock(return_value=0)
    monkeypatch.setattr("maiweave.life.living.random.random",draw)
    await receive(plugin)
    await turn(plugin)
    await receive(plugin,"先聊到这里，晚安")
    await turn(plugin)
    assert plugin.living.quiet_sleep(now)
    assert draw.call_count==1


async def test_keyword_priority_and_existing_intimacy(plugin,monkeypatch):
    now=prepare(plugin)
    person=plugin.anniversaries.save_profile("亲密对象","private","u")
    with plugin.store.db:
        plugin.store.put("profile",{**person,"intimacy":3,"intimacy_mode":"manual"})
    draw=Mock(return_value=0)
    monkeypatch.setattr("maiweave.life.living.random.random",draw)
    await receive(plugin,"陪陪我")
    await turn(plugin)
    assert not plugin.living.quiet_sleep(now)
    draw.assert_not_called()
    assert "陪伴" in plugin.store.get("runtime","awakening")["reason"]


async def test_keyword_still_requires_intimacy_with_probability_off(plugin):
    now=prepare(plugin,probability=0)
    await receive(plugin,"陪陪我")
    await turn(plugin)
    assert plugin.living.quiet_sleep(now)


def test_default_and_schema(plugin):
    defaults=plugin.get_default_config()
    assert defaults["life"]["private_wake_probability"]==0
    assert type(plugin.config.life).model_fields["private_wake_probability"].json_schema_extra["label"]=="私聊消息唤醒概率（%）"
