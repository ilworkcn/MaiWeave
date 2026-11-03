"""真实天气、订阅资讯和 MaiBot 联网资料。"""

from pydantic import BaseModel, Field
from .usage import generate

import asyncio
import hashlib
import json
import time
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
import re
from html import unescape
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "MaiWeave/0.3"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                return response.read()
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == 2 or (isinstance(error, HTTPError) and error.code not in {408, 429, 500, 502, 503, 504}):
                raise
            time.sleep(2 ** attempt)


def weather_label(code: int) -> str:
    labels = {0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴", 45: "雾", 48: "冻雾",
              51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
              61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
              71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "小阵雨", 81: "阵雨", 82: "强阵雨",
              85: "阵雪", 86: "强阵雪", 95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹"}
    return labels.get(code, f"气象代码 {code}")


def plain(value: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", value)).strip()


def parse_feed(data: bytes, source_url: str, now: str) -> list[dict]:
    root = ElementTree.fromstring(data)
    records = []
    for item in root.iter():
        if item.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        fields = {child.tag.split("}")[-1]: child for child in item}
        def value(key):
            node = fields.get(key)
            return "" if node is None else "".join(node.itertext()).strip()
        link_node = fields.get("link")
        link = (link_node.get("href") or value("link")) if link_node is not None else source_url
        title = plain(value("title"))
        records.append({"id": hashlib.sha256((source_url + link + title).encode()).hexdigest()[:20],
                        "title": title, "url": link, "summary": plain(value("description") or value("summary") or value("content"))[:2000],
                        "published_at": value("pubDate") or value("published") or value("updated"),
                        "retrieved_at": now, "source": source_url, "source_kind": "外部订阅"})
    return records[:20]


class CityPlace(BaseModel):
    name: str
    category: str = Field(min_length=1)
    outdoor: bool = False
    description: str
    source_index: int = Field(ge=0)


class CityPlaces(BaseModel):
    places: list[CityPlace] = Field(max_length=4)


class World:
    def __init__(self, engine, context, config):
        self.engine, self.ctx, self.config = engine, context, config
        self.host_config_path = Path.cwd() / "config" / "model_config.toml"

    async def search_status(self):
        """读取 MaiBot 联网任务分配，连接结果来自实际搜索或网页读取。"""
        enabled = self.config.world.search_enabled
        state = {"enabled": enabled, "configured": False, "ready": False, "service": ""}
        try:
            raw = await asyncio.to_thread(self.host_config_path.read_text, encoding="utf-8")
            config = tomllib.loads(raw)
        except (OSError, tomllib.TOMLDecodeError) as error:
            state["configuration"] = f"MaiBot 联网配置读取失败：{error}"
        else:
            selected = config.get("model_task_config", {}).get("web_search", {}).get("model_list", [])
            name = selected[0] if selected else ""
            service = config.get("web", {}).get("services", {}).get(name)
            state["service"] = name
            if not name:
                state["configuration"] = "请在 MaiBot「模型管理 → 功能分配 → 联网搜索」选择服务"
            elif service is None:
                state["configuration"] = f"请在 MaiBot 模型管理中添加联网服务「{name}」"
            elif not service.get("server_ref" if service.get("adapter") == "stepsearch_mcp" else "api_key", "").strip():
                state["configuration"] = f"请补充联网服务「{name}」的 MCP 连接名称或 API 密钥"
            else:
                state.update(configured=True, configuration=f"MaiBot 已配置：{name}")
        state["ready"] = enabled and state["configured"]
        previous = self.engine.store.find("world_status", "host_web")
        connection = "等待首次联网调用"
        if previous and previous["service"] == state["service"]:
            connection = f"上次{previous['operation']}：{previous['status']} · {previous['attempted_at'].replace('T', ' ')[:19]}"
            if previous["error"]:
                connection += f" · {previous['error']}"
        state["connection"] = connection
        switch = "插件联网已开启" if enabled else "请在插件「天气与见闻」开启 MaiBot 联网搜索"
        state["summary"] = f"{switch}；{state['configuration']}；{connection}"
        return state

    async def request_web(self, operation, now, **arguments):
        status = await self.search_status()
        if not status["ready"]:
            raise ValueError(status["configuration"] if status["enabled"] else "请在插件「天气与见闻」开启 MaiBot 联网搜索")
        record = {"id": "host_web", "service": status["service"], "operation": "搜索" if operation == "search" else "网页读取",
                  "attempted_at": now.isoformat(), "status": "连接中", "error": ""}
        with self.engine.store.db:
            self.engine.store.put("world_status", record)
        try:
            result = await getattr(self.ctx.web, operation)(**arguments)
        except Exception as error:
            record.update(status="连接失败", error=str(error))
            raise
        else:
            record["status"] = "连接成功"
            return result
        finally:
            with self.engine.store.db:
                self.engine.store.put("world_status", record)

    def due(self, key: str, now, minutes: int, force: bool) -> bool:
        record = self.engine.store.find("world_status", key)
        return force or not record or (now - self.engine.time(record["attempted_at"])).total_seconds() >= minutes * 60

    async def refresh(self, now, force: bool = False):
        cfg, store = self.config.world, self.engine.store
        jobs = []
        errors = []
        weather_place = store.find("world", "weather_place")
        city_changed = not weather_place or weather_place["query"] != cfg.city + "|" + cfg.country_code
        if cfg.weather_enabled and cfg.city and (city_changed or self.due("weather", now, cfg.weather_minutes, force)):
            jobs.append(("weather", self.weather))
        if cfg.news_enabled and cfg.feed_urls and self.due("news", now, cfg.news_minutes, force):
            jobs.append(("news", self.news))
        for key, method in jobs:
            status = {"id": key, "attempted_at": now.isoformat(), "status": "更新中", "error": ""}
            with store.db:
                store.put("world_status", status)
            try:
                await method(now)
                status["status"] = "已更新"
            except Exception as error:
                status.update(status="更新失败", error=str(error))
                errors.append(str(error))
                self.ctx.logger.exception("外部世界更新失败：%s", key)
            with store.db:
                store.put("world_status", status)
        if errors:
            raise RuntimeError("；".join(errors))

    async def weather(self, now):
        cfg = self.config.world
        if not cfg.city:
            raise ValueError("请先在配置页填写天气城市")
        place = self.engine.store.find("world", "weather_place")
        identity = cfg.city + "|" + cfg.country_code
        if not place or place["query"] != identity:
            query = {"name": cfg.city, "count": 1, "language": "zh", "format": "json"}
            if cfg.country_code:
                query["countryCode"] = cfg.country_code
            raw = json.loads(await asyncio.to_thread(download, "https://geocoding-api.open-meteo.com/v1/search?" + urlencode(query)))
            if not raw.get("results"):
                raise ValueError("未找到天气城市，请使用城市全称或补充国家地区代码")
            place = {"id": "weather_place", "query": identity, **raw["results"][0]}
            place["id"] = "weather_place"
            with self.engine.store.db:
                self.engine.store.put("world", place)
        query = {"latitude": place["latitude"], "longitude": place["longitude"],
                 "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,is_day",
                 "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
                 "timezone": self.config.life.timezone, "forecast_days": 3}
        url = "https://api.open-meteo.com/v1/forecast?" + urlencode(query)
        raw = json.loads(await asyncio.to_thread(download, url))
        current, daily = raw["current"], raw["daily"]
        forecast = [{"day": day, "weather": weather_label(daily["weather_code"][i]),
                     "high": daily["temperature_2m_max"][i], "low": daily["temperature_2m_min"][i],
                     "rain_probability": daily["precipitation_probability_max"][i],
                     "sunrise": daily["sunrise"][i], "sunset": daily["sunset"][i]} for i, day in enumerate(daily["time"])]
        result = {"id": "weather", "city": place["name"], "region": place.get("admin1", ""),
                  "latitude": place["latitude"], "longitude": place["longitude"], "current": current,
                  "label": weather_label(current["weather_code"]), "forecast": forecast,
                  "observed_at": current["time"], "retrieved_at": now.isoformat(), "source": "Open-Meteo", "url": url,
                  "outdoor_suitable": current["precipitation"] == 0 and current["wind_speed_10m"] < 40 and -5 < current["apparent_temperature"] < 35}
        with self.engine.store.db:
            self.engine.store.put("world", result)
        return result

    async def news(self, now):
        records = []
        for url in self.config.world.feed_urls:
            data = await asyncio.to_thread(download, url)
            records.extend(parse_feed(data, url, now.isoformat()))
        with self.engine.store.db:
            for record in records:
                self.engine.store.put("news", record)
        return records

    def place_status(self):
        cfg = self.config.world
        record = self.engine.store.find("world_status", "places") or {}
        current = record.get("city_key") == cfg.city + "|" + cfg.country_code
        return {"enabled": cfg.places_enabled, "city": cfg.city, "country_code": cfg.country_code,
                "status": (record.get("status", "尚未搜索") if current else "尚未搜索") if cfg.places_enabled else "已关闭",
                "error": record.get("error", "") if current else "", "checked_at": record.get("attempted_at", "") if current else "",
                "activity": record.get("activity", "") if current else "",
                "count": sum(p.get("city_key") == cfg.city + "|" + cfg.country_code for p in self.engine.store.all("place"))}

    def planning_places(self):
        cfg = self.config.world
        places = self.engine.store.all("place")
        local = [p for p in places if p.get("city_key") == cfg.city + "|" + cfg.country_code]
        categories = {p["category"] for p in local} if cfg.places_enabled else set()
        return [p for p in places if not p.get("city_key") and p["id"] not in categories] + (local if cfg.places_enabled else [])

    async def discover_places(self, spatial, now, force=False, activity=""):
        cfg, store = self.config.world, self.engine.store
        if not cfg.places_enabled:
            return []
        if not cfg.city.strip():
            raise ValueError("请填写天气城市后搜索生活地点")
        activity = activity.strip()
        if not activity:
            current = self.engine.current(now)
            activity = current["title"] if current else "、".join(self.config.life.interests) or "日常休闲活动"
        city_key = cfg.city + "|" + cfg.country_code
        keyword = re.sub(r"[\W_]+", "", activity.casefold())
        cache_id = hashlib.sha256((city_key + "|" + keyword).encode()).hexdigest()[:20]
        cached = store.find("place_search", cache_id)
        if cached is None:
            known = [p for p in store.all("place") if p.get("city_key") == city_key
                     and re.sub(r"[\W_]+", "", p.get("search_request", "").casefold()) == keyword]
            if known:
                cached = {"id": cache_id, "place_ids": [p["id"] for p in known],
                          "searched_at": min(p["retrieved_at"] for p in known)}
                with store.db:
                    store.put("place_search", cached)
        if cached and not force:
            lifetime = 30 * 86400 if cached["place_ids"] else 6 * 3600
            if (now - self.engine.time(cached["searched_at"])).total_seconds() < lifetime:
                places = [store.find("place", place_id) for place_id in cached["place_ids"]]
                places = [p for p in places if p and p.get("city_key") == city_key]
                if places or not cached["place_ids"]:
                    with store.db:
                        store.put("world_status", {"id": "places", "city_key": city_key, "activity": activity,
                                  "attempted_at": now.isoformat(), "status": "已使用缓存" if places else "暂无地点（缓存）", "error": ""})
                    return places
        status = {"id": "places", "city_key": city_key, "activity": activity, "attempted_at": now.isoformat(), "status": "搜索中", "error": ""}
        with store.db:
            store.put("world_status", status)
        try:
            query = f"{cfg.city} {cfg.country_code} {activity} 地点 名称 地址"
            result = await self.request_web("search", now, query=query, max_results=8)
            sources = result["results"]
            prompt = ("根据活动需求，从联网搜索结果中选择最多三处适合前往的当地具体地点，按适合程度排序。"
                      "输出 JSON places 数组，每项包含 name（来源中出现的完整地点名）、category（自由填写地点类别）、"
                      "outdoor（活动是否主要在室外）、description（根据来源简述适合本次活动的原因）、"
                      "source_index（依据结果的从0开始序号）。类别可随活动变化，例如餐厅、咖啡馆、商场、展馆、运动场等。"
                      "选择指定城市的具体场所，名称和介绍依据搜索资料；结果为空时 places 填空数组。"
                      f"活动需求：{activity}；城市：{cfg.city}；国家地区：{cfg.country_code}；搜索资料：{json.dumps(sources, ensure_ascii=False)}")
            def validate(value):
                for item in value.places:
                    if item.source_index >= len(sources) or item.name not in json.dumps(sources[item.source_index], ensure_ascii=False):
                        raise ValueError("地点名称和来源序号需对应搜索结果中的原文")
            selected = await generate(self.ctx, store, self.config, "城市地点", prompt, CityPlaces, validate)
            collected = []
            for item in selected.places:
                existing = next((p for p in store.all("place") if p["name"] == item.name), None)
                place = spatial.save_place(item.name, item.outdoor, item.description, existing["id"] if existing else "")
                place.update(city_key=city_key, category=item.category, source_url=sources[item.source_index]["url"],
                             retrieved_at=now.isoformat(), source_query=query, search_request=activity)
                with store.db:
                    store.put("place", place)
                if not existing:
                    spatial.save_route(place["id"], "street", 15, "步行")
                collected.append(place)
            with store.db:
                store.put("place_search", {"id": cache_id, "place_ids": [p["id"] for p in collected],
                          "searched_at": now.isoformat()})
            status["status"] = "已收录" if collected else "未找到地点"
        except Exception as error:
            status.update(status="搜索失败", error=str(error))
            raise
        finally:
            with store.db:
                store.put("world_status", status)
        return collected

    async def search(self, query: str, now):
        result = await self.request_web("search", now, query=query, max_results=5)
        record = {"id": hashlib.sha256((query + now.isoformat()).encode()).hexdigest()[:20],
                  "query": query, "retrieved_at": now.isoformat(), "results": result["results"], "source_kind": "MaiBot 联网搜索"}
        with self.engine.store.db:
            self.engine.store.put("research", record)
        return record

    async def read(self, url: str, now):
        result = await self.request_web("fetch", now, url=url)
        record = {"id": hashlib.sha256(url.encode()).hexdigest()[:20], "url": url, "title": result["title"],
                  "content": result["content"][:12000], "retrieved_at": now.isoformat(), "source_kind": "外部网页"}
        with self.engine.store.db:
            self.engine.store.put("reading", record)
        return record

    def context(self, now):
        weather = self.engine.store.find("world", "weather")
        if weather:
            weather = dict(weather)
            weather["stale"] = (now - self.engine.time(weather["retrieved_at"])).total_seconds() > self.config.world.weather_minutes * 120
        news = sorted(self.engine.store.all("news"), key=lambda n: n["retrieved_at"], reverse=True)[:5]
        readings = [{"title": r["title"], "url": r["url"], "retrieved_at": r["retrieved_at"], "content": r["content"][:1500]}
                    for r in self.engine.store.all("reading")[-2:]]
        return {"weather": weather, "news": news, "readings": readings}
