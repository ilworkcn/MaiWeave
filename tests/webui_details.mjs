// 使用页面实际函数检查嵌套详情及翻页查询。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const html=readFileSync(new URL('../webui/editor.html',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const metadata=JSON.parse(html.match(/const PAGE_METADATA=(.*);\n/)[1]);
function source(name){const start=html.indexOf(name==='showDetail'?'async function '+name:'function '+name);return html.slice(start,html.indexOf('\n}\n',start)+3)}
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
 const maibot={query:async(name,args)=>{calls.push({name,args});return {text:'详情第'+args.page+'页',page:args.page,pages:2}}};
 const valueOf=reference=>reference?.source===selection?row[reference.field]:reference;
 const show=new Function('document','maibot','valueOf','run',source('renderPager')+source('showDetail')+';return showDetail')(document,maibot,valueOf,work=>work());
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
