"""验证上下文去重后的操作关联、生活接续和内容预算。"""

from copy import deepcopy
from datetime import timedelta

import pytest

from maiweave.core.models import ExperienceInput, PlanInput
from maiweave.integrations.chat_context import deduplicate
from test_chat_context import snapshot
from test_plugin import plugin


def prepare_life(plugin):
    now = plugin.now().replace(hour=14, minute=0, second=0, microsecond=0)
    plugin.now = lambda: now
    plugin.config.context.private_mode = 3
    plugin.config.context.private_chars = 12000
    plan = plugin.engine.create_plan(PlanInput(title="在公园继续读书", kind="leisure", start=now.isoformat(),
        end=(now + timedelta(minutes=30)).isoformat(), location="住处"), "a", now)
    future = plugin.engine.create_plan(PlanInput(title="回家做晚饭", kind="meal", start=(now + timedelta(hours=3)).isoformat(),
        end=(now + timedelta(hours=4)).isoformat()), "a", now)
    for i in range(6):
        details = {"过程": "试着揉面，观察面团的变化。" * 12, "结果": f"第{i}次揉面后面团更柔软了。", "进度": "已经揉好面，接下来等它发酵。"}
        event = plugin.engine.record_experience(ExperienceInput(summary=f"第{i}次揉面后面团更柔软了。", details=details,
            occurred_at=(now-timedelta(hours=i+1)).isoformat(), source="observed", evidence="共同做饭的互动"), "a", now)
        with plugin.store.db:
            plugin.store.put("activity_memory", {"id": f"c{i}", "session_id": "a", "activity": "揉面", "subject": "面包",
                "details": details, "completed": i > 0, "next_action": "等面团发酵", "next_date": "", "location": "厨房",
                "experience_id": event["id"]})
            plugin.engine.add_effect(f"effect{i}", event["summary"], 2, -1, now, 2, "a")
    return plan, future


async def test_current_plan_occurs_once_and_can_be_changed(plugin):
    current, future = prepare_life(plugin)
    data = snapshot(plugin.context_text("a", text="今天有什么安排"))
    assert data["current"]["id"] == current["id"]
    assert [p["id"] for p in data["plans"]] == [future["id"]]
    changed = await plugin.change_plan(data["current"]["id"], "update", {"title": "回家继续读书"}, stream_id="a")
    assert changed["success"]
    assert snapshot(plugin.context_text("a"))["current"]["title"] == "回家继续读书"


async def test_completed_context_follows_topic_and_storage_stays_complete(plugin):
    prepare_life(plugin)
    saved = deepcopy(plugin.store.all("activity_memory"))
    ordinary = snapshot(plugin.context_text("a", text="你好呀"))
    assert [r["id"] for r in ordinary["continuity"]["continuations"]] == ["c0"]
    recalled = snapshot(plugin.context_text("a", text="上次揉面的进度怎么样"))
    assert len(recalled["continuity"]["continuations"]) == 6
    assert plugin.store.all("activity_memory") == saved
    assert all("过程" in row["details"] for row in saved)


@pytest.mark.parametrize("experiences", [True, False])
async def test_continuation_keeps_background_until_experience_is_selected(plugin, experiences):
    prepare_life(plugin)
    plugin.config.context.experiences = experiences
    data = snapshot(plugin.context_text("a", text="继续上次的进度，今天做了什么"))
    events = {r["id"]: r for r in data.get("experiences", [])}
    for row in data["continuity"]["continuations"]:
        assert row["source"] == "observed"
        assert row["next_action"] == "等面团发酵"
        if row["experience_id"] in events:
            assert "background" not in row
            assert "progress" not in row
        else:
            assert row["background"] and row["progress"]
    for row in data["continuity"]["effects"]:
        assert row["mood"] == 2 and row["expires_at"]
        assert row.get("reason") or row["experience_id"] in events


async def test_small_budget_continuation_retains_background(plugin):
    prepare_life(plugin)
    plugin.config.context.private_chars = 2400
    plugin.config.context.schedule = False
    data = snapshot(plugin.context_text("a", text="继续上次的进度"))
    assert data["continuity"]["continuations"]
    for row in data["continuity"]["continuations"]:
        if row["experience_id"] not in {e["id"] for e in data.get("experiences", [])}:
            assert row["background"]


def test_unrelated_or_shortened_experience_does_not_remove_unique_background():
    data = {"experiences": [{"id": "e1", "summary": "较短摘要", "details": {"结果": "缩短的结果…"}}],
        "continuity": {"continuations": [{"id": "c1", "experience_id": "e1", "background": "完整的结果包含独有细节", "next_action": "明天再试"}],
                       "effects": [{"id": "f1", "reason": "独有的原因", "mood": 2}]}}
    original = deepcopy(data)
    assert deduplicate(data) == original
    assert data == original


@pytest.mark.parametrize("approve", [True, False])
async def test_conflict_from_compact_context_drives_confirmation(plugin, approve):
    from test_review_workflow import decision
    prepare_life(plugin)
    plugin.config.context.private_chars = 2400
    person = plugin.anniversaries.save_profile("小明", "a", "u")
    birthday = plugin.anniversaries.save_date(person["id"], "生日", "solar", 8, 3)
    decision(plugin, ["生日从8月3日改为8月5日"])
    pending = await plugin.reviews.submit("anniversary", {"profile_id": person["id"], "anniversary_id": birthday["id"],
        "title": "生日", "calendar": "solar", "month": 8, "day": 5}, "a")
    text = plugin.context_text("a", text="确认生日修改，继续上次的进度")
    row = snapshot(text)["pending_changes"][0]
    assert row["id"] == pending["id"] and row["conflicts"]
    assert plugin.store.get("anniversary", birthday["id"])["day"] == 3
    result = await plugin.confirm_change(row["id"], approve, stream_id="a")
    assert result["success"]
    assert plugin.store.get("anniversary", birthday["id"])["day"] == (5 if approve else 3)
    assert "pending_changes" not in snapshot(plugin.context_text("a"))


@pytest.mark.parametrize("session", ["group", "private"])
async def test_dedup_context_tracks_sleep_wake_and_return(plugin, session):
    from test_sleep_identity import linked_person
    from test_sleep_policy import sleep_plan
    prepare_life(plugin)
    for row in plugin.store.all("plan"):
        await plugin.change_plan(row["id"], "cancel", stream_id="a")
    await linked_person(plugin)
    now, plan = sleep_plan(plugin)
    plugin.now = lambda: now
    plugin.chat_context.kinds[session] = session
    data = snapshot(plugin.context_text(session, text="上次做了什么"))
    assert data["sleep_policy"]["sleeping"]
    assert "awake=false 时结束本轮" in plugin.context_text(session)
    assert await plugin.sleep_before_send({"session_id": session}) == {"action": "abort"}
    result = await plugin.wake("现在需要陪伴", stream_id=session, user_id="u")
    assert result["content"]["awake"]
    assert not snapshot(plugin.context_text(session))["sleep_policy"]["sleeping"]
    assert await plugin.sleep_before_send({"session_id": session}) == {"action": "continue"}
    plugin.now = lambda: now + timedelta(hours=3)
    assert snapshot(plugin.context_text(session))["sleep_policy"]["sleeping"]



def test_repeated_selection_keeps_effect_link_to_its_original_experience():
    data = {"experiences": [{"id": "other", "summary": "另一件事", "details": {}},
                            {"id": "original", "summary": "面包做好了", "details": {}}],
            "continuity": {"effects": [{"id": "effect", "reason": "面包做好了", "mood": 2}]}}
    first = deduplicate(data)
    assert first["continuity"]["effects"][0]["experience_id"] == "original"
    assert deduplicate(first) == first
