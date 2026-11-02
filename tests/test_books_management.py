"""书架容量、文件清理、归档与阅读上下文测试。"""
import base64,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from test_plugin import plugin
from test_books import upload, begin_reading

async def test_slots_count_pending_uploads_and_abort(plugin):
    plugin.config.books.max_books=1
    row=await plugin.ui_book_upload('begin',filename='第一本.txt')
    with pytest.raises(ValueError,match='数量已满'):await plugin.ui_book_upload('begin',filename='第二本.txt')
    await plugin.ui_book_upload('abort',upload_id=row['id'])
    assert plugin.store.all('book_upload')==[]
    await upload(plugin)
    with pytest.raises(ValueError,match='数量已满'):await upload(plugin)

async def test_file_limit_cleans_partial_upload(plugin):
    plugin.config.books.max_file_mb=1
    row=await plugin.ui_book_upload('begin',filename='大文件.txt')
    with pytest.raises(ValueError,match='单本容量'):
        await plugin.ui_book_upload('append',upload_id=row['id'],content=base64.b64encode(b'x'*(1024*1024+1)).decode())
    assert plugin.store.all('book_upload')==[] and list(plugin.books.directory.iterdir())==[]

async def test_expanded_text_counts_toward_storage(plugin):
    plugin.config.books.storage_mb=1
    row=await plugin.ui_book_upload('begin',filename='正文.txt')
    await plugin.ui_book_upload('append',upload_id=row['id'],content=base64.b64encode(b'a'*600000).decode())
    with pytest.raises(ValueError,match='文件容量已满'):await plugin.ui_book_upload('finish',upload_id=row['id'])
    assert plugin.store.all('book')==[] and list(plugin.books.directory.iterdir())==[]

async def test_disk_and_write_errors_leave_no_partial_book(plugin):
    with patch('maiweave.books.shutil.disk_usage',return_value=SimpleNamespace(free=1024)):
        with pytest.raises(ValueError,match='磁盘剩余'):await upload(plugin)
    row=await plugin.ui_book_upload('begin',filename='写入测试.txt')
    await plugin.ui_book_upload('append',upload_id=row['id'],content=base64.b64encode('正文'.encode()).decode())
    with patch.object(Path,'write_bytes',side_effect=OSError('写入失败')):
        with pytest.raises(OSError,match='写入失败'):await plugin.ui_book_upload('finish',upload_id=row['id'])
    assert plugin.store.all('book_upload')==[] and plugin.store.all('book')==[]
    assert list(plugin.books.directory.iterdir())==[]

async def test_archive_edit_restore_and_delete_keep_history(plugin):
    book=await upload(plugin)
    await plugin.ui_book_action('edit',book_id=book['id'],title='新书名',author='作者')
    await plugin.ui_book_action('archive',book_id=book['id'])
    assert plugin.books.context()['books']==[]
    with pytest.raises(ValueError,match='恢复'):await plugin.ui_book_action('schedule',book_id=book['id'])
    await plugin.ui_book_action('restore',book_id=book['id'])
    now,plan=await begin_reading(plugin,book)
    with pytest.raises(ValueError,match='阅读日程'):await plugin.ui_book_action('delete',book_id=book['id'])
    from datetime import timedelta
    end=now+timedelta(minutes=16);plugin.engine.advance(end);await plugin.books.process(end)
    await plugin.ui_book_action('delete',book_id=book['id'])
    assert plugin.store.all('book')==[] and plugin.books.notes()['total']==1
    assert plugin.store.get('experience',plugin.store.get('plan',plan['id'])['experience_id'])
    assert list(plugin.books.directory.iterdir())==[]

async def test_context_is_bounded_and_notes_are_paginated(plugin):
    plugin.config.books.context_chars=1000
    book=await upload(plugin)
    with plugin.store.db:
        for i in range(40):
            plugin.store.put('book',{**book,'id':str(i),'title':'长书名'*60})
            plugin.store.put('book_note',{'id':str(i),'book_id':book['id'],'book_title':'书名','chapter_title':'章节','status':'已完成','finished_at':f'2026-11-02T10:{i:02d}:00+08:00','summary':'内容'*1000,'thoughts':'感想'*1000,'quote':'原文','question':'问题'})
    assert len(json.dumps(plugin.books.context(),ensure_ascii=False))<=1000
    assert len(plugin.books.notes(book['id'],1)['items'])==10
    result=await plugin.books_tool('notes',book_id=book['id'],page=1)
    assert result['total']==40 and len(result['items'])==2 and len(result['items'][0]['summary'])==500

async def test_restart_cleans_interrupted_upload(plugin):
    from maiweave.books import Books
    row=await plugin.ui_book_upload('begin',filename='未完成.txt')
    await plugin.ui_book_upload('append',upload_id=row['id'],content=base64.b64encode(b'partial').decode())
    Books(plugin.engine,plugin.ctx,plugin.config,plugin.ctx.paths.data_dir)
    assert plugin.store.all('book_upload')==[] and list(plugin.books.directory.iterdir())==[]

async def test_epub_expansion_limit_cleans_upload(plugin):
    import io
    from zipfile import ZipFile, ZIP_DEFLATED
    plugin.config.books.max_file_mb=1
    data=io.BytesIO()
    with ZipFile(data,'w',ZIP_DEFLATED) as archive:archive.writestr('large.xhtml','x'*(1024*1024+1))
    row=await plugin.ui_book_upload('begin',filename='压缩书.epub')
    await plugin.ui_book_upload('append',upload_id=row['id'],content=base64.b64encode(data.getvalue()).decode())
    with pytest.raises(ValueError,match='解压内容'):await plugin.ui_book_upload('finish',upload_id=row['id'])
    assert list(plugin.books.directory.iterdir())==[]

async def test_reading_chunk_uses_configured_limit(plugin):
    from datetime import timedelta
    from test_books import answer
    plugin.config.books.reading_chars=500
    book=await upload(plugin,'# 第一章\n风吹过窗前的银杏树。\n'+'正文。'*1000)
    now=plugin.now();plan=await plugin.ui_book_action('schedule',book_id=book['id'],minutes=15)
    plugin.engine.advance(now+timedelta(seconds=1));answer(plugin)
    await plugin.books.process(now+timedelta(seconds=1))
    note=plugin.store.get('book_note',plan['id'])
    assert note['end']==500 and note['next_offset']==500
    assert len(plugin.ctx.llm.generate.call_args.args[0].split('正文：\n')[1])==500
