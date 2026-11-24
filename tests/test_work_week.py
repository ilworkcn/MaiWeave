"""按连续七天的实际日程核对工作、休息和聊天。"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from maiweave.core.models import ActivityOutcome, Occupation, PlanInput, ShiftScene, WeeklyDuty
from test_plugin import plugin


async def test_ongoing_shift_recovers_after_model_failure(plugin, monkeypatch):
    p = plugin
    now = p.engine.time(p.engine.state()["updated_at"])
    plan = p.engine.create_plan(PlanInput(title="书店理货", kind="work", start=now.isoformat(),
        end=(now + timedelta(hours=1)).isoformat()), "", now)
    now += timedelta(minutes=1)
    p.engine.advance(now)
    scene = ShiftScene(task="整理书架", situation="日常", detail="清点书目", minutes=10, resolution="整理完一排")
    ask = AsyncMock(side_effect=[RuntimeError("模型服务暂时中断"), scene])
    monkeypatch.setattr(p.continuity, "ask", ask)
    with pytest.raises(RuntimeError, match="模型服务暂时中断"):
        await p.continuity.process(now)
    await p.continuity.process(now + timedelta(minutes=1))
    assert ask.await_count == 1
    await p.continuity.process(now + timedelta(minutes=5))
    await p.continuity.process(now + timedelta(minutes=6))
    assert ask.await_count == 2
    assert p.store.get("plan", plan["id"])["shift_scene"]["task"] == "整理书架"


async def test_weekly_class_handoff(plugin, monkeypatch):
    p = plugin
    first = p.engine.time(p.engine.state()["updated_at"])
    scene = ShiftScene(task="比较两次观测", situation="日常", detail="核对第一张图", minutes=5,
                       resolution="第一张图已经画好", next_shift="补画第二张图")
    ask = AsyncMock(return_value=scene)
    monkeypatch.setattr(p.continuity, "ask", ask)
    for day, rule, expected in [(0, "周三组会", ""), (1, "另一门课", ""), (7, "周三组会", "补画第二张图"), (14, "周三组会", "")]:
        start = first + timedelta(days=day)
        end = start + timedelta(hours=1)
        plan = p.engine.create_plan(PlanInput(title="进度讨论", kind="study", start=start.isoformat(), end=end.isoformat()), "a", start)
        with p.store.db:
            p.store.put("plan", {**plan, "routine_id": rule})
        now = start + timedelta(minutes=1)
        monkeypatch.setattr(p, "now", lambda: now)
        p.engine.advance(now)
        if day:
            ask.return_value = scene.model_copy(update={"resolution": "图已交付", "next_shift": ""})
        await p.continuity.start_shift(p.store.get("plan", plan["id"]), now)
        current = p.store.get("plan", plan["id"])["shift_scene"]
        assert current.get("carried_from", "") == expected
        assert "next_shift" not in p.engine.snapshot("a", now)["current"]["shift_scene"]
        now = end
        p.engine.advance(now)
        assert not p.store.find("payment", "工钱:" + plan["id"])
        assert "上次进展" in p.context_text("a", "", "上次进度讨论怎么样")
        assert "上次进展" not in p.context_text("b", "", "上次进度讨论怎么样")
        if not day:
            ask.return_value = ActivityOutcome(activity="组会", subject="对比图", summary="第一张图已完成",
                                              details={}, enjoyment=1, next_action="下回再把第二张图补上",
                                              importance=3, score_reason="课上取得进展")
            await p.continuity.reflect(plan["id"], now)
            assert not p.store.all("activity_memory")[-1]["next_action"]


@pytest.mark.parametrize(("identity", "lesson", "campus"), [
    ("小学四年级学生", "自然课", "小学"), ("初中二年级学生", "几何课", "中学"),
    ("高二学生", "物理课", "高中"), ("高中一年级学生", "化学课", "高中"),
    ("建筑学本科生", "设计课", "大学"), ("材料学硕士研究生", "材料实验", "大学"),
    ("研究海洋生态的博士生", "浮游生物课题组会", "大学"),
])
async def test_personal_timetable(plugin, monkeypatch, identity, lesson, campus):
    p = plugin
    p.worker.cancel()
    await asyncio.gather(p.worker, return_exceptions=True)
    with p.store.db:
        for key in ("occupation", "occupation_default"):
            p.store.delete("runtime", key)
    at = datetime.fromisoformat("2026-11-23T07:00:00+08:00")
    duty = WeeklyDuty(kind="study", title=lesson, location="校园", weekdays=[0, 2],
                      start_time="09:00", duration_minutes=45)
    ask = AsyncMock(return_value=Occupation(name=identity, duties=[duty]))
    monkeypatch.setattr(p.continuity, "ask", ask)
    await p.initialize_occupation(at)
    await p.initialize_occupation(at)
    ask.assert_awaited_once()
    p.rhythm.materialize(at)
    plans = [r for r in p.store.all("plan") if r["title"] == lesson]
    assert plans and all(r["kind"] == "study" and r["pay"] == 0 for r in plans)
    assert {plan["location"] for plan in plans} == {campus}
    assert campus in {place["name"] for place in p.store.all("place")}
    assert identity in p.get_webui_config_schema()["sections"]["profile"]["fields"]["occupation"]["hint"]
    p.config.life.occupation = "古籍修复师"
    ask.return_value = Occupation(name="修复师", duties=[duty.model_copy(update={"title": "修复室值班", "kind": "work"})])
    await p.initialize_occupation(at)
    assert p.store.get("runtime", "occupation")["name"] == "古籍修复师"
    assert all(p.store.get("plan", r["id"])["status"] == "cancelled" for r in plans)
    assert next(r for r in p.store.all("routine") if r["status"] == "active")["pay"] > 0
    p.config.life.occupation = ""
    await p.initialize_occupation(at)
    assert p.store.get("runtime", "occupation")["name"] == identity and ask.await_count == 2


@pytest.mark.parametrize(("sleep_hours", "situation"), [(8, "日常"), (6, "日常"), (8, "赶工"), (8, "体力活")])
async def test_working_week(plugin, monkeypatch, sleep_hours, situation):
    engine = plugin.engine
    at = datetime.fromisoformat("2026-11-23T07:00:00+08:00")
    with plugin.store.db:
        plugin.store.put("state", {**engine.state(), "updated_at": at.isoformat()})
    scene = ShiftScene(task="整理货架", situation="日常" if situation == "体力活" else situation,
                       load="持续用力" if situation == "体力活" else "常规", detail="还有两排货架", minutes=60,
                       deadline_minutes=50 if situation == "赶工" else None, resolution="货架整理好了")
    monkeypatch.setattr(plugin.continuity, "ask", AsyncMock(return_value=scene))
    mornings, evenings = [], []
    for day in range(7):
        shift = "work" if day < 5 else "leisure"
        for kind, hours in [("meal", .5), ("leisure", 1), ("travel", .5), (shift, 4),
                            ("meal", .5), ("rest", .5), (shift, 4), ("travel", .5),
                            ("meal", .5), ("leisure", 11 - sleep_hours), ("rest", 1),
                            ("sleep", sleep_hours)]:
            end = at + timedelta(hours=hours)
            plan = engine.create_plan(PlanInput(kind=kind, title=kind, start=at.isoformat(), end=end.isoformat()), "", at)
            if kind == "work":
                started = at + timedelta(minutes=1)
                engine.advance(started)
                await plugin.continuity.start_shift(plugin.store.get("plan", plan["id"]), started)
            state = engine.advance(end)
            if kind == "sleep":
                mornings.append(state)
            if kind == "rest" and hours == 1:
                evenings.append(state)
            at = end
    if sleep_hours == 8:
        assert all(s["energy"] == 100 and s["sleep_debt"] == 0 for s in mornings)
        assert evenings[4]["energy"] > 35
        assert evenings[5]["energy"] > evenings[4]["energy"]
        assert mornings[-1]["stress"] == 0
    else:
        assert [s["sleep_debt"] for s in mornings] == [3, 6, 9, 12, 15, 18, 18]
        assert mornings[4]["energy"] < mornings[0]["energy"]


@pytest.mark.parametrize("situation", ["日常", "赶工", "受委屈", "体力活"])
async def test_shift_context_and_settlement(plugin, monkeypatch, situation):
    p = plugin
    start = p.engine.time(p.engine.state()["updated_at"])
    end = start + timedelta(hours=2)
    plan = p.engine.create_plan(PlanInput(kind="work", title="花店班", start=start.isoformat(), end=end.isoformat()), "a", start)
    now = start + timedelta(minutes=1)
    monkeypatch.setattr(p, "now", lambda: now)
    p.engine.advance(now)
    scene = ShiftScene(task="包六束花。", detail="手边还有两束", situation="日常" if situation == "体力活" else situation,
                       load="持续用力" if situation == "体力活" else "常规", minutes=20,
                       deadline_minutes=15 if situation == "赶工" else None, resolution="最后两束已交给客人")
    ask = AsyncMock(return_value=scene)
    monkeypatch.setattr(p.continuity, "ask", ask)
    before = p.engine.state()
    await p.continuity.process(now)
    await p.continuity.process(now)
    assert ask.await_count == 1
    during = p.engine.state()
    assert during["stress"] - before["stress"] == {"日常": 0, "赶工": 5, "受委屈": 4, "体力活": 0}[situation]
    context = p.context_text("a")
    assert "包六束花" in context and "最后两束已交给客人" not in context
    assert "花。。" not in context
    assert "第一人称" in p.context_text("a", "", "今天怎么样", "回复")
    assert "resolution" not in p.engine.snapshot("a", now)["current"]["shift_scene"]
    assert not any(e["title"] == "工作收尾" for e in (await p.ui_life_timeline(plan["id"]))["events"])
    assert ("手头很赶" in context) == (situation == "赶工")
    assert "包六束花" not in p.context_text("b")
    now += timedelta(minutes=20)
    assert "最后两束已交给客人" in p.context_text("a")
    assert "花。。" not in p.context_text("a")
    assert any(e["text"] == scene.resolution for e in (await p.ui_life_timeline(plan["id"]))["events"])
    expected_cost = (7 if situation == "体力活" else 5) / 3
    assert during["energy"] - p.engine.state()["energy"] == pytest.approx(expected_cost, abs=.002)
    state = p.engine.advance(end)
    assert p.engine.advance(end) == state
    completed = p.store.get("plan", plan["id"])
    assert p.store.get("experience", completed["experience_id"])["details"]["事情的收尾"] == scene.resolution


@pytest.mark.parametrize(("payoff", "mood", "relief"), [("平常", 0, 0), ("有成就感", 3, 1), ("有人搭手", 2, 3)])
async def test_work_payoff_on_completion(plugin, monkeypatch, payoff, mood, relief):
    engine = plugin.engine
    start = engine.time(engine.state()["updated_at"])
    end = start + timedelta(hours=1)
    plan = engine.create_plan(PlanInput(kind="work", title="图书上架", start=start.isoformat(), end=end.isoformat()), "", start)
    now = start + timedelta(minutes=1)
    engine.advance(now)
    with plugin.store.db:
        plugin.store.put("state", {**engine.state(), "stress": 18, "mood": 10})
    scene = ShiftScene(task="整理新书", situation="日常", detail="还有一架", resolution="书架整理好了", minutes=10, payoff=payoff)
    monkeypatch.setattr(plugin.continuity, "ask", AsyncMock(return_value=scene))
    await plugin.continuity.start_shift(plugin.store.get("plan", plan["id"]), now)
    assert "payoff" not in engine.snapshot("", now)["current"]["shift_scene"]
    expected = engine.state()
    engine._evolve(expected, 10 / 60, "work")
    at = now + timedelta(minutes=10)
    state = engine.advance(at)
    assert state["energy"] == pytest.approx(expected["energy"], abs=.002)
    assert state["stress"] == pytest.approx(expected["stress"] - relief, abs=.002)
    assert state["mood"] == pytest.approx(expected["mood"] + mood, abs=.002)
    assert engine.advance(at) == state


def test_paid_shift_changed_to_rest(plugin):
    p = plugin
    now = p.now()
    start = now + timedelta(minutes=5)
    end = start + timedelta(hours=1)
    plan = p.engine.create_plan(PlanInput(kind="work", title="花房帮忙", flexible=True,
                                start=start.isoformat(), end=end.isoformat(), location="住处"), "", now)
    with p.store.db:
        p.store.put("plan", {**plan, "pay": 30, "hourly_pay": 30})
        p.store.put("state", {**p.engine.state(), "energy": 15})
    p.living.adjust(now)
    changed = p.store.get("plan", plan["id"])
    assert changed["kind"] == "rest" and changed["pay"] == changed["hourly_pay"] == 0
    p.engine.advance(end)
    assert not p.store.find("payment", "工钱:" + plan["id"])


@pytest.mark.parametrize(("job", "place", "persona", "task", "load", "drain", "stress"), [
    ("讲评作业", "教学楼", "中学教师", "逐桌答疑", "密集沟通", 5.5, 2.5),
    ("远程审稿", "住处", "自由编辑", "核对引文", "高度专注", 6, 2.5),
    ("入库盘点", "库房", "理货员", "搬运整箱货物", "持续用力", 7, 1.5),
    ("入库盘点", "库房", "", "核对交接单", "常规", 5, 1.5),
])
async def test_job_background_and_actual_task_load(plugin, job, place, persona, task, load, drain, stress):
    p = plugin
    p.ctx.config.get.return_value = {"personality": persona, "behavior_style": "做事细致"}
    p.config.life.background = "这周接了临时替班"
    now = p.engine.time(p.engine.state()["updated_at"])
    plan = p.engine.create_plan(PlanInput(title=job, kind="work", location=place, start=now.isoformat(),
                                end=(now + timedelta(hours=3)).isoformat()), "", now)
    start = now + timedelta(minutes=1)
    before = p.engine.advance(start)
    def model(prompt, **kwargs):
        assert all(s in prompt for s in (job, place, persona, "做事细致", "这周接了临时替班"))
        return {"success": True, "response": ShiftScene(task=task, situation="日常", load=load, detail="手头还有一半",
                minutes=90, resolution="这项工作交接好了").model_dump_json()}
    p.ctx.llm.generate.side_effect = model
    await p.continuity.process(start)
    state = p.engine.advance(start + timedelta(hours=1))
    assert before["energy"] - state["energy"] == pytest.approx(drain, abs=.002)
    assert state["stress"] - before["stress"] == pytest.approx(stress, abs=.002)
    assert p.store.get("plan", plan["id"])["shift_scene"]["task"] == task
