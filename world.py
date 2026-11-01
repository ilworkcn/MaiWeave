"""真实天气、订阅资讯和宿主联网资料。"""

from typing import Literal
from pydantic import BaseModel, Field
from .usage import generate

import asyncio
import hashlib
import json
import time
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
    category: Literal["park", "library"]
    description: str
    source_index: int = Field(ge=0)


class CityPlaces(BaseModel):
    places: list[CityPlace] = Field(max_length=4)


class World:
    def __init__(self, engine, context, config):
        self.engine, self.ctx, self.config = engine, context, config

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
                "count": sum(p.get("city_key") == cfg.city + "|" + cfg.country_code for p in self.engine.store.all("place"))}

    def planning_places(self):
        cfg = self.config.world
        places = self.engine.store.all("place")
        local = [p for p in places if p.get("city_key") == cfg.city + "|" + cfg.country_code]
        categories = {p["category"] for p in local} if cfg.places_enabled else set()
        return [p for p in places if not p.get("city_key") and p["id"] not in categories] + (local if cfg.places_enabled else [])

    async def discover_places(self, spatial, now, force=False):
        cfg, store = self.config.world, self.engine.store
        if not cfg.places_enabled:
            return
        if not cfg.city.strip():
            raise ValueError("请填写天气城市后搜索生活地点")
        city_key = cfg.city + "|" + cfg.country_code
        previous = store.find("world_status", "places")
        if not force and previous and previous.get("city_key") == city_key:
            if previous["status"] == "已收录" or not self.due("places", now, 30, False):
                return
        status = {"id": "places", "city_key": city_key, "attempted_at": now.isoformat(), "status": "搜索中", "error": ""}
        with store.db:
            store.put("world_status", status)
        try:
            queries = [f"{cfg.city} {cfg.country_code} {kind} 名称 地址" for kind in ("公园", "公共图书馆")]
            results = await asyncio.gather(*(self.ctx.web.search(query, max_results=5) for query in queries))
            sources = [source for result in results for source in result["results"]]
            query = "；".join(queries)
            prompt = ("从以下联网搜索结果中提取该城市适合角色日常活动的具体公园和公共图书馆，最多各两处，优先面向市民开放的地点。"
                      "输出 JSON places 数组，每项包含 name（来源中出现的完整地点名）、category（park或library）、"
                      "description（简短地点介绍）、source_index（依据结果的从0开始序号）。地点属于指定城市，"
                      "名称与说明依据搜索资料；结果为空时 places 填空数组。"
                      f"城市：{cfg.city}；国家地区：{cfg.country_code}；搜索资料：{json.dumps(sources, ensure_ascii=False)}")
            def validate(value):
                for item in value.places:
                    if item.source_index >= len(sources) or item.name not in json.dumps(sources[item.source_index], ensure_ascii=False):
                        raise ValueError("地点名称和来源序号需对应搜索结果中的原文")
            selected = await generate(self.ctx, store, self.config, "城市地点", prompt, CityPlaces, validate)
            for item in selected.places:
                existing = next((p for p in store.all("place") if p["name"] == item.name), None)
                place = spatial.save_place(item.name, item.category == "park", item.description, existing["id"] if existing else "")
                place.update(city_key=city_key, category=item.category, source_url=sources[item.source_index]["url"],
                             retrieved_at=now.isoformat(), source_query=query)
                with store.db:
                    store.put("place", place)
                if not existing:
                    for route in store.all("route"):
                        if item.category in {route["origin"], route["destination"]}:
                            other = route["destination"] if route["origin"] == item.category else route["origin"]
                            spatial.save_route(place["id"], other, route["minutes"], route["mode"])
            status["status"] = "已收录" if selected.places else "未找到地点"
        except Exception as error:
            status.update(status="搜索失败", error=str(error))
            raise
        finally:
            with store.db:
                store.put("world_status", status)
        return self.place_status()

    async def search(self, query: str, now):
        if not self.config.world.search_enabled:
            raise ValueError("请在天气与外部世界配置中启用宿主联网搜索")
        result = await self.ctx.web.search(query, max_results=5)
        record = {"id": hashlib.sha256((query + now.isoformat()).encode()).hexdigest()[:20],
                  "query": query, "retrieved_at": now.isoformat(), "results": result["results"], "source_kind": "宿主联网搜索"}
        with self.engine.store.db:
            self.engine.store.put("research", record)
        return record

    async def read(self, url: str, now):
        if not self.config.world.search_enabled:
            raise ValueError("请先启用宿主联网搜索")
        result = await self.ctx.web.fetch(url)
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
