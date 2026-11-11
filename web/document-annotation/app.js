"use strict";
const $=id=>document.getElementById(id), NS="http://www.w3.org/2000/svg";
const state={project:null,token:null,page:1,region:null,selection:null,mode:"select",view:null,
  dirty:false,seq:0,saving:null,timer:null,conflict:false,undo:[],redo:[],edgeStart:null,path:[],pointer:null,attribute:"length",rotation:0};
const types={text:"文字",table:"表格",diagram:"图纸"};
const statuses={pending:"尚未标注",visible:"清晰可判定",ambiguous:"存在歧义",unreadable:"有内容但看不清",not_provided:"未提供",not_applicable:"不适用"};
const relations={cable:"物理光缆",circuit:"逻辑电路",fiber_allocation:"纤芯分配",wiring:"设备配线",power:"电源连接",unknown:"无法确定"};
const attrs={cable_type:"光缆类型",cores:"芯数",length:"长度",construction:"建设状态",rate:"速率"};
const clone=v=>JSON.parse(JSON.stringify(v));
const uid=prefix=>prefix+"-"+crypto.randomUUID().slice(0,12);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const options=(values,current)=>Object.entries(values).map(([v,label])=>`<option value="${esc(v)}" ${v===current?"selected":""}>${esc(label)}</option>`).join("");
const region=()=>state.project.regions.find(r=>r.id===state.region);
const dimensions=()=>{const p=state.project.document.pages[state.page-1];return [p.width,p.height]};
function notice(message,error=false){$("notice").textContent=message;$("notice").classList.toggle("error",error);}
async function api(path,body){
  const res=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-Annotation-Token":state.token},body:JSON.stringify(body)});
  const data=await res.json();if(!res.ok){if(res.status===409)state.conflict=true;throw Error(data.error||"请求失败");}return data;
}
function snapshot(){return {regions:clone(state.project.regions),links:clone(state.project.links)};}
function change(fn,review=false){
  if(state.conflict)return notice("版本冲突：请先下载当前草稿，再刷新页面",true);
  state.undo.push(snapshot());if(state.undo.length>30)state.undo.shift();state.redo=[];
  const r=region();if(r&&!review){r.status="draft";r.reviewer="";r.reviewed_at=null;}
  fn();markDirty();
}
function markDirty(){state.dirty=true;state.seq++;clearTimeout(state.timer);state.timer=setTimeout(()=>save().catch(e=>notice(e.message,true)),1000);renderStatus();}
async function save(){
  clearTimeout(state.timer);if(state.saving){await state.saving;if(state.dirty)return save();return;}
  if(!state.dirty)return;
  if(state.conflict)throw Error("版本冲突：请下载草稿并刷新，不会覆盖其他窗口");
  const actor=$("actor").value.trim();if(!actor)throw Error("请先填写标注人，当前修改仍保留在浏览器中");
  try{localStorage.setItem("document-annotation-actor",actor);}catch(_){}
  const sequence=state.seq,payload={revision:state.project.revision,regions:clone(state.project.regions),links:clone(state.project.links),actor};
  state.saving=(async()=>{
    const project=await api("/api/save",payload);
    state.project.revision=project.revision;state.project.events=project.events;
    if(state.seq===sequence){
      state.dirty=false;
      for(const saved of project.regions){const local=state.project.regions.find(r=>r.id===saved.id);if(local)Object.assign(local,{status:saved.status,reviewer:saved.reviewer,reviewed_at:saved.reviewed_at});}
    }else{
      for(const saved of project.regions){const local=state.project.regions.find(r=>r.id===saved.id);if(local&&saved.status==="draft")Object.assign(local,{status:"draft",reviewer:"",reviewed_at:null});}
    }
    renderStatus();renderLists();notice(`已保存到本机 · 修订 ${project.revision}`);
  })();
  try{await state.saving;}finally{state.saving=null;}
}
function renderStatus(){
  const p=state.project,n=p.regions.filter(r=>r.status==="reviewed").length;
  $("documentName").textContent=p.document.name;
  $("progress").textContent=`${p.document.pages.length} 页 · ${p.regions.length} 个区域 · ${n} 个已复核`;
  $("revision").textContent=`修订 ${p.revision}${state.dirty?" · 有未保存修改":""}`;
  $("undo").disabled=!state.undo.length;$("redo").disabled=!state.redo.length;
}
function renderLists(){
  const buttons=rs=>rs.map(r=>`<button class="region-button ${r.id===state.region?"active":""}" data-region="${esc(r.id)}"><span>${types[r.type]} · 第${r.page}页 · ${esc(r.title||r.id)}</span><small>${r.status==="reviewed"?"已复核":"草稿"}</small></button>`).join("");
  $("regionList").innerHTML=buttons(state.project.regions.filter(r=>r.page===state.page));
  $("allRegions").innerHTML=buttons(state.project.regions);
  document.querySelectorAll("[data-region]").forEach(b=>b.onclick=()=>selectRegion(b.dataset.region));
}
function selectRegion(id){const r=state.project.regions.find(r=>r.id===id);state.region=id;state.selection=null;cancelMode();state.page=r.page;state.rotation=r.rotation_degrees||0;$("page").value=state.page;fit();render();}
function svg(tag,attributes){const node=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attributes))node.setAttribute(k,v);return node;}
function setView(){$("canvas").setAttribute("viewBox",state.view.join(" "));}
function displayPoint(p){const [w,h]=dimensions();return state.rotation===90?[h-p[1],p[0]]:state.rotation===180?[w-p[0],h-p[1]]:state.rotation===270?[p[1],w-p[0]]:p;}
function fit(){const [w,h]=dimensions(),transforms={0:"",90:`matrix(0 1 -1 0 ${h} 0)`,180:`matrix(-1 0 0 -1 ${w} ${h})`,270:`matrix(0 -1 1 0 0 ${w})`};$("pageLayer").setAttribute("transform",transforms[state.rotation]);state.view=state.rotation%180?[0,0,h,w]:[0,0,w,h];setView();}
function zoom(factor){const [x,y,w,h]=state.view;state.view=[x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor];setView();}
function focus(){const r=region();if(!r)return;if(state.page!==r.page){state.page=r.page;$("page").value=state.page;}state.rotation=r.rotation_degrees||0;fit();const [w,h]=dimensions(),b=r.bbox,pad=.025*Math.max(w,h),points=[[b[0]*w,b[1]*h],[b[2]*w,b[3]*h]].map(displayPoint),x=Math.min(...points.map(p=>p[0])),y=Math.min(...points.map(p=>p[1]));state.view=[x-pad,y-pad,Math.abs(points[1][0]-points[0][0])+2*pad,Math.abs(points[1][1]-points[0][1])+2*pad];setView();renderMap();}
function object(id){return region()?.data.objects?.find(o=>o.id===id);}
function edge(id){return region()?.data.connections?.find(e=>e.id===id);}
function center(o){const b=o.bbox;return [(b[0]+b[2])/2,(b[1]+b[3])/2];}
function currentEvidence(create=false){
  const r=region();if(!r)return null;const s=state.selection;let target=r.data;
  if(s?.kind==="object")target=object(s.id);
  if(s?.kind==="edge"){target=edge(s.id);if(state.attribute!=="connection"){if(create&&!target.attributes[state.attribute])target.attributes[state.attribute]=pending();target=target.attributes[state.attribute];}}
  if(s?.kind==="cell")target=r.data.cells.find(c=>c.id===s.id);
  if(s?.kind==="item")target=r.items.find(i=>i.id===s.id);
  if(!target)return null;if(create&&!target.evidence)target.evidence=[];return target.evidence||[];
}
function renderMap(){
  const [w,h]=dimensions();$("pageImage").setAttribute("href",`/api/page?page=${state.page}`);$("pageImage").setAttribute("width",w);$("pageImage").setAttribute("height",h);
  for(const id of ["regionsLayer","edgesLayer","objectsLayer","evidenceLayer","draftLayer"])$(id).replaceChildren();
  for(const r of state.project.regions.filter(r=>r.page===state.page)){
    const b=r.bbox,rect=svg("rect",{x:b[0]*w,y:b[1]*h,width:(b[2]-b[0])*w,height:(b[3]-b[1])*h,class:`region-box ${r.id===state.region?"selected":""} ${r.status}`});
    rect.onclick=e=>{if(state.mode!=="select")return;e.stopPropagation();selectRegion(r.id);};$("regionsLayer").append(rect);
    const label=svg("text",{x:b[0]*w+3,y:b[1]*h+15,class:"region-label"});label.textContent=`${types[r.type]} ${r.title||r.id}`;$("regionsLayer").append(label);
  }
  const r=region();
  if(r?.type==="diagram"&&r.page===state.page){
    for(const e of r.data.connections){
      const points=e.path.length>=2?e.path:[center(object(e.source)),center(object(e.target))];
      const line=svg("polyline",{points:points.map(p=>`${p[0]*w},${p[1]*h}`).join(" "),class:`diagram-edge ${state.selection?.id===e.id?"selected":""} ${e.path.length<2?"untraced":""}`});
      const title=svg("title",{});title.textContent=e.path.length>=2?"人工记录的线路走向":"端点示意，尚未描原图路径";line.append(title);
      line.onclick=ev=>{if(state.mode!=="select")return;ev.stopPropagation();state.selection={kind:"edge",id:e.id};renderInspector();renderMap();};$("edgesLayer").append(line);
    }
    for(const o of r.data.objects){const b=o.bbox,rect=svg("rect",{x:b[0]*w,y:b[1]*h,width:(b[2]-b[0])*w,height:(b[3]-b[1])*h,class:`diagram-object ${state.selection?.id===o.id||state.edgeStart===o.id?"selected":""}`});
      rect.onclick=e=>{e.stopPropagation();if(state.pointer?.moved)return;if(state.mode==="connection")return pickEndpoint(o.id);if(state.mode!=="select")return;state.selection={kind:"object",id:o.id};renderInspector();renderMap();};
      $("objectsLayer").append(rect);
    }
  }
  for(const e of currentEvidence()||[]){if(e.page!==state.page)continue;const b=e.bbox;$("evidenceLayer").append(svg("rect",{x:b[0]*w,y:b[1]*h,width:(b[2]-b[0])*w,height:(b[3]-b[1])*h,class:"evidence-box"}));}
  if(state.path.length){$("draftLayer").append(svg("polyline",{points:state.path.map(p=>`${p[0]*w},${p[1]*h}`).join(" "),class:"diagram-edge selected"}));}
}
function render(){renderStatus();renderLists();renderMap();renderInspector();}
function stateSelect(id,value){return `<label>信息状态<select id="${id}">${options(statuses,value)}</select></label>`;}
function evidenceHTML(){const rows=currentEvidence()||[];return `<div class="evidence-list">${rows.map((e,i)=>`<div><button data-evidence="${i}">第${e.page}页 · 证据 ${i+1}</button> <button data-remove-evidence="${i}" aria-label="删除证据${i+1}">移除</button></div>`).join("")}</div><button id="bindEvidence">框选当前记录的证据</button>`;}
function pending(){return {state:"pending",value:null,raw:"",unit:"",evidence:[],tolerance:0};}
function factHTML(spec){
  const value=spec.value??"",kind=spec.value_type||(typeof spec.value==="number"?"number":typeof spec.value==="boolean"?"boolean":state.selection?.kind==="edge"&&["cores","length","rate"].includes(state.attribute)?"number":"text");
  return `<div class="grid">${stateSelect("factState",spec.state)}<label>确认值类型<select id="valueType">${options({text:"文字",number:"数字",boolean:"是 / 否"},kind)}</select></label><label class="wide">图中原文<input id="factRaw" value="${esc(spec.raw)}"></label><label>确认值<input id="factValue" value="${esc(value)}" ${spec.state!=="visible"?"disabled":""}></label><label>单位（数量可留空）<input id="factUnit" value="${esc(spec.unit)}"></label><label class="wide">数值容差（统一到 m / Mbps 后）<input id="factTolerance" type="number" min="0" step="any" value="${spec.tolerance||0}"></label></div>`;
}
function bindFact(getter){
  $("factState").onchange=e=>{change(()=>{const f=getter(true);f.state=e.target.value;if(f.state!=="visible")f.value=null;});renderInspector();};
  $("valueType").onchange=()=>{change(()=>{getter(true).value=null;getter(true).value_type=$("valueType").value;});$("factValue").value="";};
  $("factRaw").oninput=e=>change(()=>getter(true).raw=e.target.value);
  $("factUnit").onchange=e=>change(()=>getter(true).unit=e.target.value.trim());
  $("factTolerance").onchange=e=>change(()=>getter(true).tolerance=Number(e.target.value));
  $("factValue").oninput=e=>{const raw=e.target.value,kind=$("valueType").value;let value=raw;if(kind==="number")value=raw.trim()!==""&&Number.isFinite(Number(raw))?Number(raw):null;if(kind==="boolean")value=raw==="true"||raw==="是"?true:raw==="false"||raw==="否"?false:null;change(()=>getter(true).value=value);};
}
function renderInspector(){
  const r=region();if(!r){$("editor").innerHTML="<h2>从一块区域开始</h2><p>选择页码和内容类型，再点击“框选区域”，在原图上拖出范围。文字、表格和图纸可以出现在同一页。</p><p>标注内容保存在本机；模型答案不会在此预填。</p>";return;}
  const s=state.selection;let body="";
  if(s?.kind==="item"){
    const item=r.items.find(x=>x.id===s.id);
    if(!item){state.selection=null;return renderInspector();}
    body=`<h3>预定义判断项</h3><label>模型需要回答的问题<textarea id="itemQuestion">${esc(item.question)}</textarea></label><p>问题本身会进入模型输入；人工答案和证据标签单独保存，不进入提示词。</p>${factHTML(item)}<div class="actions"><button id="deleteItem" class="danger">删除判断项</button></div>`;
  }else if(r.type==="text"){
    body=`<h3>正文 / 说明 / 图例原文</h3>${stateSelect("textState",r.data.state)}<label>人工校正后的原文<textarea id="transcript" rows="10">${esc(r.data.transcript)}</textarea></label><div class="actions"><button id="suggest">提取文字草稿</button></div><p>来源：${esc(r.data.draft_source||"人工录入")}。草稿需根据原图校正后复核。</p>`;
  }else if(r.type==="table"){
    body=tableHTML(r,s);
  }else{
    body=diagramHTML(r,s);
  }
  $("editor").innerHTML=`<h2>${types[r.type]} · ${esc(r.id)}</h2><div class="grid"><label>区域标题<input id="regionTitle" value="${esc(r.title)}"></label><label>状态<input value="${r.status==="reviewed"?"已复核":"草稿"}" disabled></label></div><div class="actions"><button id="regionOnly">区域信息</button><button id="adjustRegion">调整区域框</button><button id="reviewRegion" class="primary">复核此区域</button><button id="moveRegionUp">上移顺序</button><button id="deleteRegion" class="danger">删除区域</button></div>${body}<h3>当前记录的证据</h3>${evidenceHTML()}<h3>预定义判断项</h3><div class="row-list">${r.items.map(x=>`<button data-item="${esc(x.id)}">${esc(x.question)} · ${statuses[x.state]}</button>`).join("")}</div><button id="newItem">添加判断项</button><h3>适用上下文与区域关联</h3>${contextsHTML(r)}<label>区域备注<textarea id="regionNotes">${esc(r.notes)}</textarea></label>`;
  $("regionTitle").oninput=e=>{change(()=>r.title=e.target.value);renderLists();};
  $("regionNotes").oninput=e=>change(()=>r.notes=e.target.value);
  $("regionOnly").onclick=()=>{state.selection=null;renderInspector();renderMap();};
  $("adjustRegion").onclick=()=>{if(r.page!==state.page)return notice("请先回到该区域所在页",true);setMode("adjust-region");};
  $("reviewRegion").onclick=async()=>{try{await save();const current=region(),actor=$("actor").value.trim();if(!actor)throw Error("请填写复核人标识");change(()=>{current.status="reviewed";current.reviewer=actor;current.reviewed_at=null;},true);await save();render();}catch(e){const current=region();if(current){current.status="draft";current.reviewer="";current.reviewed_at=null;}notice(e.message,true);renderStatus();}};
  $("deleteRegion").onclick=()=>{change(()=>{state.project.regions=state.project.regions.filter(x=>x.id!==r.id);for(const x of state.project.regions)x.contexts=x.contexts.filter(c=>c.region_id!==r.id);state.project.links=state.project.links.filter(l=>l.source!==r.id&&l.target!==r.id);});state.region=null;state.selection=null;render();};
  $("moveRegionUp").onclick=()=>{change(()=>{const i=state.project.regions.findIndex(x=>x.id===r.id);if(i>0)[state.project.regions[i-1],state.project.regions[i]]=[state.project.regions[i],state.project.regions[i-1]];});renderLists();};
  $("bindEvidence").onclick=()=>setMode("evidence");
  document.querySelectorAll("[data-item]").forEach(b=>b.onclick=()=>{state.selection={kind:"item",id:b.dataset.item};renderInspector();renderMap();});
  $("newItem").onclick=()=>{const item={id:uid("Q"),question:"请填写需要判断的问题",...pending()};change(()=>r.items.push(item));state.selection={kind:"item",id:item.id};renderInspector();};
  document.querySelectorAll("[data-evidence]").forEach(b=>b.onclick=()=>{const e=currentEvidence()[Number(b.dataset.evidence)];state.page=e.page;state.rotation=0;$("page").value=state.page;fit();const [w,h]=dimensions();state.view=[e.bbox[0]*w-.03*w,e.bbox[1]*h-.03*h,(e.bbox[2]-e.bbox[0]+.06)*w,(e.bbox[3]-e.bbox[1]+.06)*h];setView();renderMap();renderLists();});
  document.querySelectorAll("[data-remove-evidence]").forEach(b=>b.onclick=()=>{change(()=>currentEvidence(true).splice(Number(b.dataset.removeEvidence),1));renderInspector();renderMap();});
  bindContexts(r);
  if(s?.kind==="item"){
    const getter=()=>region().items.find(x=>x.id===s.id);bindFact(getter);
    $("itemQuestion").oninput=e=>change(()=>getter().question=e.target.value);
    $("deleteItem").onclick=()=>{change(()=>r.items=r.items.filter(x=>x.id!==s.id));state.selection=null;renderInspector();};
  }else if(r.type==="text"){
    $("textState").onchange=e=>{change(()=>r.data.state=e.target.value);};
    $("transcript").oninput=e=>change(()=>r.data.transcript=e.target.value);
    $("suggest").onclick=async()=>{try{await save();notice("正在提取草稿…");const result=await api("/api/suggest",{region_id:r.id});change(()=>{region().data.transcript=result.text;region().data.draft_source=result.source;region().data.state="pending";});renderInspector();notice("已填入文字草稿，请校正后选择信息状态");}catch(e){notice(e.message,true);}};
  }else if(r.type==="table")bindTable(r,s);else bindDiagram(r,s);
}
function tableHTML(r,s){
  const d=r.data,cell=s?.kind==="cell"?d.cells.find(x=>x.id===s.id):null;
  let grid="<table>";for(let row=0;row<d.rows;row++){grid+="<tr>";for(const c of d.cells.filter(x=>x.row===row).sort((a,b)=>a.col-b.col))grid+=`<td rowspan="${c.rowspan}" colspan="${c.colspan}"><button class="${c.id===cell?.id?"active":""}" data-cell="${esc(c.id)}">${esc(c.text||"（空）")}<small>${statuses[c.state]}</small></button></td>`;grid+="</tr>";}grid+="</table>";
  return `<h3>表格结构</h3><div class="grid"><label>行数<input id="tableRows" type="number" min="1" max="60" value="${d.rows}"></label><label>列数<input id="tableCols" type="number" min="1" max="30" value="${d.cols}"></label><label>表头行数<input id="headerRows" type="number" min="0" value="${d.header_rows}"></label><button id="resizeTable">应用行列数</button></div><div class="cell-grid">${grid}</div>${cell?`<h3>单元格 · 第${cell.row+1}行 第${cell.col+1}列</h3>${stateSelect("cellState",cell.state)}<label>单元格原文<textarea id="cellText">${esc(cell.text)}</textarea></label><div class="grid"><label>跨行<input id="rowSpan" type="number" min="1" value="${cell.rowspan}"></label><label>跨列<input id="colSpan" type="number" min="1" value="${cell.colspan}"></label></div><div class="actions"><button id="mergeCell">应用合并范围</button><button id="splitCell">拆分此单元格</button></div>`:"<p>点击单元格，填写原文、信息状态和合并范围。空白单元格也需人工确认。</p>"}<details><summary>从 Excel 或制表符文本批量填入</summary><textarea id="tablePaste" placeholder="列间用 Tab，行间用换行"></textarea><button id="pasteTable">用粘贴内容替换表格</button></details>`;
}
function fillTable(rows,cols,cells){const occupied=new Set();for(const c of cells)for(let y=c.row;y<c.row+c.rowspan;y++)for(let x=c.col;x<c.col+c.colspan;x++)occupied.add(`${y},${x}`);const result=[...cells];for(let y=0;y<rows;y++)for(let x=0;x<cols;x++)if(!occupied.has(`${y},${x}`))result.push({id:uid("C"),row:y,col:x,rowspan:1,colspan:1,text:"",state:"pending",evidence:[]});return result;}
function bindTable(r,s){
  const d=r.data;
  document.querySelectorAll("[data-cell]").forEach(b=>b.onclick=()=>{state.selection={kind:"cell",id:b.dataset.cell};renderInspector();renderMap();});
  $("resizeTable").onclick=()=>{const rows=Number($("tableRows").value),cols=Number($("tableCols").value),headers=Number($("headerRows").value);if(!Number.isInteger(rows)||!Number.isInteger(cols)||rows<1||rows>60||cols<1||cols>30||headers<0||headers>rows)return notice("行列数或表头行数无效",true);change(()=>{d.rows=rows;d.cols=cols;d.header_rows=headers;d.cells=fillTable(rows,cols,d.cells.filter(c=>c.row+c.rowspan<=rows&&c.col+c.colspan<=cols));});state.selection=null;renderInspector();};
  $("pasteTable").onclick=()=>{const raw=$("tablePaste").value.replace(/\r\n?/g,"\n").replace(/\n$/,""),values=raw.split("\n").map(row=>row.split("\t")),cols=Math.max(...values.map(x=>x.length));if(values.length>60||cols>30)return notice("最多60行、30列",true);change(()=>{r.data={rows:values.length,cols,header_rows:1,cells:values.flatMap((row,y)=>Array.from({length:cols},(_,x)=>({id:uid("C"),row:y,col:x,rowspan:1,colspan:1,text:row[x]||"",state:"visible",evidence:[]})))};});state.selection=null;renderInspector();};
  if(s?.kind!=="cell")return;const cell=d.cells.find(c=>c.id===s.id);if(!cell)return;
  $("cellText").oninput=e=>change(()=>cell.text=e.target.value);
  $("cellState").onchange=e=>change(()=>cell.state=e.target.value);
  $("mergeCell").onclick=()=>{const rs=Number($("rowSpan").value),cs=Number($("colSpan").value);if(!Number.isInteger(rs)||!Number.isInteger(cs)||rs<1||cs<1||cell.row+rs>d.rows||cell.col+cs>d.cols)return notice("合并范围超出表格",true);const overlaps=d.cells.filter(c=>c.id!==cell.id&&c.row<cell.row+rs&&c.row+c.rowspan>cell.row&&c.col<cell.col+cs&&c.col+c.colspan>cell.col);if(overlaps.some(c=>c.row<cell.row||c.col<cell.col||c.row+c.rowspan>cell.row+rs||c.col+c.colspan>cell.col+cs))return notice("先拆分相交的合并单元格",true);change(()=>{const remove=new Set(overlaps.map(c=>c.id));cell.rowspan=rs;cell.colspan=cs;d.cells=fillTable(d.rows,d.cols,d.cells.filter(c=>!remove.has(c.id)));});renderInspector();};
  $("splitCell").onclick=()=>{change(()=>{cell.rowspan=1;cell.colspan=1;d.cells=fillTable(d.rows,d.cols,d.cells);});renderInspector();};
}
function diagramHTML(r,s){
  const d=r.data,objects=d.objects.map(o=>`<button data-object="${esc(o.id)}">${esc(o.name||o.id)} · ${esc(o.kind)}</button>`).join(""),edges=d.connections.map(e=>`<button data-edge="${esc(e.id)}">${esc(object(e.source)?.name||e.source)} ↔ ${esc(object(e.target)?.name||e.target)} · ${relations[e.relation]}</button>`).join("");
  let editor="";
  if(s?.kind==="object"){
    const o=object(s.id);editor=`<h3>对象</h3><div class="grid"><label>名称<input id="objectName" value="${esc(o.name)}"></label><label>类型<select id="objectKind">${options({station:"站点",equipment:"设备",cabinet:"机柜",unit:"单元",port:"端口",junction:"接点",other:"其他"},o.kind)}</select></label>${stateSelect("nameState",o.name_state)}<label>所属对象<select id="parentObject"><option value="">无</option>${d.objects.filter(x=>x.id!==o.id).map(x=>`<option value="${x.id}" ${o.parent_id===x.id?"selected":""}>${esc(x.name||x.id)}</option>`).join("")}</select></label></div><div class="actions"><button id="adjustObject">重新框选位置</button><button id="deleteObject" class="danger">删除对象及其连接</button></div>`;
  }else if(s?.kind==="edge"){
    const e=edge(s.id),oopts=id=>d.objects.map(o=>`<option value="${o.id}" ${o.id===id?"selected":""}>${esc(o.name||o.id)}</option>`).join(""),spec=e.attributes[state.attribute]||pending();
    editor=`<h3>连接与属性</h3><div class="grid"><label>起点<select id="edgeSource">${oopts(e.source)}</select></label><label>终点<select id="edgeTarget">${oopts(e.target)}</select></label><label>关系类型<select id="edgeRelation">${options(relations,e.relation)}</select></label><label class="checkbox"><input id="edgeDirected" type="checkbox" ${e.directed?"checked":""}>有方向</label></div><p>${e.path.length>=2?"已记录人工路径点":"尚未描原图路径，图中虚线仅示意端点"}</p><label>当前字段<select id="attribute">${options({connection:"连接本身",...attrs},state.attribute)}</select></label>${state.attribute==="connection"?"":`${factHTML(spec)}<div class="actions"><button id="dropAttribute">本轮不评此字段</button></div>`}<div class="actions"><button id="deleteEdge" class="danger">删除连接</button></div>`;
  }
  return `<h3>图示范围</h3><label>区域角色<select id="regionRole">${options({main_diagram:"主图",inset:"局部图",legend:"图例",table:"表格",notes:"说明",mixed:"混合",unknown:"无法确定"},d.region_role)}</select></label><p>框住符号本体，名称另填；不同子图分别建区域。交叉不自动产生接点。</p><div class="row-list">${objects}${edges}</div>${editor}`;
}
function bindDiagram(r,s){
  $("regionRole").onchange=e=>change(()=>r.data.region_role=e.target.value);
  document.querySelectorAll("[data-object]").forEach(b=>b.onclick=()=>{state.selection={kind:"object",id:b.dataset.object};renderInspector();renderMap();});
  document.querySelectorAll("[data-edge]").forEach(b=>b.onclick=()=>{state.selection={kind:"edge",id:b.dataset.edge};renderInspector();renderMap();});
  if(s?.kind==="object"){
    const o=object(s.id);$("objectName").oninput=e=>change(()=>o.name=e.target.value);$("objectKind").onchange=e=>change(()=>o.kind=e.target.value);$("nameState").onchange=e=>change(()=>o.name_state=e.target.value);$("parentObject").onchange=e=>change(()=>o.parent_id=e.target.value||null);
    $("adjustObject").onclick=()=>setMode("adjust-object");
    $("deleteObject").onclick=()=>{change(()=>{r.data.objects=r.data.objects.filter(x=>x.id!==o.id);for(const x of r.data.objects)if(x.parent_id===o.id)x.parent_id=null;r.data.connections=r.data.connections.filter(e=>e.source!==o.id&&e.target!==o.id);});state.selection=null;renderInspector();renderMap();};
  }else if(s?.kind==="edge"){
    const e=edge(s.id);
    for(const [id,key] of [["edgeSource","source"],["edgeTarget","target"]])$(id).onchange=event=>{const val=event.target.value;if(val===e[key==="source"?"target":"source"]){event.target.value=e[key];return notice("连接需要两个不同端点",true);}change(()=>{e[key]=val;e.path=[];for(const spec of Object.values(e.attributes))spec.state="pending";});renderMap();renderInspector();};
    $("edgeRelation").onchange=event=>change(()=>e.relation=event.target.value);
    $("edgeDirected").onchange=event=>change(()=>e.directed=event.target.checked);
    $("attribute").onchange=event=>{state.attribute=event.target.value;renderInspector();renderMap();};
    if(state.attribute!=="connection"){
      bindFact(create=>{if(create&&!e.attributes[state.attribute])e.attributes[state.attribute]=pending();return e.attributes[state.attribute]||pending();});
      $("dropAttribute").onclick=()=>{change(()=>delete e.attributes[state.attribute]);renderInspector();notice("该字段未纳入本轮属性评分；不代表图中未提供");};
    }
    $("deleteEdge").onclick=()=>{change(()=>r.data.connections=r.data.connections.filter(x=>x.id!==e.id));state.selection=null;renderInspector();renderMap();};
  }
}
function contextsHTML(r){
  const others=state.project.regions.filter(x=>x.id!==r.id),opts=others.map(x=>`<option value="${x.id}">第${x.page}页 · ${types[x.type]} · ${esc(x.title||x.id)}</option>`).join("");
  return `<div class="row-list">${r.contexts.map((c,i)=>`<div>${esc(c.region_id)} · ${esc(c.purpose)} <button data-remove-context="${i}">移除</button></div>`).join("")}</div><label>关联区域<select id="contextRegion"><option value="">选择另一块区域</option>${opts}</select></label><label>适用说明<input id="contextPurpose" placeholder="例如：解释主图中的建设状态线型"></label><div class="actions"><button id="addContext">添加为模型上下文</button></div><p>最多两块额外上下文；仅 C 条件向模型提供，标为人工选择诊断。</p><label>信息关联类型<select id="linkRelation">${options({explains:"解释",same_entity:"同一实体",conflicts:"内容冲突",applies_to:"适用于"},"explains")}</select></label><button id="addLink">记录区域关联</button><div class="row-list">${state.project.links.filter(l=>l.source===r.id).map(l=>`<div>${esc(l.relation)} → ${esc(l.target)} <button data-remove-link="${esc(l.id)}">移除</button></div>`).join("")}</div>`;
}
function bindContexts(r){
  $("addContext").onclick=()=>{const id=$("contextRegion").value,purpose=$("contextPurpose").value.trim();if(!id||!purpose)return notice("请选择区域并填写适用说明",true);if(r.contexts.length>=2||r.contexts.some(x=>x.region_id===id))return notice("上下文已存在或达到两块上限",true);change(()=>r.contexts.push({region_id:id,purpose}));renderInspector();};
  document.querySelectorAll("[data-remove-context]").forEach(b=>b.onclick=()=>{change(()=>r.contexts.splice(Number(b.dataset.removeContext),1));renderInspector();});
  $("addLink").onclick=()=>{const id=$("contextRegion").value;if(!id)return notice("请选择关联区域",true);const rel=$("linkRelation").value;change(()=>state.project.links.push({id:uid("L"),source:r.id,target:id,relation:rel,note:$("contextPurpose").value.trim()}));renderInspector();};
  document.querySelectorAll("[data-remove-link]").forEach(b=>b.onclick=()=>{change(()=>state.project.links=state.project.links.filter(l=>l.id!==b.dataset.removeLink));renderInspector();});
}
function cancelMode(){state.mode="select";state.edgeStart=null;state.path=[];state.pointer=null;$("finishPath").hidden=true;document.querySelectorAll("[data-mode]").forEach(b=>b.classList.remove("active"));$("hint").textContent="滚轮缩放 · 拖动平移 · 点击区域或对象编辑";}
function setMode(mode){
  const r=region();if(["object","connection","path","adjust-object"].includes(mode)&&(!r||r.type!=="diagram"||r.page!==state.page))return notice("请先选择本页的图纸区域",true);
  if(mode==="evidence"&&!r)return notice("请先选择要关联证据的记录",true);
  if(mode==="adjust-region"&&!r)return notice("请先选择区域",true);
  if(mode==="path"&&state.selection?.kind!=="edge")return notice("请先选择一条连接",true);
  cancelMode();state.mode=mode;
  if(mode==="path"){state.path=[center(object(edge(state.selection.id).source))];$("finishPath").hidden=false;}
  document.querySelectorAll("[data-mode]").forEach(b=>b.classList.toggle("active",b.dataset.mode===mode));
  $("hint").textContent={region:"拖动框选区域",object:"拖动框住对象符号本体",connection:"依次点击起点和终点对象",path:"沿原图点击转折点，再点击“完成路径”",evidence:"拖动框住证据；可切换页码后继续框选","adjust-object":"重新框住当前对象","adjust-region":"重新框选当前区域；须包含已有对象"}[mode];
}
function pickEndpoint(id){if(!state.edgeStart){state.edgeStart=id;renderMap();return;}if(state.edgeStart===id)return notice("请选择不同的终点",true);const r=region(),e={id:uid("E"),source:state.edgeStart,target:id,relation:"cable",directed:false,path:[],attributes:Object.fromEntries(["cable_type","cores","length","construction"].map(k=>[k,pending()])),evidence:[]};change(()=>r.data.connections.push(e));state.selection={kind:"edge",id:e.id};cancelMode();render();}
function mapPoint(event){const p=new DOMPoint(event.clientX,event.clientY).matrixTransform($("pageLayer").getScreenCTM().inverse());return [p.x,p.y];}
function canvasPoint(event){const p=new DOMPoint(event.clientX,event.clientY).matrixTransform($("canvas").getScreenCTM().inverse());return [p.x,p.y];}
function normalized(p){const [w,h]=dimensions();return [Math.max(0,Math.min(1,p[0]/w)),Math.max(0,Math.min(1,p[1]/h))].map(n=>Math.round(n*1e6)/1e6);}
function boxFrom(a,b){return [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[0],b[0]),Math.max(a[1],b[1])];}
$("canvas").addEventListener("pointerdown",e=>{if(e.button!==0)return;const p=mapPoint(e);state.pointer={p,canvas:canvasPoint(e),start:normalized(p),view:[...state.view],moved:false};if(state.mode!=="select"&&state.mode!=="connection")$("canvas").setPointerCapture(e.pointerId);});
$("canvas").addEventListener("pointermove",e=>{const drag=state.pointer;if(!drag)return;const p=mapPoint(e),delta=Math.hypot(p[0]-drag.p[0],p[1]-drag.p[1]);if(delta>state.view[2]*.003)drag.moved=true;
  if(state.mode==="select"&&drag.moved){const cp=canvasPoint(e);state.view=[state.view[0]+drag.canvas[0]-cp[0],state.view[1]+drag.canvas[1]-cp[1],state.view[2],state.view[3]];setView();}
  else if(["region","object","evidence","adjust-object","adjust-region"].includes(state.mode)){const b=boxFrom(drag.start,normalized(p)),[w,h]=dimensions();$("draftLayer").replaceChildren(svg("rect",{x:b[0]*w,y:b[1]*h,width:(b[2]-b[0])*w,height:(b[3]-b[1])*h,class:"draft-box"}));}
});
$("canvas").addEventListener("pointerup",e=>{const drag=state.pointer;if(!drag)return;const end=normalized(mapPoint(e));
  if(state.mode==="path"){state.path.push(end);renderMap();state.pointer=null;return;}
  if(!["region","object","evidence","adjust-object","adjust-region"].includes(state.mode)){setTimeout(()=>{state.pointer=null;},0);return;}
  const b=boxFrom(drag.start,end);if(b[2]-b[0]<.0005||b[3]-b[1]<.0005){state.pointer=null;$("draftLayer").replaceChildren();return;}
  const r=region();
  if(state.mode==="region"){
    const kind=$("newType").value,id=uid("R"),data=kind==="text"?{transcript:"",state:"pending",draft_source:null}:kind==="table"?{rows:3,cols:3,header_rows:1,cells:fillTable(3,3,[])}:{region_role:"main_diagram",objects:[],connections:[]};
    change(()=>state.project.regions.push({id,page:state.page,bbox:b,rotation_degrees:state.rotation,type:kind,title:"",status:"draft",reviewer:"",reviewed_at:null,items:[],contexts:[],notes:"",data}));state.region=id;state.selection=null;
  }else if(state.mode==="adjust-region"){
    if(r.type==="diagram"&&r.data.objects.some(o=>o.bbox[0]<b[0]||o.bbox[1]<b[1]||o.bbox[2]>b[2]||o.bbox[3]>b[3])){state.pointer=null;return notice("新区域框必须包含已有对象",true);}
    change(()=>r.bbox=b);
  }else if(state.mode==="evidence")change(()=>currentEvidence(true).push({page:state.page,bbox:b}));
  else{
    if(b[0]<r.bbox[0]||b[1]<r.bbox[1]||b[2]>r.bbox[2]||b[3]>r.bbox[3]){state.pointer=null;$("draftLayer").replaceChildren();return notice("对象框必须在当前图纸区域内",true);}
    if(state.mode==="adjust-object")change(()=>object(state.selection.id).bbox=b);
    else {const o={id:uid("N"),bbox:b,kind:"station",name:"",name_state:"pending",parent_id:null,evidence:[]};change(()=>r.data.objects.push(o));state.selection={kind:"object",id:o.id};}
  }
  cancelMode();render();
});
$("canvas").addEventListener("wheel",e=>{e.preventDefault();zoom(e.deltaY>0?1.15:1/1.15);},{passive:false});
$("pageImage").addEventListener("load",()=>$("imageStatus").textContent=`第 ${state.page} 页`);
$("pageImage").addEventListener("error",()=>notice("页面图像载入失败，请检查服务终端",true));
$("finishPath").onclick=()=>{const e=edge(state.selection.id);change(()=>e.path=[...state.path,center(object(e.target))]);cancelMode();render();};
document.querySelectorAll("[data-mode]").forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
$("cancelMode").onclick=()=>{cancelMode();renderMap();};$("fit").onclick=fit;$("focus").onclick=focus;$("zoomIn").onclick=()=>zoom(1/1.4);$("zoomOut").onclick=()=>zoom(1.4);
$("page").onchange=e=>{state.page=Number(e.target.value);state.rotation=0;if(state.mode!=="evidence"){state.region=null;state.selection=null;cancelMode();}fit();render();};
function rotate(delta){state.rotation=(state.rotation+delta+360)%360;const r=region();if(r&&r.page===state.page)change(()=>r.rotation_degrees=state.rotation);fit();renderMap();notice(`额外旋转 ${state.rotation}°；标注坐标仍保存到原始整页`);}
$("rotateLeft").onclick=()=>rotate(-90);$("rotateRight").onclick=()=>rotate(90);
$("actor").onchange=()=>{try{localStorage.setItem("document-annotation-actor",$("actor").value);}catch(_){}if(state.dirty)save().catch(e=>notice(e.message,true));};
$("save").onclick=()=>save().catch(e=>notice(e.message,true));
for(const direction of ["undo","redo"])$(direction).onclick=()=>{const from=state[direction],to=state[direction==="undo"?"redo":"undo"];if(!from.length)return;to.push(snapshot());const next=from.pop();state.project.regions=next.regions;state.project.links=next.links;for(const r of state.project.regions){r.status="draft";r.reviewer="";r.reviewed_at=null;}if(!region()){state.region=null;state.selection=null;}markDirty();cancelMode();render();};
$("backup").onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(state.project,null,2)],{type:"application/json"})),a=document.createElement("a");a.href=url;a.download="annotation-browser-draft.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$("export").onclick=async()=>{try{await save();const result=await api("/api/export",{condition:$("condition").value});$("exportResult").replaceChildren();const p=document.createElement("p");p.textContent="评测集已保存："+result.directory;const a=document.createElement("a");a.href=result.download_url;a.textContent="下载标注与评测清单";$("exportResult").append(p,a);notice("导出完成：推理只使用 manifest.json，评分另外使用 gold.json");}catch(e){notice(e.message,true);}};
window.addEventListener("beforeunload",e=>{if(state.dirty){e.preventDefault();e.returnValue="";}});
window.addEventListener("keydown",e=>{if(e.key==="Escape"){cancelMode();renderMap();}});
(async()=>{try{state.token=(await api("/api/session")).token;state.project=await api("/api/project");try{$("actor").value=localStorage.getItem("document-annotation-actor")||"";}catch(_){}$("page").innerHTML=state.project.document.pages.map(p=>`<option value="${p.page}">第 ${p.page} 页</option>`).join("");fit();render();notice("已打开本地工程 · 先填写标注人，再框选区域");}catch(e){notice(e.message,true);}})();
