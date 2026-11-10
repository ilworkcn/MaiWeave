// 使用页面实际章节函数，验证轮询刷新、翻页与切换书籍。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const html=readFileSync(new URL('../webui/editor.html',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const code=html.slice(html.indexOf('let chapterPage='),html.indexOf('let uploadCancelled='));
const elements=new Map();
function $(id){if(!elements.has(id))elements.set(id,{value:'',replaceChildren(...items){this.items=items;this.value=String(items[0]?.value??'')}});return elements.get(id)}
const calls=[],shelf={books:[{id:'a',chapter:3,chapter_count:5},{id:'b',chapter:23,chapter_count:45}]};
const context=vm.createContext({$,shelf,Option:class{constructor(text,value){this.text=text;this.value=value}},
 run:fn=>fn(),tracked:async(button,title,fn)=>fn(),showBooks:async()=>{},
 maibot:{query:async(name,args)=>{calls.push(args);const b=shelf.books.find(x=>x.id===args.book_id);return {page:args.page,total:b.chapter_count,chapter:b.chapter,items:Array.from({length:Math.min(20,b.chapter_count-args.page*20)},(_,n)=>({index:args.page*20+n,title:'章节',preview:'正文'+(args.page*20+n)}))}},action:async()=>({status:'成功'})}});
vm.runInContext(code,context);
$('bookChoice').value='a';await vm.runInContext('showChapters()',context);
assert.equal(String($('chapterChoice').value),'3');
$('chapterChoice').value='4';$('chapterChoice').onchange();
await vm.runInContext('showChapters()',context);
assert.equal($('chapterChoice').value,'4');assert.equal($('chapterPreview').textContent,'正文4');
$('bookChoice').value='b';await vm.runInContext('showChapters()',context);
assert.equal(calls.at(-1).page,1);assert.equal(String($('chapterChoice').value),'23');
await $('nextChapter').onclick();assert.equal(calls.at(-1).page,2);
await vm.runInContext('showChapters()',context);assert.equal(calls.at(-1).page,2);
assert.equal($('nextChapter').disabled,true);
await $('prevChapter').onclick();assert.equal(calls.at(-1).page,1);
console.log('章节选择经轮询保留，切书定位当前章，翻页范围与按钮状态通过');
