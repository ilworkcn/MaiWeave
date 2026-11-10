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
        for row in data['share_summary']:
            page.locator('#shareSummary').get_by_role('heading', name=row['session_name'], exact=True).first.wait_for()
            assert not page.locator('#shareSummary').get_by_text(row['session'], exact=True).is_visible()
        assert page.locator('#shareMode').inner_text() == '当前主动交流模式：' + data['share_settings']['mode']
        if data['share_settings']['mode'] == '自然交流':
            assert '可能选择静默，本轮不发送消息' in page.locator('#shareContext').inner_text()
        assert '预计下次检查' in page.locator('#shareSettings').inner_text()
        assert '最近判断' in page.locator('#shareSummary').inner_text()
        assert not errors, errors
        assert page.locator('#error').is_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '页面出现横向滚动'
        if width > 850:
            chart = page.locator('.chart-card').bounding_box()
            book = page.locator('.book-card').bounding_box()
            assert abs(book['y'] - chart['y'] - chart['height'] - 18) < 1
            summary = page.locator('.accumulation-card').bounding_box()
            assert abs(summary['y'] - book['y'] - book['height'] - 18) < 1
        else:
            diary = page.locator('.diary-card').bounding_box()
            summary = page.locator('.accumulation-card').bounding_box()
            assert abs(summary['y'] - diary['y'] - diary['height'] - 18) < 1
        page.locator('#proactiveCard').screenshot(path=str(out/f'card-{width}.png'))
        page.locator('.life-columns').screenshot(path=str(out/f'layout-{width}.png'))
        page.screenshot(path=str(out/f'proactive-{width}.png'), full_page=True)
    browser.close()
print('主动交流概览在桌面和窄屏均正常显示')
