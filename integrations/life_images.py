"""把具体生活经历展示给当前聊天对象，复用图片与发送回执。"""
import asyncio
import json
import re
from datetime import datetime, timedelta
from uuid import uuid4
from weakref import WeakValueDictionary

from ..core.models import ExperienceInput
from .diary_images import DiaryImages
from .chat_context import brief


FINISH_IMAGE = "图片仍在生成，完成后自动发到当前聊天。只需等图时用无时间参数的 wait 结束本轮。对方另有问题时用 reply 回答并设 after_reply=stop；问进度时按正在准备回答。"
SENT_IMAGE = "图片已发到当前聊天。还有文字要说时用 reply 并设 after_reply=stop，否则用无时间参数的 wait 结束本轮。"


def asks_selfie(text):
    return re.fullmatch(r"(?:(?:我想)?看看你|(?:我想)?看看(?:现在|此刻)的你|想看你的自拍|给我发(?:一)?张自拍)[呀啊吧。！!？?\s]*", text)


def asks_progress(text, *, standalone=False):
    if standalone:
        return bool(re.fullmatch(r"(?:那张|那|这张|我的)?(?:图片?|照片|自拍)?(?:拍|画|生成|发|传)?"
                                 r"(?:好了吗|好了没|好了没有|完了吗|了吗|了没|怎么样了)[呀啊？?。！!\s]*", text.strip()))
    return bool(re.search(r"(?:图|照片|自拍).{0,6}(?:好了吗|好了没|拍好|发了吗|发了没|还没|怎么样了)|(?:拍|画|生成|传)好了[吗没]", text))


def deferred_picture(text):
    action = r"(?:拍|配|画|发|生成)"
    return bool(re.search(r"(?:不用|不要|别再?|不必)\s*(?:给我|再)?" + action, text)
                or re.search(r"(?:明天|后天|下次|等.{1,10}后).{0,18}" + action, text))


def incoming_photo_message(items):
    for item in reversed(items):
        if item.get("item_type") != "UserMessageItem":
            continue
        for part in item.get("parts", []):
            header = part.get("text", "").partition(">")[0]
            if header.startswith(("<plugin_proactive_task", "<internal_", "<subagent_result")):
                return "", ""
            if header.startswith("<message ") and 'is_self_message="true"' not in header:
                match = re.search(r'\bmsg_id="([^"]+)"', header)
                body = "".join(p.get("text", "") for p in item.get("parts", []) if p.get("type") == "text")
                return (match[1] if match else ""), MEDIA_DESCRIPTION.sub("", body.partition(">")[2].removesuffix("</message>")).strip()
    return "", ""


PHOTO_WORDS = re.compile(r"想看|看看|瞅瞅|瞧瞧|给我看|给你看|什么样|啥样|长啥样|照片|图片|拍张|拍一|自拍|发.{0,4}(张|图|来|给我)|photo|selfie|picture", re.I)
MEDIA_DESCRIPTION = re.compile(r"\[(?:图片|表情包|视频)(?:\s*[:：][^\]]*)?\]")
RESEND_PHOTO = re.compile(r"重发|再发一次|再发一遍|(?:原图|旧照|那张|上次|昨天|前天).{0,16}(?:再发|发我|发给我)|(?:再发|发我|发给我).{0,12}(?:原图|旧照|那张|上次|昨天|前天)")


def photo_turn(items, group=False):
    """找出本轮要图的消息，群里允许其他人插话。"""
    checked = 0
    searched = False
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        call = item.get("tool_call") or {}
        if not checked and call.get("func_name") == "tool_search":
            query = str(call.get("args", {}).get("query", ""))
            searched |= bool(re.search(r"maiweave_show_life|图片|照片|自拍|发图", query, re.I))
        if item.get("item_type") != "UserMessageItem":
            continue
        text = "".join(p.get("text", "") for p in item.get("parts", []) if p.get("type") == "text")
        header, _, body = text.partition(">")
        if header.startswith(("<plugin_proactive_task", "<internal_", "<subagent_result")):
            return searched, None, "", ""
        if not header.startswith("<message "):
            continue
        if 'is_self_message="true"' in header:
            return searched, None, "", ""
        body = MEDIA_DESCRIPTION.sub("", body.removesuffix("</message>")).strip()
        checked += 1
        if (PHOTO_WORDS.search(body) or RESEND_PHOTO.search(body)) and not deferred_picture(body):
            message = re.search(r'\bmsg_id="([^"]+)"', header)
            return True, index, message[1] if message else "", body
        if not group or checked == 5 or deferred_picture(body):
            break
    return searched or checked == 0, None, "", ""


def image_tool_relevant(items):
    return photo_turn(items)[0]


def distinct_image_calls(items, allowed=None):
    """执行本轮提供的图片操作，同一批次中每张图片只发送一次。"""
    seen = set()
    kept = []
    rejected = None
    for item in items:
        call = item.get("tool_call") or {}
        if item.get("item_type") == "FunctionCallItem" and call.get("func_name") == "maiweave_show_life":
            args = call.get("args") or {}
            action = args.get("action")
            if allowed is not None and (action not in allowed or
                                        (action == "send" and args.get("experience_id") not in allowed["send"])):
                rejected = item
                continue
            if action == "list" or (action == "send" and isinstance(args.get("experience_id"), str)):
                key = (action, args.get("experience_id") if action == "send" else "")
                if key in seen:
                    continue
                seen.add(key)
        kept.append(item)
    if rejected and not any(i.get("item_type") == "FunctionCallItem" for i in kept):
        # 只有失效图片操作时，用宿主的静默结束动作收尾。
        kept.append({**rejected, "tool_call": {**rejected["tool_call"], "func_name": "wait", "args": {}}})
    if any((i.get("tool_call") or {}).get("func_name") == "maiweave_show_life"
           and i["tool_call"].get("args", {}).get("action") == "send" for i in kept):
        kept = [i for i in kept if (i.get("tool_call") or {}).get("func_name") not in {"reply", "wait"}]
    return kept


def current_image_candidates(items, is_sent_image=None, request_index=None):
    """给 Planner 保留本轮的选图过程，上一轮的候选随消息翻篇。"""
    old = set()
    current = set()
    for index, item in enumerate(items):
        if item.get("item_type") == "UserMessageItem":
            if request_index is not None and index > request_index:
                continue
            text = next((p.get("text", "") for p in item.get("parts", []) if p.get("type") == "text"), "")
            if (text.startswith("<message ") and 'is_self_message="true"' not in text.split(">", 1)[0]
                    or text.startswith(("<plugin_proactive_task", "<internal_", "<subagent_result"))):
                header = text.partition(">")[0]
                message = re.search(r'\bmsg_id="([^"]+)"', header) if current and header.startswith("<message ") else None
                if message and is_sent_image and is_sent_image(message[1]):
                    continue
                old.update(current)
                current.clear()
        elif item.get("item_type") == "FunctionCallItem":
            call = item["tool_call"]
            if call.get("func_name") == "maiweave_show_life" and call.get("args", {}).get("action") in {"list", "send"}:
                current.add(call["call_id"])
    if not old:
        return items
    return [i for i in items if (i.get("tool_call") or {}).get("call_id") not in old and i.get("call_id") not in old]


def image_request_stage(items, is_sent_image=None, request_index=None, list_receipt=None):
    """从本轮回执识别已列出的候选和已受理的发图。"""
    calls = {}
    stage = ""
    for index, item in enumerate(items):
        kind = item.get("item_type")
        if kind == "UserMessageItem":
            if request_index is not None and index > request_index:
                continue
            text = next((p.get("text", "") for p in item.get("parts", []) if p.get("type") == "text"), "")
            header = text.split(">", 1)[0]
            incoming = text.startswith("<message ") and 'is_self_message="true"' not in header
            if incoming and stage == "sent" and is_sent_image:
                # 宿主同步 QQ 图片时，消息头可能只带回执编号。
                message = re.search(r'\bmsg_id="([^"]+)"', header)
                if message and is_sent_image(message[1]):
                    continue
            if incoming or text.startswith(("<plugin_proactive_task", "<internal_", "<subagent_result")):
                calls.clear()
                stage = ""
        elif kind == "FunctionCallItem":
            call = item.get("tool_call", {})
            if call.get("func_name") == "maiweave_show_life":
                calls[call.get("call_id")] = call.get("args", {}).get("action")
        elif kind == "FunctionCallOutputItem" and calls and item.get("call_id") in calls:
            if (stage != "sent" and list_receipt and list_receipt[0] == item["call_id"]
                    and calls[item["call_id"]] == "list" and list_receipt[1] is not None):
                stage = list_receipt[1] or "empty"
                continue
            output = item.get("output")
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except ValueError:
                    continue
            if isinstance(output, dict):
                action = calls[item["call_id"]]
                if action == "send" and output.get("status") in {"进行中", "正在生成，请稍等", "已发送", "失败"}:
                    stage = "sent"
                elif stage != "sent" and action == "list" and isinstance(output.get("items"), list):
                    stage = tuple(row["id"] for row in output["items"] if isinstance(row, dict) and row.get("id")) or "empty"
    return stage


class LifeImages:
    def __init__(self, engine, ctx, config):
        self.engine, self.ctx, self.config = engine, ctx, config
        self.store = engine.store
        self.images = DiaryImages(engine, ctx, config)
        self.pending_requests = set()
        self.planner_actions = {}
        self.list_receipts = {}
        self.group_photo_requests = {}
        self.turn_notes = {}
        self.send_locks = WeakValueDictionary()

    @staticmethod
    def content(event):
        return json.dumps(brief({key: event[key] for key in ("summary", "details", "source")}, 600, 6), ensure_ascii=False)

    def reusable(self, event, image):
        return bool(image.get("image_file") and image.get("content") == self.content(event))

    short_wait = 35

    def history_request(self, session, text, now):
        """识别明确的回顾配图与旧图查询，按本地日期和活动挑选经历。"""
        if deferred_picture(text) or RESEND_PHOTO.search(text):
            return None
        original = re.search(r"原照|旧照|拍过|拍了.*吗|有拍|有.{0,3}照片吗|当时拍的|拍的吗", text)
        render = re.search(r"(?:配|画|补|还原|生成)(?:个|一张|张|幅|一下|.{0,4}[图画])", text) and re.search(
            r"给我|帮我|请|想看|能否|能不能|可以|发来|发一|按.{1,30}(?:配|画|补|还原|生成)|^(?:配|画|补|还原|生成)", text)
        when = re.search(r"前天|昨天|今天|今早|上午|早上|中午|下午|傍晚|晚上|上次|当时|那会儿", text)
        if not (when or (original and re.search(r"这张|这幅", text))) or not (original or render):
            return None
        day = now.date() - timedelta(days=2 if "前天" in text else 1 if "昨天" in text else 0)
        dated = re.search(r"前天|昨天|今天|今早|上午|早上|中午|下午|傍晚|晚上", text)
        periods = {"上午|早上|今早": (5, 12), "中午": (11, 14), "下午": (12, 18), "傍晚|晚上": (17, 24)}
        window = next((span for key, span in periods.items() if re.search(key, text)), None)
        topic = re.sub(r"前天|昨天|今天|今早|上午|早上|中午|下午|傍晚|晚上|上次|当时|那会儿|经历|配一张|给我|拍过", " ", text)
        topic = re.sub(r"工位|办公桌|上班|公司", "办公", topic)
        terms = set(re.findall(r"(?=([\w]{2}))", topic))
        work_scene = re.search(r"工位|办公桌|上班|工作", text) and not re.search(r"路上|通勤|路途", text)
        lo = now.replace(hour=window[0] if window else 0, minute=0, second=0, microsecond=0)
        lo = lo.replace(year=day.year, month=day.month, day=day.day)
        hi = lo + timedelta(hours=(window[1] - window[0]) if window else 24)
        matches = []
        for event in self.store.all("experience"):
            if not self.engine.visible(event, session) or event["source"] == "dream":
                continue
            moment = self.engine.time(event["occurred_at"])
            plan = self.store.find("plan", event.get("plan_id", "")) or {}
            start = self.engine.time(plan["start"]) if plan.get("start") else moment
            if dated and (moment < lo or (start < moment and moment == lo) or start >= hi):
                continue
            material = event["summary"] + str(event["details"]) + plan.get("title", "")
            material = re.sub(r"上班|公司", "办公", material)
            score = sum(term in material for term in terms)
            if work_scene and plan.get("kind") == "work":
                score += 1
            if score:
                matches.append((score, event))
        if original and not render and re.search(r"这张|这幅", text):
            sent = self.context(session)
            if sent:
                event = self.store.find("experience", sent[-1]["experience_id"])
                matches = [(1, event)] if event and self.engine.visible(event, session) else []
        best = max((score for score, _ in matches), default=0)
        chosen = [e for score, e in matches if score == best]
        if len(chosen) != 1:
            names = "、".join(e["summary"][:32] for e in chosen[:3])
            return "", ("请问对方指的是哪一段：" + names if names else "当前可见经历里没有匹配的记录，请对方补充是哪一天、哪件事。")
        event = chosen[0]
        image = self.store.find("experience_image", event["id"]) or {}
        if original and not render:
            note = ("这段经历对应的生活配图生成于 " + image["image_at"] if image.get("image_file") and image.get("image_at")
                    else "这段经历没有保存过生活配图。")
            return "", note + " 本次只询问原照，按记录回答。"
        return event["id"], "按已完成的经历补画：" + event["summary"][:50]

    def prepare_request(self, session, message_id, text, now, start_job):
        previous = self.turn_notes.get(session)
        if previous and previous[0] == message_id:
            return previous[3]
        self.turn_notes.pop(session, None)
        query = text
        if previous and previous[3] == "澄清" and re.fullmatch(r"(?:就|是)?(?:上午|下午|中午|晚上|早上)(?:的|那班|那次|那个|那段|班|吧|呀|啊|[。！ ])*", text):
            query = previous[1] + "，" + text
        elif previous and previous[3] == "查询" and re.fullmatch(
                r"(?:那|就|请|帮我|给我|按|照|上午|下午|当时|那段|那次|的|经历|再|来)*(?:配|画|补)一张(?:图|吧|看看|给我|[。！ ])*", text):
            query = previous[1] + "，按那段经历" + text
        selfie = asks_selfie(text)
        if selfie:
            job = self.store.find("job", f"配图:{session}:{message_id}")
            if job:
                result = ("", "这条来信的自拍任务状态：" + job["status"])
            elif self.config.diary.image_service_enabled:
                event = self.selfie_subject(session, now)
                result = (event["id"], "按此刻所在地点和时间发一张麦麦自己的自拍。")
            else:
                result = ("", "图片生成服务已关闭，当前无法拍一张新的自拍。")
        else:
            result = self.history_request(session, query, now)
        if not result:
            return ""
        experience_id, note = result
        mode = "澄清" if note.startswith("请问对方") else "查询"
        if experience_id:
            job_id = f"配图:{session}:{message_id}"
            job = self.store.find("job", job_id)
            key = (session, experience_id)
            event = self.engine.owned("experience", experience_id, session)
            cached = self.reusable(event, self.store.find("experience_image", experience_id) or {})
            if job:
                note = "配图请求已经处理，任务状态：" + job["status"]
            elif not cached and not self.config.diary.image_service_enabled:
                note = "这段经历没有可复用的图，生图服务已关闭。"
            elif key in self.pending_requests:
                note = "这段经历已有图片正在准备，完成后自动发送。"
                mode = "发送"
            else:
                self.pending_requests.add(key)
                framing = note if selfie else "按经历发生时的场景补画。" + query
                start_job("发送麦麦的自拍" if selfie else "按已完成经历配图并发到当前聊天", self.requested(
                    experience_id, session, now, framing=framing, request_id=message_id), job_id=job_id)
                note = "已接单，完成后自动发图。" + note
                mode = "发送"
        self.turn_notes[session] = (message_id, text, note, mode)
        return mode

    def selfie_subject(self, session, now):
        location = self.engine.state()["location"]
        plan = self.engine.current(now)
        details = {"地点": location}
        if plan and self.engine.visible(plan, session):
            details["正在做的事"] = plan["title"]
        return self.engine.record_experience(ExperienceInput(summary=f"在{location}应邀准备自拍",
            occurred_at=now.isoformat(), source="observed", details=details, evidence="收到当前聊天的自拍请求"), session, now)

    def candidates(self, session, framing="", shared_id="", *, request="", now=None):
        if not self.config.diary.chat_images_enabled:
            return []
        rows = sorted((row for row in self.store.all("experience") if self.engine.visible(row, session)),
                      key=lambda row: self.engine.time(row["occurred_at"]), reverse=True)
        # 中文菜名和物件名常嵌在整句话里，用相邻字匹配经历的摘要和细节。
        terms = set(re.findall(r"(?=([\w]{2}))", framing.casefold()))

        deliveries = [r for r in self.store.all("image_delivery") if r["session_id"] == session and r["status"] == "已发送"]
        sent = {r["experience_id"] for r in deliveries}
        resend = RESEND_PHOTO.search(request)
        if resend:
            offset = 2 if "前天" in request else 1 if "昨天" in request else 0 if "今天" in request else None
            last = {r["experience_id"]: r["at"] for r in deliveries if offset is None or
                    (now.date() - self.engine.time(r["at"]).date()).days == offset}
            rows = [r for r in rows if r["id"] in last]
            rows.sort(key=lambda r: last[r["id"]], reverse=True)

        def relevance(row):
            content = row["summary"] + " " + json.dumps(row["details"], ensure_ascii=False)
            score = sum(term in content.casefold() for term in terms)
            shared = row["id"] == shared_id
            return score, shared, row["id"] not in sent
        rows.sort(key=relevance, reverse=True)
        candidates = []
        for row in rows:
            cached = self.reusable(row, self.store.find("experience_image", row["id"]) or {})
            if not cached and not self.config.diary.image_service_enabled:
                continue
            if resend and not cached:
                continue
            if row["id"] in sent and not resend and re.fullmatch(r"(?:给我)?(?:发|来|拍)(?:一)?张(?:照片|图|自拍)(?:看看)?[呀吧。！!？?\s]*", request):
                continue
            candidates.append({"id": row["id"], "summary": row["summary"][:160],
                               "details": brief(dict(list(row["details"].items())[:4]), 64, 2), "occurred_at": row["occurred_at"],
                               "sent_in_this_chat": row["id"] in sent, "has_image": cached, "action_hint": "可直接发送" if cached else "可根据这段经历生成"})
            if len(candidates) == 10:
                break
        return candidates

    def tool_definitions(self, definitions, session, items=(), turn=None):
        """按当前聊天可用的图片能力提供工具。"""
        enabled = self.config.life.enabled and self.config.diary.chat_images_enabled
        enabled = enabled and (self.config.diary.image_service_enabled or bool(self.candidates(session)))
        relevant, index, _, _ = turn if turn is not None else photo_turn(items)
        result = []
        for tool in definitions:
            function = tool.get("function", {})
            if function.get("name") == "maiweave_show_life":
                if not enabled or not relevant:
                    continue
                stage = image_request_stage(items, lambda mid: self.sent_image(session, mid), index, self.list_receipts.get(session))
                if stage in {"empty", "sent"}:
                    continue
                params = function.get("parameters", {})
                properties = dict(params.get("properties", {}))
                listed = isinstance(stage, tuple)
                properties["action"] = {"type": "string", "enum": ["send" if listed else "list"]}
                required = ["action"]
                if listed:
                    properties["experience_id"] = {"type": "string", "enum": list(stage)}
                    required.append("experience_id")
                else:
                    properties.pop("experience_id", None)
                description = "从本轮候选中选一张符合请求的图发送。" if listed else "先查本轮相关的生活经历和已有图片。"
                tool = {**tool, "function": {**function, "description": description,
                        "parameters": {**params, "properties": properties, "required": required}}}
            result.append(tool)
        return result

    def sent_image(self, session, message_id):
        return self.store.db.execute(
            """SELECT 1 FROM records WHERE category='image_delivery'
               AND json_extract(data,'$.session_id')=? AND json_extract(data,'$.message_id')=?
               AND json_extract(data,'$.status')='已发送' LIMIT 1""", (session, message_id)
        ).fetchone() is not None

    async def requested(self, experience_id, session, now, *, framing="", request_id=""):
        """追问图片的后台任务失败时，在原聊天告知结果。"""
        self.engine.owned("experience", experience_id, session)
        try:
            return await self.send(experience_id, session, now, framing=framing, request_id=request_id)
        except Exception:
            result = await self.ctx.send.text("这次图片没发出来，可以稍后再让我试试。", session,
                return_details=True, storage_message=True, sync_to_maisaka_history=True)
            if not result["sent"]:
                self.ctx.logger.error("生活图片失败提示发送失败：%s", result.get("error"))
            raise
        finally:
            self.pending_requests.discard((session, experience_id))

    async def send(self, experience_id, session, now, *, share_id="", framing="", since=None, request_id=""):
        if not self.config.diary.chat_images_enabled:
            raise ValueError("聊天图片已关闭，可在「插件配置 → 模型设置 → 图片用途」中开启")
        event = self.engine.owned("experience", experience_id, session)
        lock = self.send_locks.setdefault(experience_id, asyncio.Lock())
        if lock.locked() and since is None:
            since = now
        async with lock:
            if since is not None:
                for row in reversed(self.store.all("image_delivery")):
                    if (row["experience_id"] == experience_id and row["session_id"] == session
                            and (not share_id or row.get("share_id", "") in {"", share_id})
                            and row["status"] == "已发送" and self.engine.time(row["at"]) >= since):
                        return {"status": "已发送", "message_id": row["message_id"], "experience_id": experience_id,
                                "description": row["description"]}
            return await self._deliver(event, session, now, share_id, framing, request_id)

    async def _deliver(self, event, session, now, share_id, framing, request_id):
        experience_id = event["id"]
        image = self.store.find("experience_image", experience_id)
        content = self.content(event)
        if image and image["content"] != content:
            image = self.images.remove("experience_image", experience_id)
            image["content"] = content
            self.images.put("experience_image", image)
        if not image:
            image = {"id": experience_id, "status": "草稿", "session_id": event["session_id"],
                     "content": content}
            self.images.put("experience_image", image)
        if not image.get("image_file"):
            if framing:
                image["image_request"] = framing[:300]
                image.pop("image_prompt", None)
                image.pop("image_character_visible", None)
                image.pop("image_selfie", None)
                image.pop("image_environment", None)
                self.images.put("experience_image", image)
            image = await self.images.illustrate("experience_image", experience_id, now)
        delivery = {"id": uuid4().hex, "at": now.isoformat(), "session_id": session,
                    "experience_id": experience_id, "share_id": share_id, "status": "发送中",
                    "description": image["image_prompt"][:600], "error": ""}
        if request_id:
            delivery["request_message_id"] = request_id
        self.images.put("image_delivery", delivery)
        try:
            encoded = await asyncio.to_thread(self.images.original_base64, image)
            result = await self.ctx.send.image(encoded, session, return_details=True,
                                               storage_message=True, sync_to_maisaka_history=True)
            if not result["sent"]:
                raise RuntimeError(result.get("error") or "图片发送失败")
            delivery.update(status="已发送", message_id=result["message_id"], at=datetime.now(now.tzinfo).isoformat())
        except (Exception, asyncio.CancelledError) as error:
            delivery.update(status="发送失败", error=str(error) or "图片发送已中断")
            raise
        finally:
            self.images.put("image_delivery", delivery)
        return {"status": "已发送", "message_id": delivery["message_id"], "experience_id": experience_id,
                "description": delivery["description"]}

    def context(self, session):
        rows = [row for row in self.store.all("image_delivery") if row["session_id"] == session and row["status"] == "已发送"]
        return [{key: row[key] for key in ("experience_id", "description", "at")} for row in rows[-2:]]
