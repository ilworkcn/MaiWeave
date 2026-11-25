"""配图环境、穿着接续和图片复用。"""
from datetime import timedelta
from unittest.mock import Mock
import pytest
from test_plugin import plugin
from test_life_images import setup
from test_diary_images import PNG


def weather(p, now, temperature=-8):
    p.config.world.weather_enabled = True
    with p.store.db:
        p.store.put("world", {"id": "weather", "city": "测试城", "label": "晴", "observed_at": now.isoformat(),
            "retrieved_at": now.isoformat(), "current": {"temperature_2m": temperature, "apparent_temperature": temperature-2, "wind_speed_10m": 12}})


@pytest.mark.parametrize("location,expected", [("公园", "户外"), ("厨房", "室内")])
async def test_environment_reaches_prompt_and_image(plugin, monkeypatch, location, expected):
    p=plugin
    now,event=setup(p,monkeypatch)
    weather(p,now)
    event["details"].update(地点=location,穿着="白色毛衣")
    p.life_images.images.put("experience",event)
    request=Mock(return_value=(PNG,{}))
    monkeypatch.setattr(type(p.life_images.images),'request',lambda self,*args:request(*args))
    await p.life_images.send(event['id'],'chat-a',now,framing='白色毛衣，展示手工作品')
    prompt=request.call_args.args[0]
    assert expected in prompt and '气温℃' in prompt and '-8' in prompt
    assert '白色毛衣' in prompt and '室外气温仅供出门参考' in prompt
    llm=p.ctx.llm.generate.call_args.args[0]
    assert expected in llm and '白色毛衣' in llm
    stamp = "本次生成时间：" + now.isoformat(timespec="minutes")
    assert stamp in llm and stamp in prompt
    weather(p,now,30)
    await p.life_images.send(event['id'],'chat-a',now)
    assert request.call_count==1


def test_old_experience_and_old_publication_use_original_material(plugin,monkeypatch):
    now,event=setup(plugin,monkeypatch)
    weather(plugin,now)
    event['occurred_at']=(now-timedelta(days=2)).isoformat()
    plugin.life_images.images.put('experience',event)
    context=plugin.life_images.images.environment('experience_image',{'id':event['id']},now)
    assert '气温℃' not in context
    context=plugin.life_images.images.environment('diary_publication',{'id':(now-timedelta(days=1)).date().isoformat()},now)
    assert '气温℃' not in context


def test_same_activity_keeps_visible_outfit(plugin,monkeypatch):
    p=plugin
    now,event=setup(p,monkeypatch)
    event['plan_id']='activity-test'
    p.life_images.images.put('experience',event)
    for key,session,outfit in [('visible','chat-a','绿色外套'),('private','chat-other','私聊独有服装')]:
        p.life_images.images.put('experience',{**event,'id':key,'session_id':session})
        p.life_images.images.put('experience_image',{'id':key,'image_file':'example.png','image_at':now.isoformat(),'image_prompt':outfit})
    context=p.life_images.images.environment('experience_image',{'id':event['id']},now)
    assert '绿色外套' in context and '私聊独有服装' not in context


async def test_new_trial_refreshes_weather(plugin,monkeypatch):
    import asyncio
    p=plugin
    now,event=setup(p,monkeypatch)
    weather(p,now)
    await p.ui_test_image(scene='自拍',prompt='公园里散步')
    await asyncio.gather(*list(p.jobs))
    first=p.store.get('image_test','latest')['image_environment']
    weather(p,now,30)
    await p.ui_test_image(scene='物件近景',prompt='厨房里展示饭菜')
    await asyncio.gather(*list(p.jobs))
    second=p.store.get('image_test','latest')['image_environment']
    assert '-8' in first and '30' in second and '-8' not in second

async def test_current_mirror_selfie_uses_current_time_and_visible_phone(plugin, monkeypatch):
    now, event = setup(plugin, monkeypatch)
    images = plugin.life_images.images
    event["occurred_at"] = (now - timedelta(days=2)).isoformat()
    images.put("experience", event)
    request = Mock(return_value=(PNG, {}))
    monkeypatch.setattr(type(images), "request", lambda self, *args: request(*args))
    await plugin.life_images.send(event["id"], "chat-a", now, framing="现在拍一张对镜自拍")
    prompt = request.call_args.args[0]
    assert "拍摄时间：" + now.isoformat() in prompt
    assert "手机和倒影一起入镜" in prompt
    assert "设备和持机手处于画外" not in prompt
