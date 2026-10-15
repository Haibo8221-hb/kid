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

function filterErrors(subj){ errorFilter=subj; document.querySelectorAll('#error-filter-bar .filter-btn').forEach(b=>b.classList.toggle('active',b.dataset.subject===subj)); renderErrors(); }

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
 renderTasks(child);
 }catch(e){
 // Fallback to localStorage
 const ls=localStorage.getItem('tasks_'+child);
 tasksData[child]=ls?JSON.parse(ls):{};
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
 });
 localStorage.setItem('tasks_'+child,JSON.stringify(tasksData[child]));
 toast('Synced ');
 }catch(e){
 // Save to localStorage as fallback
 localStorage.setItem('tasks_'+child,JSON.stringify(tasksData[child]));
 toast('Saved locally');
 }
}

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
