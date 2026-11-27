"""聊天配图的来源、复用、开关与主动发送结算。"""
import asyncio
import base64
import json
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from test_plugin import plugin
from test_diary_images import PNG, service
from maiweave.core.models import ExperienceInput
from maiweave.integrations.life_images import current_image_candidates, image_request_stage, image_tool_relevant


def test_photo_request_and_tool_search_apply_to_current_turn():
    def msg(text):
        return {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "<message >" + text}]}
    search = {"item_type": "FunctionCallItem", "tool_call": {"func_name": "tool_search",
              "args": {"query": "maiweave_show_life"}}}
    assert image_tool_relevant([msg("我想看看你")])
    assert image_tool_relevant([msg("折成什么样了？")])
    assert image_tool_relevant([msg("拿来瞅瞅"), search])
    assert not image_tool_relevant([search, msg("能仔细讲讲吗")])
    assert not image_tool_relevant([msg("我想看看你"), msg("这样呀")])


def test_previous_candidate_lookup_is_removed_as_a_pair():
    def call(key, action):
        return {"item_type": "FunctionCallItem", "tool_call": {"func_name": "maiweave_show_life",
                "call_id": key, "args": {"action": action}}}
    def output(key):
        return {"item_type": "FunctionCallOutputItem", "call_id": key, "output": "{}"}
    message = {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "<message >这样呀"}]}
    keep = [call("photo", "send"), output("photo"), message, call("now", "list"), output("now")]
    assert current_image_candidates([call("old", "list"), output("old"), *keep]) == keep[2:]


async def test_group_picture_request_survives_an_interruption(plugin, monkeypatch):
    p = plugin
    _, event = setup(p, monkeypatch)
    session = "chat-a"
    p.chat_context.kinds[session] = "group"
    def message(mid, body, own=False):
        header = f'<message msg_id="{mid}" is_self_message="{str(own).lower()}">'
        return {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": header + body + "</message>"}]}
    tool = {"function": {"name": "maiweave_show_life", "parameters": {"properties": {"action": {}}}}}
    async def offered(items):
        result = await p.planner_context(session_id=session, items=list(items), tool_definitions=[tool])
        return result["modified_kwargs"]["tool_definitions"]
    ask = message("a", "[图片：人物自拍]给我看看书签")
    aside = message("b", "哈哈")
    assert await offered([ask, aside])
    assert p.life_images.group_photo_requests[session] == ("a", "给我看看书签")
    assert (await p.show_life("list", stream_id=session))["items"]
    await p.show_life("send", event["id"], stream_id=session)
    assert p.store.all("image_delivery")[-1]["request_message_id"] == "a"
    call = {"item_type": "FunctionCallItem", "tool_call": {"func_name": "maiweave_show_life", "args": {"action": "send"}, "call_id": "s"}}
    receipt = {"item_type": "FunctionCallOutputItem", "call_id": "s", "output": {"status": "已发送"}}
    assert not await offered([ask, call, receipt, aside])
    assert not await offered([ask, message("self", "给你", True), aside])
    assert not await offered([message("media", "[图片：自拍][表情包:发张照片][视频：看看]")])
    assert not await offered([ask] + [aside] * 5)


def test_planner_stops_offering_images_after_accepted_send():
    def message(body):
        return {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": body}]}
    call = {"item_type": "FunctionCallItem", "tool_call": {"call_id": "one", "func_name": "maiweave_show_life", "args": {"action": "send"}}}
    result = {"item_type": "FunctionCallOutputItem", "call_id": "one", "output": '{"status":"进行中"}'}
    items = [message('<message msg_id="1">看看书签'), call]
    assert not image_request_stage(items)
    assert not image_request_stage([*items, {**result, "output": "发送失败"}])
    items += [result, message('<message msg_id="2" is_self_message="true">[图片]'), message("请分析后决定下一步")]
    assert image_request_stage(items) == "sent"
    assert image_request_stage([*items, message('<message msg_id="image-1" user="麦麦">[图片]')],
                                 lambda mid: mid == "image-1") == "sent"
    echoed = [*items, message('<message msg_id="image-1" user="麦麦">[图片]')]
    assert current_image_candidates(echoed, lambda mid: mid == "image-1") == echoed
    assert not image_request_stage([*items, message('<message msg_id="3">再发一次')])
    assert not image_request_stage([*items, message('<plugin_proactive_task id="next">')])


async def test_planner_batch_sends_each_cached_image_once(plugin, monkeypatch):
    p = plugin
    now, event = setup(p, monkeypatch)
    await p.life_images.send(event["id"], "chat-a", now)
    p.ctx.send.image.reset_mock()
    def call(cid, action, eid=""):
        return {"item_type": "FunctionCallItem", "tool_call": {"call_id": cid, "func_name": "maiweave_show_life",
                "args": {"action": action, "experience_id": eid}}}
    items = [call("a", "list"), call("b", "list"), call("c", "send", event["id"]),
             call("d", "send", event["id"]), call("e", "send", "other")]
    result = await p.planner_receipt("chat-a", items, item_schema_version=1)
    kept = result["modified_kwargs"]["output_items"]
    assert kept == [items[0], items[2], items[4]]
    assert result["modified_kwargs"]["item_schema_version"] == 1
    for item in kept[:2]:
        await p.show_life(**item["tool_call"]["args"], stream_id="chat-a")
    p.ctx.send.image.assert_awaited_once()
    assert await p.planner_receipt("chat-a", [items[2]]) == {"action": "continue"}


async def test_planner_repeating_list_across_rounds(plugin, monkeypatch):
    _, event = setup(plugin, monkeypatch)
    tool = {"function": {"name": "maiweave_show_life", "parameters": {
        "properties": {"action": {"enum": ["list", "send"]}}, "required": ["action"]}}}
    message = {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "<message >我想看看你"}]}
    first = {"item_type": "FunctionCallItem", "tool_call": {"func_name": "maiweave_show_life",
             "args": {"action": "list"}, "call_id": "first"}}
    second = {**first, "tool_call": {**first["tool_call"], "call_id": "second"}}
    async def prepare(items):
        await plugin.planner_context(session_id="chat-a", items=list(items), tool_definitions=[tool])
    await prepare([message])
    assert await plugin.planner_receipt("chat-a", [first]) == {"action": "continue"}
    result = await plugin.show_life("list", stream_id="chat-a")
    history = [message, first, {"item_type": "FunctionCallOutputItem", "call_id": "first",
                               "output": json.dumps(result)}]
    await prepare(history)
    response = await plugin.planner_receipt("chat-a", [second])
    assert response["modified_kwargs"]["output_items"][0]["tool_call"] == {
        "func_name": "wait", "call_id": "second", "args": {}}
    for name, args in [("reply", {}), ("maiweave_show_life", {"action": "send", "experience_id": event["id"]})]:
        await prepare(history)
        valid = {"item_type": "FunctionCallItem", "tool_call": {"func_name": name, "args": args, "call_id": "ok"}}
        response = await plugin.planner_receipt("chat-a", [second, valid])
        assert response["modified_kwargs"]["output_items"] == [valid]
    await prepare([*history, message])
    assert await plugin.planner_receipt("chat-a", [second]) == {"action": "continue"}


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
@pytest.mark.parametrize("first", ["追问", "主动", "已送达"])
async def test_same_picture_reaches_chat_once(plugin, monkeypatch, mode, first):
    p = plugin
    now, event = setup(p, monkeypatch)
    monkeypatch.setattr(p.life_images, "short_wait", 0.01)
    row = {"id": "share", "session": "chat-a", "created": now.isoformat(), "sent_at": now.isoformat(),
           "outcome": "实际已发送", "mode": mode, "experience_id": event["id"], "image_requested": True}
    with p.store.db:
        p.store.put("share_receipt", row)
    started, resume = asyncio.Event(), asyncio.Event()
    async def deliver(*args, **kwargs):
        started.set()
        await resume.wait()
        return {"sent": True, "message_id": "image-1"}
    p.ctx.send.image.side_effect = deliver
    if first == "主动":
        share = asyncio.create_task(p.living.share_images(now))
        await asyncio.wait_for(started.wait(), 5)
        chat = asyncio.create_task(p.show_life("send", event["id"], stream_id="chat-a"))
        await asyncio.sleep(0)
        resume.set()
        await asyncio.gather(share, chat)
    else:
        job = await p.show_life("send", event["id"], stream_id="chat-a")
        assert "after_reply=stop" in job["instruction"]
        again = await p.show_life("send", event["id"], stream_id="chat-a")
        assert again["status"] == "正在生成，请稍等"
        await asyncio.wait_for(started.wait(), 5)
        if first == "已送达":
            resume.set()
            await asyncio.gather(*list(p.jobs))
        share = asyncio.create_task(p.living.share_images(now))
        await asyncio.sleep(0)
        resume.set()
        await asyncio.gather(share, *list(p.jobs))
    assert len(p.store.all("image_delivery")) == p.ctx.send.image.await_count == 1
    assert p.store.get("share_receipt", "share")["image_message_id"] == "image-1"
    assert len(p.living.receipts.sent_times("chat-a", now.tzinfo)) == 1
    resent = await p.show_life("send", event["id"], stream_id="chat-a")
    assert "after_reply=stop" in resent["instruction"] and p.ctx.send.image.await_count == 2


async def test_auto_image_uses_only_matching_deliveries(plugin, monkeypatch):
    p = plugin
    now, event = setup(p, monkeypatch, session="")
    await p.life_images.send(event["id"], "chat-b", now)
    await p.life_images.send(event["id"], "chat-a", now)
    later = p.now() + timedelta(minutes=1)
    with p.store.db:
        for key, session, status in [("other", "chat-b", "已发送"), ("failed", "chat-a", "发送失败")]:
            p.store.put("image_delivery", {"id": key, "experience_id": event["id"], "session_id": session,
                                           "status": status, "at": later.isoformat(), "share_id": ""})
    row = {"id": "share", "session": "chat-a", "created": later.isoformat(), "sent_at": later.isoformat(),
           "outcome": "实际已发送", "experience_id": event["id"], "image_requested": True}
    with p.store.db:
        p.store.put("share_receipt", row)
    await p.living.share_images(later)
    assert p.ctx.send.image.await_count == 3


async def test_accepted_selfie_finishes_current_request(plugin):
    p = plugin
    p.life_images.turn_notes["chat-a"] = ("msg-a", "我想看看你", "已接单", "发送")
    p.chat_context.messages["chat-a"] = "我想看看你"
    reply = [{"item_type": "FunctionCallItem", "tool_call": {"call_id": "reply1", "func_name": "reply", "args": {}}}]
    done = await p.planner_receipt("chat-a", reply)
    assert [i["tool_call"]["func_name"] for i in done["modified_kwargs"]["output_items"]] == ["wait"]
    assert done["modified_kwargs"]["output_items"][0]["meta"]["item_id"]
    p.chat_context.messages["chat-a"] = "这张图是在哪拍的？"
    assert await p.planner_receipt("chat-a", reply) == {"action": "continue"}


def setup(p, monkeypatch, session="chat-a"):
    service(p, monkeypatch)
    p.config.diary.chat_images_enabled = True
    now = p.now()
    event = p.engine.record_experience(ExperienceInput(summary="折好一个三角书角书签", source="simulated",
        occurred_at=now.isoformat(), evidence="完成折纸", details={"外形": "三角形"}), session, now)
    p.ctx.llm.generate.side_effect = None
    p.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"content": "书签", "image_prompt": "木桌上的三角书角书签"})}
    return now, event


async def test_send_reuses_image_and_checks_owner(plugin, monkeypatch):
    p = plugin
    now, event = setup(p, monkeypatch)
    await p.show_life("send", event["id"], stream_id="chat-a")
    import asyncio
    await asyncio.gather(*list(p.jobs))
    first = p.store.get("experience_image", event["id"])["image_file"]
    p.config.diary.image_service_enabled = False
    await p.show_life("send", event["id"], stream_id="chat-a")
    assert p.store.get("experience_image", event["id"])["image_file"] == first
    assert len([r for r in p.store.all("model_usage") if r["feature"] == "生活图片"]) == 1
    assert p.ctx.send.image.await_count == 2
    assert all(base64.b64decode(call.args[0]) == PNG for call in p.ctx.send.image.await_args_list)
    assert len(p.life_images.context("chat-a")) == 2
    assert not p.life_images.context("other")
    with pytest.raises(ValueError, match="不属于"):
        await p.show_life("send", event["id"], stream_id="other")
    p.config.diary.chat_images_enabled = False
    with pytest.raises(ValueError, match="关闭"):
        await p.show_life("send", event["id"], stream_id="chat-a")


@pytest.mark.parametrize("mode", ["自然交流", "主动表达"])
async def test_share_image_waits_for_text_and_counts_once(plugin, monkeypatch, mode):
    p = plugin
    now, event = setup(p, monkeypatch)
    row = {"id": "share", "session": "chat-a", "created": now.isoformat(), "mode": mode,
           "outcome": "MaiBot 已受理", "experience_id": event["id"], "image_requested": True}
    with p.store.db: p.store.put("share_receipt", row)
    await p.living.share_images(now)
    p.ctx.send.image.assert_not_awaited()
    row.update(outcome="实际已发送", sent_at=now.isoformat())
    with p.store.db: p.store.put("share_receipt", row)
    await p.living.share_images(now)
    await p.living.share_images(now)
    assert p.ctx.send.image.await_count == 1
    assert base64.b64decode(p.ctx.send.image.await_args.args[0]) == PNG
    assert len(p.living.receipts.sent_times("chat-a", now.tzinfo)) == 1
    assert p.store.get("share_receipt", "share")["image_delivery"] == "已发送"


async def test_image_send_failure_preserves_reusable_picture(plugin, monkeypatch):
    p = plugin
    now, event = setup(p, monkeypatch)
    p.ctx.send.image.return_value = {"sent": False, "error": "连接断开"}
    with pytest.raises(RuntimeError, match="连接断开"):
        await p.life_images.send(event["id"], "chat-a", now)
    assert p.store.get("experience_image", event["id"])["image_file"]
    assert p.store.all("image_delivery")[-1]["status"] == "发送失败"
    assert p.life_images.context("chat-a") == []


async def test_requested_failure_notifies_original_chat_and_can_retry(plugin, monkeypatch):
    import asyncio
    p = plugin
    now, event = setup(p, monkeypatch)
    request = p.life_images.images.request
    def fail(prompt):
        raise TimeoutError("生图超时")
    monkeypatch.setattr(p.life_images.images, "request", fail)
    p.ctx.send.text.return_value = {"sent": True}
    job = await p.show_life("send", event["id"], stream_id="chat-a")
    await asyncio.gather(*list(p.jobs))
    assert p.store.get("job", job["task_id"])["status"] == "失败"
    assert p.store.get("experience_image", event["id"])["image_status"] == "生成失败"
    assert p.ctx.send.text.await_args.args[1] == "chat-a"
    assert p.ctx.send.text.await_args.kwargs["storage_message"] is True
    p.ctx.send.image.assert_not_awaited()
    monkeypatch.setattr(p.life_images.images, "request", request)
    await p.show_life("send", event["id"], stream_id="chat-a")
    await asyncio.gather(*list(p.jobs))
    assert p.ctx.send.image.await_count == 1
    assert p.ctx.send.text.await_count == 1
    assert p.life_images.context("chat-a")


async def test_corrected_experience_regenerates_image(plugin, monkeypatch):
    import asyncio
    p = plugin
    now, event = setup(p, monkeypatch)
    await p.show_life("send", event["id"], stream_id="chat-a")
    await asyncio.gather(*list(p.jobs))
    original = p.store.get("experience_image", event["id"])["image_file"]
    p.engine.revise_experience(event["id"], {"外形": "心形"}, "折好一个心形书签", "更正折纸形状", "chat-a", now)
    p.ctx.llm.generate.return_value = {"success": True, "response": json.dumps({"content": "心形书签", "image_prompt": "木桌上的心形书签"})}
    result = await p.show_life("send", event["id"], stream_id="chat-a")
    await asyncio.gather(*list(p.jobs))
    assert "task_id" in result
    row = p.store.get("experience_image", event["id"])
    assert row["image_file"] != original
    assert "心形" in row["content"] and "心形" in row["image_prompt"]


async def test_image_tool_follows_switches_and_chat_ownership(plugin, monkeypatch):
    import copy
    p = plugin
    registered = next(c for c in p.get_components() if c["name"] == "maiweave_show_life")
    assert registered["metadata"]["visibility"] == "visible"
    now, event = setup(p, monkeypatch)
    tools = [{"type": "function", "function": {"name": "maiweave_show_life", "description": "生成或复用图片"}},
             {"type": "function", "function": {"name": "maiweave_query", "description": "查询生活"}}]
    original = copy.deepcopy(tools)

    async def definitions(session="chat-a"):
        result = await p.planner_context(session_id=session, items=[], tool_definitions=tools)
        return result["modified_kwargs"]["tool_definitions"]

    assert len(await definitions()) == 2
    p.config.diary.image_service_enabled = False
    assert len(await definitions()) == 1
    assert (await p.show_life("list", stream_id="chat-a"))["items"] == []
    p.config.diary.image_service_enabled = True
    await p.life_images.send(event["id"], "chat-a", now)
    p.config.diary.image_service_enabled = False
    p.ctx.llm.generate.reset_mock()
    available = await definitions()
    assert "已有" in available[0]["function"]["description"]
    assert "生成" not in available[0]["function"]["description"]
    assert len(await definitions("chat-b")) == 1
    result = await p.show_life("send", event["id"], stream_id="chat-a")
    assert result["status"] == "已发送"
    p.ctx.llm.generate.assert_not_awaited()
    p.config.diary.chat_images_enabled = False
    assert len(await definitions()) == 1
    assert (await p.show_life("list", stream_id="chat-a"))["enabled"] is False
    with pytest.raises(ValueError, match="聊天图片已关闭"):
        await p.show_life("send", event["id"], stream_id="chat-a")
    p.config.diary.chat_images_enabled = True
    p.config.life.enabled = False
    assert len(await definitions()) == 1
    assert tools == original


async def test_service_off_lists_existing_images_before_recent_limit(plugin, monkeypatch):
    from datetime import timedelta
    p = plugin
    now, event = setup(p, monkeypatch)
    await p.life_images.send(event["id"], "chat-a", now)
    for index in range(11):
        p.engine.record_experience(ExperienceInput(summary=f"新的生活片刻{index}", source="simulated",
            occurred_at=(now + timedelta(minutes=index + 1)).isoformat(), evidence="生活记录"), "chat-a", now + timedelta(minutes=index + 1))
    p.config.diary.image_service_enabled = False
    assert [row["id"] for row in p.life_images.candidates("chat-a")] == [event["id"]]
    event["summary"] = "修改后的书签"
    with p.store.db:
        p.store.put("experience", event)
    assert p.life_images.candidates("chat-a") == []


async def test_successful_list_offers_only_send_until_next_message(plugin, monkeypatch):
    setup(plugin, monkeypatch)
    tool = {"function": {"name": "maiweave_show_life", "parameters": {
        "properties": {"action": {"enum": ["list", "send"]}}, "required": ["action"]}}}
    call = {"item_type": "FunctionCallItem", "tool_call": {"func_name": "maiweave_show_life",
            "call_id": "pick", "args": {"action": "list"}}}
    result = {"item_type": "FunctionCallOutputItem", "call_id": "pick", "output": '{"items":[{"id":"photo"}]}'}
    definitions = plugin.life_images.tool_definitions
    params = definitions([tool], "chat-a", [call, result])[0]["function"]["parameters"]
    assert params["properties"]["action"]["enum"] == ["send"]
    assert params["required"] == ["action", "experience_id"]
    assert definitions([tool], "chat-a", [call, {**result, "output": '{"items":[]}'}]) == []
    new = {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": '<message msg_id="new">我想看看你'}]}
    assert definitions([tool], "chat-a", [call, result, new])[0]["function"]["parameters"]["properties"]["action"]["enum"] == ["list"]
    assert params["properties"]["experience_id"]["enum"] == ["photo"]


async def test_old_photo_requires_a_matching_request_and_candidate(plugin, monkeypatch):
    p = plugin
    now, old = setup(p, monkeypatch)
    await p.life_images.send(old["id"], "chat-a", now)
    row = p.store.all("image_delivery")[-1]
    with p.store.db:
        p.store.put("image_delivery", {**row, "at": (now - timedelta(days=1)).isoformat()})
    images = p.life_images
    assert not images.candidates("chat-a", request="发张照片", now=now)
    old_items = images.candidates("chat-a", request="把昨天那张再发一次", now=now)
    assert [(r["id"], r["sent_in_this_chat"]) for r in old_items] == [(old["id"], True)]
    assert not images.candidates("chat-b", request="把昨天那张再发一次", now=now)
    assert images.prepare_request("chat-a", "new-photo", "看看现在的你", now, p.start_job) == "发送"
    await asyncio.gather(*list(p.jobs))
    delivery = p.store.all("image_delivery")[-1]
    assert delivery["experience_id"] != old["id"] and delivery["request_message_id"] == "new-photo"
    assert "已展示图片" in p.chat_context.build("chat-a")

async def test_finished_dish_before_recent_unrelated_events(plugin, monkeypatch):
    p = plugin
    now, _ = setup(p, monkeypatch)
    def event(summary, offset, session="chat-a"):
        at = now + timedelta(minutes=offset)
        return p.engine.record_experience(ExperienceInput(summary=summary, source="simulated",
            occurred_at=at.isoformat(), evidence="活动结束"), session, at)
    dish = event("做完番茄炒蛋，盛在白瓷碗里", -60)
    private_dish = event("做完番茄炒蛋，盛在蓝碗里", -1, "chat-b")
    for i in range(12):
        event("整理书桌和工具", -i)
    result = await p.show_life("list", framing="番茄炒蛋做好了是什么样？", stream_id="chat-a")
    first = result["items"][0]
    assert first["id"] == dish["id"] and not first["has_image"]
    assert first["action_hint"] == "可根据这段经历生成"
    assert private_dish["id"] not in {item["id"] for item in result["items"]}
    sent = await p.show_life("send", dish["id"], stream_id="chat-a")
    assert sent["status"] == "已发送"
    p.ctx.send.image.assert_awaited_once()

async def test_reply_knows_which_chat_is_waiting_for_a_photo(plugin):
    plugin.life_images.pending_requests.add(("private-a", "dish"))
    for session, waiting in [("private-a", True), ("private-b", False)]:
        plugin.chat_context.messages[session] = "照片好了吗？"
        result = await plugin.reply_context(session_id=session, reply_reason="聊聊刚做好的菜", extra_prompt="")
        assert ("尚未送达" in result["modified_kwargs"]["extra_prompt"]) == waiting

    plugin.chat_context.messages["private-a"] = "书签选薄荷绿还是栗子棕？"
    result = await plugin.reply_context(session_id="private-a", reply_reason="图片仍在生成", extra_prompt="")
    assert "尚未送达" not in result["modified_kwargs"]["extra_prompt"]


async def test_short_progress_question_gets_one_delivery_receipt(plugin):
    p = plugin
    p.config.diary.chat_images_enabled = True
    p.config.life.schedule_enabled = False
    p.life_images.pending_requests.add(("chat-a", "dish"))
    p.ctx.send.text.return_value = {"sent": True}
    for _ in range(2):
        items = [{"item_type": "UserMessageItem", "parts": [
            {"type": "text", "text": '<message msg_id="progress">图好了吗？</message>'}]}]
        await p.planner_context(session_id="chat-a", items=items, tool_definitions=[])
    p.ctx.send.text.assert_awaited_once()


async def test_reconstruct_finished_shift_once(plugin, monkeypatch):
    p = plugin
    now, _ = setup(p, monkeypatch)
    at = (now - timedelta(days=1)).replace(hour=10)
    event = p.engine.record_experience(ExperienceInput(summary="在公司办公室核对资料", occurred_at=at.isoformat(),
        evidence="上午工作已完成", source="simulated"), "chat-a", now)
    images = p.life_images
    text = "按昨天上午上班的经历给我配一张工位图"
    assert images.history_request("chat-a", text, now)[0] == event["id"]
    assert images.history_request("chat-b", text, now)[0] == ""
    assert images.history_request("chat-a", "明天上午再配一张工位图", now) is None
    assert images.history_request("chat-a", "昨天上午不用配一张图了", now) is None
    assert images.history_request("chat-a", "昨天我画了一张工位图", now) is None
    assert "没有保存" in images.history_request("chat-a", "昨天上午有拍过工位吗", now)[1]
    p.config.life.schedule_enabled = False
    p.chat_context.messages["chat-a"] = text
    for _ in range(3):
        items = [{"item_type":"UserMessageItem", "parts":[{"type":"text","text":'<message msg_id="qa-one">'+text}]}]
        result = await p.planner_context(session_id="chat-a",items=items,tool_definitions=[])
        assert "已接单" in str(result)
        await asyncio.gather(*list(p.jobs))
    p.ctx.send.image.assert_awaited_once()
    assert "生成于" in images.history_request("chat-a", "这张是昨天上午拍的吗", now)[1]
    images.prepare_request("chat-a", "ask", "昨天上午有拍过工位吗", now, p.start_job)
    assert images.prepare_request("chat-a", "continue", "按上午的经历配一张", now, p.start_job) == "发送"
    await asyncio.gather(*list(p.jobs))
    assert images.prepare_request("chat-a", "selfie", "我想看看你", now, p.start_job) == "发送"
    await asyncio.gather(*list(p.jobs))
    assert p.ctx.send.image.await_count == 3


async def test_noon_shift_has_ended_before_afternoon_photo(plugin, monkeypatch):
    p = plugin
    now, _ = setup(p, monkeypatch)
    day = (now - timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    ids = []
    for label, start, end, kind in [("上午", 9, 12, "work"), ("下午", 13, 18, "work"), ("通勤", 12, 13, "travel")]:
        at = day.replace(hour=end)
        event = p.engine.record_experience(ExperienceInput(summary=f"{label}在公司办公室核对资料",
            occurred_at=at.isoformat(), source="simulated", evidence="已下班"), "chat-a", now)
        event["plan_id"] = label
        with p.store.db:
            p.store.put("plan", {"id": label, "start": day.replace(hour=start).isoformat(), "title": "办公", "kind": kind})
            p.store.put("experience", event)
        ids.append(event["id"])
    images = p.life_images
    assert images.history_request("chat-a", "按昨天上班经历配一张工位图", now)[0] == ""
    assert images.history_request("chat-a", "按昨天下午上班经历配一张工位图", now)[0] == ids[1]
