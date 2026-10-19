/* kid-common.js — Diya / Jemma 两份前端的公共层（路线图 #10）
 *
 * 用法：页面在引入本文件**之前**设置 window.KID_CHILD（'diya' 或 'jemma'）。
 * 本文件只收录「两份页面字节级等价（或仅空白差异）」的函数；生成时做了
 * 双向交叉校验——分别从 app.html 与 jemma.html 各生成一遍并断言一致，
 * 因此本次抽取是等价变换，不含任何行为改动。
 *
 * 抽取清单（20 个）：changeWeek, closeErrorModal, closeUploadDetail, dateKey, filterErrors, formatDate, formatRelativeDate, getTimeLeft, getWeekDates, loadChatHistory, loadTasks, nextTip, openErrorModal, prevTip, saveTasks, toast, toggleCollapse, toggleRoute, updatePlannerLabel, uploadStatusBadge
 */

// KID_CHILD 由宿主页面在引入本文件前设置；兜底避免漏设时整页报错
var KID_CHILD = window.KID_CHILD || 'diya';

function changeWeek(child,delta){
 plannerWeeks[child]=(plannerWeeks[child]||0)+delta;
 renderPlannerGrid(child);
 updatePlannerLabel(child);
}

function closeErrorModal(){ document.getElementById('error-modal').classList.remove('open'); }

function closeUploadDetail(){
  document.getElementById('upload-detail-modal').classList.remove('open');
}

function dateKey(d){
 return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

/* #12：错题改为服务端分页后，科目筛选不能再只过滤本地已加载的那页，
 * 必须重新向服务端请求（否则"筛选结果为空"是假的）。
 * 有 loadErrorBook 就重新拉取（会重置到第 1 页），否则退回本地渲染。 */
function filterErrors(subj){
 errorFilter=subj;
 document.querySelectorAll('#error-filter-bar .filter-btn').forEach(b=>b.classList.toggle('active',b.dataset.subject===subj));
 if(typeof loadErrorBook==='function'){ loadErrorBook(); } else { renderErrors(); }
}

function formatDate(d){
 return `${d.getMonth()+1}/${d.getDate()}`;
}

function formatRelativeDate(dateStr){
 if(!dateStr) return '';
 const d = new Date(dateStr);
 if(isNaN(d.getTime())) return dateStr;
 const now = new Date();
 const diffMs = now - d;
 const diffMins = Math.floor(diffMs/60000);
 const diffHrs = Math.floor(diffMs/3600000);
 const diffDays = Math.floor(diffMs/86400000);
 if(diffMins < 1) return 'Just now';
 if(diffMins < 60) return `${diffMins} mins ago`;
 if(diffHrs < 24) return `${diffHrs} hours ago`;
 if(diffDays === 1) return 'Yesterday';
 if(diffDays < 7) return `${diffDays} days ago`;
 if(diffDays < 14) return 'Last week';
 if(diffDays < 30) return `${Math.floor(diffDays/7)} weeks ago`;
 return d.toLocaleDateString('en-GB',{day:'numeric',month:'short'});
}

function getTimeLeft(target){
 const now=Date.now(), end=new Date(target).getTime();
 const diff=end-now;
 if(diff<=0) return 'Passed';
 const d=Math.floor(diff/86400000);
 const h=Math.floor((diff%86400000)/3600000);
 const m=Math.floor((diff%3600000)/60000);
 return d>0?`${d}D ${h}H`:h>0?`${h}H ${m}M`:`${m}M`;
}

function getWeekDates(child){
 const now=new Date();
 const weekStart=plannerWeeks[child]||0;
 const start=new Date(now);
 start.setDate(now.getDate()-now.getDay()+1+weekStart*7);
 const days=[];
 for(let i=0;i<7;i++){
 const d=new Date(start);
 d.setDate(start.getDate()+i);
 days.push(d);
 }
 return days;
}

async function loadChatHistory(){
 const container=document.getElementById('chat-messages');
 if(!container) return;
 try{
 const r=await fetch(`/api/chat/${KID_CHILD}?limit=50`);
 const data=await r.json();
 const msgs=Array.isArray(data)?data:(data.messages||[]);
 container.innerHTML='';
 msgs.forEach(m=>appendChatMsg(m.role,m.content));
 container.scrollTop=container.scrollHeight;
 }catch(e){
 container.innerHTML='<div style="color:var(--text2);padding:20px;text-align:center">Load failed</div>';
 }
}

async function loadTasks(child){
 try{
 const r=await fetch('/api/tasks?user='+child);
 const data=await r.json();
 tasksData[child]=data;
 // R2（2026-10-19）：恢复在线后自动补推离线期间的勾选（saveTasks 全量 PUT 天然带上）
 try{ if(localStorage.getItem('twp_pending_'+child)){ saveTasks(child); } }catch(e){}
 if(window.KidTasksBridge) window.KidTasksBridge.replay(child);
 renderTasks(child);
 }catch(e){
 // Fallback to localStorage
 const ls=localStorage.getItem('tasks_'+child);
 tasksData[child]=ls?JSON.parse(ls):{};
 if(window.KidTasksBridge) window.KidTasksBridge.replay(child);
 renderTasks(child);
 }
}

function nextTip(){
 if (currentTipIndex < todayTips.length - 1) {
  currentTipIndex++;
  const t = todayTips[currentTipIndex];
  const STORAGE_KEY = `study_tips_viewed_${KID_CHILD}`;
  let viewed = {};
  try { viewed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch(e) {}
  viewed[t.id] = (viewed[t.id] || 0) + 1;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(viewed)); } catch(e) {}
  renderTipCard();
 }
}

function openErrorModal(id){ document.getElementById('error-edit-id').value=''; document.getElementById('error-modal-title').textContent='Add Error'; document.getElementById('error-modal').classList.add('open'); }

function prevTip(){
 if (currentTipIndex > 0) { currentTipIndex--; renderTipCard(); }
}

async function saveTasks(child){
 try{
 await fetch('/api/tasks',{
 method:'POST',
 headers:{'Content-Type':'application/json'},
 body:JSON.stringify({user:child,tasks:tasksData[child]})
 }).then(r=>{ if(!r.ok) throw new Error('status '+r.status); });
 localStorage.setItem('tasks_'+child,JSON.stringify(tasksData[child]));
 try{ localStorage.removeItem('twp_pending_'+child); }catch(e){}
 toast('Synced ');
 return true;
 }catch(e){
 // Save to localStorage as fallback
 localStorage.setItem('tasks_'+child,JSON.stringify(tasksData[child]));
 try{ localStorage.setItem('twp_pending_'+child,'1'); }catch(e2){}
 toast('Saved locally');
 return false;
 }
}

/* ── R2 任务单源化（2026-10-19）─────────────────────────────────────
   三周计划卡（KidCheck data-sync="server"）的勾选状态以服务端 tasks_<child>.json
   的 twp-* 前缀键为唯一真源；localStorage kid-check-* 只作渲染缓存。
   - 写路径：KidCheck 勾选 → KidTasksBridge.setDone → tasksData[twp-*] → saveTasks PUT
     （失败置 twp_pending 标记，恢复在线后 loadTasks 自动补推）
   - 读路径：loadTasks 成功 → replay() 把服务端 twp-* 对账回 kid-check-*（跨设备一致）；
     有未同步的本地勾选时跳过对账（本地优先，防离线勾选被服务端旧值覆盖）
   - tasksData 未就绪时的早期点击挂入 twp_prewrite_ 队列，replay 时回放（防 PUT 清空任务） */
window.KidTasksBridge = {
 setDone: function(taskId, done){
  const child = (window.KID_CHILD || 'diya');
  try{
   if (tasksData && tasksData[child]) {
    this._apply(child, taskId, done);
    saveTasks(child);
   } else {
    const q = JSON.parse(localStorage.getItem('twp_prewrite_' + child) || '{}');
    q[taskId] = !!done;
    localStorage.setItem('twp_prewrite_' + child, JSON.stringify(q));
   }
  }catch(e){}
 },
 _apply: function(child, taskId, done){
  const key = 'twp-' + taskId;
  const cur = tasksData[child][key];
  if (cur && typeof cur === 'object') cur.done = !!done;
  else tasksData[child][key] = !!done;
 },
 replay: function(child){
  try{
   // 1) 回放：tasks 未就绪时的挂起勾选
   const pre = JSON.parse(localStorage.getItem('twp_prewrite_' + child) || '{}');
   if (Object.keys(pre).length && tasksData && tasksData[child]) {
    const self = this;
    Object.keys(pre).forEach(function(tid){ self._apply(child, tid, pre[tid]); });
    localStorage.setItem('twp_prewrite_' + child, '{}');
    saveTasks(child);
   }
   // 2) 对账：无未同步标记时，服务端 twp-* → localStorage kid-check-*（仅渲染缓存）
   let pending = false;
   try { pending = !!localStorage.getItem('twp_pending_' + child); } catch(e) {}
   if (!pending && tasksData && tasksData[child]) {
    const pid = window.KID_CHILD || child;
    Object.keys(tasksData[child]).forEach(function(k){
     if (typeof k !== 'string' || k.indexOf('twp-') !== 0) return;
     const v = tasksData[child][k];
     const done = (v && typeof v === 'object') ? !!v.done : !!v;
     const lkey = 'kid-check-' + pid + '-' + k.slice(4);
     const cur = localStorage.getItem(lkey);
     if ((cur === '1') !== done) localStorage.setItem(lkey, done ? '1' : '0');
    });
   }
  }catch(e){}
  if (typeof window.onTwpReconciled === 'function') { try { window.onTwpReconciled(); } catch(e) {} }
 }
};

function toast(msg,dur=2500){
 const t=document.getElementById('toast');
 t.textContent=msg;t.classList.add('show');
 setTimeout(()=>t.classList.remove('show'),dur);
}

function toggleCollapse(id){
 const body=document.getElementById(id);
 const tog=document.getElementById('tog-'+id);
 body.classList.toggle('collapsed');
 tog.classList.toggle('open');
}

function toggleRoute(id){
 const card=document.getElementById('route-'+id);
 if(card) card.classList.toggle('open');
}

function updatePlannerLabel(child){
 const days=getWeekDates(child);
 const label=document.getElementById(child+'-week-label');
 if(label) label.textContent=`${formatDate(days[0])} – ${formatDate(days[6])}`;
 const syncLabel=document.getElementById('planner-sync-label');
 if(syncLabel&&child===KID_CHILD) syncLabel.textContent=`${formatDate(days[0])} – ${formatDate(days[6])}`;
}

function uploadStatusBadge(s){
  const map = {
    queued:['排队中','var(--text2)'], rendering:['渲染中','var(--accent)'],
    ocr:['识别中','var(--accent)'], extract:['抽取中','var(--accent)'],
    done:['完成','var(--accent2)'], failed:['失败','var(--red)'],
  };
  const [txt,color] = map[s] || [s||'未知','var(--text2)'];
  return `<span style="font-size:10px;font-weight:700;color:${color};text-transform:uppercase;letter-spacing:.5px">${txt}</span>`;
}
