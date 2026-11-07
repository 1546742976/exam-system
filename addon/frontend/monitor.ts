// Source for addon/public/exam-monitor.js; compiled with the frontend build script.
const message = document.getElementById('message')!;
async function action(action: string, extra: object={}) {
  const response=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...extra})});
  if(!response.ok) throw new Error(`操作失败 (${response.status})`); message.textContent='操作已提交';await refresh();
}
async function loadHistory(studentId:number) {
  const response=await fetch(`${location.pathname}?history=${studentId}`);if(!response.ok)throw new Error('无法读取图片');
  const {rows}=await response.json();const host=document.getElementById('monitor-history')!;host.replaceChildren();
  for(const row of rows){const details=document.createElement('details');const title=document.createElement('summary');title.textContent=new Date(row.completedAt).toLocaleString();details.append(title);details.addEventListener('toggle',()=>{if(details.open&&!details.querySelector('img')){const image=document.createElement('img');image.src=`${location.pathname}?image=${encodeURIComponent(row._id)}`;image.style.maxWidth='100%';details.append(image);}});host.append(details);}
}
async function refresh(){const response=await fetch(`${location.pathname}?status=1`);if(!response.ok)throw new Error('状态获取失败');const data=await response.json();const host=document.getElementById('monitor-list')!;host.replaceChildren();
  const labels:Record<string,string>={UNREACHABLE:'设备不可达',TLS_FAILED:'证书校验失败',AUTH_FAILED:'认证失败',FRAME_UNAVAILABLE:'截图不可用',TIMEOUT:'任务超时',IMAGE_TOO_LARGE:'图片过大',CANCELLED:'已取消',INVALID_JPEG:'图片格式错误',CAPTURE_FAILED:'采集失败'};
  if(data.active && (!data.collectorSeenAt || Date.now()-data.collectorSeenAt>10000))message.textContent='采集进程未更新状态，请检查服务';
  for(const mapping of data.mappings){const row=data.rows.find((r:any)=>r._id===mapping.studentId)?.latest;const runtime=data.runtime.find((r:any)=>r.studentId===mapping.studentId);const success=data.successes.find((r:any)=>r._id===mapping.studentId);const line=document.createElement('p');line.textContent=`学生 ${mapping.studentId} · ${mapping.deviceId} · ${row ? (labels[row.error]??row.error??'截图成功')+' · '+new Date(row.completedAt).toLocaleTimeString() : '等待首次检测'} · ${runtime?.pending?'排队 '+Math.round(runtime.queueDelayMs/1000)+'秒':runtime?.running?'正在采集':'等待下次检测'} · 最近成功 ${success?new Date(success.lastSuccessAt).toLocaleTimeString():'暂无'}`;
    for(const [label,fn] of [['抽查',()=>action('capture',{studentId:mapping.studentId})],['历史图片',()=>loadHistory(mapping.studentId)]] as const){const button=document.createElement('button');button.textContent=label;button.onclick=()=>void fn().catch(showError);line.append(button);}host.append(line);}}
function showError(e:Error){message.textContent=e.message;}
document.querySelectorAll<HTMLButtonElement>('[data-action]').forEach(button=>button.onclick=()=>void action(button.dataset.action!,button.dataset.action==='import'?{mappings:(document.getElementById('mapping') as HTMLTextAreaElement).value}:{}).catch(showError));
void refresh().catch(showError);setInterval(()=>void refresh().catch(showError),15000);

