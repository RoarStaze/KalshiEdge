"""Weather History presentation matching Bitcoin History, using verified records only."""

HISTORY_STYLE = r"""
.wx-shell[data-weather-section="history"]{max-width:1480px;margin:12px auto 0;gap:17px}
.wx-shell[data-weather-section="history"]>.wx-heading{display:none}
.wx-history{display:grid;gap:17px}
.wx-history-statusline{display:flex;justify-content:space-between;gap:10px;align-items:center;
  color:#a8b9c2;border-top:1px solid #ffffff22;padding:10px 1px 6px;
  font-size:11px;font-variant-numeric:tabular-nums}
.wx-history-statusline strong{font-weight:650;color:#aee5df}
.wx-history-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.wx-history-kpi{min-width:0;min-height:137px;padding:20px 19px!important;
 display:flex;flex-direction:column;justify-content:center;gap:9px}
.wx-history-kpi-label{color:#b5c3cb;font-size:11px;line-height:1.4}
.wx-history-kpi-num{font-size:clamp(24px,2.3vw,34px);line-height:1.05;
 font-weight:850;letter-spacing:-1px;color:#f2f5f7;font-variant-numeric:tabular-nums}
#wx-history-accuracy{color:#36e5c4}
.wx-history-kpi-caption{font-size:10px;color:#a5b5bc;line-height:1.5}
.wx-history-ledger{padding:0!important;min-width:0;overflow:hidden}
.wx-history-controls{display:flex;justify-content:space-between;align-items:center;
 gap:13px;flex-wrap:wrap;padding:19px 18px;border-bottom:1px solid #8a9b9d3a;
 background:rgba(30,39,39,.55)}
.wx-history-controls h2{font-size:14px;margin:0;color:#edf5f0}
.wx-history-selectors{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.wx-history-selectors label{display:flex;gap:7px;align-items:center;font-size:11px;color:#bbc9d2}
.wx-history-selectors select{border:1px solid #567686;border-radius:7px;
  padding:9px 12px;background:#11252b;color:#e8f6f3;cursor:pointer;font-size:12px}
.wx-history-table-wrap{width:100%;overflow-x:auto}
.wx-history-table{width:100%;min-width:1030px;border-collapse:collapse;font-size:11px}
.wx-history-table thead th{text-align:left;color:#a5bbc5;font-size:9px;font-weight:750;
  letter-spacing:1.1px;text-transform:uppercase;background:#11272e;
  padding:15px 16px;white-space:nowrap}
.wx-history-table tbody tr:nth-child(odd){background:#31332d74}
.wx-history-table tbody tr:nth-child(even){background:#1b323083}
.wx-history-table tbody td{padding:14px 16px;color:#d7e7e5;
  border-top:1px solid #71818c2a;vertical-align:middle;font-variant-numeric:tabular-nums}
.wx-history-table tbody tr:hover{background:#2449429a}
.wx-history-table .wx-h-ticker{font-size:10px;white-space:normal;overflow-wrap:anywhere;max-width:220px}
.wx-history-table .wx-h-ticker span{display:block;color:#90aeb6;margin-top:5px;font-size:10px}
.wx-history-pill{display:inline-block;border:1px solid #526476;
  padding:5px 8px;border-radius:5px;font-size:10px;font-weight:850;
  letter-spacing:.4px;line-height:1.1;white-space:nowrap}
.wx-history-pill.ABOVE,.wx-history-pill.CORRECT{color:#55e9cb;background:#124d4480;border-color:#238a76}
.wx-history-pill.BELOW,.wx-history-pill.INCORRECT{color:#ff87a8;background:#59283b70;border-color:#98536b}
.wx-history-pill.SKIP{color:#ffdb86;background:#4735248a;border-color:#987040}
.wx-history-pill.PENDING{color:#b3cce0;background:#263f527d;border-color:#597788}
.wx-history-blank{text-align:center;padding:33px 18px!important;color:#a9c4ce!important}
.wx-history-pager{display:flex;align-items:center;justify-content:flex-end;gap:13px;
  padding:12px 19px;border-top:1px solid #8a9b9d2a}
.wx-history-pager button{background:#102b31;border:1px solid #51817c;
  color:#bfe8de;border-radius:7px;padding:8px 15px;cursor:pointer}
.wx-history-pager button:disabled{opacity:.36;cursor:not-allowed}
.wx-history-pager span{font-size:11px;color:#a9c5cf}
.wx-history-footnote{padding:12px 1px;color:#9cbbc2;font-size:11px;line-height:1.65}
.wx-history-proof{margin-top:0}
.wx-history-proof summary{font-size:11px;color:#b8e3d4;cursor:pointer}
.wx-history-proof .wx-panel{margin-top:12px}
@media(max-width:980px){.wx-history-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:550px){
 .wx-history-kpis{gap:9px}
 .wx-history-kpi{padding:13px 12px!important;min-height:115px}
 .wx-history-kpi-num{font-size:24px}
 .wx-history-controls{padding:13px}
 .wx-history-selectors select{padding:7px}
 .wx-history-statusline{flex-wrap:wrap}
}
"""

HISTORY_MARKUP = """
<section class="wx-history" aria-label="Recorded hourly weather predictions">
 <div class="wx-history-statusline">
  <span id="wx-history-status" role="status">Loading recorded weather forecasts…</span>
  <span>Official Kalshi Index + exact settled market · paper only</span>
 </div>
 <div class="wx-history-kpis">
  <article class="wx-panel wx-history-kpi">
   <span class="wx-history-kpi-label">Settled forecast accuracy</span>
   <strong class="wx-history-kpi-num" id="wx-history-accuracy">—</strong>
   <span class="wx-history-kpi-caption" id="wx-history-accuracy-detail">Awaiting verified directional predictions</span>
  </article>
  <article class="wx-panel wx-history-kpi">
   <span class="wx-history-kpi-label">Correct / Incorrect</span>
   <strong class="wx-history-kpi-num" id="wx-history-correctness">0 / 0</strong>
   <span class="wx-history-kpi-caption">Only source-verified settled ABOVE/BELOW forecasts</span>
  </article>
  <article class="wx-panel wx-history-kpi">
   <span class="wx-history-kpi-label">SKIP decisions</span>
   <strong class="wx-history-kpi-num" id="wx-history-skips">0</strong>
   <span class="wx-history-kpi-caption">Exact 50/50 ties only · excluded from accuracy</span>
  </article>
  <article class="wx-panel wx-history-kpi">
   <span class="wx-history-kpi-label">Pending settlement</span>
   <strong class="wx-history-kpi-num" id="wx-history-pending">0</strong>
   <span class="wx-history-kpi-caption">Unsettled or unverified · never counted as wrong</span>
  </article>
 </div>
 <section class="wx-panel wx-history-ledger" aria-label="Weather forecast result table">
  <div class="wx-history-controls">
   <h2>Recorded weather forecasts</h2>
   <div class="wx-history-selectors">
    <label for="wx-history-city">City
     <select id="wx-history-city">
      <option value="all">All cities</option>
      <option value="chicago">Chicago Metro</option>
      <option value="nyc">New York City</option>
      <option value="miami">Miami</option>
      <option value="la-coastal">Coastal Los Angeles</option>
     </select>
    </label>
    <label for="wx-history-filter">Show
     <select id="wx-history-filter">
      <option value="ALL">All forecasts</option>
      <option value="CORRECT">Correct</option>
      <option value="INCORRECT">Incorrect</option>
      <option value="SKIP">SKIP</option>
      <option value="PENDING">Pending</option>
     </select>
    </label>
   </div>
  </div>
  <div class="wx-history-table-wrap">
   <table class="wx-history-table">
    <thead><tr>
     <th>Market closes (local)</th>
     <th>Market</th>
     <th>Forecast</th>
     <th>Probability</th>
     <th>Confidence</th>
     <th>Settled outcome</th>
     <th>Result</th>
    </tr></thead>
    <tbody id="wx-history-rows">
     <tr><td colspan="7" class="wx-history-blank">Loading saved weather forecasts…</td></tr>
    </tbody>
   </table>
  </div>
  <div class="wx-history-pager">
   <button type="button" id="wx-history-prev" disabled>Previous</button>
   <span id="wx-history-page-label">0 records</span>
   <button type="button" id="wx-history-next" disabled>Next</button>
  </div>
 </section>
 <div class="wx-history-footnote">
  Accuracy = Correct ÷ (Correct + Incorrect), counting only saved, settled,
  source-reconciled predictions. Confidence is derived model directional strength,
  not proven accuracy. SKIPs are exact 50/50 ties only; no retrospective 69% weather gate.
  The five V9 lead-time checkpoints are assessed separately on Train and never multiply
  one market's results in this history.
 </div>
</section>
"""

HISTORY_SCRIPT = r"""<script>
(() => {
 "use strict";
 const el=(id)=>document.getElementById(id);
 const fill=(id,value)=>{const node=el(id);if(node)node.textContent=String(value);};
 const selector=el("wx-history-filter"),city=el("wx-history-city");
 const rows=el("wx-history-rows");
 const prev=el("wx-history-prev"),next=el("wx-history-next");
 if(!selector||!city||!rows)return;
 let offset=0,loading=false;
 const pageSize=50;
 const localTime=(value)=>{
    if(!value)return "Close unavailable";
    const date=new Date(value);
    return Number.isFinite(date.getTime())?date.toLocaleString():"Close unavailable";
 };
 const percentage=(value)=>Number.isFinite(value)?
      (100*value).toFixed(2)+"%":"—";
 const cell=(tr,content,css)=>{
   const td=document.createElement("td");
   if(css)td.className=css;
   if(content instanceof Node)td.append(content);
   else td.textContent=String(content);
   tr.append(td);return td;
 };
 const pill=(kind)=>{
   const span=document.createElement("span");
   const known=["ABOVE","BELOW","CORRECT","INCORRECT","SKIP","PENDING"];
   const safe=known.includes(kind)?kind:"PENDING";
   span.className="wx-history-pill "+safe;
   span.textContent=safe;return span;
 };
 const blank=(message)=>{
   rows.replaceChildren();
   const tr=document.createElement("tr"),td=document.createElement("td");
   td.colSpan=7;td.className="wx-history-blank";td.textContent=message;
   tr.append(td);rows.append(tr);
 };
 const render=(data)=>{
   const s=data.summary||{};
   fill("wx-history-accuracy",percentage(s.accuracy));
   fill("wx-history-accuracy-detail",
      (s.settled_directional_forecasts||0)+" verified settled directional predictions");
   fill("wx-history-correctness",(s.correct||0)+" / "+(s.incorrect||0));
   fill("wx-history-skips",s.skipped||0);
   fill("wx-history-pending",s.pending_settlement||0);
   const size=Number.isInteger(data.filtered_count)?data.filtered_count:0;
   const label=data.status==="READY"?"":String(data.status||"WAITING")+" · ";
   fill("wx-history-status",label+(s.total_final_forecasts||0)+
     " saved forecasts · Refreshed "+new Date().toLocaleTimeString());
   prev.disabled=offset<=0;
   next.disabled=data.next_offset==null;
   const shown=(Array.isArray(data.forecasts)?data.forecasts:[]).length;
   fill("wx-history-page-label",size?
     (offset+1)+"–"+(offset+shown)+" of "+size:"0 records");
   rows.replaceChildren();
   if(!shown){
      blank(size?"No records on this page.":"No recorded forecasts match these filters yet.");
      return;
   }
   for(const record of data.forecasts){
      const tr=document.createElement("tr");
      const close=cell(tr,localTime(record.market_close));
      if(!record.market_close)close.title="Official close was not available in the original snapshot";
      const marketCell=document.createElement("span");
      marketCell.textContent=String(record.market_ticker||"—");
      const location=document.createElement("span");
      location.textContent=String(record.city||"—")+
        " · threshold "+(Number.isFinite(record.threshold_f)?
                          record.threshold_f+"°F":"—");
      marketCell.append(location);
      const td=cell(tr,marketCell,"wx-h-ticker");
      td.title=String(record.event_ticker||"");
      cell(tr,pill(record.forecast));
      cell(tr,percentage(record.directional_probability));
      cell(tr,percentage(record.confidence));
      cell(tr,record.actual_side||"Awaiting verified settlement");
      cell(tr,pill(record.verdict));
      rows.append(tr);
   }
 };
 async function refreshWeatherHistory(){
    if(loading)return;
    loading=true;
    try{
       const params=new URLSearchParams({
          city:city.value,filter:selector.value,offset:String(offset),
          limit:String(pageSize)});
       const response=await fetch("/api/weather/hourly/history?"+params.toString(),
          {cache:"no-store"});
       if(!response.ok)throw new Error("History HTTP "+response.status);
       const data=await response.json();
       if(data.status==="HOURLY_HISTORY_AUDIT_ERROR")
          throw new Error("Source or verdict audit failed · cannot report accuracy");
       render(data);
    }catch(error){
       fill("wx-history-status","Weather history unavailable · "+error.message);
       for(const id of ["wx-history-accuracy","wx-history-correctness",
                         "wx-history-skips","wx-history-pending"])
          fill(id,"—");
       fill("wx-history-accuracy-detail","Scores withheld; evidence unavailable");
       fill("wx-history-page-label","—");
       prev.disabled=true;next.disabled=true;
       blank("Weather history cannot be verified. No results are counted.");
    }finally{loading=false;}
 }
 selector.addEventListener("change",()=>{offset=0;refreshWeatherHistory();});
 city.addEventListener("change",()=>{offset=0;refreshWeatherHistory();});
 prev.addEventListener("click",()=>{offset=Math.max(0,offset-pageSize);refreshWeatherHistory();});
 next.addEventListener("click",()=>{offset+=pageSize;refreshWeatherHistory();});
 refreshWeatherHistory();
 setInterval(refreshWeatherHistory,15000);
})();
</script>"""
