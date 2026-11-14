"""日常话题评分、轮换与实际发送回执的耦合。"""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from maiweave.core.models import ExperienceInput, PlanInput
from test_plugin import plugin


def event(plugin, now, activity, score):
    item = plugin.engine.record_experience(ExperienceInput(summary=activity + "中的新感受", importance=score,
        score_reason="有具体细节想分享", source="observed", evidence="测试活动", occurred_at=now.isoformat()), "a", now)
    with plugin.store.db:
        plugin.store.put("plan", {"id": item["id"], "book_id": "book" if activity == "阅读" else "",
            "activity_label": activity, "kind": "leisure"})
        item["plan_id"] = item["id"]
        plugin.store.put("experience", item)
    return item


def receipt(plugin, now, activity, session="a", sent=True):
    with plugin.store.db:
        plugin.store.put("share_receipt", {"id": "receipt-" + session, "session": session,
            "created": now.isoformat(), "outcome": "实际已发送" if sent else "Planner 选择沉默",
            "share_activity": activity, **({"sent_at": now.isoformat()} if sent else {})})


@pytest.mark.parametrize("sent,session,hours,expected", [
    (True,"a",2,"听歌"), (False,"a",2,"阅读"), (True,"other",2,"阅读"), (True,"a",25,"阅读")])
def test_daily_choice_uses_recent_actual_sends(plugin, sent, session, hours, expected):
    now = plugin.now()
    books, music = event(plugin,now,"阅读",6), event(plugin,now,"听歌",5)
    receipt(plugin,now-timedelta(hours=hours),"阅读",session,sent)
    selected, reason = plugin.living.select_share([books,music],"a",now)
    assert plugin.living.share_activity(selected) == expected
    assert "近24小时" in reason
    assert plugin.config.proactive.share_threshold == 5


def test_important_events_and_threshold_keep_priority(plugin):
    now = plugin.now()
    important, small = event(plugin,now,"阅读",9), event(plugin,now,"听歌",5)
    receipt(plugin,now-timedelta(hours=2),"阅读")
    assert plugin.living.select_share([important,small],"a",now)[0] == important
    plugin.config.proactive.share_threshold = 6
    small["importance"] = 4
    important["importance"] = 5
    assert plugin.living.select_share([important,small],"a",now)[0] is None


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_meal_reflection_reaches_share_and_receipt(plugin, mode):
    now = (plugin.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now + timedelta(minutes=16)
    cfg = plugin.config.proactive
    cfg.enabled, cfg.session_ids, cfg.mode = True, ["a"], mode
    cfg.start_hour = cfg.end_hour = 0
    plugin.config.life.schedule_enabled = False
    with plugin.store.db:
        plugin.store.put("state", {**plugin.engine.state(), "updated_at": now.isoformat(), "energy": 80, "hunger": 20})
    plan = plugin.engine.create_plan(PlanInput(title="炒蛋炒饭", kind="meal", start=now.isoformat(),
        end=(now+timedelta(minutes=15)).isoformat(), location="住处"),"a",now)
    plugin.engine.advance(plugin.now())
    plugin.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({
        "activity":"做饭", "subject":"蛋炒饭", "summary":"这次炒饭粒粒分明", "details":{"感受":"火候掌握得恰好"},
        "enjoyment":3, "importance":5, "score_reason":"想聊聊终于炒散米饭的小满足"},ensure_ascii=False)}
    await plugin.living.share(plugin.now())
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    await plugin.continuity.reflect(plan["id"],plugin.now())
    plugin.ctx.db.get.return_value = []
    plugin.ctx.maisaka.reply = SimpleNamespace(generate=AsyncMock(return_value={"success":True,"text":"今天炒饭终于粒粒分明了。"}))
    plugin.ctx.send.text.return_value = {"sent":True,"message_id":"daily-meal"}
    await plugin.living.share(plugin.now())
    row, = plugin.store.all("share_receipt")
    assert row["share_activity"] == "做饭"
    assert row["experience_id"] == plugin.store.get("plan",plan["id"])["experience_id"]
    assert "5/10" in row["score"] and "做饭" in row["reason"]
    assert bool(row.get("sent_at")) == (mode == "主动表达")


@pytest.mark.parametrize("level,group,expected", [(0,False,False),(1,False,False),(2,False,True),(3,False,True),(3,True,False)])
async def test_relationship_opens_specific_daily_candidates(plugin, level, group, expected):
    now = plugin.now()
    item = event(plugin, now, "听歌", 3)
    item["details"] = {"感受": "今天听清了间奏里的口琴声"}
    audience = {"场景": "群聊" if group else "私聊", "有效亲密度": level}
    assert bool(plugin.living.share_candidates([item], "a", now, audience)) == expected
    item["details"] = {}
    assert plugin.living.share_candidates([item], "a", now, audience) == []


@pytest.mark.parametrize("kind", ["sleep", "travel", "diary"])
async def test_close_daily_pool_skips_routine_records(plugin, kind):
    now = plugin.now()
    item = event(plugin, now, "日常活动", 3)
    item["details"] = {"地点": "住处"}
    with plugin.store.db:
        plugin.store.put("plan", {**plugin.store.get("plan", item["plan_id"]), "kind": kind})
    assert plugin.living.share_candidates([item], "a", now, {"场景":"私聊", "有效亲密度":3}) == []


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_model_choice_controls_delivery_and_receipt(plugin, monkeypatch, mode):
    from test_proactive_modes import prepare
    now = prepare(plugin, monkeypatch)
    plugin.config.proactive.mode = mode
    items = plugin.store.all("experience")
    with plugin.store.db:
        for i, item in enumerate(items):
            plugin.store.put("experience", {**item, "summary": f"独立话题{i}"})
    candidates = plugin.living.share_candidates(plugin.store.all("experience"), "chat-a", now, {"场景":"私聊"})
    plugin.ctx.llm.generate.side_effect = None
    plugin.ctx.llm.generate.return_value = {"success":True,"response":'{"send":true,"candidate":2,"reason":"第二件正好是对方关心的进展"}'}
    await plugin.living.share(now)
    row, = plugin.store.all("share_receipt")
    assert row["experience_id"] == candidates[1]["id"]
    assert row["topic"] == candidates[1]["summary"]
    request = plugin.ctx.maisaka.reply.generate if mode == "主动表达" else plugin.ctx.maisaka.proactive.trigger
    intent = request.await_args.args[1]
    assert candidates[1]["summary"] in intent and candidates[0]["summary"] not in intent
    assert bool(row.get("sent_at")) == (mode == "主动表达")


async def test_waiting_retries_whole_candidate_batch_without_using_quota(plugin, monkeypatch):
    from test_proactive_modes import prepare
    now = prepare(plugin, monkeypatch)
    plugin.ctx.llm.generate.side_effect = None
    plugin.ctx.llm.generate.return_value = {"success":True,"response":'{"send":false,"reason":"等对方接上刚才的话"}'}
    await plugin.living.share(now)
    row, = plugin.store.all("share_receipt")
    assert len(row["candidate_ids"]) == 2
    assert not plugin.living.receipts.sent_times("chat-a", now.tzinfo)
    for record_id in row["candidate_ids"]:
        assert not plugin.living.share_ready(record_id, "chat-a", now)
        assert plugin.living.share_ready(record_id, "chat-a", now+timedelta(minutes=plugin.config.proactive.retry_minutes))
    await plugin.living.share(now+timedelta(minutes=1))
    assert plugin.ctx.llm.generate.await_count == 1


@pytest.mark.parametrize("level,expected", [(0,False),(2,True)])
async def test_daily_candidate_reaches_model_only_for_close_private_chat(plugin, monkeypatch, level, expected):
    from test_proactive_modes import prepare
    now = prepare(plugin, monkeypatch)
    plugin.ctx.chat.get_all_streams.return_value = [{"stream_id":"chat-a","platform":"qq","user_id":"alice"}]
    with plugin.store.db:
        plugin.store.put("profile", {"id":"alice","session_id":"chat-a","user_id":"alice","person_id":"qq:alice","intimacy_mode":"manual","intimacy":level})
        for item in plugin.store.all("experience"):
            plugin.store.put("experience", {**item,"importance":3,"summary":"口琴的间奏让我想起小时候","details":{"感受":"今天才听清这个音色"}})
    await plugin.living.share(now)
    assert bool(plugin.store.all("share_receipt")) == expected
    assert bool(plugin.ctx.maisaka.reply.generate.await_count) == expected


@pytest.mark.parametrize("budget", [1200,2400,4800])
async def test_three_candidates_keep_their_text_inside_budget(plugin, budget):
    plugin.config.context.private_chars = budget
    await plugin.living.receipts.decide("a", [name*1000 for name in ("甲话题","乙话题","丙话题")], plugin.now(), plugin.config,
        {"场景":"私聊","有效亲密度":2,"原生人物画像":"喜欢音乐"*200,"当前聊天相关记忆":["喜欢口琴"*100]})
    prompt = plugin.ctx.llm.generate.await_args.args[0]
    assert len(prompt) <= budget
    assert all(name in prompt for name in ("候选1", "候选2", "候选3", "甲话题", "乙话题", "丙话题", "喜欢音乐", "喜欢口琴"))


async def test_invalid_candidate_number_retries(plugin):
    plugin.ctx.llm.generate.side_effect = [{"success":True,"response":'{"send":true,"candidate":3,"reason":"新发现"}'},
        {"success":True,"response":'{"send":true,"candidate":1,"reason":"第一条是对方关心的事"}'}]
    result = await plugin.living.receipts.decide("a", ["刚听懂了口琴间奏"], plugin.now(), plugin.config)
    assert result.candidate == 1 and plugin.ctx.llm.generate.await_count == 2


async def test_used_greeting_window_does_not_open_ordinary_shares(plugin, monkeypatch):
    from test_proactive_modes import prepare
    now = prepare(plugin, monkeypatch)
    plugin.config.proactive.start_hour, plugin.config.proactive.end_hour = 16, 18
    plugin.living.greetings.window = lambda at: {"kind":"早安"}
    plugin.living.greetings.candidate = AsyncMock(return_value={"key":"used","kind":"早安","until":(now+timedelta(minutes=10)).isoformat(),"intent":"早安"})
    with plugin.store.db:
        plugin.store.put("share_receipt", {"id":"quiet-greeting","session":"chat-a","record_id":"greeting:used",
            "created":now.isoformat(),"finished_at":now.isoformat(),"outcome":"Planner 选择沉默"})
    await plugin.living.share(now)
    plugin.ctx.llm.generate.assert_not_awaited()
    plugin.ctx.send.text.assert_not_awaited()
    assert len(plugin.store.all("share_receipt")) == 1


async def test_small_budget_keeps_actual_candidate_summaries(plugin, monkeypatch):
    from test_proactive_modes import prepare
    now = prepare(plugin, monkeypatch)
    plugin.config.context.private_chars = 1200
    summaries = ["今天整理观察卡时补上了之前一直没记清的那个细节，现在想告诉你" + word
                 for word in ("白鹭脚趾", "翠鸟梳羽", "雨燕尾羽")]
    with plugin.store.db:
        for item in plugin.store.all("experience"):
            plugin.store.delete("experience", item["id"])
    for summary in summaries:
        plugin.engine.record_experience(ExperienceInput(summary=summary, importance=6,
            occurred_at=now.isoformat(), source="simulated", evidence="角色模拟"), "chat-a", now)
    await plugin.living.share(now)
    prompt = plugin.ctx.llm.generate.await_args.args[0]
    assert len(prompt) <= 1200
    assert all(summary in prompt for summary in summaries)
    assert prompt.count("simulated") == 3
