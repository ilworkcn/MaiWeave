"""日记按日期写作、次日整理和用量统计。"""
from datetime import timedelta

import pytest

from test_plugin import plugin


def history(plugin, at):
    with plugin.store.db:
        plugin.store.put("state_history", {"id": at.isoformat(), "at": at.isoformat(), "energy": 60, "mood": 10, "hunger": 20, "stress": 5})


async def test_diary_automatic_once_and_usage(plugin):
    now = plugin.now().replace(hour=12)
    history(plugin, now - timedelta(days=1))
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "昨天过得很平静。", "prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
    await plugin.diary.process(now)
    await plugin.diary.process(now + timedelta(minutes=1))
    plugin.ctx.llm.generate.assert_awaited_once()
    record = plugin.store.all("diary")[0]
    assert record["day"] == (now.date() - timedelta(days=1)).isoformat()
    assert record["complete"] and record["content"] == "昨天过得很平静。"
    assert plugin.store.all("model_usage")[0]["feature"] == "生活日记"
    assert (await plugin.ui_diaries(record["day"]))["entries"][0]["coverage"] == "全天记录"


async def test_diary_today_preview_finalized_next_day(plugin):
    now = plugin.now().replace(hour=12)
    history(plugin, now - timedelta(hours=1))
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "上午看了书。"}
    preview = await plugin.diary.write("", now)
    assert preview["complete"] is False
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "一天平静地过去了。"}
    await plugin.diary.process(now + timedelta(days=1))
    assert len(plugin.store.all("diary")) == 1
    assert plugin.store.get("diary", preview["id"])["complete"] is True
    assert plugin.ctx.llm.generate.await_count == 2


async def test_diary_failure_can_be_rewritten(plugin):
    now = plugin.now().replace(hour=12)
    history(plugin, now - timedelta(days=1))
    plugin.ctx.llm.generate.return_value = {"success": False, "error": "模型繁忙"}
    with pytest.raises(RuntimeError, match="模型繁忙"):
        await plugin.diary.process(now)
    await plugin.diary.process(now + timedelta(minutes=1))
    assert plugin.ctx.llm.generate.await_count == 1
    row = plugin.store.all("diary")[0]
    assert row["status"] == "生成失败"
    plugin.ctx.llm.generate.return_value = {"success": True, "response": "记录昨天的生活。"}
    await plugin.diary.write(row["day"], now)
    assert plugin.store.get("diary", row["id"])["status"] == "已生成"


async def test_diary_switch_and_empty_day(plugin):
    now = plugin.now()
    plugin.config.diary.enabled = False
    history(plugin, now - timedelta(days=1))
    await plugin.diary.process(now)
    plugin.ctx.llm.generate.assert_not_awaited()
    with pytest.raises(ValueError, match="生活记录"):
        await plugin.diary.write((now.date() - timedelta(days=3)).isoformat(), now)
    with pytest.raises(ValueError, match="过去"):
        await plugin.diary.write((now.date() + timedelta(days=1)).isoformat(), now)
