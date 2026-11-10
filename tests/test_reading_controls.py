"""换读、跳章与阅读完成、笔记接续的联动。"""
import asyncio
from datetime import timedelta
import pytest
from test_plugin import plugin
from test_books import upload, begin_reading, answer

async def test_switch_keeps_window_and_books_separate(plugin):
    first = await upload(plugin)
    second = await upload(plugin, "# 第一章 海\n潮水退去了。", "潮汐.md")
    now, old = await begin_reading(plugin, first)
    plugin.now = lambda: now + timedelta(minutes=2)
    result = await plugin.ui_book_action('switch', book_id=second['id'])
    new = result['plan']
    assert new['end'] == old['end'] and new['location'] == old['location']
    assert plugin.store.get('plan', old['id'])['status'] == 'cancelled'
    answer(plugin, '潮水退去了。')
    await plugin.books.process(plugin.now())
    plugin.engine.advance(now + timedelta(minutes=16))
    await plugin.books.process(now + timedelta(minutes=16))
    assert plugin.store.get('book', first['id'])['read_characters'] == 0
    assert plugin.books.notes(first['id'])['total'] == 0
    assert plugin.books.notes(second['id'])['total'] == 1
    assert plugin.store.get('book', second['id'])['status'] == '已读完'

async def test_seek_and_skip_rebuild_active_window(plugin):
    book = await upload(plugin)
    now, old = await begin_reading(plugin, book)
    plugin.now = lambda: now + timedelta(minutes=1)
    result = await plugin.ui_book_action('skip', book_id=book['id'])
    assert result['book']['chapter'] == 1
    assert result['book']['read_characters'] == 0
    answer(plugin, '夜里一盏灯照着书页。')
    await plugin.books.process(plugin.now())
    end = now + timedelta(minutes=16)
    plugin.engine.advance(end)
    await plugin.books.process(end)
    assert plugin.books.notes(book['id'])['items'][0]['chapter'] == 1
    plugin.now = lambda: end
    await plugin.ui_book_action('seek', book_id=book['id'], chapter=0)
    assert plugin.store.get('book', book['id'])['status'] == '在读'
    assert plugin.books.notes(book['id'])['total'] == 1
    with pytest.raises(ValueError):
        await plugin.ui_book_action('seek', book_id=book['id'], chapter=999)
    assert plugin.store.get('book', book['id'])['chapter'] == 0

async def test_directory_bypassed_but_short_body_preserved(plugin):
    book = await upload(plugin, '# 目录\n# 第一章 风\n# 第二章 灯\n# 第一章 风\n风吹过窗前的银杏树。\n# 第二章 灯\n夜里一盏灯照着书页。')
    chapters = await plugin.ui_book_chapters(book['id'])
    assert [c['index'] for c in chapters['items'] if c['directory']] == [0, 1, 2]
    now, plan = await begin_reading(plugin, book)
    note = plugin.store.get('book_note', plan['id'])
    assert note['text_chapter'] == 3 and note['skipped_chapters'] == [0, 1, 2]
    plugin.engine.advance(now + timedelta(minutes=16))
    await plugin.books.process(now + timedelta(minutes=16))
    assert plugin.store.get('book', book['id'])['chapter'] == 4
    assert plugin.store.get('book', book['id'])['read_characters'] == note['read_characters']
    assert plugin.books.notes(book['id'])['total'] == 1

async def test_inflight_old_note_cannot_advance_after_switch(plugin):
    first = await upload(plugin)
    second = await upload(plugin, '# 海\n潮水退去了。', '海.md')
    now = plugin.now()
    old = await plugin.ui_book_action('schedule', book_id=first['id'], minutes=15)
    plugin.engine.advance(now + timedelta(seconds=1))
    started, release = asyncio.Event(), asyncio.Event()
    answer(plugin)
    value = plugin.ctx.llm.generate.return_value
    async def waiting(*args, **kwargs):
        started.set()
        await release.wait()
        return value
    plugin.ctx.llm.generate.side_effect = waiting
    task = asyncio.create_task(plugin.books.process(now + timedelta(seconds=1)))
    await started.wait()
    plugin.now = lambda: now + timedelta(minutes=1)
    await plugin.ui_book_action('switch', book_id=second['id'])
    release.set()
    await task
    assert plugin.store.get('book', first['id'])['read_characters'] == 0
    assert plugin.books.notes(first['id'])['total'] == 0

async def test_chapter_pages_and_switch_respect_influence(plugin):
    book = await upload(plugin, ''.join(f'# 第{i}章\n正文{i}。\n' for i in range(42)))
    assert len((await plugin.ui_book_chapters(book['id']))['items']) == 20
    assert len((await plugin.ui_book_chapters(book['id'], 2))['items']) == 2
    now = plugin.now()
    plan = await plugin.ui_book_action('schedule', book_id=book['id'], minutes=15)
    plugin.engine.advance(now + timedelta(seconds=1))
    def denied(*args):
        raise ValueError('亲密程度不足')
    plugin.spatial.check_influence = denied
    with pytest.raises(ValueError, match='亲密程度'):
        plugin.books.navigate('seek', book['id'], 1, plugin.spatial, now + timedelta(seconds=3), 'group', 'someone')
    assert plugin.store.get('plan', plan['id'])['status'] == 'active'
    assert plugin.store.get('book', book['id'])['chapter'] == 0


async def test_seek_retains_reading_goal(plugin):
    book = await upload(plugin)
    now = plugin.now()
    from maiweave.core.models import GoalInput, PlanInput
    goal = plugin.engine.create_goal(GoalInput(title='读完这本书'), '', now)
    plan = plugin.spatial.schedule(PlanInput(title='阅读', book_id=book['id'], goal_id=goal['id'], start=now.isoformat(), end=(now+timedelta(minutes=15)).isoformat()), '', now)
    plugin.engine.advance(now + timedelta(seconds=1))
    result = plugin.books.navigate('seek', book['id'], 1, plugin.spatial, now+timedelta(seconds=2))
    assert result['plan']['goal_id'] == goal['id']
    answer(plugin, '夜里一盏灯照着书页。')
    await plugin.books.process(now+timedelta(seconds=3))
    plugin.engine.advance(now+timedelta(minutes=16))
    await plugin.books.process(now+timedelta(minutes=16))
    assert plugin.store.get('goal',goal['id'])['completed_sessions'] == 1
