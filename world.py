"""真实天气、订阅资讯和宿主联网资料。"""

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
        if cfg.weather_enabled and cfg.city and self.due("weather", now, cfg.weather_minutes, force):
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
