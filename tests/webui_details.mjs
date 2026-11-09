// 使用页面实际函数检查嵌套详情及翻页查询。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const html=readFileSync(new URL('../webui/editor.html',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const metadata=JSON.parse(html.match(/const PAGE_METADATA=(.*);\n/)[1]);
function source(name){const start=html.indexOf(['showDetail','renderDiaryPublication'].includes(name)?'async function '+name:'function '+name);return html.slice(start,html.indexOf('\n}\n',start)+3)}
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.style={};this.textContent=''}
 append(...items){this.children.push(...items)}
 replaceChildren(){this.children=[]}
 showModal(){this.open=true}
 remove(){this.removed=true}
}
function nodes(element){return [element,...element.children.flatMap(nodes)]}
for(const [page,category,selection,field] of [['diary','diary','entry','material'],['records','experience','record','history']]){
 const row={id:'record-id',detail_kind:category,content:'日记正文',details_text:'地点：公园',summary:'阅读',source_label:'交互观测'};
 const document={body:new Element('body'),createElement:tag=>new Element(tag)};const calls=[];
 const maibot={query:async(name,args)=>{if(name==='diary_publication')return {record:{},connection:{status:'尚未检查'},mode:'关闭',visibility:'好友'};calls.push({name,args});return {text:'详情第'+args.page+'页',page:args.page,pages:2}}};
 const valueOf=reference=>reference?.source===selection?row[reference.field]:reference;
 const show=new Function('document','maibot','valueOf','run',source('renderPager')+source('renderDiaryPublication')+source('showDetail')+';return showDetail')(document,maibot,valueOf,work=>work());
 const declaration=metadata[page].view.content.find(item=>item.type==='dialog');
 await show(declaration,row);
 assert.equal(calls[0].name,'record_detail');assert.deepEqual(calls[0].args,{category,record_id:'record-id',field,page:1});
 assert(nodes(document.body).some(node=>node.textContent.includes('详情第1页')));
 if(page==='diary')assert(nodes(document.body).some(node=>node.tag==='details'));
 if(page==='records')assert(nodes(document.body).some(node=>node.textContent.includes('地点：公园')));
 const next=nodes(document.body).find(node=>node.textContent==='下一页');await next.onclick();
 assert.equal(calls[1].args.page,2);assert(nodes(document.body).some(node=>node.textContent.includes('详情第2页')));
 assert.equal(nodes(document.body).find(node=>node.textContent==='下一页').disabled,true);
}
console.log('日记折叠素材与经历历史翻页通过');

// 空间发布在页面内确认，适用于 MaiBot 的内嵌页面。
{
 const document={body:new Element('body'),createElement:tag=>new Element(tag)};
 const confirm=new Function('document',source('confirmPublication')+';return confirmPublication')(document);
 const accepted=confirm('仅自己可见');let box=document.body.children.at(-1);
 nodes(box).find(n=>n.textContent==='确认').onclick();assert.equal(await accepted,true);assert(box.removed);
 const cancelled=confirm('核对结果');box=document.body.children.at(-1);
 nodes(box).find(n=>n.textContent==='取消').onclick();assert.equal(await cancelled,false);
 assert(!source('renderDiaryPublication').includes('window.confirm'));
}
console.log('空间发布页面内确认通过');

// 已发布记录展示当时的可见范围，配置修改后仍保留发布时的值。
{
 const document={createElement:tag=>new Element(tag)},dialog=new Element('dialog');
 const maibot={query:async()=>({mode:'关闭',visibility:'好友',connection:{status:'尚未检查'},record:{status:'已发布',content:'测试',visibility:'仅自己',published_at:'2026-11-09T15:57:35+08:00'}})};
 const render=new Function('document','maibot',source('renderDiaryPublication')+';return renderDiaryPublication')(document,maibot);
 await render(dialog,{id:'2026-11-03',status:'已生成'});
 assert(nodes(dialog).some(n=>n.textContent.includes('仅自己可见 · 已发布')));
 assert.equal(nodes(dialog).find(n=>n.textContent==='发布到空间').disabled,true);
}
console.log('已发布可见范围与重复发布按钮状态通过');

// 连接检查和取消发布保留编辑内容，发布按钮可直接使用当前输入。
{
 const document={createElement:tag=>new Element(tag)},dialog=new Element('dialog'),calls=[];
 const maibot={query:async()=>({mode:'手动发布',visibility:'好友',connection:{status:'已连接'},record:{status:'草稿',content:'原草稿'}}),action:async(name,args)=>{calls.push(args);return {message:'已完成'}}};
 const render=new Function('document','maibot','run','tracked','waitOperation','confirmPublication',source('renderDiaryPublication')+';return renderDiaryPublication')(document,maibot,work=>work(),async(button,title,work)=>work(()=>{}),async result=>result.message,async()=>false);
 await render(dialog,{id:'2026-11-09',status:'已生成'});
 const text=nodes(dialog).find(n=>n.tag==='textarea');text.value='正在修改的文案';text.oninput();
 await nodes(dialog).find(n=>n.textContent==='检查空间连接').onclick();assert.equal(text.value,'正在修改的文案');
 await nodes(dialog).find(n=>n.textContent==='发布到空间').onclick();assert.equal(text.value,'正在修改的文案');assert.equal(calls.length,1);
 text.value='';text.oninput();assert(nodes(dialog).find(n=>n.textContent==='保存文案').disabled);
 text.value='新写的文案';text.oninput();assert.equal(nodes(dialog).find(n=>n.textContent==='发布到空间').disabled,false);
}
console.log('草稿编辑、取消发布与按钮联动通过');
