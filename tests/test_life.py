"""验证计划、事实与持续生活的主要行为。"""

from datetime import datetime, timedelta

import pytest

from maiweave.core.engine import LifeEngine
from maiweave.core.models import ExperienceInput, GoalInput, PlanInput, PlanPatch, RelationshipChange
from maiweave.core.store import Store


@pytest.fixture
def life(tmp_path):
    now = datetime.fromisoformat("2026-10-31T12:00:00+08:00")
    store = Store(tmp_path / "life.sqlite3")
    engine = LifeEngine(store, "Asia/Shanghai", now)
    yield engine, now
    store.close()


def plan(now, **changes):
    return PlanInput.model_validate({"title": "看书", "start": now.isoformat(),
                                    "end": (now + timedelta(hours=1)).isoformat(), **changes})


def test_change_cancel_and_restart(life, tmp_path):
    engine, now = life
    p = engine.create_plan(plan(now), "chat-a", now)
    moved = engine.change_plan(p["id"], PlanPatch(start=(now + timedelta(hours=2)).isoformat(), end=(now + timedelta(hours=3)).isoformat()), "chat-a", now)
    assert moved["id"] == p["id"]
    assert len(moved["changes"]) == 1
    engine.transition(p["id"], "cancel", "chat-a", now)
    engine.advance(now + timedelta(hours=4))
    assert engine.experiences("chat-a") == []
    reopened = Store(tmp_path / "life.sqlite3")
    try:
        assert reopened.get("plan", p["id"])["status"] == "cancelled"
    finally:
        reopened.close()


def test_shared_plan_does_not_become_fact(life):
    engine, now = life
    p = engine.create_plan(plan(now, participants=["用户"]), "chat-a", now)
    engine.advance(now + timedelta(hours=2))
    assert engine.store.get("plan", p["id"])["status"] == "awaiting_confirmation"
    assert engine.experiences("chat-a") == []


def test_simulation_finishes_once_and_progresses_goal(life):
    engine, now = life
    goal = engine.create_goal(GoalInput(title="读完一本书", target_sessions=1), "chat-a", now)
    p = engine.create_plan(plan(now, goal_id=goal["id"]), "chat-a", now)
    engine.advance(now + timedelta(hours=2))
    engine.advance(now + timedelta(hours=3))
    events = engine.experiences("chat-a")
    assert len(events) == 1
    assert events[0]["source"] == "simulated"
    assert events[0]["plan_id"] == p["id"]
    assert engine.store.get("goal", goal["id"])["status"] == "completed"


def test_detail_revision_keeps_source_and_history(life):
    engine, now = life
    event = engine.record_experience(ExperienceInput(summary="昨晚吃面", occurred_at=(now - timedelta(days=1)).isoformat(), evidence="用户说昨晚吃面"), "chat-a", now)
    revised = engine.revise_experience(event["id"], {"同行者": "小林"}, "", "用户补充和小林一起", "chat-a", now)
    assert revised["source"] == "reported"
    assert revised["history"][0]["details"] == {}
    assert revised["details"] == {"同行者": "小林"}
    assert revised["revision"] == 2


def test_record_completes_shared_plan(life):
    engine, now = life
    p = engine.create_plan(plan(now, participants=["用户"]), "chat-a", now)
    later = now + timedelta(hours=2)
    engine.advance(later)
    event = engine.record_experience(ExperienceInput(summary="一起看完书", occurred_at=later.isoformat(), evidence="用户确认", plan_id=p["id"]), "chat-a", later)
    assert engine.store.get("plan", p["id"])["experience_id"] == event["id"]


def test_pause_resume_retains_remaining_time(life):
    engine, now = life
    p = engine.create_plan(plan(now), "chat-a", now)
    half = now + timedelta(minutes=30)
    engine.advance(half)
    engine.transition(p["id"], "pause", "chat-a", half)
    resumed_at = now + timedelta(hours=2)
    engine.advance(resumed_at)
    assert not engine.experiences("chat-a")
    resumed = engine.transition(p["id"], "resume", "chat-a", resumed_at)
    assert engine.time(resumed["end"]) == resumed_at + timedelta(minutes=30)
    engine.advance(resumed_at + timedelta(minutes=31))
    assert engine.store.get("plan", p["id"])["elapsed_seconds"] == 3600


def test_overlap_and_wrong_session(life):
    engine, now = life
    p = engine.create_plan(plan(now), "chat-a", now)
    with pytest.raises(ValueError, match="已有安排"):
        engine.create_plan(plan(now), "chat-b", now)
    assert engine.plans("chat-b") == []
    with pytest.raises(ValueError, match="不属于"):
        engine.transition(p["id"], "cancel", "chat-b", now)


def test_sleep_crosses_midnight_and_restores_energy(life):
    engine, now = life
    night = now.replace(hour=23)
    engine.advance(night)
    before = engine.state()["energy"]
    engine.create_plan(plan(night, title="睡觉", kind="sleep", end=(night + timedelta(hours=8)).isoformat()), "", night)
    engine.advance(night + timedelta(hours=8))
    assert engine.state()["energy"] > before
    assert len(engine.experiences("chat-a")) == 1


def test_offline_gap_does_not_invent_experiences(life):
    engine, now = life
    engine.advance(now + timedelta(days=7))
    assert engine.experiences("chat-a") == []
    assert 0 <= engine.state()["energy"] <= 100


def test_emotion_has_inertia_and_relationship_is_persistent(life):
    engine, now = life
    engine.affect(-10, 5, "计划没有完成，有些失落", now)
    engine.advance(now + timedelta(hours=1))
    assert -10 < engine.state()["mood"] < 0
    engine.relate("chat-a", "user-a", RelationshipChange(trust=2, reason="对方履行了约定", boundary="晚上不主动打扰"), now)
    assert engine.relationship("chat-a", "user-a")["trust"] == 52
    assert engine.relationship("chat-b", "user-a")["trust"] == 50


def test_future_experience_is_rejected(life):
    engine, now = life
    with pytest.raises(ValueError, match="尚未发生"):
        engine.record_experience(ExperienceInput(summary="明天出门", occurred_at=(now + timedelta(days=1)).isoformat(), evidence="尚未发生"), "chat-a", now)


def test_shared_meal_requires_confirmation_and_changes_hunger(life):
    engine, now = life
    p = engine.create_plan(plan(now, title="一起吃饭", kind="meal", participants=["用户"]), "chat-a", now)
    later = now + timedelta(hours=1)
    engine.advance(later)
    before = engine.state()["hunger"]
    with pytest.raises(ValueError, match="共同安排"):
        engine.record_experience(ExperienceInput(summary="吃过饭", source="simulated", occurred_at=later.isoformat(), evidence="模拟", plan_id=p["id"]), "chat-a", later)
    engine.record_experience(ExperienceInput(summary="吃过饭", occurred_at=later.isoformat(), evidence="用户确认", plan_id=p["id"]), "chat-a", later)
    assert engine.state()["hunger"] < before


def test_old_sharing_scores_migrate_once(tmp_path):
    from maiweave.core.store import Store
    from maiweave.core.engine import LifeEngine
    from datetime import datetime, timezone
    store = Store(tmp_path / "scores.db")
    now = datetime.now(timezone.utc)
    with store.db:
        for score in (1, 2, 3):
            store.put("experience", {"id": str(score), "importance": score})
    LifeEngine(store, "Asia/Shanghai", now)
    LifeEngine(store, "Asia/Shanghai", now)
    assert [e["importance"] for e in store.all("experience")] == [2, 6, 9]
    assert store.get("experience", "2")["score_reason"] == "旧版重要度 2/3 分换算"
    store.close()
