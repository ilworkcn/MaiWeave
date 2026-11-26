"""空间随笔与聊天共用的图片生成、保存和用量记录。"""

import asyncio
import base64
import json
from math import gcd
import io
import re
from PIL import Image
from datetime import datetime
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from .usage import generate
from ..core.image_retention import ImageRetention


class RetryableImageError(RuntimeError):
    """生图请求遇到可重试的临时服务错误。"""


class CharacterDraft(BaseModel):
    appearance: str = Field(min_length=1, max_length=800)
    clothing: str = Field(min_length=1, max_length=400)


class IllustratedText(BaseModel):
    content: str = Field(min_length=1)
    image_prompt: str = Field(min_length=1, max_length=1200)
    character_visible: bool = False
    selfie: bool = False


SELFIE_COMPOSITION = "前置自拍成片：观看者的位置就是镜头，镜头距脸较近并略高于视线，人物看向镜头，脸靠画面一侧，头部自然裁切，轻微广角与略倾斜构图。持机侧肩膀靠近镜头，设备和持机手处于画外，另一只手可将物件举到脸旁前景展示。"


CHARACTER_FIELDS = ("image_character_enabled", "image_character_reference_enabled", "image_character_reference_url",
                    "image_character_appearance", "image_character_clothing", "image_style", "chat_image_style",
                    "image_negative_prompt")


CLOTHING_INSTRUCTION = "穿着优先采用本次拍摄要求和素材中明确的衣服，其次延续同一活动已有画面，最后结合室内外、天气和活动选择。户外按气温、体感和风雨搭配；室内按房间用途与已知供暖情况搭配，室外气温仅供出门参考。参考图提供人物外貌，衣服按本次场景调整。物件近景保持物件为主体，穿着只体现于入镜的袖口等局部。"


IMAGE_INSTRUCTION = "输出 JSON：content（正文）、image_prompt（1200字以内的中文画面描述）。配图选正文中的一个生活瞬间，着重场景、物件、光线与氛围，具体细节取自素材。"


class DiaryImages:
    request_timeout = 180
    download_timeout = 60
    retry_delay = 3

    def __init__(self, engine, context, config):
        self.engine, self.store, self.ctx, self.config = engine, engine.store, context, config
        saved = self.store.find("image_settings", "character")
        if saved is None:
            saved = {"id": "character", **self.character_settings()}
            with self.store.db:
                self.store.put("image_settings", saved)
        for key in CHARACTER_FIELDS:
            setattr(config.diary, key, saved[key])
        with self.store.db:
            for category in ("diary", "diary_publication", "experience_image", "image_test"):
                for row in self.store.all(category):
                    if row.get("image_status") == "生成中":
                        row.update(image_status="生成失败", image_error="配图已中断，可以重新生成", image_progress="")
                        self.store.put(category, row)

    def character_settings(self):
        return {key: getattr(self.config.diary, key) for key in CHARACTER_FIELDS}

    def save_character(self, values):
        if set(values) - set(CHARACTER_FIELDS):
            raise ValueError("这里只保存角色卡和画风")
        config = self.config.diary
        updated = type(config).model_validate({**config.model_dump(), **values})
        settings = {key: getattr(updated, key) for key in CHARACTER_FIELDS}
        with self.store.db:
            self.store.put("image_settings", {"id": "character", **settings})
        for key, value in settings.items():
            setattr(config, key, value)
        return {"message": "角色与图片设置已保存，新生成的图片会采用这些设置。"}

    def instruction(self):
        text = IMAGE_INSTRUCTION + (
            "同时输出 selfie 布尔值：用户要求前置自拍、或本次画面选用前置自拍时为 true，"
            "物件随手拍、旁观抓拍和对镜拍照为 false。画面描述只安排主体、动作、场景和视角，画风由用户设置提供。"
            "自拍时单手持机、另一只手展示物件；物件随手拍采用第一人称近景或俯拍，物件为主体，按需展示局部手部，脸和身体留在画外；看做事的样子可用旁观抓拍。"
            "沿用本次用户的拍摄要求，场景与时段取自素材。人物入镜时，在画面描述中写明选定衣服的款式和颜色，供同一活动延续。" + CLOTHING_INSTRUCTION)
        if self.config.diary.image_character_enabled:
            text += ("同时输出 character_visible 布尔值：画面包含麦麦本人时为 true，物件或风景特写为 false。"
                     "根据本次展示要求和生活素材选择构图：看书签、饭菜等物件时聚焦物件；"
                     "看麦麦本人、做某事的样子或合照时让麦麦入镜。人物只表现素材中的活动，外貌由角色卡统一提供。")
        return text

    def environment(self, category, row, now):
        """按配图对应的经历取环境，同一活动沿用已有画面。"""
        parts = []
        moment = now
        location = ""
        if category == "experience_image":
            event = self.store.get("experience", row["id"])
            moment = self.engine.time(event["occurred_at"])
            request = row.get("image_request", "")
            current_selfie = re.search(r"现在|此刻|这会儿", request) and re.search(r"自拍|看你|本人|对镜", request)
            if current_selfie:
                moment = now
                location = self.engine.state()["location"]
                parts.append("本次拍摄的是此刻的麦麦。")
            plan = self.store.find("plan", event.get("plan_id", "")) or {}
            location = location or event["details"].get("地点", "") or plan.get("location", "")
            parts.append(("拍摄时间：" if current_selfie else "经历时间：") + moment.isoformat())
            if event.get("plan_id") and not current_selfie:
                related = {e["id"] for e in self.store.all("experience")
                           if e.get("plan_id") == event["plan_id"] and e["id"] != event["id"]
                           and self.engine.visible(e, event["session_id"])
                           and self.engine.time(e["occurred_at"]) <= moment}
                previous = [image for image in self.store.all("experience_image")
                            if image["id"] in related and image.get("image_file")]
                if previous:
                    latest = max(previous, key=lambda image: image["image_at"])
                    parts.append("同一活动已有画面，延续其中的穿着：" + latest["image_prompt"][:600])
        elif category == "diary_publication" and row.get("day", row["id"]) != now.date().isoformat():
            return "回顾场景的环境和穿着取自正文及本次画面描述。"
        else:
            parts.append("当前本地时间：" + now.isoformat() + "。正文标明了场景时间时，按正文取景。")
        if location:
            place = next((p for p in self.store.all("place") if location in {p["id"], p["name"]}), None)
            parts.append("经历地点：" + location[:120])
            if place:
                parts.append("环境：" + ("户外" if place["outdoor"] else "室内") + "；" + place["description"][:240])
        weather = self.store.find("world", "weather") if self.config.world.weather_enabled else None
        identity = "|".join((self.config.world.weather_provider, self.config.world.city, self.config.world.country_code))
        if weather and (weather.get("source", "Open-Meteo") != self.config.world.weather_provider or weather.get("query", identity) != identity):
            weather = None
        if weather and abs((moment - self.engine.time(weather["retrieved_at"])).total_seconds()) <= self.config.world.weather_minutes * 120:
            current = weather["current"]
            parts.append("所在地室外天气参考：" + json.dumps({"城市": weather["city"], "天气": weather["label"],
                "观测时间": weather["observed_at"], "气温℃": current["temperature_2m"],
                "体感℃": current.get("apparent_temperature"), "风速km/h": current.get("wind_speed_10m")}, ensure_ascii=False))
        return "\n".join(parts)

    def character_prompt(self, visible):
        config = self.config.diary
        if not config.image_character_enabled or not visible:
            return ""
        parts = ["画面中的麦麦使用以下角色设定："]
        if config.image_character_appearance.strip():
            parts.append("稳定外貌：" + config.image_character_appearance.strip())
        if config.image_character_clothing.strip():
            parts.append("常用穿着参考，结合本次环境和活动调整：" + config.image_character_clothing.strip())
        return "\n".join(parts) if len(parts) > 1 else ""

    def put(self, category, row):
        with self.store.db:
            self.store.put(category, row)

    def original_base64(self, row):
        path = Path(self.ctx.paths.data_dir) / row["image_file"]
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def encoded(self, row, size=None):
        with Image.open(Path(self.ctx.paths.data_dir) / row["image_file"]) as image:
            if size:
                image.thumbnail((size, size))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=80 if size else 90)
        return base64.b64encode(output.getvalue()).decode()

    def preview(self, row, size=512):
        return "data:image/jpeg;base64," + self.encoded(row, size) if row.get("image_file") else ""

    def editable(self, category, day):
        row = self.store.get(category, day)
        if row.get("image_status") == "生成中" or row["status"] in {"生成中", "发布中", "已发布", "待核对"}:
            raise ValueError("请等待当前操作完成，已发布的文案可在发布记录中查看")
        if not row.get("content"):
            raise ValueError("请先完成正文")
        return row

    async def character_draft(self):
        personality = await self.ctx.config.get("personality")
        prompt = ("根据下面的 MaiBot 人设，为图片生成整理一张角色外观卡，输出 JSON："
                  "appearance（稳定外貌，800字以内）和 clothing（常用穿着，400字以内）。"
                  "沿用人设明确的外貌与身份；缺少的外貌、服装细节可以按人设补成一份供用户修改的建议。"
                  "只描述可见特征，保持简洁，人物性格用于选择衣着气质。\n人设："
                  + json.dumps(personality, ensure_ascii=False)[:6000])
        return await generate(self.ctx, self.store, self.config, "配图提示词", prompt, CharacterDraft)

    @property
    def reference_path(self):
        return Path(self.ctx.paths.data_dir) / "character_reference.jpg"

    def reference_source(self):
        return self.reference_path if self.reference_path.exists() else self.config.diary.image_character_reference_url.strip()

    def reference_view(self):
        path = self.reference_path
        return {"uploaded": path.exists(), "preview": "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode() if path.exists() else "",
                "url": self.config.diary.image_character_reference_url, "enabled": self.config.diary.image_character_enabled and self.config.diary.image_character_reference_enabled,
                "protocol": self.config.diary.image_reference_protocol, "model": self.config.diary.image_model}

    def upload_reference(self, content):
        data = base64.b64decode(content, validate=True)
        if len(data) > 512 * 1024:
            raise ValueError("参考图请压缩到 512 KiB 以内")
        with Image.open(io.BytesIO(data)) as picture:
            picture.thumbnail((1024, 1024))
            output = io.BytesIO()
            picture.convert("RGB").save(output, format="JPEG", quality=88)
        self.reference_path.write_bytes(output.getvalue())
        return {"message": "参考图已保存，空间和聊天共用；新的人物图片会采用这张参考图。"}

    def reference_upload(self, action, content=""):
        temporary = self.reference_path.with_suffix(".upload")
        if action == "begin":
            temporary.write_bytes(b"")
        elif action == "append":
            chunk = base64.b64decode(content, validate=True)
            if temporary.stat().st_size + len(chunk) > 512 * 1024:
                raise ValueError("参考图请压缩到 512 KiB 以内")
            with temporary.open("ab") as output:
                output.write(chunk)
        elif action == "finish":
            result = self.upload_reference(base64.b64encode(temporary.read_bytes()).decode())
            temporary.unlink()
            return result
        elif action == "abort":
            temporary.unlink(missing_ok=True)
        return {"message": "参考图上传中"}

    def remove_reference(self):
        self.reference_path.unlink(missing_ok=True)
        return {"message": "上传的参考图已移除。"}

    def separate_negative(self):
        config = self.config.diary
        return config.image_reference_protocol == "DashScope 多模态" and not config.image_model.startswith(("qwen-image-2.1", "wan2.7-"))

    def request_endpoint(self, reference=False):
        protocol = self.config.diary.image_reference_protocol
        if protocol == "Unsloth 原生":
            return "/api/inference/images/generate"
        if protocol == "OpenAI Chat 生图":
            return "/chat/completions"
        if protocol == "Gemini 原生":
            return f"/v1beta/models/{self.config.diary.image_model}:generateContent"
        if protocol == "DashScope 多模态":
            return "/api/v1/services/aigc/multimodal-generation/generation"
        return "/images/edits" if reference and protocol == "OpenAI 图片编辑" else "/images/generations"

    def request(self, prompt, reference=""):
        config = self.config.diary
        payload = {"model": config.image_model, "prompt": prompt, "n": 1,}
        size = config.requested_image_size
        if size:
            payload["size"] = size
            width, height = map(int, size.split("x"))
            divisor = gcd(width, height)
            ratio = f"{width // divisor}:{height // divisor}"
        endpoint = self.request_endpoint(bool(reference))
        reference_data = ""
        content_type = "application/json"
        data = json.dumps(payload).encode()
        if reference:
            if isinstance(reference, Path):
                original = reference.read_bytes()
            else:
                if not reference.startswith(("http://", "https://")):
                    raise ValueError("参考图片地址请填写 HTTP(S) 图片直链")
                with urlopen(reference, timeout=self.download_timeout) as response:
                    original = response.read()
            with Image.open(io.BytesIO(original)) as picture:
                output = io.BytesIO()
                picture.convert("RGB").save(output, format="PNG")
            reference_data = base64.b64encode(output.getvalue()).decode()
            if config.image_reference_protocol == "通义千问兼容接口":
                payload["image"] = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
                data = json.dumps(payload).encode()
            elif config.image_reference_protocol == "OpenAI 图片编辑":
                boundary = "maiweave-" + uuid4().hex
                parts = []
                for key, value in payload.items():
                    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
                parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="character.png"\r\nContent-Type: image/png\r\n\r\n').encode() + output.getvalue() + b"\r\n")
                data = b"".join(parts) + f"--{boundary}--\r\n".encode()
                endpoint = "/images/edits"
                content_type = "multipart/form-data; boundary=" + boundary
        headers = {"Authorization": "Bearer " + config.image_api_key, "Content-Type": content_type}
        protocol = config.image_reference_protocol
        if protocol == "Unsloth 原生":
            payload = {"prompt": prompt, "batch_size": 1}
            if size:
                payload.update(width=width, height=height)
            if reference_data:
                payload.update(workflow="reference", init_image="data:image/png;base64," + reference_data)
            data = json.dumps(payload).encode()
        elif protocol == "OpenAI Chat 生图":
            content = [{"type": "text", "text": (f"请生成一张 {ratio} {'横向' if width > height else '竖向' if height > width else '方形'}图片，尺寸 {size}。" if size else "") + prompt}]
            if reference_data:
                content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + reference_data}})
            data = json.dumps({"model": config.image_model, "messages": [{"role": "user", "content": content}]}).encode()
        elif protocol == "Gemini 原生":
            parts = [{"text": prompt}]
            if reference_data:
                parts.append({"inlineData": {"mimeType": "image/png", "data": reference_data}})
            generation = {"responseModalities": ["TEXT", "IMAGE"]}
            if size:
                image_config = {"aspectRatio": ratio}
                if max(width, height) >= 2048:
                    image_config["imageSize"] = "4K" if max(width, height) >= 3840 else "2K"
                generation["imageConfig"] = image_config
            data = json.dumps({"contents": [{"role": "user", "parts": parts}],
                               "generationConfig": generation}).encode()
            headers = {"x-goog-api-key": config.image_api_key, "Content-Type": "application/json"}
        elif protocol == "DashScope 多模态":
            content = [{"text": prompt}]
            if reference_data:
                content.append({"image": "data:image/png;base64," + reference_data})
            parameters = {"n": 1}
            if size:
                parameters["size"] = size.replace("x", "*")
            if config.image_negative_prompt.strip() and self.separate_negative():
                parameters["negative_prompt"] = config.image_negative_prompt.strip()
            data = json.dumps({"model": config.image_model, "input": {"messages": [{"role": "user", "content": content}]},
                               "parameters": parameters}).encode()
        request = Request(config.image_base_url.rstrip('/') + endpoint, data=data, headers=headers)
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            exhausted = any(word in detail.lower() for word in ("insufficient_quota", "insufficient_balance", "quota_exceeded", "余额不足", "额度不足"))
            kind = RetryableImageError if error.code in {429, 502, 503, 504} and not exhausted else RuntimeError
            raise kind(f"图片生成服务 {endpoint} 返回 HTTP {error.code}：{detail}") from error
        except (TimeoutError, ConnectionError) as error:
            raise RetryableImageError(f"图片生成服务连接超时或中断：{error}") from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, ConnectionError)):
                raise RetryableImageError(f"图片生成服务连接超时或中断：{error.reason}") from error
            raise
        tokens = result.get("usage", {})
        if protocol == "Unsloth 原生":
            image_id = result["images"][0]["id"]
            download = Request(config.image_base_url.rstrip('/') + f"/api/inference/images/gallery/{image_id}/file",
                               headers={"Authorization": "Bearer " + config.image_api_key})
            with urlopen(download, timeout=self.download_timeout) as response:
                return response.read(), tokens
        if protocol == "Gemini 原生":
            parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            item = next(({"b64_json": part["inlineData"]["data"]} for part in parts if "inlineData" in part), {})
            usage = result.get("usageMetadata", {})
            tokens = {"input_tokens": usage.get("promptTokenCount"), "output_tokens": usage.get("candidatesTokenCount"),
                      "total_tokens": usage.get("totalTokenCount")}
        elif protocol == "DashScope 多模态":
            output = result.get("output", {})
            parts = output.get("choices", [{}])[0].get("message", {}).get("content", [])
            item = next(({"url": part["image"]} for part in parts if "image" in part), {})
        elif protocol == "OpenAI Chat 生图":
            message = result.get("choices", [{}])[0].get("message", {})
            content = message.get("content") or ""
            parts = message.get("images", []) + (content if isinstance(content, list) else [])
            item = next(({"url": part["image_url"]["url"]} for part in parts if "image_url" in part), {})
            if not item and isinstance(content, str):
                match = re.search(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]+|!\[[^\]]*\]\((https?://[^\s)]+)\)", content)
                if match:
                    item = {"url": match.group(1) or match.group(0)}
        else:
            item = result.get("data", [{}])[0]
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"], validate=True)
        elif item.get("url", "").startswith("data:image/"):
            data = base64.b64decode(item["url"].split(",", 1)[1], validate=True)
        elif item.get("url"):
            with urlopen(item["url"], timeout=self.download_timeout) as response:
                data = response.read()
        else:
            raise ValueError("图片生成服务未返回图片：" + str(result.get("error") or result.get("message") or "请检查模型与接口格式"))
        return data, tokens

    async def illustrate(self, category, day, now, prompt=""):
        if not self.config.diary.image_service_enabled:
            raise ValueError("图片生成服务已关闭，请在模型设置中开启")
        row = self.editable(category, day)
        config = self.config.diary
        if not all(value.strip() for value in (config.image_base_url, config.image_api_key, config.image_model)):
            raise ValueError("请在模型设置的图片生成服务中填写生图接口地址、API Key 和模型")
        supplied_prompt = prompt.strip()
        if supplied_prompt:
            row.pop("image_selfie", None)
        prompt = supplied_prompt or row.get("image_prompt", "")
        if len(prompt) > 1200:
            raise ValueError("画面描述请控制在1200字以内")
        row.update(image_status="生成中", image_error="", image_progress="正在生成", image_attempts=0)
        self.put(category, row)
        started = perf_counter()
        usage = {"id": uuid4().hex, "at": datetime.now(ZoneInfo(self.config.life.timezone)).isoformat(),
                 "feature": {"experience_image": "生活图片", "image_test": "生图测试"}.get(category, "空间配图"), "task": "images/generations", "model": config.image_model,
                 "status": "失败", "error": "", "prompt_tokens": None, "completion_tokens": None,
                 "total_tokens": None, "prompt_cache_hit_tokens": None}
        try:
            if "image_environment" not in row:
                row["image_environment"] = self.environment(category, row, now)
            request_time = now.astimezone(ZoneInfo(self.config.life.timezone)).isoformat(timespec="minutes")
            environment = f"本次生成时间：{request_time}。画面光线以场景发生时间为准。\n" + row["image_environment"]
            decide_character = (config.image_character_enabled and category != "image_test"
                                and (bool(supplied_prompt) or "image_character_visible" not in row))
            if not prompt or decide_character:
                instructions = self.instruction() + "正文沿用下面的内容，只补充画面描述：" + row["content"][:4000]
                instructions += "\n环境与穿着参考：" + environment
                if prompt:
                    instructions += "\n本次画面描述：" + prompt
                if row.get("image_request"):
                    instructions += "\n本次展示要求：" + row["image_request"][:300]
                idea = await generate(self.ctx, self.store, self.config, "配图提示词", instructions, IllustratedText)
                prompt = prompt or idea.image_prompt
                row["image_selfie"] = idea.selfie
                if config.image_character_enabled:
                    row["image_character_visible"] = idea.character_visible or idea.selfie
                else:
                    row.pop("image_character_visible", None)
            row["image_prompt"] = prompt
            character_scene = category != "image_test" or bool(row.get("test_scene"))
            style = config.chat_image_style if category == "experience_image" or row.get("test_scene") else config.image_style
            character = self.character_prompt(row.get("image_character_visible", False)) if character_scene else ""
            full_prompt = prompt + ("\n" + character if character else "") + "\n画面风格：" + style
            full_prompt += "\n穿着安排：" + CLOTHING_INSTRUCTION + "\n环境与穿着参考：" + environment
            mirror = re.compile(r"对镜|镜前自拍|镜子自拍|镜中自拍")
            if mirror.search(row.get("image_request", "") + prompt) or (row.get("image_selfie") and mirror.search(style)):
                full_prompt += "\n拍摄构图：对着镜子取景，镜中人物手持手机，手机和倒影一起入镜。"
            elif character_scene and row.get("image_selfie"):
                full_prompt += "\n拍摄构图：" + SELFIE_COMPOSITION
            if row.get("image_request"):
                full_prompt += "\n本次用户的拍摄要求：" + row["image_request"][:300]
            if config.image_negative_prompt.strip() and not self.separate_negative():
                full_prompt += "\n负面提示词（画面中避免）：" + config.image_negative_prompt.strip()
            reference = ""
            if config.image_character_enabled and config.image_character_reference_enabled and row.get("image_character_visible") and character_scene:
                reference = self.reference_source()
                if not reference:
                    raise ValueError("请在角色与图片页面上传参考图，或填写图片直链")
                full_prompt += "\n人物外貌以参考图为准，包括脸部、发型、发色和眼睛；文字角色卡补充参考图未体现的细节。动作、场景和构图按本次画面描述，穿着采用本次画面描述和环境安排。"
            usage["task"] = self.request_endpoint(bool(reference)).lstrip("/")
            retry_count = config.image_retry_count
            for attempt in range(retry_count + 1):
                row["image_attempts"] = usage["attempts"] = attempt + 1
                self.put(category, row)
                try:
                    data, tokens = await asyncio.to_thread(self.request, full_prompt, reference) if reference else await asyncio.to_thread(self.request, full_prompt)
                    break
                except RetryableImageError as error:
                    if attempt == retry_count:
                        raise
                    row["image_progress"] = f"正在重试（第 {attempt + 1}/{retry_count} 次，第 {attempt + 2} 次请求）"
                    self.put(category, row)
                    self.ctx.logger.warning("图片生成请求失败，%s 秒后重试（%s/%s）：%s", self.retry_delay, attempt + 1, retry_count, error)
                    await asyncio.sleep(self.retry_delay)
            if len(data) > self.config.retention.image_max_mb * 1024 * 1024:
                raise ValueError("生成的图片超过配图容量上限，请调整容量设置")
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime, extension = "image/png", "png"
            elif data.startswith(b"\xff\xd8"):
                mime, extension = "image/jpeg", "jpg"
            elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                mime, extension = "image/webp", "webp"
            else:
                raise ValueError("图片生成服务返回的内容不是 PNG、JPEG 或 WebP 图片")
            relative = f"diary_images/{category}-{uuid4().hex}.{extension}"
            path = Path(self.ctx.paths.data_dir) / relative
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, data)
            previous = row.get("image_file")
            if category == "image_test":
                with Image.open(io.BytesIO(data)) as picture:
                    row["returned_size"] = f"{picture.width}x{picture.height}"
            row.update(image_status="已生成", image_file=relative, image_mime=mime, image_model=config.image_model,
                       image_content=row["content"], image_at=now.isoformat())
            self.put(category, row)
            if previous:
                await asyncio.to_thread((Path(self.ctx.paths.data_dir) / previous).unlink)
            usage.update(status="成功", prompt_tokens=tokens.get("input_tokens", tokens.get("prompt_tokens")),
                         completion_tokens=tokens.get("output_tokens", tokens.get("completion_tokens")), total_tokens=tokens.get("total_tokens"))
        except (Exception, asyncio.CancelledError) as error:
            row.update(image_status="生成失败", image_error=str(error) or "配图已中断")
            usage["error"] = row["image_error"]
            raise
        finally:
            row["image_progress"] = ""
            self.put(category, row)
            usage["seconds"] = round(perf_counter() - started, 3)
            self.put("model_usage", usage)
        ImageRetention(self.store, self.config, self.ctx.paths.data_dir).clean(now)
        return self.store.get(category, day)

    def remove(self, category, day):
        row = self.editable(category, day)
        if row.get("image_file"):
            (Path(self.ctx.paths.data_dir) / row["image_file"]).unlink()
        for key in list(row):
            if key.startswith("image_"):
                row.pop(key)
        self.put(category, row)
        return row
