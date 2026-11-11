"""自主阅读节奏和精力影响，手动日程沿用用户安排。"""
import json
from datetime import timedelta

import pytest
from test_plugin import plugin
from test_books import upload
from test_proactive_modes import prepare
from maiweave.core.models import PlanInput


def past_reading(plugin, now, minutes, ended_minutes=0, title="看书", status="completed"):
    end = now - timedelta(minutes=ended_minutes)
    start = end - timedelta(minutes=minutes)
    plan = plugin.engine.create_plan(PlanInput(title=title, kind="leisure", start=start.isoformat(), end=end.isoformat()), "", start)
    with plugin.store.db:
        plan.update(status=status, elapsed_seconds=minutes * 60)
        plugin.store.put("plan", plan)
    return plan


@pytest.mark.parametrize("read,ago,expected", [(0, 0, 40), (30, 59, 0), (30, 60, 40), (75, 60, 15), (85, 60, 0), (90, 60, 0)])
async def test_reading_minutes_and_break(plugin, read, ago, expected):
    now = plugin.now().replace(hour=16, minute=0, second=0, microsecond=0)
    if read:
        past_reading(plugin, now, read, ago)
    assert plugin.living.reading_allowance(now)["available_minutes"] == expected


async def test_reading_midnight_and_paused(plugin):
    now = plugin.now().replace(hour=0, minute=20, second=0, microsecond=0)
    past_reading(plugin, now, 40)
    assert plugin.living.reading_allowance(now)["read_minutes"] == 20
    assert plugin.living.reading_allowance(now + timedelta(hours=1))["available_minutes"] == 40
    later = now.replace(hour=16)
    plan = past_reading(plugin, later, 30, 70, status="paused")
    with plugin.store.db:
        plan["end"] = (later + timedelta(hours=2)).isoformat()
        plugin.store.put("plan", plan)
    assert plugin.living.reading_allowance(later)["available_minutes"] == 40


async def test_autonomous_limit_reselects_and_manual_reading_still_works(plugin):
    now = plugin.now().replace(hour=16, minute=0, second=0, microsecond=0)
    book = await upload(plugin)
    past_reading(plugin, now, 90, 70)
    plugin.config.life.autonomous = True
    plugin.config.life.schedule_enabled = False
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "updated_at": now.isoformat(), "energy": 80, "hunger": 20})
    choices = [{"title": "继续读书", "book_id": book["id"], "kind": "leisure", "minutes": 30, "location": "住处", "motivation": "想继续"},
               {"title": "收拾书桌", "kind": "leisure", "minutes": 20, "location": "住处", "motivation": "整理房间"}]
    plugin.ctx.llm.generate.side_effect = [{"success": True, "response": json.dumps(c)} for c in choices]
    await plugin.living.choose_activity(now)
    assert plugin.engine.current(now)["title"] == "收拾书桌"
    assert plugin.ctx.llm.generate.await_count == 2
    prompt = plugin.ctx.llm.generate.call_args_list[0].args[0]
    assert '书架：[]' in prompt and '先结合时段和生活背景决定做什么' in prompt
    planned = plugin.books.schedule(book["id"], (now + timedelta(hours=1)).isoformat(), 60, plugin.spatial, now, session="private")
    assert planned["session_id"] == "private"
    assert plugin.engine.time(planned["end"]) - plugin.engine.time(planned["start"]) == timedelta(minutes=60)


@pytest.mark.parametrize("used,expected", [(0,40),(75,15)])
async def test_reading_duration_is_trimmed(plugin, used, expected):
    now = plugin.now().replace(hour=16, minute=0, second=0, microsecond=0)
    book = await upload(plugin)
    if used:
        past_reading(plugin, now, used, 60)
    plugin.config.life.autonomous = True
    plugin.config.life.schedule_enabled = False
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "updated_at": now.isoformat(), "energy": 80, "hunger": 20})
    plugin.ctx.llm.generate.return_value = {"success":True,"response":json.dumps({"title":"看书","book_id":book["id"],"kind":"study","minutes":120,"location":"住处","motivation":"午后读一会儿"})}
    await plugin.living.choose_activity(now)
    plan=plugin.engine.current(now)
    assert plugin.engine.time(plan["end"])-plugin.engine.time(plan["start"])==timedelta(minutes=expected)


async def test_disable_autonomous_reading_and_config_group(plugin):
    plugin.config.books.autonomous_minutes = 0
    assert plugin.living.reading_allowance(plugin.now())["available_minutes"] == 0
    schema=plugin.get_webui_config_schema()
    assert "autonomous_minutes" in schema["sections"]["reading_rhythm"]["fields"]
    assert "reading_rhythm" in next(t for t in schema["layout"]["tabs"] if t["id"] == "growth")["sections"]


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_low_energy_waits_then_shares_after_recovery(plugin, monkeypatch, mode):
    now=prepare(plugin,monkeypatch)
    plugin.config.proactive.mode=mode
    with plugin.store.db:
        plugin.store.put("state",{**plugin.engine.state(),"energy":25})
        for row in plugin.store.all("experience"):
            row["importance"]=6
            plugin.store.put("experience",row)
    await plugin.living.share(now)
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    plugin.ctx.maisaka.reply.generate.assert_not_awaited()
    assert "精力较低" in plugin.living.share_decisions[0]["reason"]
    assert not plugin.store.all("share_receipt")
    with plugin.store.db:
        plugin.store.put("state",{**plugin.engine.state(),"energy":26})
    await plugin.living.share(now+timedelta(minutes=10))
    assert plugin.store.all("share_receipt")


@pytest.mark.parametrize("reminder", [False,True])
async def test_tired_important_share_and_reminder_continue(plugin,monkeypatch,reminder):
    now=prepare(plugin,monkeypatch)
    with plugin.store.db:
        plugin.store.put("state",{**plugin.engine.state(),"energy":20})
    if reminder:
        plugin.engine.create_plan(PlanInput(title="约好聊一会儿",kind="social",participants=["用户"],start=(now+timedelta(minutes=20)).isoformat(),end=(now+timedelta(minutes=40)).isoformat()),"chat-a",now)
    await plugin.living.share(now)
    assert "现在有些累" in plugin.ctx.maisaka.reply.generate.await_args.args[1]
    assert plugin.store.all("share_receipt")[0]["notice"] == reminder
    assert not (await plugin.get_life_status())["quiet"]
    assert "energy<=25" in plugin.context_text("chat-a")


async def test_reading_category_stays_with_its_plan(plugin):
    now=plugin.now().replace(hour=16,minute=0,second=0,microsecond=0)
    with plugin.store.db:
        plugin.store.put("activity_memory",{"id":"novel","activity":"读小说"})
    plan=past_reading(plugin,now,90,60,title="海边的故事第三章")
    with plugin.store.db:
        plan.update(continuation_id="novel",activity_label="阅读")
        plugin.store.put("plan",plan)
        plugin.store.put("activity_memory",{"id":"novel","activity":"购物"})
    assert plugin.living.reading_allowance(now)["available_minutes"] == 0
    assert plugin.living.activity_balance(now)["totals"][0]["activity"] == "阅读"


async def test_reading_followup_can_shop_after_reading_limit(plugin):
    now=plugin.now().replace(hour=16,minute=0,second=0,microsecond=0)
    past_reading(plugin,now,90,60)
    plugin.config.life.autonomous=True
    plugin.config.life.schedule_enabled=False
    with plugin.store.db:
        plugin.store.put("state",{**plugin.engine.state(),"updated_at":now.isoformat(),"hunger":20,"energy":80})
        plugin.store.put("activity_memory",{"id":"recipe","session_id":"","activity":"阅读","subject":"菜谱","kind":"leisure","next_action":"去买菜","completed":True,"details":{},"updated_at":now.isoformat()})
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"title":"去买蔬菜","kind":"leisure","minutes":15,"location":"住处","motivation":"试试菜谱里的菜","continuation_id":"recipe"})}
    await plugin.living.choose_activity(now)
    plan=plugin.engine.current(now)
    assert plan["title"]=="去买蔬菜" and not plan["book_id"]
    assert plugin.ctx.llm.generate.await_count==1
    plugin.engine.advance(now+timedelta(minutes=15))
    assert plugin.living.reading_allowance(now+timedelta(minutes=15))["read_minutes"]==90


@pytest.mark.parametrize("midnight",[False,True])
async def test_paused_reading_counts_active_minutes_per_day(plugin,midnight):
    now=plugin.now().replace(hour=23 if midnight else 12,minute=30,second=0,microsecond=0)
    with plugin.store.db:
        plugin.store.put("state",{**plugin.engine.state(),"updated_at":now.isoformat()})
    plan=plugin.engine.create_plan(PlanInput(title="看书",start=now.isoformat(),end=(now+timedelta(minutes=60)).isoformat()),"",now)
    pause=now+timedelta(minutes=20)
    plugin.engine.advance(pause)
    plugin.engine.transition(plan["id"],"pause","",pause)
    resumed=now+timedelta(hours=2)
    plugin.engine.advance(resumed)
    plugin.engine.transition(plan["id"],"resume","",resumed)
    end=resumed+timedelta(minutes=40)
    plugin.engine.advance(end)
    allowance=plugin.living.reading_allowance(end)
    assert allowance["read_minutes"]==(40 if midnight else 60)
    assert allowance["next_at"]==(end+timedelta(minutes=60)).isoformat()
    assert plugin.living.activity_balance(end)["totals"][0]["minutes"]==(40 if midnight else 60)
    assert plugin.living.reading_allowance(end+timedelta(minutes=59))["available_minutes"]==0
    assert plugin.living.reading_allowance(end+timedelta(minutes=60))["available_minutes"]==(40 if midnight else 30)


async def test_unlimited_daily_reading_keeps_session_and_break(plugin):
    now=plugin.now().replace(hour=16,minute=0,second=0,microsecond=0)
    past_reading(plugin,now,120,30)
    plugin.config.books.autonomous_minutes=-1
    plugin.config.books.session_minutes=25
    assert plugin.living.reading_allowance(now)["available_minutes"]==0
    plugin.config.books.break_minutes=30
    assert plugin.living.reading_allowance(now)["available_minutes"]==25
    plugin.config.books.break_minutes=0
    assert plugin.living.reading_allowance(now)["available_minutes"]==25
    plugin.config.books.autonomous_minutes=0
    assert plugin.living.reading_allowance(now)["available_minutes"]==0
    schema=plugin.get_webui_config_schema()
    assert set(schema["sections"]["reading_details"]["fields"])=={"session_minutes","break_minutes"}
    assert "reading_details" in next(t for t in schema["layout"]["tabs"] if t["id"]=="advanced")["sections"]
