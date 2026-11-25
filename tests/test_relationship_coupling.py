"""资料判断与跨聊天亲密等级、分享、问候和日程影响的联动。"""
from unittest.mock import AsyncMock
import pytest
from test_plugin import plugin
from test_review_workflow import decision
from test_greetings import prepare


async def review_profile(p, mode, level):
    p.ctx.config.get.return_value = {"nickname":"测试角色", "alias_names":["角色别名"]}
    p.ctx.person.get_value = AsyncMock(return_value="u")
    p.memory.person_profile = AsyncMock(return_value={"person_name":"朋友", "profile_text":"朋友是测试角色的恋人", "manual_override_text":""})
    decision(p, level=level if mode == "auto" else 3)
    row = next(r for r in p.store.all("profile") if r["session_id"] == "a")
    return await p.reviews.submit("profile", {"profile_id":row["id"], "person_id":"qq:u", "session_id":"a", "intimacy_mode":mode,
        "intimacy":level, "relationship":"恋人" if level == 3 else "朋友"})


async def test_auto_refresh_updates_same_person_in_other_chat(plugin):
    p=plugin
    old=p.anniversaries.save_profile("朋友","old-chat","u",person_id="qq:u")
    with p.store.db:p.store.put("profile",{**old,"intimacy_mode":"auto","intimacy":0,"relationship":"普通关系"})
    await prepare(p)
    await review_profile(p,"auto",3)
    audience=await p.living.share_recipient("a","")
    assert audience["有效亲密度"] == 3
    assert audience["关系设置"]["relationship"] == "恋人"
    assert p.store.get("profile",old["id"])["intimacy"] == 3


async def test_linked_manual_level_applies_before_chat_identity_cache(plugin):
    p=plugin
    await prepare(p)
    await review_profile(p,"auto",3)
    manual=p.anniversaries.save_profile("朋友","old-chat","u",person_id="qq:u")
    with p.store.db:p.store.put("profile",{**manual,"intimacy_mode":"manual","intimacy":0,"relationship":"朋友"})
    assert p.living.intimacy("a","u") == 0
    assert (await p.living.share_recipient("a",""))["有效亲密度"] == 0


@pytest.mark.parametrize("mode", ["auto","manual"])
@pytest.mark.parametrize("level", [0,1,2,3])
@pytest.mark.parametrize("kind", ["早安","晚安"])
async def test_review_level_controls_greeting_and_schedule(plugin,mode,level,kind):
    p=plugin
    now=await prepare(p,kind)
    before=p.config.proactive.model_dump()
    await review_profile(p,mode,level)
    assert p.living.intimacy("a","u") == level
    recipient=await p.living.share_recipient("a","")
    assert recipient["有效亲密度"] == level
    for expression in ("自然交流","主动表达"):
        p.config.proactive.mode=expression
        window=p.living.greetings.window(now)
        assert window and window["kind"]==kind
        candidate=await p.living.greetings.candidate("a",now,window)
        assert bool(candidate) == (level>=2)
    p.config.proactive.mode=before["mode"]
    assert p.config.proactive.model_dump()==before
    for activity,minimum in (("leisure",1),("study",2),("sleep",3)):
        plan={"autonomous":True,"kind":activity}
        if level>=minimum:p.living.check_influence("a","u",[plan])
        else:
            with pytest.raises(ValueError,match="空闲"):
                p.living.check_influence("a","u",[plan])
    p.living.check_influence("a","u",[{"autonomous":False,"kind":"study"}])


async def test_manual_anniversary_keeps_linked_auto_assessment(plugin):
    p=plugin
    await prepare(p)
    await review_profile(p,"manual",1)
    other=p.anniversaries.save_profile("朋友","old-chat","u",person_id="qq:u")
    with p.store.db:p.store.put("profile",{**other,"intimacy_mode":"auto","intimacy":3,"relationship":"恋人"})
    current=next(r for r in p.store.all("profile") if r["session_id"]=="a")
    decision(p,level=1)
    result=await p.reviews.submit("anniversary",{"profile_id":current["id"],"title":"生日","calendar":"solar","month":8,"day":3})
    assert result["status"]=="已保存"
    assert p.store.get("profile",other["id"])["intimacy"]==3
    assert p.store.get("profile",current["id"])["intimacy"]==1


async def test_auto_reassessment_preserves_manual_and_other_people(plugin):
    p=plugin
    await prepare(p)
    with p.store.db:
        p.store.put("profile",{"id":"manual","session_id":"other","user_id":"u","person_id":"qq:u","intimacy_mode":"manual","intimacy":1,"relationship":"朋友"})
        p.store.put("profile",{"id":"stranger","session_id":"a","user_id":"v","person_id":"qq:v","intimacy_mode":"auto","intimacy":0,"relationship":"普通关系"})
    await review_profile(p,"auto",3)
    assert p.store.get("profile","manual")["intimacy"]==1
    assert p.store.get("profile","stranger")["intimacy"]==0
    assert p.living.intimacy("a","u")==1
    assert (await p.living.share_recipient("a",""))["有效亲密度"]==1


@pytest.mark.parametrize("mode",["auto","manual"])
@pytest.mark.parametrize("expression",["自然交流","主动表达"])
async def test_review_keeps_greeting_quota_and_cooldown(plugin,mode,expression):
    p=plugin
    now=await prepare(p)
    await review_profile(p,mode,3)
    p.config.proactive.mode=expression
    p.config.proactive.daily_limit=0
    p.ctx.llm.generate.reset_mock()
    await p.living.share(now)
    p.ctx.llm.generate.assert_not_awaited()
    p.ctx.maisaka.proactive.trigger.assert_not_awaited()
    p.config.proactive.daily_limit=4
    with p.store.db:p.store.put("share_receipt",{"id":"recent","session":"a","created":now.isoformat(),"outcome":"实际已发送","sent_at":now.isoformat()})
    await p.living.share(now)
    p.ctx.llm.generate.assert_not_awaited()
    p.ctx.maisaka.proactive.trigger.assert_not_awaited()
    assert len(p.living.receipts.sent_times("a",now.tzinfo))==1


@pytest.mark.parametrize("mode", ["auto", "manual"])
async def test_review_downgrade_controls_real_plan_tools(plugin, mode):
    from datetime import timedelta
    from maiweave.core.models import PlanInput
    p = plugin
    now = await prepare(p)
    await review_profile(p, mode, 3)
    plan = p.engine.create_plan(PlanInput(title="读书", kind="study", start=(now+timedelta(hours=1)).isoformat(), end=(now+timedelta(hours=2)).isoformat()), "", now, autonomous=True)
    changed = await p.change_plan(plan["id"], "update", {"title":"读下一章"}, stream_id="a", user_id="u")
    assert changed["success"]
    await review_profile(p, mode, 0)
    denied = await p.change_plan(plan["id"], "cancel", stream_id="a", user_id="u")
    assert not denied["success"]
    assert p.store.get("plan",plan["id"])["status"] == "scheduled"
    await review_profile(p, mode, 3)
    cancelled = await p.change_plan(plan["id"], "cancel", stream_id="a", user_id="u")
    assert cancelled["success"]
    assert p.store.get("plan",plan["id"])["status"] == "cancelled"
    foreign = p.engine.create_plan(PlanInput(title="别人的约定", start=(now+timedelta(hours=3)).isoformat(), end=(now+timedelta(hours=4)).isoformat()), "other", now)
    assert not (await p.change_plan(foreign["id"], "cancel", stream_id="a", user_id="u"))["success"]


@pytest.mark.parametrize("accept", [True, False])
async def test_pending_review_changes_relationship_only_after_accept(plugin, accept):
    p = plugin
    await prepare(p)
    await review_profile(p, "auto", 3)
    row = p.store.all("profile")[0]
    decision(p, ["关系有变化，请确认"], 0)
    pending = await p.reviews.submit("profile", {"profile_id":row["id"], "person_id":"qq:u", "session_id":"a", "intimacy_mode":"auto"})
    assert pending["status"] == "待确认"
    assert p.living.intimacy("a","u") == 3
    await p.reviews.decide(pending["id"], accept)
    expected = 0 if accept else 3
    assert p.living.intimacy("a","u") == expected
    await p.on_unload()
    await p.on_load()
    assert p.living.intimacy("a","u") == expected
    assert (await p.living.share_recipient("a",""))["有效亲密度"] == expected


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("kind", ["早安", "晚安"])
async def test_review_to_expressed_greeting_receipt(plugin, mode, kind):
    from types import SimpleNamespace
    p = plugin
    now = await prepare(p, kind)
    await review_profile(p, mode, 3)
    p.config.proactive.mode = "主动表达"
    p.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success":True,"text":kind+"，今天也想和你聊聊"}))
    p.ctx.send.text.return_value = {"sent":True,"message_id":"greeting-message"}
    await p.living.share(now)
    receipt, = p.store.all("share_receipt")
    assert receipt["greeting_kind"] == kind
    assert receipt["outcome"] == "实际已发送"
    assert receipt["message_id"] == "greeting-message"
    assert len(p.living.receipts.sent_times("a",now.tzinfo)) == 1
    assert p.ctx.send.text.await_args.kwargs["sync_to_maisaka_history"]
    assert p.ctx.send.text.await_args.kwargs["storage_message"]
    assert await p.living.greetings.candidate("a",now,p.living.greetings.window(now)) is None
    p.ctx.send.text.assert_awaited_once()


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("sent", [True, False])
async def test_review_to_planner_receipt(plugin, mode, sent):
    import json
    p = plugin
    now = await prepare(p)
    await review_profile(p, mode, 3)
    p.config.proactive.mode = "自然交流"
    await p.living.share(now)
    receipt, = p.store.all("share_receipt")
    assert receipt["outcome"] == "MaiBot 已受理"
    assert p.living.receipts.sent_times("a",now.tzinfo) == []
    name = "reply" if sent else "stop"
    p.living.receipts.turn["a"] = receipt["id"]
    p.living.receipts.observe("a", [{"item_type":"FunctionCallItem","tool_call":{"call_id":"greeting-call","func_name":name}}])
    async def get(table, **kwargs):
        if table == "ToolRecord":
            return {"tool_name":name,"timestamp":now.isoformat(),"tool_data":json.dumps({"success":True,"metadata":{"sent_message_ids":["greeting-msg"]} if sent else {}})}
        return {"timestamp":now.isoformat()}
    p.ctx.db.get = AsyncMock(side_effect=get)
    await p.living.receipts.refresh(now)
    row = p.store.get("share_receipt",receipt["id"])
    assert row["outcome"] == ("实际已发送" if sent else "Planner 选择沉默")
    assert len(p.living.receipts.sent_times("a",now.tzinfo)) == int(sent)
    await p.living.receipts.refresh(now)
    assert len(p.living.receipts.sent_times("a",now.tzinfo)) == int(sent)

async def test_public_relationship_reads_existing_identity_only(plugin):
    p = plugin
    profile = p.anniversaries.save_profile("小禾", "private-a", "a", person_id="test:a")
    with p.store.db:
        p.store.put("profile", {**profile, "intimacy_mode": "manual", "intimacy": 2, "relationship": "朋友"})
        p.store.put("identity", {"id": "group:a", "session_id": "group", "person_id": "test:a"})
    before = p.store.db.total_changes
    relation = await p.get_relationship("group", "a")
    assert relation == {"version": 1, "found": True, "relationship": "朋友", "intimacy": 2,
                        "intimacy_label": "亲近", "scores": None}
    assert not (await p.get_relationship("group", "b"))["found"]
    assert p.store.db.total_changes == before

async def test_public_relationship_linked_scores_stay_in_source_chat(plugin):
    p = plugin
    with p.store.db:
        for session in ("private-a", "group"):
            p.store.put("identity", {"id": session + ":a", "session_id": session, "person_id": "test:a"})
        p.store.put("relationship", {**p.engine.relationship("private-a", "a"), "closeness": 85})
    group = await p.get_relationship("group", "a")
    assert group["found"] and group["intimacy"] == 3 and group["scores"] is None
    assert (await p.get_relationship("private-a", "a"))["scores"]["closeness"] == 85
