"""大量历史记录的页面传输与 Planner 前缀测试。"""
import copy
import json
from datetime import timedelta

from maiweave.core.models import PlanInput
from test_plugin import plugin


def check_payload(value):
    pending = [value]
    count = 0
    while pending:
        row = pending.pop()
        count += 1
        if isinstance(row, dict):
            pending.extend(row.values())
        elif isinstance(row, list):
            pending.extend(row)
    assert count < 20000
    assert len(json.dumps(value, ensure_ascii=False).encode()) < 524288


async def test_life_context_keeps_original_prefix_and_current_time(plugin):
    original = [{"item_type": "SystemMessageItem", "parts": [{"type": "text", "text": "原有规则"}]},
                {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "现在做什么？"}]}]
    now = plugin.now().replace(microsecond=123456)
    plugin.now = lambda: now
    first = (await plugin.planner_context(items=copy.deepcopy(original), session_id="group"))["modified_kwargs"]["items"]
    now += timedelta(minutes=1)
    second = (await plugin.planner_context(items=copy.deepcopy(original), session_id="group"))["modified_kwargs"]["items"]
    assert first[:-1] == second[:-1] == original
    assert second[-1]["item_type"] == "UserMessageItem"
    snapshot = json.loads(second[-1]["parts"][0]["text"].split("\n")[-1])
    assert snapshot["now"] == now.isoformat(timespec="minutes")
    assert "updated_at" not in snapshot["state"]
    assert snapshot["now"] != json.loads(first[-1]["parts"][0]["text"].split("\n")[-1])["now"]


async def test_diary_pages_and_complete_material(plugin):
    material = {"活动": [{"title": "读书", "notes": ["感想"] * 10} for _ in range(2200)]}
    with plugin.store.db:
        for i in range(45):
            plugin.store.put("diary", {"id": str(i), "day": f"2026-01-{i:02}", "complete": True,
                                       "content": "今天读了书。", "material": material})
    ids = set()
    for page in range(1, 4):
        result = await plugin.ui_diaries(page=page)
        check_payload(result)
        assert result["pagination"]["pages"] == 3
        ids.update(row["id"] for row in result["entries"])
    assert len(ids) == 45
    first = await plugin.ui_record_detail("diary", "0", "material")
    chunks = [first["text"]]
    for page in range(2, first["pages"] + 1):
        detail = await plugin.ui_record_detail("diary", "0", "material", page)
        check_payload(detail)
        chunks.append(detail["text"])
    assert json.loads("".join(chunks)) == material
    assert plugin.store.get("diary", "0")["material"] == material


async def test_schedule_paging_and_edit_selection_preserve_changes(plugin):
    now = plugin.now()
    plan = plugin.engine.create_plan(PlanInput(title="阅读", start=(now + timedelta(hours=1)).isoformat(),
                                             end=(now + timedelta(hours=2)).isoformat()), "", now)
    changes = [{"at": now.isoformat(), "before": {"title": "旧书"}, "after": {"title": "新书"}} for _ in range(3000)]
    with plugin.store.db:
        for i in range(205):
            plugin.store.put("plan", {**plan, "id": str(i), "start": (now + timedelta(hours=3 + 2*i)).isoformat(),
                                           "end": (now + timedelta(hours=4 + 2*i)).isoformat(), "changes": changes})
    seen = set()
    first = await plugin.ui_schedule()
    for page in range(1, first["pagination"]["pages"] + 1):
        result = await plugin.ui_schedule(page=page)
        check_payload(result)
        seen.update(p["id"] for p in result["plans"])
    assert len(seen) == 206
    selected = await plugin.ui_editor_records("plan", record_id=plan["id"])
    assert selected["pagination"]["page"] > 1
    assert any(p["id"] == plan["id"] for p in selected["records"])
    check_payload(selected)
    updated = await plugin.ui_update_plan("0", new_title="读完后散步")
    check_payload(updated)
    assert len(plugin.store.get("plan", "0")["changes"]) > len(changes)


async def test_review_result_omits_full_revision_history(plugin):
    event = {"id": "event", "summary": "一次阅读", "history": [{"old": "内容"}] * 21000}
    with plugin.store.db:
        plugin.store.put("review", {"id": "review", "status": "待确认", "proposed": event,
                                    "conflicts": ["时间不同"], "existing": {"原生记忆": "原记录"}})
    result = await plugin.ui_change_status("review")
    check_payload(result)
    assert result["conflicts"] == ["时间不同"]
    assert result["proposed"]["summary"] == "一次阅读"
    assert len(plugin.store.get("review", "review")["proposed"]["history"]) == 21000
