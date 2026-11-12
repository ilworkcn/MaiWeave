"""分享新意判断、发送原文与聊天预算的联动。"""
import json
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from test_plugin import plugin
from test_proactive_modes import prepare


@pytest.mark.parametrize("mode", ["主动表达", "自然交流"])
async def test_similar_meal_waits_and_new_progress_sends(plugin, monkeypatch, mode):
    now=prepare(plugin,monkeypatch)
    plugin.config.proactive.mode=mode
    with plugin.store.db:
        plugin.store.put("share_receipt",{"id":"earlier","session":"chat-a","sent_at":(now-timedelta(hours=2)).isoformat(),"created":(now-timedelta(hours=2)).isoformat(),"outcome":"实际已发送","topic":"煮面","sent_text":"煮了一碗面，吃完了。"})
        for row in plugin.store.all("experience"):
            row.update(summary="又煮了一碗面",occurred_at=(now-timedelta(hours=1)).isoformat(),importance=8)
            plugin.store.put("experience",row)
    plugin.ctx.llm.generate.side_effect=None
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"send":False,"reason":"刚分享过煮面，暂时没有新进展"})}
    await plugin.living.share(now)
    plugin.ctx.send.text.assert_not_awaited()
    plugin.ctx.maisaka.proactive.trigger.assert_not_awaited()
    row=plugin.store.all("share_receipt")[-1]
    assert row["outcome"]=="稍后再聊" and not row.get("sent_at")
    assert not plugin.living.share_ready(row["record_id"],"chat-a",now+timedelta(minutes=1))
    prompt=plugin.ctx.llm.generate.await_args.args[0]
    assert "煮了一碗面，吃完了" in prompt
    assert "本次经历发生时间=" in prompt and "当前时间=" in prompt
    assert "刚发生" not in prompt
    plugin.ctx.llm.generate.return_value={"success":True,"response":json.dumps({"send":True,"reason":"这次做法有具体新发现"})}
    await plugin.living.share(now+timedelta(minutes=plugin.config.proactive.retry_minutes))
    if mode=="主动表达":
        plugin.ctx.send.text.assert_awaited_once()
        assert plugin.store.all("share_receipt")[-1]["sent_text"]==plugin.ctx.maisaka.reply.generate.return_value["text"]
    else:
        plugin.ctx.maisaka.proactive.trigger.assert_awaited_once()
    assert all(r["feature"]=="分享判断" for r in plugin.store.all("model_usage"))


@pytest.mark.parametrize("budget",[1200,2400,4800])
@pytest.mark.parametrize("kind",["group","private"])
async def test_sent_text_respects_chat_budget(plugin,budget,kind):
    now=plugin.now()
    plugin.chat_context.kinds[kind]=kind
    setattr(plugin.config.context,kind+"_chars",budget)
    with plugin.store.db:
        for i in range(3):
            plugin.store.put("share_receipt",{"id":str(i),"session":kind,"sent_at":now.isoformat(),"topic":"煮面","sent_text":"今天的面条很好吃。"*60})
    text=plugin.context_text(kind,text="你刚才说的面条")
    assert len(text)<=budget
    assert len(plugin.living.receipts.context(kind,now))==3


async def test_planner_receipt_collects_sent_segments(plugin):
    now=plugin.now()
    with plugin.store.db:
        plugin.store.put("share_receipt",{"id":"task","session":"chat-a","created":now.isoformat(),"outcome":"MaiBot 已受理","calls":{"call":"reply"}})
    async def get(table,**kwargs):
        if table=="ToolRecord":
            return {"tool_name":"reply","timestamp":now.isoformat(),"tool_data":json.dumps({"success":True,"metadata":{"sent_message_ids":["one","two"]}})}
        return {"timestamp":now.isoformat(),"processed_plain_text":{"one":"傍晚煮了面。","two":"这次加了蘑菇。"}[kwargs["filters"]["message_id"]]}
    plugin.ctx.db.get.side_effect=get
    await plugin.living.receipts.refresh(now)
    row=plugin.store.get("share_receipt","task")
    assert row["sent_text"]=="傍晚煮了面。\n这次加了蘑菇。"
    assert row["outcome"]=="实际已发送"
