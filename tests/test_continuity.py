"""验证活动结果、动态时间线和主动跟进的完整联动。"""

import json
import asyncio
from contextlib import suppress
from datetime import timedelta

import pytest

from maiweave.life.continuity import Continuity
from maiweave.life.living import Living
from maiweave.core.models import ExperienceInput, GoalInput, PlanInput, PlanPatch, RelationshipChange
from test_features import life
from test_plugin import plugin


def continuity(x):
    return Continuity(x.engine, x.ctx, x.cfg)


def living(x):
    return Living(x.engine, x.ctx, x.cfg, x.spatial, x.rhythm, x.world, continuity(x))


def result(**values):
    return {"success": True, "response": json.dumps({"activity": "阅读", "subject": "虚拟小说", "summary": "读完第二章，记住了主角的选择",
            "details": {"进度": "第二章", "地点": "模型生成的地点", "感受": "情节很吸引人"}, "enjoyment": 3, "importance": 7, "score_reason": "完成了作品的重要阶段",
            "mood": 2, "stress": -1, "next_action": "从第三章开始", "goal_units": 1, **values}, ensure_ascii=False)}


def complete(x, title="阅读", start=None, **kwargs):
    start = start or x.now
    x.engine.advance(start)
    plan = x.engine.create_plan(PlanInput(title=title, start=start.isoformat(), end=(start + timedelta(minutes=30)).isoformat(), location="住处", **kwargs), "a", start)
    x.engine.advance(start + timedelta(minutes=30))
    return x.store.get("plan", plan["id"])


async def test_concrete_results_persist_once_and_continue(life):
    x, c = life, continuity(life)
    plan = complete(x)
    x.ctx.llm.generate.return_value = result()
    await c.reflect(plan["id"], x.now + timedelta(minutes=31))
    await c.reflect(plan["id"], x.now + timedelta(minutes=32))
    event = x.store.get("experience", plan["experience_id"])
    assert (event["importance"], event["score_reason"]) == (7, "完成了作品的重要阶段")
    assert event["source"] == "simulated" and event["revision"] == 2
    assert event["details"]["进度"] == "第二章" and event["details"]["地点"] == "住处"
    assert len(x.store.all("effect")) == 1 and x.ctx.llm.generate.await_count == 1
    assert x.store.all("activity_memory")[0]["next_action"] == "从第三章开始"
    next_plan = complete(x, start=x.now + timedelta(days=1))
    x.ctx.llm.generate.return_value = result(details={"进度": "第三章"}, next_action="从第四章开始")
    await c.reflect(next_plan["id"], x.now + timedelta(days=1, minutes=31))
    prompt = x.ctx.llm.generate.call_args.args[0]
    assert "从第三章开始" in prompt
    assert len(x.store.all("activity_memory")) == 1
    assert x.store.all("activity_memory")[0]["next_action"] == "从第四章开始"


@pytest.mark.parametrize("outcomes_enabled", [True, False])
async def test_share_waits_only_for_enabled_activity_results(life, outcomes_enabled, plugin):
    x = life
    x.ctx.maisaka = plugin.ctx.maisaka
    plan = complete(x, title="做午饭", kind="meal")
    x.cfg.continuity.outcomes_enabled = outcomes_enabled
    x.cfg.proactive.enabled = True
    x.cfg.proactive.start_hour = x.cfg.proactive.end_hour = 0
    x.cfg.proactive.session_ids = ["a"]
    x.cfg.proactive.share_threshold = 1
    service = living(x)
    now = x.now + timedelta(minutes=31)
    await service.share(now)
    if outcomes_enabled:
        x.ctx.maisaka.proactive.trigger.assert_not_awaited()
        assert "1 条等待" in service.share_decisions[0]["reason"]
        x.ctx.llm.generate.return_value = result(score_reason="第一次做成了这道菜")
        await service.continuity.reflect(plan["id"], now)
        await service.share(now)
        assert "第一次做成了这道菜" in x.ctx.maisaka.proactive.trigger.await_args.args[1]
    else:
        assert "日常用餐" in x.ctx.maisaka.proactive.trigger.await_args.args[1]


async def test_reported_experience_is_only_classified_not_invented(life):
    x, c = life, continuity(life)
    plan = x.engine.create_plan(PlanInput(title="一起读书", start=x.now.isoformat(), end=(x.now + timedelta(minutes=30)).isoformat(), participants=["用户"]), "a", x.now)
    event = x.engine.record_experience(ExperienceInput(summary="读了几页", occurred_at=x.now.isoformat(), source="reported", evidence="用户说的", plan_id=plan["id"]), "a", x.now)
    x.ctx.llm.generate.return_value = {"success": True, "response": '{"activity":"阅读","subject":""}'}
    await c.reflect(plan["id"], x.now)
    assert x.store.get("experience", event["id"]) == event
    assert x.store.all("preference") == []


async def test_failed_reflection_is_visible_and_explicitly_retryable(life):
    x, c = life, continuity(life)
    plan = complete(x)
    x.ctx.llm.generate.return_value = {"success": False, "error": "模型调用失败"}
    with pytest.raises(RuntimeError, match="模型调用失败"):
        await c.reflect(plan["id"], x.now + timedelta(hours=1))
    assert x.store.get("reflection", plan["id"])["status"] == "失败"
    x.ctx.llm.generate.return_value = result()
    await c.reflect(plan["id"], x.now + timedelta(hours=1))
    assert x.store.get("reflection", plan["id"])["status"] == "已完成"


async def test_semantic_habits_and_learned_dislike(life):
    x, c = life, continuity(life)
    for day, title in enumerate(["翻几页书", "读一下小说", "看书"]):
        plan = complete(x, title=title, start=x.now + timedelta(days=day))
        x.ctx.llm.generate.return_value = result(enjoyment=-3)
        await c.reflect(plan["id"], x.now + timedelta(days=day, hours=1))
    x.rhythm.learn(x.now + timedelta(days=2, hours=1))
    assert len(x.store.all("habit")) == 1
    assert x.store.all("habit")[0]["title"] == "阅读"
    assert x.store.all("preference")[0]["samples"] == 3
    assert x.rhythm.candidate(x.now + timedelta(days=7)) is None


async def test_goal_outline_progress_failure_and_next_step(life):
    x, c = life, continuity(life)
    goal = x.engine.create_goal(GoalInput(title="读完短篇", target_sessions=2), "a", x.now)
    x.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"steps": [
        {"title": "读前半部", "criterion": "读完前两章", "sessions": 1},
        {"title": "读后半部", "criterion": "读完后两章", "sessions": 1}]})}
    outlined = await c.outline(goal["id"], x.now)
    first = complete(x, goal_id=goal["id"], goal_step_id=outlined["steps"][0]["id"])
    assert c.next_step() is None
    x.ctx.llm.generate.return_value = result(goal_units=0, next_action="没读懂，先重读第一章")
    await c.reflect(first["id"], x.now + timedelta(minutes=31))
    assert x.store.get("goal", goal["id"])["completed_sessions"] == 0
    assert c.next_step()[1]["note"] == "没读懂，先重读第一章"
    second = complete(x, start=x.now + timedelta(hours=1), goal_id=goal["id"], goal_step_id=outlined["steps"][0]["id"])
    x.ctx.llm.generate.return_value = result()
    await c.reflect(second["id"], x.now + timedelta(hours=2))
    event = x.store.get("experience", second["experience_id"])
    assert event["importance"] == 7 and "读前半部" in event["score_reason"]
    assert c.next_step()[1]["title"] == "读后半部"
    third = complete(x, start=x.now + timedelta(hours=3), goal_id=goal["id"], goal_step_id=outlined["steps"][1]["id"])
    await c.reflect(third["id"], x.now + timedelta(hours=4))
    assert x.store.get("goal", goal["id"])["status"] == "completed"


async def test_invalid_goal_outline_does_not_claim_success(life):
    x, c = life, continuity(life)
    goal = x.engine.create_goal(GoalInput(title="练习", target_sessions=3), "a", x.now)
    x.ctx.llm.generate.return_value = {"success": True, "response": '{"steps":[{"title":"一次练习","criterion":"完成一次","sessions":1}]}'}
    with pytest.raises(ValueError, match="总和"):
        await c.outline(goal["id"], x.now)
    assert x.store.get("goal", goal["id"])["outline_status"] == "失败"


def test_cancelling_previous_activity_rebuilds_entire_route(life):
    x = life
    a = x.spatial.schedule(PlanInput(title="散步", start=(x.now + timedelta(hours=1)).isoformat(), end=(x.now + timedelta(hours=2)).isoformat(), location="公园"), "a", x.now)
    b = x.spatial.schedule(PlanInput(title="看书", start=(x.now + timedelta(hours=3)).isoformat(), end=(x.now + timedelta(hours=4)).isoformat(), location="图书馆"), "a", x.now)
    assert x.store.get("plan", b["travel_id"])["route"]["minutes"] == 10
    x.spatial.transition(a["id"], "cancel", "a", x.now)
    b = x.store.get("plan", b["id"])
    route = x.store.get("plan", b["travel_id"])["route"]
    assert route["origin"] == "住处" and route["minutes"] == 21


def test_fixed_conflict_blocks_execution_until_corrected(life):
    x = life
    a = x.spatial.schedule(PlanInput(title="散步", start=(x.now + timedelta(hours=1)).isoformat(), end=(x.now + timedelta(hours=2)).isoformat(), location="公园"), "a", x.now)
    b = x.spatial.schedule(PlanInput(title="看书", start=(x.now + timedelta(hours=3)).isoformat(), end=(x.now + timedelta(hours=4)).isoformat(), location="图书馆"), "a", x.now)
    x.spatial.change(a["id"], PlanPatch(end=(x.now + timedelta(hours=2, minutes=55)).isoformat()), "a", x.now)
    assert x.store.get("plan", b["id"])["status"] == "blocked"
    assert "最早" in x.store.get("schedule_issue", b["id"])["reason"]
    x.spatial.change(b["id"], PlanPatch(start=(x.now + timedelta(hours=3, minutes=20)).isoformat()), "a", x.now)
    assert x.store.get("plan", b["id"])["status"] == "scheduled"
    assert x.store.find("schedule_issue", b["id"]) is None


def test_new_explicit_plan_moves_autonomous_plan(life):
    x = life
    auto = x.spatial.schedule(PlanInput(title="读书", start=x.now.isoformat(), end=(x.now + timedelta(hours=2)).isoformat(), location="住处"), "", x.now, autonomous=True)
    fixed = x.spatial.schedule(PlanInput(title="临时讨论", start=(x.now + timedelta(minutes=30)).isoformat(), end=(x.now + timedelta(hours=1)).isoformat(), location="住处", participants=["用户"]), "a", x.now)
    auto = x.store.get("plan", auto["id"])
    assert x.engine.time(auto["start"]) >= x.engine.time(fixed["end"])
    assert auto["status"] == "scheduled"


def test_bad_weather_changes_flexible_activity_but_keeps_commitment(life):
    x, service = life, living(life)
    a = x.spatial.schedule(PlanInput(title="散步", start=(x.now + timedelta(hours=1)).isoformat(), end=(x.now + timedelta(hours=2)).isoformat(), location="公园"), "", x.now, autonomous=True)
    b = x.spatial.schedule(PlanInput(title="约好见面", start=(x.now + timedelta(hours=3)).isoformat(), end=(x.now + timedelta(hours=4)).isoformat(), location="公园", participants=["用户"]), "a", x.now)
    with x.store.db:
        x.store.put("world", {"id": "weather", "city": "上海", "label": "雷暴", "outdoor_suitable": False, "retrieved_at": x.now.isoformat()})
    service.adjust(x.now)
    assert x.store.get("plan", a["id"])["location"] == "住处"
    assert x.store.get("plan", b["id"])["location"] == "公园"
    assert x.store.get("schedule_notice", b["id"])["reason"]
    assert x.store.all("adjustment")


async def test_temporary_event_persists_and_prevents_new_activity(life):
    x, service = life, living(life)
    x.cfg.life.autonomous = True
    plan = x.spatial.schedule(PlanInput(title="读书", start=(x.now + timedelta(minutes=30)).isoformat(), end=(x.now + timedelta(hours=1)).isoformat(), location="住处"), "", x.now, autonomous=True)
    service.adjust(x.now, "临时处理事情", (x.now + timedelta(hours=2)).isoformat())
    assert x.engine.time(x.store.get("plan", plan["id"])["start"]) >= x.now + timedelta(hours=2)
    await service.choose_activity(x.now + timedelta(minutes=10))
    x.ctx.llm.generate.assert_not_awaited()


def test_lasting_effect_is_integrated_once_then_resolved(life):
    x, c = life, continuity(life)
    relation = x.engine.relate("a", "user", RelationshipChange(reason="有一件事还没谈好", unresolved="讨论之后的安排"), x.now)
    c.relationship_thread(relation, x.now)
    key = "relationship:" + relation["id"]
    assert x.store.get("effect", key)["status"] == "active"
    x.engine.advance(x.now + timedelta(hours=12))
    mood = x.engine.state()["mood"]
    assert mood < 0
    x.engine.advance(x.now + timedelta(hours=12))
    assert x.engine.state()["mood"] == mood
    c.resolve_thread(key, "a", x.now + timedelta(hours=12))
    assert x.store.get("effect", key)["status"] == "resolved"
    assert x.engine.relationship("a", "user")["unresolved"] == ""


async def test_reminder_and_followup_request_are_once_only(plugin):
    now = plugin.now().replace(hour=12)
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["a"]
    plugin.config.proactive.cooldown_minutes = 1
    plugin.config.proactive.daily_limit = 5
    plan = plugin.engine.create_plan(PlanInput(title="一起聊天", start=(now + timedelta(minutes=15)).isoformat(), end=(now + timedelta(minutes=45)).isoformat(), participants=["用户"]), "a", now)
    thread = plugin.continuity.save_thread("上次的作品", "用户说今天完成后想聊聊", "a", now.isoformat(), now)
    await plugin.living.share(now)
    task = plugin.ctx.maisaka.proactive.trigger.return_value["task_id"]
    row = plugin.store.get("share_receipt", task)
    with plugin.store.db:
        plugin.store.put("share_receipt", {**row, "outcome": "Planner 选择沉默"})
    plugin.ctx.maisaka.proactive.trigger.return_value = {"success": True, "task_id": task + "2"}
    await plugin.living.share(now + timedelta(minutes=2))
    await plugin.living.share(now + timedelta(minutes=4))
    assert plugin.ctx.maisaka.proactive.trigger.await_count == 2
    assert plugin.store.get("thread", thread["id"])["requested_at"] == ""
    assert not plugin.store.all("sharing")


async def test_continuity_webui_and_context_show_saved_state(plugin):
    now = plugin.now()
    thread = await plugin.ui_save_thread("继续讨论", "接着聊书中的人物", (now + timedelta(hours=1)).isoformat(), "a")
    data = await plugin.ui_continuity()
    assert data["threads"][0]["title"] == "继续讨论"
    assert "接着聊书中的人物" in plugin.context_text("a")
    assert "接着聊书中的人物" not in plugin.context_text("b")
    await plugin.ui_resolve_thread(thread["id"])
    assert plugin.store.get("thread", thread["id"])["status"] == "resolved"


def test_flexible_chain_moves_around_fixed_appointment(life):
    x = life
    a = x.spatial.schedule(PlanInput(title="散步", start=(x.now + timedelta(hours=1)).isoformat(), end=(x.now + timedelta(hours=2)).isoformat(), location="公园"), "a", x.now)
    b = x.spatial.schedule(PlanInput(title="看书", start=(x.now + timedelta(hours=3)).isoformat(), end=(x.now + timedelta(hours=4)).isoformat(), location="图书馆", flexible=True), "a", x.now)
    x.spatial.change(a["id"], PlanPatch(end=(x.now + timedelta(hours=2, minutes=55)).isoformat()), "a", x.now)
    b = x.store.get("plan", b["id"])
    assert x.engine.time(b["start"]) == x.now + timedelta(hours=3, minutes=5)
    assert b["status"] == "scheduled"


def test_resume_remote_activity_includes_travel(life):
    x = life
    plan = x.spatial.schedule(PlanInput(title="阅读", start=(x.now + timedelta(minutes=21)).isoformat(), end=(x.now + timedelta(hours=2)).isoformat(), location="图书馆"), "a", x.now)
    paused_at = x.now + timedelta(minutes=30)
    x.engine.advance(paused_at)
    x.spatial.transition(plan["id"], "pause", "a", paused_at)
    x.spatial.move("住处", "a", paused_at)
    arrived = paused_at + timedelta(minutes=21)
    x.engine.advance(arrived)
    resumed = x.spatial.transition(plan["id"], "resume", "a", arrived)
    assert x.engine.time(resumed["start"]) == arrived + timedelta(minutes=21)
    assert x.store.get("plan", resumed["travel_id"])["route"]["destination"] == "图书馆"


async def test_goal_reoutline_keeps_completed_progress(life):
    x, c = life, continuity(life)
    goal = x.engine.create_goal(GoalInput(title="练习", target_sessions=3), "a", x.now)
    goal["completed_sessions"] = 1
    goal["steps"] = [{"id": "old", "title": "旧阶段", "status": "completed", "completed_sessions": 1, "sessions": 1}]
    with x.store.db:
        x.store.put("goal", goal)
    x.ctx.llm.generate.return_value = {"success": True, "response": '{"steps":[{"title":"剩余练习","criterion":"完成第二部分","sessions":2}]}'}
    revised = await c.outline(goal["id"], x.now, revise=True)
    assert revised["completed_sessions"] == 1 and revised["steps"][0]["sessions"] == 2
    assert revised["outline_history"][0]["steps"][0]["id"] == "old"


async def test_manual_revised_simulation_is_not_overwritten(life):
    x, c = life, continuity(life)
    plan = complete(x)
    original = x.engine.revise_experience(plan["experience_id"], {"进度": "只读到第一章"}, "读了第一章", "用户更正角色设定", "a", x.now + timedelta(hours=1))
    x.ctx.llm.generate.return_value = {"success": True, "response": '{"activity":"阅读","subject":"小说"}'}
    await c.reflect(plan["id"], x.now + timedelta(hours=1))
    assert x.store.get("experience", original["id"]) == original


def test_tiredness_replaces_flexible_work_with_rest(life):
    x, service = life, living(life)
    plan = x.spatial.schedule(PlanInput(title="写文章", kind="work", start=(x.now + timedelta(hours=1)).isoformat(), end=(x.now + timedelta(hours=2)).isoformat(), location="住处"), "", x.now, autonomous=True)
    state = x.engine.state()
    state["energy"] = 10
    with x.store.db:
        x.store.put("state", state)
    service.adjust(x.now)
    assert x.store.get("plan", plan["id"])["kind"] == "rest"


async def test_failed_proactive_request_remains_pending(plugin):
    now = plugin.now().replace(hour=12)
    plugin.config.proactive.enabled = True
    plugin.config.proactive.session_ids = ["a"]
    thread = plugin.continuity.save_thread("关心作品", "上次说还在修改", "a", now.isoformat(), now)
    plugin.ctx.maisaka.proactive.trigger.return_value = {"success": False, "error": "MaiBot 拒绝请求"}
    with pytest.raises(RuntimeError, match="MaiBot 拒绝"):
        await plugin.living.share(now)
    assert plugin.store.get("thread", thread["id"])["requested_at"] == ""
    assert plugin.store.find("sharing", "a") is None
    decision = (await plugin.ui_overview())["share_decisions"][0]
    assert decision["outcome"] == "请求失败"
    assert "MaiBot 拒绝请求" in decision["reason"]


async def test_full_tick_goal_activity_result_and_webui(plugin, monkeypatch):
    plugin.worker.cancel()
    with suppress(asyncio.CancelledError):
        await plugin.worker
    now = plugin.now().replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(plugin, "now", lambda: now)
    state = plugin.engine.state()
    state.update(updated_at=now.isoformat(), energy=80, hunger=20)
    with plugin.store.db:
        plugin.store.put("state", state)
    goal = plugin.engine.create_goal(GoalInput(title="读完一篇短文", target_sessions=1), "a", now)
    plugin.config.life.autonomous = True
    async def generate_choice(prompt, **kwargs):
        if "把角色长期目标拆为可执行阶段" in prompt:
            return {"success": True, "response": '{"steps":[{"title":"读短文","criterion":"完成阅读","sessions":1,"minutes":30}]}'}
        step = plugin.store.get("goal", goal["id"])["steps"][0]
        return {"success": True, "response": json.dumps({"title":"读短文","kind":"leisure","minutes":30,
            "location":"住处","motivation":"今天先推进短文阅读", "goal_id":goal["id"],"goal_step_id":step["id"]})}
    plugin.ctx.llm.generate.side_effect = generate_choice
    plugin.config.diary.enabled = False
    await plugin.tick()
    await asyncio.gather(*plugin.background.values())
    plan = next(p for p in plugin.store.all("plan") if p["goal_id"] == goal["id"])
    assert plan["goal_step_id"]
    now += timedelta(minutes=31)
    plugin.config.life.autonomous = False
    plugin.ctx.llm.generate.side_effect = None
    plugin.ctx.llm.generate.return_value = result(summary="读完了短文", next_action="下次比较另一篇")
    plugin.config.diary.enabled = False
    await plugin.tick()
    await asyncio.gather(*plugin.background.values())
    assert plugin.last_errors == {}
    assert plugin.store.get("goal", goal["id"])["status"] == "completed"
    event = plugin.store.get("experience", plugin.store.get("plan", plan["id"])["experience_id"])
    assert event["importance"] >= 9 and goal["title"] in event["score_reason"]
    assert "读短文" in event["score_reason"]
    data = await plugin.ui_continuity()
    assert data["steps"][0]["status_label"] == "已完成"
    assert data["continuations"][0]["next_action"] == "下次比较另一篇"
    assert "读完了短文" in plugin.context_text("a")


async def test_reload_retains_followup_effect_and_progress(plugin):
    now = plugin.now()
    thread = plugin.continuity.save_thread("下次聊", "还没说完的书", "a", (now + timedelta(days=1)).isoformat(), now)
    plugin.engine.affect(-2, 3, "还惦记着一件事", now, session="a")
    await plugin.on_config_update("self", plugin.get_plugin_config_data(), "0.3.0")
    assert plugin.store.get("thread", thread["id"])["context"] == "还没说完的书"
    assert plugin.continuity.context("a", now)["effects"][0]["reason"] == "还惦记着一件事"
