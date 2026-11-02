"""真实文件解析及书籍阅读与日程、日记、对话的接续测试。"""
import base64
import io
import json
from datetime import timedelta
from zipfile import ZipFile
import pytest
from maiweave.books import parse_book
from test_plugin import plugin

TEXT = '# 第一章 风\n风吹过窗前的银杏树。\n# 第二章 灯\n夜里一盏灯照着书页。'

async def upload(plugin, text=TEXT):
    row = await plugin.ui_book_upload('begin', filename='银杏书.md', author='测试作者')
    raw = text.encode('utf-8')
    for offset in range(0, len(raw), 7):
        await plugin.ui_book_upload('append', upload_id=row['id'], content=base64.b64encode(raw[offset:offset+7]).decode())
    return await plugin.ui_book_upload('finish', upload_id=row['id'])

def answer(plugin, quote='风吹过窗前的银杏树。'):
    plugin.ctx.llm.generate.return_value = {'success':True,'response':json.dumps({'summary':'窗前的银杏树随风摆动。','thoughts':'想起安静的下午。','quote':quote,'question':'下一章会写什么？'},ensure_ascii=False)}

async def begin_reading(plugin, book):
    now=plugin.now()
    plan=await plugin.ui_book_action('schedule',book_id=book['id'],minutes=15)
    plugin.engine.advance(now+timedelta(seconds=2))
    answer(plugin)
    await plugin.books.process(now+timedelta(seconds=2))
    return now,plan

async def test_upload_read_finish_context_diary_and_next_chapter(plugin):
    book=await upload(plugin)
    assert book['chapter_count']==2 and book['author']=='测试作者'
    now,plan=await begin_reading(plugin,book)
    assert plugin.store.get('book',book['id'])['read_characters']==0
    assert plugin.store.get('book_note',plan['id'])['status']=='等待活动结束'
    assert plugin.books.context()['notes']==[]
    end=now+timedelta(minutes=16)
    plugin.engine.advance(end)
    await plugin.books.process(end)
    b=plugin.store.get('book',book['id'])
    assert b['chapter']==1 and b['offset']==0
    note=plugin.books.context()['notes'][0]
    assert note['quote']=='风吹过窗前的银杏树。'
    plugin.now=lambda:end+timedelta(seconds=1)
    assert note['quote'] in plugin.context_text('group')
    assert plugin.diary.material(end.date(),end+timedelta(seconds=1))['阅读笔记']
    event=plugin.store.get('experience',plugin.store.get('plan',plan['id'])['experience_id'])
    assert event['source']=='observed' and event['details']['书名']=='银杏书'
    calls=plugin.ctx.llm.generate.await_count
    assert (await plugin.continuity.reflect(plan['id'],end))['status']=='阅读笔记由书架整理'
    assert plugin.ctx.llm.generate.await_count==calls
    await plugin.books.process(end+timedelta(seconds=1))
    assert plugin.store.get('book',book['id'])==b
    p=await plugin.ui_book_action('schedule',book_id=book['id'],minutes=15)
    plugin.engine.advance(end+timedelta(seconds=3));answer(plugin,'夜里一盏灯照着书页。')
    await plugin.books.process(end+timedelta(seconds=3))
    assert '夜里一盏灯' in plugin.ctx.llm.generate.call_args.args[0]
    plugin.engine.advance(end+timedelta(minutes=16));await plugin.books.process(end+timedelta(minutes=16))
    assert plugin.store.get('book',book['id'])['status']=='已读完'
    assert any(u['feature']=='书籍阅读' for u in plugin.store.all('model_usage'))

async def test_pause_restart_resume_and_cancel(plugin):
    book=await upload(plugin)
    now,plan=await begin_reading(plugin,book)
    plugin.spatial.transition(plan['id'],'pause','',now+timedelta(minutes=1))
    await plugin.on_unload();await plugin.on_load()
    await plugin.books.process(now+timedelta(minutes=20))
    assert plugin.store.get('book',book['id'])['read_characters']==0
    plugin.spatial.transition(plan['id'],'resume','',now+timedelta(minutes=20))
    plugin.engine.advance(now+timedelta(minutes=35));await plugin.books.process(now+timedelta(minutes=35))
    assert plugin.store.get('book',book['id'])['chapter']==1
    plugin.now=lambda:now+timedelta(minutes=36)
    p=await plugin.ui_book_action('schedule',book_id=book['id'],minutes=15)
    plugin.engine.advance(now+timedelta(minutes=36,seconds=1));answer(plugin,'夜里一盏灯照着书页。')
    await plugin.books.process(now+timedelta(minutes=36,seconds=1))
    plugin.spatial.transition(p['id'],'cancel','',now+timedelta(minutes=37))
    await plugin.books.process(now+timedelta(minutes=60))
    assert plugin.store.get('book',book['id'])['chapter']==1
    assert (await plugin.ui_books())['sessions'][-1]['status']=='日程已取消'

async def test_bad_quote_failure_and_retry(plugin):
    book=await upload(plugin)
    now=plugin.now();p=await plugin.ui_book_action('schedule',book_id=book['id'],minutes=15)
    plugin.engine.advance(now+timedelta(seconds=2));answer(plugin,'正文里没有这一句')
    with pytest.raises(ValueError,match='逐字引用'):
        await plugin.books.process(now+timedelta(seconds=2))
    assert plugin.ctx.llm.generate.await_count==3
    assert plugin.store.get('book',book['id'])['read_characters']==0
    answer(plugin)
    await plugin.books.process(now+timedelta(minutes=6))
    assert plugin.store.get('book_note',p['id'])['status']=='等待活动结束'

async def test_autonomous_and_native_book_tools(plugin):
    book=await upload(plugin)
    plugin.config.life.autonomous=True;plugin.config.life.schedule_enabled=False
    answer_value={'title':'读几页书','kind':'leisure','minutes':20,'location':'住处','motivation':'继续阅读'}
    plugin.ctx.llm.generate.return_value={'success':True,'response':json.dumps(answer_value)}
    await plugin.living.choose_activity(plugin.now())
    p=next(p for p in plugin.store.all('plan') if p['kind']!='travel')
    assert p['book_id']==book['id'] and book['title'] in p['title']
    assert (await plugin.books_tool('list',stream_id='a'))['books'][0]['id']==book['id']
    assert any(c['name']=='maiweave_books' for c in plugin.get_components())

def test_epub_uses_spine_and_metadata(tmp_path):
    p=tmp_path/'书.epub'
    with ZipFile(p,'w') as z:
        z.writestr('META-INF/container.xml','<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        z.writestr('OPS/book.opf','<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>灯下</dc:title><dc:creator>作者</dc:creator></metadata><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        z.writestr('OPS/a.xhtml','<html><body><h1>第一章</h1><p>树下有风。</p><script>跳过脚本</script></body></html>')
        z.writestr('OPS/b.xhtml','<html><body><h1>第二章</h1><p>灯下有书。</p></body></html>')
    title,author,chapters=parse_book(p,'','')
    assert title=='灯下' and author=='作者'
    assert chapters[0]['title']=='第一章' and '跳过脚本' not in chapters[0]['text']
    assert chapters[1]['title']=='第二章'

def test_chinese_text_encoding_and_empty_file(tmp_path):
    p=tmp_path/'书.txt';p.write_bytes('第一章 风\n风吹过窗前。'.encode('gb18030'))
    assert parse_book(p,'','')[2][0]['title']=='第一章 风'
    p.write_text('',encoding='utf-8')
    with pytest.raises(ValueError,match='正文'):parse_book(p,'','')
