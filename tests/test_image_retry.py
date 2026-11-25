"""图片重试与聊天、主动分享、空间及配置测试的联动。"""
import asyncio
import io
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
import pytest
from test_plugin import plugin
from test_diary_images import PNG
from test_life_images import setup
from maiweave.integrations.diary_images import RetryableImageError


@pytest.mark.parametrize("error,retry", [(TimeoutError(),True),(ConnectionResetError(),True),(URLError(TimeoutError()),True),
    (HTTPError('https://example.com',504,'timeout',{},io.BytesIO(b'timeout')),True),
    (HTTPError('https://example.com',429,'limited',{},io.BytesIO(b'rate limit')),True),
    (HTTPError('https://example.com',429,'quota',{},io.BytesIO(b'insufficient_quota')),False),
    (HTTPError('https://example.com',401,'key',{},io.BytesIO(b'bad key')),False),
    (HTTPError('https://example.com',400,'parameters',{},io.BytesIO(b'bad size')),False)])
def test_request_classifies_service_errors(plugin, monkeypatch, error, retry):
    monkeypatch.setattr('maiweave.integrations.diary_images.urlopen',Mock(side_effect=error))
    plugin.config.diary.image_base_url='https://example.com/v1'
    with pytest.raises(RuntimeError) as caught:plugin.diary_publisher.images.request('测试图片')
    assert isinstance(caught.value,RetryableImageError)==retry


@pytest.mark.parametrize("entry", ['追问','自然交流','主动表达','空间','配置测试'])
async def test_retry_shared_paths_send_once(plugin,monkeypatch,entry):
    p=plugin
    now,event=setup(p,monkeypatch)
    images=p.diary_publisher.images
    monkeypatch.setattr(type(images),'retry_delay',0)
    request=Mock(side_effect=[RetryableImageError('HTTP 504'),(PNG,{})])
    monkeypatch.setattr(type(images),'request',lambda self,*args:request(*args))
    if entry=='追问':
        await p.show_life('send',event['id'],stream_id='chat-a')
        await asyncio.gather(*list(p.jobs))
    elif entry in ('自然交流','主动表达'):
        with p.store.db:p.store.put('share_receipt',{'id':'share','session':'chat-a','created':now.isoformat(),'sent_at':now.isoformat(),'mode':entry,'outcome':'实际已发送','experience_id':event['id'],'image_requested':True})
        await p.living.share_images(now)
        await p.living.share_images(now)
        assert len(p.living.receipts.sent_times('chat-a',now.tzinfo))==1
    elif entry=='空间':
        with p.store.db:p.store.put('diary_publication',{'id':'day','status':'草稿','content':'桌上的茶'})
        await images.illustrate('diary_publication','day',now,'桌上的茶')
    else:
        await p.ui_test_image()
        await asyncio.gather(*list(p.jobs))
        assert (await p.ui_image_test())['attempts']==2
    assert request.call_count==2
    assert request.call_args_list[0]==request.call_args_list[1]
    assert p.ctx.send.image.await_count==(1 if entry in ('追问','自然交流','主动表达') else 0)
    p.ctx.send.text.assert_not_awaited()
    usages=[r for r in p.store.all('model_usage') if r['feature'] in ('生活图片','空间配图','生图测试')]
    assert len(usages)==1 and usages[0]['attempts']==2 and usages[0]['status']=='成功'


@pytest.mark.parametrize('retries',[1,3])
async def test_retry_failure_reports_once_and_keeps_busy(plugin,monkeypatch,retries):
    p=plugin
    now,event=setup(p,monkeypatch)
    monkeypatch.setattr(p.life_images, "short_wait", 0.01)
    images=p.life_images.images
    p.config.diary.image_retry_count=retries
    request=Mock(side_effect=RetryableImageError('HTTP 504'))
    monkeypatch.setattr(type(images),'request',lambda self,*args:request(*args))
    monkeypatch.setattr(type(images),'retry_delay',0.05)
    p.ctx.send.text.return_value={'sent':True}
    await p.show_life('send',event['id'],stream_id='chat-a')
    while request.call_count==0:await asyncio.sleep(0)
    await asyncio.sleep(0.01)
    row=p.store.get('experience_image',event['id'])
    assert row['image_status']=='生成中' and '重试' in row['image_progress']
    repeated=await p.show_life('send',event['id'],stream_id='chat-a')
    assert repeated['status']=='正在生成，请稍等'
    with pytest.raises(ValueError):images.remove('experience_image',event['id'])
    await asyncio.gather(*list(p.jobs))
    assert request.call_count==retries+1
    p.ctx.send.text.assert_awaited_once()
    p.ctx.send.image.assert_not_awaited()
    assert p.store.get('experience_image',event['id'])['image_status']=='生成失败'


async def test_reference_and_prompt_are_reused_on_retry(plugin,monkeypatch):
    p=plugin
    now,event=setup(p,monkeypatch)
    images=p.life_images.images
    p.config.diary.image_character_enabled=True
    p.config.diary.image_character_reference_enabled=True
    monkeypatch.setattr(images,'reference_source',lambda:'https://example.com/character.png')
    p.ctx.llm.generate.return_value={'success':True,'response':'{"content":"书签","image_prompt":"拿着书签","character_visible":true,"selfie":true}'}
    request=Mock(side_effect=[RetryableImageError('timeout'),(PNG,{})])
    monkeypatch.setattr(type(images),'request',lambda self,*args:request(*args))
    monkeypatch.setattr(type(images),'retry_delay',0)
    await p.life_images.send(event['id'],'chat-a',now)
    assert request.call_args_list[0]==request.call_args_list[1]
    assert request.call_args.args[1]=='https://example.com/character.png'
    assert p.ctx.llm.generate.await_count==1
    p.ctx.send.image.assert_awaited_once()


async def test_delivery_failure_does_not_retry_generation(plugin,monkeypatch):
    p=plugin
    now,event=setup(p,monkeypatch)
    request=Mock(return_value=(PNG,{}))
    monkeypatch.setattr(type(p.life_images.images),'request',lambda self,*args:request(*args))
    p.ctx.send.image.return_value={'sent':False,'error':'QQ 连接中断'}
    with pytest.raises(RuntimeError,match='QQ'):await p.life_images.send(event['id'],'chat-a',now)
    assert request.call_count==1
    p.ctx.send.image.assert_awaited_once()
    assert p.store.get('experience_image',event['id'])['image_file']


def test_restart_clears_retry_progress(plugin):
    from maiweave.integrations.diary_images import DiaryImages
    p=plugin
    with p.store.db:p.store.put('image_test',{'id':'latest','status':'草稿','image_status':'生成中','image_progress':'正在重试（第 2 次请求）'})
    DiaryImages(p.engine,p.ctx,p.config)
    row=p.store.get('image_test','latest')
    assert row['image_status']=='生成失败' and row['image_progress']==''


@pytest.mark.parametrize("retries", [0, 2, 3])
async def test_configured_retry_limit(plugin, monkeypatch, retries):
    now, event = setup(plugin, monkeypatch)
    images = plugin.life_images.images
    plugin.config.diary.image_retry_count = retries
    request = Mock(side_effect=RetryableImageError("服务超时"))
    monkeypatch.setattr(type(images), "request", lambda self, *args: request(*args))
    monkeypatch.setattr(type(images), "retry_delay", 0)
    with pytest.raises(RuntimeError):
        await plugin.life_images.send(event["id"], "chat-a", now)
    assert request.call_count == retries + 1
    plugin.ctx.send.image.assert_not_awaited()


async def test_default_retry_last_attempt_sends_once(plugin, monkeypatch):
    now, event = setup(plugin, monkeypatch)
    assert plugin.config.diary.image_retry_count == 3
    images = plugin.life_images.images
    request = Mock(side_effect=[RetryableImageError("超时")] * 3 + [(PNG, {})])
    monkeypatch.setattr(type(images), "request", lambda self, *args: request(*args))
    monkeypatch.setattr(type(images), "retry_delay", 0)
    await plugin.life_images.send(event["id"], "chat-a", now)
    assert request.call_count == 4
    assert all(call == request.call_args_list[0] for call in request.call_args_list)
    plugin.ctx.send.image.assert_awaited_once()


def test_retry_setting_is_in_advanced(plugin):
    schema = plugin.get_webui_config_schema()
    advanced = next(tab for tab in schema["layout"]["tabs"] if tab["id"] == "advanced")
    assert "image_retry" in advanced["sections"]
    section = schema["sections"]["image_retry"]
    assert section["name"] == "diary"
    assert "image_retry_count" in section["fields"]
