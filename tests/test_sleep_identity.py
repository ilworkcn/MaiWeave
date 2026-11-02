"""群聊与私聊共享人物唤醒、发送控制及恢复作息。"""
from datetime import timedelta
from copy import deepcopy
import pytest
from test_plugin import plugin
from test_sleep_policy import sleep_plan

async def linked_person(plugin):
    p = plugin.anniversaries.save_profile("亲密对象", "private", "u", person_id="qq:u")
    with plugin.store.db:
        plugin.store.put("profile", {**p, "intimacy": 3, "intimacy_mode": "manual", "notes": "私聊备注"})
    for stream in ["private", "group"]:
        await plugin.identify_person(stream, "u", "qq")
    return p

@pytest.mark.parametrize("start,end", [("private", "group"), ("group", "private")])
async def test_same_person_wakes_continues_and_says_goodnight(plugin,start,end):
    await linked_person(plugin)
    now, _ = sleep_plan(plugin)
    plugin.now = lambda: now
    assert plugin.living.intimacy(start,"u") == plugin.living.intimacy(end,"u") == 3
    assert plugin.living.wake_for_message("陪陪我",start,now,"u")
    assert plugin.living.wake_for_message("继续聊聊",end,now+timedelta(minutes=30),"u")
    assert plugin.living.awake_until(now+timedelta(minutes=30)) == now+timedelta(minutes=150)
    assert "私聊备注" not in plugin.context_text("group")
    assert plugin.living.wake_for_message("先聊到这里，晚安",end,now+timedelta(minutes=40),"u")
    assert plugin.living.sleeping(now+timedelta(minutes=40))
    assert plugin.engine.current(now+timedelta(minutes=40))["kind"] == "sleep"

async def test_expiry_restart_all_day_and_morning(plugin):
    await linked_person(plugin)
    now, _ = sleep_plan(plugin)
    assert plugin.living.wake_for_message("陪陪我","group",now,"u")
    await plugin.on_unload()
    await plugin.on_load()
    when=now+timedelta(minutes=121)
    assert plugin.living.sleeping(when)
    plugin.living.apply_schedule(when)
    assert plugin.engine.current(when)["kind"] == "sleep"
    assert not plugin.living.sleeping(now+timedelta(hours=9))
    plugin.config.life.schedule_enabled=False
    plugin.living.apply_schedule(when)
    assert not plugin.living.sleeping(when)

async def test_other_person_and_platform_do_not_inherit_wake(plugin):
    await linked_person(plugin)
    now, _ = sleep_plan(plugin)
    await plugin.identify_person("other-platform","u","telegram")
    assert plugin.living.intimacy("other-platform","u") == 0
    assert not plugin.living.wake_for_message("陪陪我","other-platform",now,"u")
    assert plugin.living.wake_for_message("陪陪我","group",now,"u")
    before=deepcopy(plugin.store.get("runtime","awakening"))
    assert not plugin.living.wake_for_message("陪陪我","group",now+timedelta(minutes=1),"other")
    assert not plugin.living.wake_for_message("晚安","group",now+timedelta(minutes=1),"other")
    assert plugin.store.get("runtime","awakening") == before

@pytest.mark.parametrize("stream",["private","group"])
async def test_sleep_keeps_inbound_history_and_stops_outbound(plugin,stream):
    await linked_person(plugin)
    now, _ = sleep_plan(plugin)
    plugin.now=lambda:now
    message={"session_id":stream,"platform":"qq","processed_plain_text":"普通聊天", "message_info":{"user_info":{"user_id":"u"}}}
    assert await plugin.management_message(message) == {"action":"continue"}
    assert await plugin.sleep_before_send({"session_id":stream}) == {"action":"abort"}
    assert await plugin.sleep_before_send({"session_id":stream},storage_message=False) == {"action":"continue"}
    plugin.config.proactive.enabled=True
    plugin.config.proactive.start_hour=0
    plugin.config.proactive.end_hour=0
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    result=await plugin.wake("现在需要陪伴",stream_id=stream,user_id="u")
    assert result["content"]["awake"]
    assert await plugin.sleep_before_send({"session_id":stream}) == {"action":"continue"}

async def test_automatic_intimacy_and_schedule_influence_follow_person(plugin):
    profile = await linked_person(plugin)
    with plugin.store.db:
        plugin.store.put("profile", {**profile, "intimacy_mode": "auto", "intimacy": 0})
    now, plan = sleep_plan(plugin)
    with plugin.store.db:
        plugin.store.put("relationship", {**plugin.engine.relationship("private", "u"), "closeness": 80})
    assert plugin.living.intimacy("group", "u") == 3
    plugin.living.check_influence("group", "u", [plan])
    with pytest.raises(ValueError):
        plugin.living.check_influence("group", "other", [plan])
    assert plugin.living.wake_for_message("陪陪我", "group", now, "u")
