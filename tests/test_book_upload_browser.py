import subprocess
from pathlib import Path


def test_upload_browser_flow():
    """验证分块节奏、失败清理与重新上传的页面状态。"""
    page = Path(__file__).parents[1] / 'webui/editor.html'
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[1],'utf8');
const source=html.slice(html.indexOf('async function uploadBook(){'),html.indexOf("$('bookChoice').onchange"));
async function scenario(failAppend=false,failCleanup=false){
 const fields=Object.fromEntries(['bookFile','uploadBook','cancelUpload','bookTitle','bookAuthor','bookFilter','bookSearch','bookStatus'].map(k=>[k,{value:'',textContent:''}]));
 const file=new Blob([Buffer.alloc(6000015,65)]);file.name='大文件.txt';fields.bookFile.files=[file];
 let clock=0,last=0,chunks=0,rendered=0,aborted=0;
 const ctx={Uint8Array,Error,Promise,Math,String,btoa,uploadCancelled:false,activeUpload:'',
  shelf:{capacity:{max_file_mb:20},uploads:[{id:'upload'}]},
  $:id=>fields[id],setTimeout:fn=>{clock+=100;fn()},
  tracked:async(button,title,work)=>work(()=>{}),showBooks:async()=>{},renderUploads:()=>{rendered++},
  maibot:{action:async(name,args)=>{
   if(args.action==='begin')return {id:'upload'};
   if(args.action==='append'){assert.ok(clock-last>=100);last=clock;chunks++;assert.ok(args.content.length<=32768);if(failAppend)throw Error('原始上传失败');}
   if(args.action==='abort'){aborted++;if(failCleanup)throw Error('清理连接中断');}
   if(args.action==='finish')return {id:'book',title:'大文件',chapter_count:1};
  }}
 };
 vm.createContext(ctx);vm.runInContext(source,ctx);
 if(failAppend){
  await assert.rejects(ctx.uploadBook(),e=>e.message.includes('原始上传失败')&&e.message.includes(failCleanup?'清理连接中断':'临时文件已清理'));
  assert.equal(aborted,1);assert.equal(ctx.shelf.uploads.length,failCleanup?1:0);
  assert.ok(fields.bookStatus.textContent.includes('原始上传失败'));
 }else{await ctx.uploadBook();assert.equal(chunks,245);assert.equal(fields.bookStatus.textContent,'已导入《大文件》，共1章');}
 assert.equal(fields.cancelUpload.hidden,true);assert.equal(ctx.activeUpload,'');assert.equal(rendered,1);
}
(async()=>{await scenario();await scenario(true);await scenario(true,true)})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node', '-e', script, str(page)], check=True, timeout=30)
