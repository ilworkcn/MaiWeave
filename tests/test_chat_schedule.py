"""聊天约定与日程、归属和上下文预算的联动。"""

import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from maiweave.core.models import ChatArrangement, RoutineInput, PlanInput
from maiweave.integrations.chat_schedule import ChatSchedule
from test_plugin import plugin


@pytest.mark.parametrize("when", ["arrival", "completion"])
async def test_promise_follows_rescheduled_activity(plugin, monkeypatch, when):
    from maiweave.core.models import PlanPromise, PlanPatch
    p = plugin
    now = p.now()
    start = now + timedelta(hours=1)
    plan = p.spatial.schedule(PlanInput(title="去图书馆看书", kind="study", location="图书馆",
        start=start.isoformat(), end=(start + timedelta(hours=1)).isoformat()), "chat-a", now)
    value = PlanPromise(plan_id=plan["id"], when=when, action="promise", evidence="到时候告诉你")
    with pytest.raises(ValueError, match="当前聊天流"):
        p.continuity.plan_promise(value, "chat-b", now)
    p.continuity.plan_promise(value, "chat-a", now)
    p.continuity.plan_promise(value, "chat-a", now)
    thread, = p.store.all("thread")
    p.spatial.change(plan["id"], PlanPatch(start=(start + timedelta(hours=1)).isoformat(),
        end=(start + timedelta(hours=2)).isoformat()), "chat-a", now)
    for offset, ready in [(0, False), (1, when == "arrival"), (2, True)]:
        at = start + timedelta(hours=offset)
        p.engine.advance(at)
        p.continuity.expire_threads(at)
        thread = p.store.get("thread", thread["id"])
        assert bool(p.continuity.can_report(thread)) == ready
    p.chat_schedule.mark("chat-a", at, "用户")
    assert not p.continuity.can_report(thread)
    p.continuity.plan_promise(value.model_copy(update={"action": "answered"}), "chat-a", at)
    p.continuity.plan_promise(value, "chat-a", at)
    assert p.store.get("thread", thread["id"])["status"] == "resolved"


async def test_class_finishes_before_scheduled_end(plugin):
    from maiweave.core.models import ExperienceInput, PlanPromise
    p = plugin
    now = p.now()
    plan = p.engine.create_plan(PlanInput(title="讨论课", kind="study", start=now.isoformat(),
        end=(now + timedelta(hours=1)).isoformat()), "", now)
    value = PlanPromise(plan_id=plan["id"], when="completion", action="promise", evidence="下课再讲")
    p.continuity.plan_promise(value, "chat-a", now)
    at = now + timedelta(minutes=40)
    p.engine.record_experience(ExperienceInput(summary="提前下课", occurred_at=at.isoformat(),
        source="reported", evidence="今天提前下课了", plan_id=plan["id"]), "", at)
    p.continuity.expire_threads(at)
    thread, = p.store.all("thread")
    assert thread["due_at"] == at.isoformat() and p.continuity.can_report(thread)


async def test_promise_follows_the_task_inside_a_shift(plugin):
    from maiweave.core.models import PlanPromise
    p = plugin
    now = p.now()
    plan = p.engine.create_plan(PlanInput(title="审稿", kind="work", start=now.isoformat(),
        end=(now + timedelta(hours=1)).isoformat()), "", now)
    scene = {"task": "核对引文", "started_at": now.isoformat(), "done": False}
    plan["shift_scene"] = scene
    with p.store.db:
        p.store.put("plan", plan)
    value = PlanPromise(plan_id=plan["id"], when="task_completion", action="promise",
                        evidence="核完这条引文就回你", topic="用哪种纸折船好？")
    p.continuity.plan_promise(value, "chat-a", now)
    thread, = p.store.all("thread")
    assert not p.continuity.can_report(thread)
    at = now + timedelta(minutes=3)
    scene.update(done=True, finished_at=at.isoformat())
    with p.store.db:
        p.store.put("plan", plan)
    p.continuity.expire_threads(at)
    thread = p.store.get("thread", thread["id"])
    assert thread["due_at"] == at.isoformat() and p.continuity.can_report(thread)
    assert thread["topic"] == value.topic
    scene["started_at"] = at.isoformat()
    with p.store.db:
        p.store.put("plan", plan)
    p.continuity.expire_threads(at)
    assert p.store.get("thread", thread["id"])["status"] == "resolved"


async def test_cancelled_promise_silences_queued_planner(plugin, monkeypatch):
    from maiweave.core.models import PlanPromise
    p = plugin
    now = moment(p, monkeypatch)
    plan = p.engine.create_plan(PlanInput(title="整理相册", start=now.isoformat(),
        end=(now + timedelta(minutes=20)).isoformat()), "", now)
    p.continuity.plan_promise(PlanPromise(plan_id=plan["id"], when="completion", action="promise",
        evidence="整理好就告诉你"), "chat-a", now)
    thread, = p.store.all("thread")
    task = "proactive:ilworkcn.maiweave:123"
    p.living.receipts.accept(task, {"thread_id": thread["id"], "session": "chat-a"}, now)
    p.engine.transition(plan["id"], "cancel", "", now)
    p.continuity.expire_threads(now)
    marker = {"item_type": "UserMessageItem", "parts": [{"type": "text", "text":
        f'<plugin_proactive_task id="{task}" plugin_id="ilworkcn.maiweave">'}]}
    tools = [{"type": "function", "function": {"name": name}} for name in ("reply", "wait")]
    result = await p.planner_context(session_id="chat-a", items=[marker], tool_definitions=tools)
    assert result["modified_kwargs"]["tool_definitions"] == tools[1:]
    assert p.store.get("thread", thread["id"])["status"] == "resolved"
    await p.planner_receipt("chat-a", [{"item_type": "FunctionCallItem",
        "tool_call": {"call_id": "test-wait", "func_name": "wait", "args": {}}}])
    assert p.store.get("share_receipt", task)["outcome"] == "Planner 选择沉默"
    assert not p.living.receipts.sent_times("chat-a", now.tzinfo)


def moment(plugin, monkeypatch):
    now = plugin.now().replace(hour=9, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(plugin, "now", lambda: now)
    return now


def holiday(now, **kwargs):
    return ChatArrangement(title="今天下午调休", start=now.replace(hour=12).isoformat(),
        end=now.replace(hour=18).isoformat(), evidence="今天下午我调休", source="observed", speaker_id="bot",
        blocked_kinds=["work"], **kwargs)


async def test_leave_cancels_occurrence_and_trip_preserves_tomorrow(plugin, monkeypatch):
    now = moment(plugin, monkeypatch)
    plugin.spatial.save_place("办公室", False, "上班地点")
    plugin.spatial.save_route("住处", "办公室", 20, "步行")
    rule = plugin.rhythm.save(RoutineInput(title="下午上班", frequency="daily", anchor_date=now.date().isoformat(),
        start_time="14:00", duration_minutes=120, kind="work", location="办公室"), "chat-a", now)
    plugin.rhythm.materialize(now)
    today = next(p for p in plugin.store.all("plan") if p.get("routine_date") == now.date().isoformat())
    record = plugin.chat_schedule.save(holiday(now), "chat-a", "user", now)
    assert plugin.store.get("plan", today["id"])["status"] == "cancelled"
    assert plugin.store.get("plan", today["travel_id"])["status"] == "cancelled"
    plugin.rhythm.materialize(now)
    plugin.chat_schedule.reconcile(now)
    assert plugin.store.get("routine", rule["id"])["status"] == "active"
    assert any(p["status"] == "scheduled" and p.get("routine_date") == (now + timedelta(days=1)).date().isoformat() for p in plugin.store.all("plan"))
    assert plugin.chat_schedule.active(now, "chat-b") == []
    assert record["title"] in str(plugin.continuity.chat_arrangements(now))
    assert not plugin.chat_schedule.active(now.replace(hour=18))
    assert record["title"] in str((await plugin.ui_schedule())["notices"])


async def test_cross_chat_change_rejected_and_amendment_replaces(plugin, monkeypatch):
    now = moment(plugin, monkeypatch)
    row = plugin.chat_schedule.save(holiday(now), "chat-a", "user", now)
    with pytest.raises(ValueError, match="当前聊天流"):
        plugin.chat_schedule.save(holiday(now, record_id=row["id"]), "chat-b", "other", now)
    changed = holiday(now, record_id=row["id"]).model_copy(update={"blocked_kinds": [], "title": "下午照常上班"})
    plugin.chat_schedule.save(changed, "chat-a", "user", now)
    assert len(plugin.store.all("chat_arrangement")) == 1
    assert not plugin.chat_schedule.active(now)[0]["blocked_kinds"]
    foreign = plugin.engine.create_plan(PlanInput(title="别的聊天约定", start=now.replace(hour=14).isoformat(),
        end=now.replace(hour=15).isoformat()), "chat-b", now)
    with pytest.raises(ValueError, match="当前聊天流"):
        plugin.chat_schedule.save(holiday(now, cancel_plan_ids=[foreign["id"]]), "chat-a", "user", now)
    assert plugin.store.get("plan", foreign["id"])["status"] == "scheduled"


async def test_native_chat_both_speakers_and_dedup(plugin, monkeypatch):
    now = moment(plugin, monkeypatch)
    plugin.chat_schedule.mark("chat-a", now - timedelta(seconds=10), "user", "bot")
    plugin.ctx.db.get.return_value = [
        {"timestamp": now.isoformat(), "user_id": "bot", "user_nickname": "麦麦", "processed_plain_text": "今天下午我调休"},
        {"timestamp": now.isoformat(), "user_id": "user", "user_nickname": "用户", "processed_plain_text": "你今天上班吗"},
        {"timestamp": now.isoformat(), "user_id": "user", "processed_plain_text": "/maiweave 状态"}]
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"arrangements": [holiday(now).model_dump()]}, ensure_ascii=False)}
    await plugin.chat_schedule.process(now)
    prompt = plugin.ctx.llm.generate.await_args.args[0]
    assert "你今天上班吗" in prompt and '"角色本人": true' in prompt
    assert "/maiweave" not in prompt
    assert len(plugin.store.all("chat_arrangement")) == 1
    plugin.chat_schedule.mark("chat-a", now - timedelta(seconds=6), "user")
    await plugin.chat_schedule.process(now)
    assert plugin.ctx.llm.generate.await_count == 1


@pytest.mark.parametrize("invalid", [{"evidence": "这句话是另一人说的"}, {"end": "无效时间"}])
async def test_invalid_batch_waits_for_new_history_and_other_chat_runs(plugin, monkeypatch, invalid):
    now = moment(plugin, monkeypatch)
    monkeypatch.setattr("maiweave.integrations.usage.asyncio.sleep", AsyncMock())
    for session in ["chat-a", "chat-b"]:
        plugin.chat_schedule.mark(session, now - timedelta(seconds=10), "user", "bot")
    plugin.ctx.db.get.return_value = [
        {"timestamp": now.isoformat(), "user_id": "bot", "processed_plain_text": "今天下午我调休"},
        {"timestamp": now.isoformat(), "user_id": "user", "processed_plain_text": "这句话是另一人说的"}]
    valid = holiday(now).model_dump()
    def response(items):
        return {"success": True, "response": json.dumps({"arrangements": items})}
    plugin.ctx.llm.generate.side_effect = [response([valid, {**valid, **invalid}])] * 3 + [response([valid]), response([valid])]
    await plugin.chat_schedule.process(now)
    assert [r["session_id"] for r in plugin.store.all("chat_arrangement")] == ["chat-b"]
    assert not plugin.store.get("runtime", "chat_schedule:chat-a")["pending"]
    plugin.chat_schedule.mark("chat-a", now, "user")
    await plugin.chat_schedule.process(now + timedelta(seconds=10))
    assert plugin.ctx.llm.generate.await_count == 4
    plugin.ctx.db.get.return_value.insert(0, {"timestamp": now.isoformat(), "user_id": "user", "processed_plain_text": "下午休息吧"})
    plugin.chat_schedule.mark("chat-a", now + timedelta(seconds=11), "user")
    await plugin.chat_schedule.process(now + timedelta(seconds=20))
    assert sorted(r["session_id"] for r in plugin.store.all("chat_arrangement")) == ["chat-a", "chat-b"]
    assert plugin.ctx.llm.generate.await_count == 5


@pytest.mark.parametrize("recovers", [True, False])
async def test_service_failure_has_one_delayed_batch(plugin, monkeypatch, recovers):
    now = moment(plugin, monkeypatch)
    monkeypatch.setattr("maiweave.integrations.usage.asyncio.sleep", AsyncMock())
    plugin.chat_schedule.mark("chat-a", now - timedelta(seconds=10), "user")
    plugin.ctx.db.get.return_value = [{"timestamp": now.isoformat(), "user_id": "user", "processed_plain_text": "下午见"}]
    failure = {"success": False, "error": "服务繁忙"}
    plugin.ctx.llm.generate.side_effect = [failure] * 3 + ([{"success": True, "response": "{}"}] if recovers else [failure] * 3)
    await plugin.chat_schedule.process(now)
    restarted = ChatSchedule(plugin)
    restarted.mark("chat-a", now + timedelta(seconds=5), "user")
    await restarted.process(now + timedelta(seconds=50))
    assert plugin.ctx.llm.generate.await_count == 3
    await restarted.process(now + timedelta(minutes=2))
    await restarted.process(now + timedelta(minutes=3))
    assert plugin.ctx.llm.generate.await_count == (4 if recovers else 6)
    assert not plugin.store.get("runtime", "chat_schedule:chat-a")["pending"]


async def test_new_message_can_run_during_old_snapshot_cooldown(plugin, monkeypatch):
    now = moment(plugin, monkeypatch)
    monkeypatch.setattr("maiweave.integrations.usage.asyncio.sleep", AsyncMock())
    plugin.chat_schedule.mark("chat-a", now - timedelta(seconds=10), "user")
    history = [{"timestamp": now.isoformat(), "user_id": "user", "processed_plain_text": "下午见"}]
    plugin.ctx.db.get.return_value = history
    plugin.ctx.llm.generate.side_effect = [TimeoutError()] * 3 + [{"success": True, "response": "{}"}]
    await plugin.chat_schedule.process(now)
    history.insert(0, {**history[0], "processed_plain_text": "我们改天再聊"})
    plugin.chat_schedule.mark("chat-a", now + timedelta(seconds=1), "user")
    await plugin.chat_schedule.process(now + timedelta(seconds=10))
    await plugin.chat_schedule.process(now + timedelta(minutes=2))
    assert plugin.ctx.llm.generate.await_count == 4
    row = plugin.store.get("runtime", "chat_schedule:chat-a")
    assert not row["pending"] and "retry_digest" not in row


@pytest.mark.parametrize("bad_field", ["plan_id", "evidence"])
async def test_invalid_promise_stops_at_three_and_preserves_new_message(plugin, monkeypatch, bad_field):
    now = moment(plugin, monkeypatch)
    monkeypatch.setattr("maiweave.integrations.usage.asyncio.sleep", AsyncMock())
    plan = plugin.engine.create_plan(PlanInput(title="去图书馆", start=(now + timedelta(hours=1)).isoformat(),
        end=(now + timedelta(hours=2)).isoformat()), "chat-a", now)
    promise = {"plan_id": plan["id"], "when": "arrival", "action": "promise", "evidence": "到了再跟你说"}
    promise[bad_field] = "没有这项记录"
    message = {"timestamp": now.isoformat(), "user_id": "bot", "processed_plain_text": "到了再跟你说"}
    plugin.ctx.db.get.return_value = [message]
    plugin.chat_schedule.mark("chat-a", now - timedelta(seconds=10), "user", "bot")
    async def respond(*args, **kwargs):
        if plugin.ctx.llm.generate.await_count == 3:
            plugin.chat_schedule.mark("chat-a", now + timedelta(seconds=1), "user")
        return {"success": True, "response": json.dumps({"promises": [promise]})}
    plugin.ctx.llm.generate.side_effect = respond
    await plugin.chat_schedule.process(now)
    assert f"promises[0].{bad_field}" in plugin.ctx.llm.generate.await_args.args[0]
    assert plugin.store.get("runtime", "chat_schedule:chat-a")["pending"]
    for minute in range(1, 6):
        await plugin.chat_schedule.process(now + timedelta(minutes=minute))
    assert plugin.ctx.llm.generate.await_count == 3
    assert not plugin.store.get("runtime", "chat_schedule:chat-a")["pending"]


@pytest.mark.parametrize("budget", [1200, 2400, 4800])
async def test_arrangements_fit_chat_budget(plugin, monkeypatch, budget):
    now = moment(plugin, monkeypatch)
    plugin.config.context.private_chars = budget
    plugin.chat_context.kinds["chat-a"] = "private"
    plugin.chat_schedule.save(holiday(now), "chat-a", "user", now)
    text = plugin.context_text("chat-a", text="今天下午怎么安排")
    assert len(text) <= budget
    assert "调休" not in plugin.context_text("chat-b", text="今天下午怎么安排")


async def test_planning_rejects_work_during_leave(plugin, monkeypatch):
    now = moment(plugin, monkeypatch).replace(hour=13)
    plugin.config.life.autonomous = True
    plugin.chat_schedule.save(holiday(now), "chat-a", "user", now)
    responses = iter([
        {"title": "上班", "kind": "work", "minutes": 30, "location": "住处", "motivation": "习惯"},
        {"title": "听音乐", "kind": "leisure", "minutes": 20, "location": "住处", "motivation": "下午调休"}])
    async def reply(*args, **kwargs):
        return {"success": True, "response": json.dumps(next(responses), ensure_ascii=False)}
    plugin.ctx.llm.generate.side_effect = reply
    await plugin.living.choose_activity(now)
    assert plugin.ctx.llm.generate.await_count == 2
    assert all(p["kind"] != "work" for p in plugin.store.all("plan"))


async def test_sent_hook_only_actual_stored_messages(plugin, monkeypatch):
    now = moment(plugin, monkeypatch)
    message = {"session_id": "chat-a", "message_info": {"user_info": {"user_id": "bot"}}}
    await plugin.schedule_after_send(message, sent=False)
    await plugin.schedule_after_send(message, sent=True, storage_message=False)
    assert not plugin.store.find("runtime", "chat_schedule:chat-a")
    await plugin.schedule_after_send(message, sent=True)
    assert plugin.store.get("runtime", "chat_schedule:chat-a")["bot_id"] == "bot"


async def test_agreed_reschedule_updates_trip(plugin, monkeypatch):
    from maiweave.core.models import ChatPlanUpdate, PlanPatch
    now = moment(plugin, monkeypatch)
    plan = plugin.spatial.schedule(PlanInput(title="去公园碰面", kind="social", location="公园",
        start=now.replace(hour=14).isoformat(), end=now.replace(hour=15).isoformat()), "chat-a", now)
    value = ChatArrangement(title="碰面改到16点", start=now.replace(hour=16).isoformat(),
        end=now.replace(hour=17).isoformat(), evidence="16点再到公园吧", source="reported",
        plan_updates=[ChatPlanUpdate(plan_id=plan["id"], patch=PlanPatch(start=now.replace(hour=16).isoformat(), end=now.replace(hour=17).isoformat()))])
    plugin.chat_schedule.save(value, "chat-a", "user", now)
    changed = plugin.store.get("plan", plan["id"])
    trip = plugin.store.get("plan", changed["travel_id"])
    assert plugin.engine.time(changed["start"]).hour == 16
    assert plugin.engine.time(trip["end"]).hour == 16
    assert plugin.store.get("plan", plan["travel_id"])["status"] == "cancelled"


async def test_reported_leave_keeps_existing_relationship_rule(plugin, monkeypatch):
    now = moment(plugin, monkeypatch)
    plan = plugin.engine.create_plan(PlanInput(title="手头工作", kind="work", start=now.replace(hour=13).isoformat(),
        end=now.replace(hour=14).isoformat()), "", now, autonomous=True)
    value = holiday(now).model_copy(update={"source": "reported"})
    with pytest.raises(ValueError, match="当前关系"):
        plugin.chat_schedule.save(value, "chat-a", "user", now)
    assert plugin.store.get("plan", plan["id"])["status"] == "scheduled"
    assert not plugin.store.all("chat_arrangement")


@pytest.mark.parametrize("status", ["blocked", "paused"])
@pytest.mark.parametrize("entry", ["explicit", "kind", "reconcile"])
async def test_leave_includes_pending_work(plugin, monkeypatch, status, entry):
    now = moment(plugin, monkeypatch)
    plan = plugin.engine.create_plan(PlanInput(title="下午上班", kind="work",
        start=now.replace(hour=14).isoformat(), end=now.replace(hour=16).isoformat()), "chat-a", now)
    plan["status"] = status
    with plugin.store.db:
        plugin.store.put("plan", plan)
    value = holiday(now, cancel_plan_ids=[plan["id"]] if entry == "explicit" else [])
    if entry == "explicit":
        value.blocked_kinds = []
    if entry == "reconcile":
        with plugin.store.db:
            plugin.store.put("chat_arrangement", {"id":"leave", "type":"chat_schedule", "status":"active",
                "session_id":"chat-a", "source":"observed", "starts_at":value.start,
                "expires_at":value.end, "blocked_kinds":["work"]})
        plugin.chat_schedule.reconcile(now)
    else:
        plugin.chat_schedule.save(value, "chat-a", "user", now)
    assert plugin.store.get("plan", plan["id"])["status"] == "cancelled"
