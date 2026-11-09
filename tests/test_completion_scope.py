"""验证公共计划在聊天中完成后的回顾与升级接续。"""

from datetime import timedelta

import pytest

from maiweave.core.engine import LifeEngine
from maiweave.core.models import ExperienceInput, PlanInput
from maiweave.life.continuity import Continuity
from test_features import life
from test_continuity import result


def record_completion(x, session=""):
    plan = x.engine.create_plan(PlanInput(title="去图书馆读书", start=x.now.isoformat(),
        end=(x.now + timedelta(minutes=40)).isoformat(), location="图书馆"), session, x.now,
        autonomous=not session)
    later = x.now + timedelta(minutes=35)
    x.engine.advance(later)
    event = x.engine.record_experience(ExperienceInput(summary="发现一本有趣的书", occurred_at=later.isoformat(),
        source="simulated", evidence="活动完成记录", plan_id=plan["id"]), "chat-a", later)
    return plan, event


@pytest.mark.parametrize("session", ["", "chat-a"])
async def test_chat_completion_reflects_in_plan_scope(life, session):
    x = life
    plan, event = record_completion(x, session)
    assert event["session_id"] == session
    x.ctx.llm.generate.return_value = result()
    c = Continuity(x.engine, x.ctx, x.cfg)
    await c.process(x.now + timedelta(minutes=41))
    await c.process(x.now + timedelta(minutes=46))
    assert x.ctx.llm.generate.await_count == 1
    assert x.store.get("reflection", plan["id"])["status"] == "已完成"
    assert x.store.get("experience", event["id"])["revision"] == 2
    for category in ("reflection", "preference", "effect", "activity_memory"):
        assert x.store.all(category)
        assert all(row["session_id"] == session for row in x.store.all(category))
    assert x.engine.visible(x.store.get("experience", event["id"]), "chat-b") == (session == "")


def test_independent_experience_keeps_chat_scope(life):
    x = life
    event = x.engine.record_experience(ExperienceInput(summary="聊天中提到的一件事", occurred_at=x.now.isoformat(),
        source="reported", evidence="用户陈述"), "chat-a", x.now)
    assert event["session_id"] == "chat-a"
    with pytest.raises(ValueError, match="该记录不属于当前聊天流"):
        x.engine.revise_experience(event["id"], {}, "", "补充", "chat-b", x.now)


async def test_upgrade_repairs_old_scope_and_resumes_failed_reflection(life):
    x = life
    plan, event = record_completion(x)
    event["session_id"] = "chat-a"
    with x.store.db:
        x.store.put("experience", event)
        x.store.delete("runtime", "completion_scope_v1")
        x.store.put("reflection", {"id": plan["id"], "session_id": "", "status": "失败",
            "error": "该记录不属于当前聊天流", "retry_at": (x.now + timedelta(hours=1)).isoformat()})
    later = x.now + timedelta(minutes=41)
    engine = LifeEngine(x.store, "Asia/Shanghai", later)
    repaired = x.store.get("experience", event["id"])
    assert repaired == {**event, "session_id": ""}
    assert x.store.get("reflection", plan["id"])["retry_at"] == later.isoformat()
    x.ctx.llm.generate.return_value = result()
    c = Continuity(engine, x.ctx, x.cfg)
    await c.process(later)
    snapshot = x.store.get("experience", event["id"])
    LifeEngine(x.store, "Asia/Shanghai", later + timedelta(minutes=5))
    await c.process(later + timedelta(minutes=5))
    assert x.store.get("experience", event["id"]) == snapshot
    assert snapshot["revision"] == 2
    assert x.ctx.llm.generate.await_count == 1
    assert x.store.get("reflection", plan["id"])["status"] == "已完成"


def test_private_plan_stays_in_its_chat(life):
    x = life
    plan = x.engine.create_plan(PlanInput(title="一起画画", start=x.now.isoformat(),
        end=(x.now + timedelta(minutes=30)).isoformat()), "chat-b", x.now)
    with pytest.raises(ValueError, match="该记录不属于当前聊天流"):
        x.engine.record_experience(ExperienceInput(summary="画完了", occurred_at=x.now.isoformat(),
            source="reported", evidence="用户陈述", plan_id=plan["id"]), "chat-a", x.now)
    assert x.store.get("plan", plan["id"])["status"] == "scheduled"
    assert x.store.all("experience") == []
