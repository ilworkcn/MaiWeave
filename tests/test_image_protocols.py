"""图片协议、参考图传递与用量解析。"""
import base64
import io
import json
import pytest
from test_plugin import plugin
from test_diary_images import PNG
from maiweave.integrations import diary_images


@pytest.mark.parametrize("protocol", ["OpenAI 图片编辑", "通义千问兼容接口", "OpenAI Chat 生图", "Gemini 原生", "DashScope 多模态"])
@pytest.mark.parametrize("reference", [False, True])
async def test_protocol_request_and_response(plugin, monkeypatch, protocol, reference):
    images = plugin.diary_publisher.images
    cfg = plugin.config.diary
    cfg.image_reference_protocol = protocol
    cfg.image_api_key = "test"
    cfg.image_model = "qwen-image-2.0-pro"
    encoded = base64.b64encode(PNG).decode()
    images.upload_reference(encoded)
    requests = []
    def respond(request, timeout):
        requests.append(request)
        if protocol == "Gemini 原生":
            result = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": encoded}}]}}],
                      "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 20, "totalTokenCount": 32}}
        elif protocol == "DashScope 多模态":
            result = {"output": {"choices": [{"message": {"content": [{"image": "data:image/png;base64," + encoded}]}}]}}
        elif protocol == "OpenAI Chat 生图":
            result = {"choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64," + encoded}}]}}]}
        else:
            result = {"data": [{"b64_json": encoded}]}
        return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr(diary_images, "urlopen", respond)
    data, tokens = images.request("窗边", images.reference_path if reference else "")
    assert data == PNG
    request = requests[0]
    assert request.full_url.endswith(images.request_endpoint(reference))
    if protocol == "OpenAI 图片编辑" and reference:
        assert b'filename="character.png"' in request.data
        assert b'1024x768' in request.data
    else:
        payload = json.loads(request.data)
        assert ("data:image/png;base64," in request.data.decode() or '"inlineData"' in request.data.decode()) == reference
        if protocol == "DashScope 多模态":
            assert payload["parameters"]["negative_prompt"] == cfg.image_negative_prompt
            assert payload["parameters"]["size"] == "1024*768"
        if protocol in {"OpenAI 图片编辑", "通义千问兼容接口"}:
            assert payload["size"] == "1024x768"
        if protocol == "OpenAI Chat 生图":
            assert "4:3 横向" in payload["messages"][0]["content"][0]["text"]
        if protocol == "Gemini 原生":
            assert payload["generationConfig"]["imageConfig"]["aspectRatio"] == "4:3"
            assert request.get_header("X-goog-api-key") == "test"
            assert "Authorization" not in request.headers
            assert tokens == {"input_tokens": 12, "output_tokens": 20, "total_tokens": 32}


@pytest.mark.parametrize("content", ["![图片](https://example.com/image.png)", "data:image/png;base64," + base64.b64encode(PNG).decode()])
async def test_chat_image_text_formats(plugin, monkeypatch, content):
    images = plugin.diary_publisher.images
    plugin.config.diary.image_reference_protocol = "OpenAI Chat 生图"
    def respond(request, timeout):
        if isinstance(request, str):
            assert request == "https://example.com/image.png"
            return io.BytesIO(PNG)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(diary_images, "urlopen", respond)
    assert images.request("窗边")[0] == PNG


async def test_qwen21_negative_prompt_in_text(plugin, monkeypatch):
    images = plugin.diary_publisher.images
    plugin.config.diary.image_reference_protocol = "DashScope 多模态"
    plugin.config.diary.image_model = "qwen-image-2.1-pro"
    assert not images.separate_negative()
    def respond(request, timeout):
        assert "negative_prompt" not in json.loads(request.data)["parameters"]
        return io.BytesIO(b'{"message":"test error"}')
    monkeypatch.setattr(diary_images, "urlopen", respond)
    with pytest.raises(ValueError, match="test error"):
        images.request("窗边")


@pytest.mark.parametrize("reference", [False, True])
async def test_unsloth_reference_and_authenticated_download(plugin, monkeypatch, reference):
    images = plugin.diary_publisher.images
    cfg = plugin.config.diary
    cfg.image_reference_protocol = "Unsloth 原生"
    cfg.image_base_url = "http://localhost:8888"
    cfg.image_api_key = "test-key"
    images.upload_reference(base64.b64encode(PNG).decode())
    calls = []
    def respond(request, timeout):
        calls.append(request)
        assert request.get_header("Authorization") == "Bearer test-key"
        if request.full_url.endswith("/file"):
            assert request.full_url.endswith("/gallery/generated-image/file")
            return io.BytesIO(PNG)
        assert request.full_url.endswith("/api/inference/images/generate")
        payload = json.loads(request.data)
        assert (payload["width"], payload["height"]) == (1024, 768)
        assert (payload.get("workflow") == "reference") == reference
        assert ("init_image" in payload) == reference
        return io.BytesIO(b'{"images":[{"id":"generated-image"}]}')
    monkeypatch.setattr(diary_images, "urlopen", respond)
    assert images.request("窗边", images.reference_path if reference else "") == (PNG, {})
    assert len(calls) == 2

@pytest.mark.parametrize("choice,expected", [("自定义", "2560x1440"), ("2048x2048", "2048x2048"), ("服务默认", None)])
async def test_image_size_config_reaches_provider(plugin, monkeypatch, choice, expected):
    cfg = plugin.config.diary
    cfg.image_size = choice
    cfg.image_custom_size = "2560x1440"
    encoded = base64.b64encode(PNG).decode()
    def respond(request, timeout):
        body = json.loads(request.data)
        assert body.get("size") == expected
        return io.BytesIO(json.dumps({"data": [{"b64_json": encoded}]}).encode())
    monkeypatch.setattr(diary_images, "urlopen", respond)
    assert plugin.diary_publisher.images.request("成品近景")[0] == PNG
