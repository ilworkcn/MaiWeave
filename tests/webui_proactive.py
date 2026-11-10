"""用实机概览数据检查主动交流卡的桌面和窄屏布局。"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
data = json.loads(Path(sys.argv[1]).read_text('utf-8'))
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.add_init_script('window.maibot={ready:Promise.resolve(),query:async()=>('+json.dumps(data, ensure_ascii=False)+'),navigate:()=>{}};')
    for width in (1280, 390):
        page.set_viewport_size({'width':width,'height':900})
        page.goto((root/'webui/overview.html').as_uri())
        page.wait_for_function("document.getElementById('loading').hidden")
        assert page.locator('#error').is_hidden(), page.locator('#error').inner_text()
        page.locator('#shareSummary').get_by_text('今天已发送',exact=False).first.wait_for()
        assert not errors, errors
        assert page.locator('#error').is_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '页面出现横向滚动'
        page.screenshot(path=str(out/f'proactive-{width}.png'), full_page=True)
    browser.close()
print('主动交流概览在桌面和窄屏均正常显示')
