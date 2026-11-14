"""日程完成、模型重试和阅读目标的衔接验证。"""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from maiweave.core.models import PlanInput, GoalInput
from maiweave.life.dreams import Dreams
from maiweave.life.continuity import Continuity
from test_plugin import plugin
from test_books import upload, answer

@pytest.mark.parametrize("with_stage", [True, False])
async def test_book_progresses_goal_after_notes_complete(plugin, with_stage):
    now=plugin.now();book=await upload(plugin)
    goal=plugin.engine.create_goal(GoalInput(title='读一章',target_sessions=1),'',now)
    goal['steps']=[{'id':'chapter','sessions':1,'completed_sessions':0,'status':'pending'}] if with_stage else []
    with plugin.store.db:plugin.store.put('goal',goal)
    plan=plugin.engine.create_plan(PlanInput(title='读书',book_id=book['id'],goal_id=goal['id'],goal_step_id='chapter' if with_stage else '',start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat()),'',now)
    plugin.engine.advance(now+timedelta(minutes=16))
    assert plugin.store.get('goal',goal['id'])['completed_sessions']==0
    answer(plugin);await plugin.books.process(now+timedelta(minutes=16))
    assert plugin.store.get('goal',goal['id'])['status']=='completed'
    event = plugin.store.get('experience', plugin.store.get('plan', plan['id'])['experience_id'])
    assert event['importance'] >= 9 and '读一章' in event['score_reason']
    assert '解开了上次留下的问题' in event['score_reason']
    await plugin.books.process(now+timedelta(minutes=17))
    assert plugin.store.get('goal',goal['id'])['completed_sessions']==1

async def test_diary_recovers_after_window_and_keeps_later_edit(plugin):
    now=plugin.now()
    p=plugin.engine.create_plan(PlanInput(title='日记',kind='diary',diary_day=now.date().isoformat(),start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat()),'',now)
    with plugin.store.db:plugin.store.put('diary',{'id':now.date().isoformat(),'plan_id':p['id'],'status':'生成失败','created_at':now.isoformat(),'retry_at':(now+timedelta(minutes=20)).isoformat()})
    plugin.engine.advance(now+timedelta(minutes=16));plugin.diary.write=AsyncMock()
    await plugin.diary.process(now+timedelta(minutes=16));plugin.diary.write.assert_not_awaited()
    await plugin.diary.process(now+timedelta(minutes=21));plugin.diary.write.assert_awaited_once()
    old=plugin.store.get('diary',now.date().isoformat());old.update(plan_id='later',created_at=(now+timedelta(minutes=22)).isoformat(),status='已生成')
    with plugin.store.db:plugin.store.put('diary',old)
    plugin.diary.write.reset_mock();await plugin.diary.process(now+timedelta(minutes=23));plugin.diary.write.assert_not_awaited()

async def test_dream_failure_retries_and_restart_recovers(plugin):
    now=plugin.now();p=plugin.engine.create_plan(PlanInput(title='睡眠',kind='sleep',start=now.isoformat(),end=(now+timedelta(hours=8)).isoformat()),'',now)
    end=now+timedelta(hours=8);plugin.engine.advance(end)
    plugin.ctx.llm.generate.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await plugin.dreams.generate(p['id'],end)
    assert plugin.store.get('dream',p['id'])['status']=='生成失败'
    plugin.dreams.generate=AsyncMock();await plugin.dreams.process(end+timedelta(minutes=1));plugin.dreams.generate.assert_not_awaited()
    await plugin.dreams.process(end+timedelta(minutes=6));plugin.dreams.generate.assert_awaited_once()
    row=plugin.store.get('dream',p['id']);row['status']='生成中'
    with plugin.store.db:plugin.store.put('dream',row)
    Dreams(plugin.engine,plugin.ctx,plugin.config)
    assert plugin.store.get('dream',p['id'])['status']=='生成失败'

async def test_reflection_and_goal_retry_after_interruption(plugin):
    now=plugin.now();p=plugin.engine.create_plan(PlanInput(title='画画',start=now.isoformat(),end=(now+timedelta(minutes=15)).isoformat()),'',now)
    end=now+timedelta(minutes=16);plugin.engine.advance(end)
    plugin.ctx.llm.generate.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await plugin.continuity.reflect(p['id'],end)
    plugin.continuity.reflect=AsyncMock()
    await plugin.continuity.process(end+timedelta(minutes=1));plugin.continuity.reflect.assert_not_awaited()
    await plugin.continuity.process(end+timedelta(minutes=6));plugin.continuity.reflect.assert_awaited_once()
    g=plugin.engine.create_goal(GoalInput(title='画一幅画'),'',end)
    with pytest.raises(asyncio.CancelledError):await plugin.continuity.outline(g['id'],end)
    plugin.continuity.outline=AsyncMock();await plugin.continuity.process(end+timedelta(minutes=6));plugin.continuity.outline.assert_awaited_once()

async def test_fatigue_replaces_reading_with_rest(plugin):
    now=plugin.now();book=await upload(plugin)
    p=plugin.engine.create_plan(PlanInput(title='阅读',kind='leisure',book_id=book['id'],flexible=True,start=now.isoformat(),end=(now+timedelta(minutes=30)).isoformat()),'',now,autonomous=True)
    with plugin.store.db:
        state=plugin.engine.state();state['energy']=10;plugin.store.put('state',state)
    plugin.living.adjust(now)
    changed=plugin.store.get('plan',p['id'])
    assert changed['kind']=='rest' and changed['book_id']==''
    plugin.engine.advance(now+timedelta(minutes=31));await plugin.books.process(now+timedelta(minutes=31))
    assert plugin.store.get('book',book['id'])['read_characters']==0
    assert plugin.store.all('book_note')==[]


@pytest.mark.parametrize("action", ["cancel", "skip", "edit"])
@pytest.mark.parametrize("delay", [0, 30])
async def test_blocked_routine_occurrence_stays_cancelled(plugin, action, delay):
    from maiweave.core.models import RoutineInput
    now = plugin.now().replace(hour=9, minute=40, second=0, microsecond=0)
    plugin.config.rhythm.horizon_days = 1
    value = RoutineInput(title="公园散步", anchor_date=now.date().isoformat(), start_time="10:00",
                         frequency="daily", duration_minutes=30, location="公园")
    rule = plugin.rhythm.save(value, "private", now)
    plugin.rhythm.materialize(now)
    plan = next(p for p in plugin.store.all("plan") if p.get("routine_id") == rule["id"])
    plugin.spatial.save_route("street", "park", 40, "步行")
    plugin.spatial.reconcile(now)
    assert plugin.store.get("plan", plan["id"])["status"] == "blocked"
    now += timedelta(minutes=delay)
    if action == "cancel":
        plugin.rhythm.cancel(rule["id"], "private", now)
    elif action == "skip":
        plugin.rhythm.skip(rule["id"], now.date().isoformat(), "private", now)
    else:
        plugin.rhythm.save(value.model_copy(update={"start_time": "11:00"}), "private", now, rule["id"])
    plugin.spatial.save_route("street", "park", 12, "步行")
    plugin.spatial.reconcile(now)
    plugin.rhythm.materialize(now)
    assert plugin.store.get("plan", plan["id"])["status"] == "cancelled"
    assert plugin.store.find("schedule_issue", plan["id"]) is None


async def test_renaming_place_updates_blocked_plan(plugin):
    now = plugin.now().replace(hour=9, minute=40, second=0, microsecond=0)
    plan = plugin.spatial.schedule(PlanInput(title="公园散步", location="公园",
        start=now.replace(hour=10, minute=0).isoformat(), end=now.replace(hour=10, minute=30).isoformat()), "private", now)
    plugin.spatial.save_route("street", "park", 40, "步行")
    plugin.spatial.reconcile(now)
    assert plugin.store.get("plan", plan["id"])["status"] == "blocked"
    plugin.spatial.save_place("湖畔公园", True, "散步地点", "park")
    plugin.spatial.save_route("street", "park", 12, "步行")
    plugin.spatial.reconcile(now)
    updated = plugin.store.get("plan", plan["id"])
    assert updated["location"] == "湖畔公园"
    assert updated["status"] == "scheduled"


async def test_invalid_reading_plan_keeps_existing_activity(plugin):
    now = plugin.now()
    book = await upload(plugin)
    plugin.books.manage("archive", book["id"])
    old = plugin.spatial.schedule(PlanInput(title="整理书桌", start=now.isoformat(),
        end=(now + timedelta(minutes=30)).isoformat()), "", now, autonomous=True)
    with pytest.raises(ValueError, match="归档"):
        plugin.spatial.schedule(PlanInput(title="阅读", book_id=book["id"], start=now.isoformat(),
            end=(now + timedelta(minutes=15)).isoformat()), "", now)
    assert plugin.store.get("plan", old["id"]) == old


@pytest.mark.parametrize("target", ["activity", "travel"])
async def test_cancelling_trip_clears_movement_display(plugin, target):
    now = plugin.now().replace(hour=9, minute=40, second=0, microsecond=0)
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "updated_at": now.isoformat(), "location": "住处"})
    plan = plugin.spatial.schedule(PlanInput(title="公园散步", location="公园",
        start=now.replace(hour=10, minute=0).isoformat(), end=now.replace(hour=10, minute=30).isoformat()), "private", now)
    at = now + timedelta(minutes=10)
    plugin.engine.advance(at)
    assert plugin.engine.state()["journey"]["next"]
    plugin.spatial.transition(plan["id"] if target == "activity" else plan["travel_id"], "cancel", "private", at)
    assert plugin.engine.state()["journey"] is None


@pytest.mark.parametrize("status", ["blocked", "awaiting_confirmation", "completed"])
async def test_goal_reoutline_waits_for_unsettled_activity(plugin, status):
    now = plugin.now()
    goal = plugin.engine.create_goal(GoalInput(title="练习速写", target_sessions=2), "private", now)
    goal["steps"] = [{"id": "sketch", "title": "画轮廓", "sessions": 2,
                      "completed_sessions": 0, "status": "pending"}]
    with plugin.store.db:
        plugin.store.put("goal", goal)
    plan = plugin.engine.create_plan(PlanInput(title="画一张轮廓", goal_id=goal["id"], goal_step_id="sketch",
        start=now.isoformat(), end=(now + timedelta(minutes=20)).isoformat()), "private", now)
    with plugin.store.db:
        plugin.store.put("plan", {**plan, "status": status})
    plugin.ctx.llm.generate.return_value = {"success": True,
        "response": '{"steps":[{"title":"重新练习","criterion":"完成速写","sessions":2}]}'}
    with pytest.raises(ValueError, match="阶段"):
        await plugin.continuity.outline(goal["id"], now, revise=True)
    assert plugin.store.get("goal", goal["id"])["steps"] == goal["steps"]
    plugin.ctx.llm.generate.assert_not_awaited()


async def test_reoutline_waits_for_book_note_then_uses_updated_progress(plugin):
    now = plugin.now()
    book = await upload(plugin)
    goal = plugin.engine.create_goal(GoalInput(title="阅读两次", target_sessions=2), "", now)
    plan = plugin.engine.create_plan(PlanInput(title="阅读", book_id=book["id"], goal_id=goal["id"],
        start=now.isoformat(), end=(now+timedelta(minutes=15)).isoformat()), "", now)
    end = now+timedelta(minutes=16)
    plugin.engine.advance(end)
    assert plugin.store.get("plan", plan["id"])["reading_goal_pending"]
    with pytest.raises(ValueError, match="结算"):
        await plugin.continuity.outline(goal["id"], end, revise=True)
    answer(plugin)
    await plugin.books.process(end)
    assert plugin.store.get("goal", goal["id"])["completed_sessions"] == 1
    plugin.ctx.llm.generate.return_value = {"success": True,
        "response": '{"steps":[{"title":"继续阅读","criterion":"完成阅读","sessions":1}]}'}
    revised = await plugin.continuity.outline(goal["id"], end, revise=True)
    assert revised["completed_sessions"] == 1
    assert sum(step["sessions"] for step in revised["steps"]) == 1
