"""天气来源、免费字段和失败刷新间隔回归。"""
import json
from datetime import timedelta
from urllib.error import HTTPError
from io import BytesIO
import pytest
from test_features import life
from test_plugin import plugin


def responses(url):
    if "now.json" in url:
        return json.dumps({"results": [{"location": {"name": "测试城", "path": "测试地区"},
            "now": {"text": "晴", "code": "0", "temperature": "18"},
            "last_update": "2026-10-31T12:00:00+08:00"}]}).encode()
    return json.dumps({"results": [{"daily": [{"date": "2026-10-31", "text_day": "晴", "high": "20", "low": "12"}]}]}).encode()


async def test_free_weather_and_provider_switch(life, monkeypatch):
    x = life
    x.cfg.world.weather_provider = "心知天气"
    x.cfg.world.weather_api_key = "test-key"
    monkeypatch.setattr("maiweave.integrations.world.download", responses)
    result = await x.world.weather(x.now)
    assert result["source"] == "心知天气" and result["outdoor_suitable"]
    assert result["current"]["apparent_temperature"] is None
    assert result["current"]["wind_speed_10m"] is None
    assert result["forecast"][0]["high"] == 20
    assert "test-key" not in json.dumps(result)
    x.cfg.world.weather_provider = "Open-Meteo"
    assert x.world.context(x.now)["weather"] is None


async def test_initial_failure_waits_for_refresh_interval(life, monkeypatch):
    calls = []
    def failed(url):
        calls.append(url)
        raise TimeoutError("天气握手超时")
    monkeypatch.setattr("maiweave.integrations.world.download", failed)
    with pytest.raises(RuntimeError, match="天气握手超时"):
        await life.world.refresh(life.now)
    await life.world.refresh(life.now + timedelta(minutes=1))
    assert len(calls) == 1
    life.cfg.world.city = "另一座城"
    with pytest.raises(RuntimeError):
        await life.world.refresh(life.now + timedelta(minutes=2))
    assert len(calls) == 2


async def test_seniverse_key_error_and_partial_forecast(life, monkeypatch):
    life.cfg.world.weather_provider = "心知天气"
    with pytest.raises(ValueError, match="API 密钥"):
        await life.world.weather(life.now)
    life.cfg.world.weather_api_key = "test-key"
    def failed(url):
        if "now.json" in url:
            return responses(url)
        raise HTTPError(url, 403, "Forbidden", {}, BytesIO(b'{"status":"permission denied","status_code":"AP010006"}'))
    monkeypatch.setattr("maiweave.integrations.world.download", failed)
    with pytest.raises(RuntimeError, match="AP010006"):
        await life.world.refresh(life.now)
    weather = life.world.context(life.now)["weather"]
    assert weather["current"]["temperature_2m"] == 18 and not weather["forecast"]
    assert "test-key" not in life.store.get("world_status", "weather")["error"]


async def test_free_weather_webui_and_image_context(plugin, monkeypatch):
    plugin.config.world.weather_enabled = True
    plugin.config.world.weather_provider = "心知天气"
    plugin.config.world.weather_api_key = "test-key"
    monkeypatch.setattr("maiweave.integrations.world.download", responses)
    await plugin.world.weather(plugin.now())
    view = await plugin.ui_world()
    assert view["source"] == "心知天气" and "18" in view["summary"]
    assert (await plugin.ui_record_support("images"))["items"] == []
    assert await plugin.ui_record_support("retention") == await plugin.ui_retention()


def test_upgrade_0120_keeps_weather_and_adds_provider():
    from maibot_sdk.config import rebuild_plugin_config_data
    from maiweave.plugin import create_plugin
    instance = create_plugin()
    defaults = instance.get_default_config()
    old = instance.get_default_config()
    old['plugin']['config_version'] = '0.12.0'
    old['world'].pop('weather_provider')
    old['world'].pop('weather_api_key')
    old['world'].update(city='测试城市', weather_minutes=90)
    upgraded = rebuild_plugin_config_data(defaults, old)
    instance.set_plugin_config(upgraded)
    assert instance.config.plugin.config_version == '0.13.1'
    assert instance.config.world.city == '测试城市'
    assert instance.config.world.weather_minutes == 90
    assert instance.config.world.weather_provider == 'Open-Meteo'
    assert instance.config.world.weather_api_key == ''
