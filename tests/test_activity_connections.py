"""活动接续、自然收尾和阅读兑现的联动。"""
import json
from datetime import timedelta

import pytest
from test_plugin import plugin
from test_books import upload, answer
from maiweave.core.models import PlanInput


def set_time(plugin, now):
    plugin.now = lambda: now
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "updated_at": now.isoformat(), "hunger": 20, "energy": 80})


def result(plugin, activity, subject, summary, next_action="", next_date=None):
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "activity": activity, "subject": subject, "summary": summary, "details": {"结果": summary},
        "enjoyment": 2, "importance": 4, "score_reason": "一件日常小事", "completed": True,
        "next_action": next_action, "next_date": next_date}, ensure_ascii=False)}


async def test_cooking_leads_to_shopping_and_finishes(plugin):
    now = plugin.now().replace(hour=12, minute=0, second=0, microsecond=0)
    set_time(plugin, now)
    plan = plugin.engine.create_plan(PlanInput(title="做午饭", kind="meal", start=now.isoformat(), end=(now+timedelta(minutes=30)).isoformat()), "", now)
    plugin.engine.advance(now+timedelta(minutes=30))
    result(plugin,"烹饪","午饭","做饭时发现盐快用完了","明天出门时买一袋盐",(now+timedelta(days=1)).date().isoformat())
    await plugin.continuity.reflect(plan["id"],now+timedelta(minutes=30))
    memory, = plugin.store.all("activity_memory")
    tomorrow=now+timedelta(days=1)
    set_time(plugin,tomorrow)
    plugin.config.life.autonomous=True
    plugin.config.life.schedule_enabled=False
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"title":"买一袋盐","kind":"leisure","minutes":15,"location":"住处","motivation":"昨天做饭时盐快用完了","continuation_id":memory["id"]})}
    await plugin.living.choose_activity(tomorrow)
    followup=plugin.engine.current(tomorrow)
    assert followup["continuation_id"]==memory["id"]
    plugin.engine.advance(tomorrow+timedelta(minutes=15))
    result(plugin,"购物","食盐","买好一袋盐，放回厨房")
    await plugin.continuity.reflect(followup["id"],tomorrow+timedelta(minutes=15))
    saved=plugin.store.get("activity_memory",memory["id"])
    assert saved["activity"]=="购物" and saved["subject"]=="食盐"
    assert saved["completed"] and saved["next_action"]==""
    event=plugin.store.get("experience",saved["experience_id"])
    assert event["details"]["接续缘由"]=="明天出门时买一袋盐"
    assert event["details"]["上次经历"]=="做饭时发现盐快用完了"
    assert event["source"]=="simulated"
    assert "这次接着做的事" in plugin.ctx.llm.generate.call_args.args[0]
    assert "买好一袋盐" in plugin.context_text("private",text="盐买了吗")


@pytest.mark.parametrize("status", ["scheduled","paused","blocked","awaiting_confirmation"])
async def test_already_arranged_continuation_waits(plugin,status):
    now=plugin.now().replace(hour=12,minute=0,second=0,microsecond=0)
    set_time(plugin,now)
    plugin.config.life.autonomous=True
    plugin.config.life.schedule_enabled=False
    with plugin.store.db:
        plugin.store.put("activity_memory",{"id":"salt","session_id":"","activity":"烹饪","subject":"午饭","kind":"meal","next_action":"买盐","updated_at":now.isoformat(),"details":{},"experience_id":"old"})
    plan=plugin.engine.create_plan(PlanInput(title="买盐",continuation_id="salt",start=(now+timedelta(hours=4)).isoformat(),end=(now+timedelta(hours=5)).isoformat()),"",now)
    with plugin.store.db:
        plan["status"]=status;plugin.store.put("plan",plan)
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"title":"听两首歌","kind":"rest","minutes":10,"location":"住处","motivation":"午后歇一会儿"})}
    await plugin.living.choose_activity(now)
    prompt=plugin.ctx.llm.generate.call_args.args[0]
    candidates=prompt.split("可接续的具体念头：")[1].split("。选择接续")[0]
    assert json.loads(candidates)==[]
    assert plugin.engine.current(now)["title"]=="听两首歌"


async def test_plain_activity_can_end_without_new_task(plugin):
    now=plugin.now().replace(hour=12,minute=0,second=0,microsecond=0)
    set_time(plugin,now)
    plan=plugin.engine.create_plan(PlanInput(title="坐着听两首歌",kind="rest",start=now.isoformat(),end=(now+timedelta(minutes=10)).isoformat()),"",now)
    plugin.engine.advance(now+timedelta(minutes=10))
    result(plugin,"听音乐","","听完两首歌，放松了一会儿")
    await plugin.continuity.reflect(plan["id"],now+timedelta(minutes=10))
    assert plugin.store.all("activity_memory")[0]["next_action"]==""
    assert "顺利收尾时填空字符串" in plugin.ctx.llm.generate.call_args.args[0]


async def test_book_reading_completes_saved_intention(plugin):
    now=plugin.now().replace(hour=12,minute=0,second=0,microsecond=0)
    set_time(plugin,now)
    book=await upload(plugin)
    with plugin.store.db:
        plugin.store.put("activity_memory",{"id":"read_later","session_id":"","activity":"散步","subject":"银杏树","kind":"leisure","next_action":"回来读一读这本写银杏的书","experience_id":"old","details":{},"completed":True,"next_date":""})
    plan=plugin.engine.create_plan(PlanInput(title="读一会儿银杏书",kind="study",book_id=book["id"],continuation_id="read_later",start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat()),"",now)
    answer(plugin)
    await plugin.books.prepare(plan,now)
    plugin.engine.advance(now+timedelta(minutes=15))
    plugin.books.complete(plugin.store.get("plan",plan["id"]),now+timedelta(minutes=15))
    memory=plugin.store.get("activity_memory","read_later")
    assert memory["completed"] and memory["next_action"]==""
    assert memory["activity"]=="阅读" and memory["subject"]==book["title"]
    assert memory["kind"]=="study"
    assert memory["experience_id"]==plugin.store.get("plan",plan["id"])["experience_id"]
    event=plugin.store.get("experience",memory["experience_id"])
    assert event["source"]=="observed"
    assert event["details"]["接续缘由"]=="回来读一读这本写银杏的书"


async def test_plan_continuation_uses_chat_ownership(plugin):
    now=plugin.now()
    with plugin.store.db:
        plugin.store.put("activity_memory",{"id":"private-memory","session_id":"private-a"})
    value=PlanInput(title="继续画画",continuation_id="private-memory",start=now.isoformat(),end=(now+timedelta(minutes=20)).isoformat())
    with pytest.raises(ValueError,match="不属于当前聊天流"):
        plugin.engine.create_plan(value,"private-b",now)
    assert not plugin.store.all("plan")
    assert plugin.engine.create_plan(value,"private-a",now)["continuation_id"]=="private-memory"
