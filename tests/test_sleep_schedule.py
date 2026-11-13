"""夜间作息、固定安排和出行衔接。"""
from datetime import datetime, timedelta

import pytest

from maiweave.core.models import PlanInput, RoutineInput
from test_plugin import plugin


def prepare(p, hour=1, minute=30):
    now = datetime(2026, 11, 13, hour, minute, tzinfo=p.now().tzinfo)
    p.now = lambda: now
    p.config.life.autonomous = True
    p.config.life.sleep_hour, p.config.life.wake_hour = 1, 8
    state = p.engine.state()
    state.update(updated_at=now.isoformat(), location="卧室", energy=70, hunger=20)
    p.store.put("state", state)
    return now


async def test_sleep_reserves_trip_to_fixed_sleep_and_stays_single(plugin):
    p = plugin
    now = prepare(p)
    p.spatial.schedule(PlanInput(title="旧作息", kind="sleep", location="卧室",
        start=now.replace(hour=2, minute=0).isoformat(), end=now.replace(hour=7, minute=0).isoformat()), "", now)
    await p.living.choose_activity(now)
    sleeps = [x for x in p.store.all("plan") if x["autonomous"] and x["kind"] == "sleep"]
    assert len(sleeps) == 1
    assert p.engine.time(sleeps[0]["start"]) == now + timedelta(minutes=1)
    assert p.engine.time(sleeps[0]["end"]).strftime("%H:%M") == "01:59"
    for minute in range(1, 5):
        at = now + timedelta(minutes=minute)
        p.living.apply_schedule(at)
        p.spatial.reconcile(at)
        await p.living.choose_activity(at)
    assert len([x for x in p.store.all("plan") if x["kind"] == "sleep"]) == 2


async def test_delayed_night_sleep_expires_instead_of_moving_to_day(plugin):
    p = plugin
    now = prepare(p, 7, 30)
    plan = p.spatial.schedule(PlanInput(title="睡觉", kind="sleep", location="住处",
        start=(now + timedelta(minutes=1)).isoformat(), end=now.replace(hour=8, minute=0).isoformat()),
        "", now, autonomous=True, sleep_deadline=now.replace(hour=8, minute=0).isoformat())
    p.spatial.make_room(now, now.replace(hour=9, minute=0), now)
    p.spatial.reconcile(now)
    assert p.store.get("plan", plan["id"])["status"] == "cancelled"


async def test_change_hours_updates_night_but_keeps_nap_and_rule(plugin):
    p = plugin
    now = prepare(p, 23, 0)
    p.config.life.sleep_hour, p.config.life.wake_hour = 23, 7
    p.living.apply_schedule(now)
    await p.living.choose_activity(now)
    night = next(x for x in p.store.all("plan") if x["kind"] == "sleep")
    tomorrow = now + timedelta(days=1)
    nap = p.engine.create_plan(PlanInput(title="午睡", kind="sleep", start=tomorrow.replace(hour=13).isoformat(),
        end=tomorrow.replace(hour=14).isoformat()), "", now, autonomous=True)
    rule = p.rhythm.save(RoutineInput(title="固定作息", anchor_date=tomorrow.date().isoformat(), start_time="02:00",
        duration_minutes=300, kind="sleep", frequency="daily"), "", now)
    p.config.life.wake_hour = 8
    p.living.apply_schedule(now + timedelta(minutes=2))
    assert p.engine.time(p.store.get("plan", night["id"])["end"]).hour == 8
    assert p.store.get("plan", nap["id"])["end"] == nap["end"]
    assert p.store.get("routine", rule["id"])["start_time"] == "02:00"
    assert any("02:00" in x["reason"] for x in (await p.ui_schedule())["schedule_issues"])


async def test_legacy_duplicates_are_cancelled_on_load(plugin):
    p = plugin
    now = prepare(p)
    for hour in (9, 10, 11):
        p.engine.create_plan(PlanInput(title="睡觉", kind="sleep", motivation="按习惯休息，恢复精力",
            start=now.replace(hour=hour).isoformat(), end=now.replace(hour=hour+1).isoformat()), "", now, autonomous=True)
    p.living.apply_schedule(now)
    rows = [x for x in p.store.all("plan") if x["kind"] == "sleep"]
    assert sum(x["status"] == "cancelled" for x in rows) == 2
    await p.living.choose_activity(now)
    assert len([x for x in p.store.all("plan") if x["kind"] == "sleep" and x["status"] == "scheduled"]) == 1


async def test_cancel_night_stays_cancelled_and_delete_keeps_occurrence(plugin):
    p = plugin
    now = prepare(p)
    await p.living.choose_activity(now)
    night = next(x for x in p.store.all("plan") if x["kind"] == "sleep")
    with pytest.raises(ValueError, match="先取消"):
        await p.ui_plan_action(night["id"], "delete")
    await p.ui_plan_action(night["id"], "cancel")
    await p.living.choose_activity(now + timedelta(minutes=1))
    assert len([x for x in p.store.all("plan") if x["kind"] == "sleep"]) == 1
    p.store.put("occurrence", {"id": "rule:1:today", "plan_id": night["id"]})
    p.store.put("book_note", {"id": night["id"], "status": "已完成"})
    assert (await p.ui_plan_action(night["id"], "delete"))["deleted"]
    assert p.store.find("plan", night["id"]) is None
    assert p.store.get("book_note", night["id"])["plan_status"] == "cancelled"
    assert p.store.find("occurrence", "rule:1:today")


async def test_wake_and_changed_hours_resume_with_new_deadline(plugin):
    p = plugin
    now = prepare(p, 23, 0)
    p.config.life.sleep_hour, p.config.life.wake_hour = 23, 7
    p.living.apply_schedule(now)
    await p.living.choose_activity(now)
    at = now + timedelta(minutes=2)
    p.engine.advance(at)
    assert p.living.wake("private", at, "测试唤醒", require_intimacy=False)
    assert p.store.find("runtime", "sleep_skip") is None
    p.config.life.wake_hour = 8
    p.living.apply_schedule(at)
    awakening = p.store.get("runtime", "awakening")
    assert p.engine.time(awakening["sleep_end"]).hour == 8
    end = p.engine.time(awakening["until"]) + timedelta(seconds=1)
    p.living.apply_schedule(end)
    sleep = p.engine.current(end)
    assert sleep and sleep["title"] == "继续睡觉"
    assert p.engine.time(sleep["sleep_deadline"]).hour == 8


async def test_later_wake_hour_keeps_fixed_morning_trip(plugin):
    p = plugin
    now = prepare(p, 23, 0)
    p.config.life.sleep_hour, p.config.life.wake_hour = 23, 7
    p.living.apply_schedule(now)
    await p.living.choose_activity(now)
    night = next(x for x in p.store.all("plan") if x["kind"] == "sleep")
    morning = (now + timedelta(days=1)).replace(hour=7, minute=30)
    fixed = p.spatial.schedule(PlanInput(title="早晨约定", location="公园", start=morning.isoformat(),
        end=(morning + timedelta(minutes=30)).isoformat()), "", now)
    p.engine.advance(now + timedelta(minutes=2))
    p.config.life.wake_hour = 8
    p.living.apply_schedule(now + timedelta(minutes=2))
    assert p.engine.time(p.store.get("plan", night["id"])["end"]) == morning - timedelta(minutes=15)
    assert p.store.get("plan", fixed["id"])["start"] == fixed["start"]
    assert p.store.get("plan", fixed["id"])["status"] == "scheduled"
