// ============================================================
// V2 Widgets — shared rendering engine
// ============================================================

const W = {}; // Widget namespace

// --- Utils ---
function $(id) { return document.getElementById(id); }
W.$ = $;

// --- Widget Config ---
W.config = {
  // Default visibility — all on
  visible: {},
  init: function() {
    const c = CONFIG.children[CONFIG.active];
    const key = 'widget_config_' + CONFIG.active;
    let saved;
    try { saved = JSON.parse(localStorage.getItem(key)); } catch(e) {}
    const defaults = {
      risk: true, countdown: true, radar: true, subjects: true,
      sw: true, summer: true, route: true, tips: true,
      tasks: true, errors: true, planner: true, wiki: true, chat: true
    };
    this.visible = saved || defaults;
    // Store defaults if never saved
    if (!saved) localStorage.setItem(key, JSON.stringify(defaults));
    return this.visible;
  },
  save: function() {
    const key = 'widget_config_' + CONFIG.active;
    localStorage.setItem(key, JSON.stringify(this.visible));
    // Re-render dashboard
    W.renderAll();
  },
  toggle: function(widgetId) {
    this.visible[widgetId] = !this.visible[widgetId];
    this.save();
  },
  isVisible: function(id) { return this.visible[id] !== false; },
  // List for config panel
  list: [
    { id: 'risk', label: 'Risk Alerts', icon: '🚨' },
    { id: 'countdown', label: 'Countdown Timers', icon: '⏳' },
    { id: 'radar', label: 'Grades Radar', icon: '🎯' },
    { id: 'subjects', label: 'Subject Cards', icon: '📊' },
    { id: 'sw', label: 'Strengths & Weaknesses', icon: '💪' },
    { id: 'summer', label: 'Summer Plan', icon: '📅' },
    { id: 'route', label: 'Long-Term Route', icon: '🗺️' },
    { id: 'tips', label: 'Study Tips', icon: '💡' },
    { id: 'tasks', label: 'Tasks', icon: '✅' },
    { id: 'errors', label: 'Error Book', icon: '❌' },
    { id: 'planner', label: 'Weekly Planner', icon: '📋' },
    { id: 'wiki', label: 'Wiki Logs', icon: '📖' },
    { id: 'chat', label: 'AI Chat', icon: '💬' }
  ]
};

// ============================================================
// 0. COUNTDOWN STRIP
// ============================================================
W.renderCountdown = function(containerId) {
  const container = $(containerId);
  if (!container) return;
  if (!W.config.isVisible('countdown')) { container.innerHTML = ''; return; }

  const c = CONFIG.children[CONFIG.active];
  if (!c.countdowns) { container.innerHTML = ''; return; }

  const html = c.countdowns.map(cd => {
    const diff = new Date(cd.target).getTime() - Date.now();
    const d = Math.floor(diff / 86400000);
    let cls = 'far', val = `${d} days`;
    if (diff <= 0) { val = 'Today'; cls = 'urgent'; }
    else if (d <= 60) cls = 'urgent';
    else if (d <= 180) cls = 'warm';
    else if (d <= 400) cls = 'cool';

    return `<div class="countdown-item ${cls}" id="cd-${cd.name.replace(/\s/g,'')}">
      <div class="cd-name">${cd.icon} ${cd.name}</div>
      <div class="cd-val">${val}</div>
      <div class="cd-date">${new Date(cd.target).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})}</div>
    </div>`;
  }).join('');

  container.innerHTML = `<div class="widget" data-widget-id="countdown"><div class="widget-header"><span class="widget-title">⏳ Countdown</span></div><div class="countdown-strip">${html}</div></div>`;
};

// Live update countdown
W.updateCountdown = function() {
  const c = CONFIG.children[CONFIG.active];
  if (!c.countdowns) return;
  c.countdowns.forEach(cd => {
    const el = document.getElementById('cd-' + cd.name.replace(/\s/g,''));
    if (!el) return;
    const diff = new Date(cd.target).getTime() - Date.now();
    const d = Math.floor(diff / 86400000);
    let cls = 'far', val = `${d} days`;
    if (diff <= 0) { val = 'Today'; cls = 'urgent'; }
    else if (d <= 60) cls = 'urgent';
    else if (d <= 180) cls = 'warm';
    else if (d <= 400) cls = 'cool';

    el.className = el.className.replace(/\b(urgent|warm|cool|far)\b/g,'').replace(/\s+/g,' ').trim();
    el.classList.add(cls);
    el.querySelector('.cd-val').textContent = val;
  });
};

// ============================================================
// 2. GRADES RADAR
// ============================================================
W.renderRadar = function(containerId) {
  const container = $(containerId);
  if (!container || !W.config.isVisible('radar')) { if (container) container.innerHTML = ''; return; }

  const c = CONFIG.children[CONFIG.active];
  const { axes, values, max } = c.radar;
  const n = axes.length;
  const R = 110, cx = 140, cy = 140;
  const angleStep = 2 * Math.PI / n;
  const labelR = R + 22;

  let svg = `<svg viewBox="0 0 280 280" style="width:100%;max-width:300px">`;
  for (let r = 1; r <= 4; r++) {
    const radius = (R / 4) * r, pts = [];
    for (let i = 0; i < n; i++) { const a = -Math.PI/2 + i * angleStep; pts.push(`${cx + radius * Math.cos(a)},${cy + radius * Math.sin(a)}`); }
    svg += `<polygon points="${pts.join(' ')}" fill="none" stroke="var(--border)" stroke-width="1"/>`;
  }
  for (let i = 0; i < n; i++) {
    const a = -Math.PI/2 + i * angleStep;
    svg += `<line x1="${cx}" y1="${cy}" x2="${cx + R * Math.cos(a)}" y2="${cy + R * Math.sin(a)}" stroke="var(--border)" stroke-width="1"/>`;
  }
  const dataPts = [];
  for (let i = 0; i < n; i++) { const a = -Math.PI/2 + i * angleStep; const r = (values[i]/max)*R; dataPts.push(`${cx + r*Math.cos(a)},${cy + r*Math.sin(a)}`); }
  svg += `<polygon points="${dataPts.join(' ')}" fill="var(--accent)" fill-opacity="0.15" stroke="var(--accent)" stroke-width="2"/>`;
  for (let i = 0; i < n; i++) { const a = -Math.PI/2 + i * angleStep; const r = (values[i]/max)*R; svg += `<circle cx="${cx + r*Math.cos(a)}" cy="${cy + r*Math.sin(a)}" r="4" fill="var(--accent2)"/>`; }
  for (let i = 0; i < n; i++) {
    const a = -Math.PI/2 + i * angleStep, lx = cx + labelR*Math.cos(a), ly = cy + labelR*Math.sin(a);
    const deg = (a*180/Math.PI + 360)%360;
    let anchor = 'middle', dy = '0.35em';
    if (deg > 240 && deg < 300) anchor = 'end';
    else if (deg > 60 && deg < 120) anchor = 'start';
    if (deg > 200 && deg < 340) dy = '-0.3em';
    else if (deg > 20 && deg < 160) dy = '1.1em';
    svg += `<text x="${lx}" y="${ly}" text-anchor="${anchor}" dominant-baseline="middle" dy="${dy}" font-size="11" font-weight="600" fill="var(--text)">${axes[i]}</text>`;
  }
  svg += '</svg>';

  container.innerHTML = `<div class="widget"><div class="widget-header"><span class="widget-title">🎯 Grades Radar</span><span class="widget-badge" style="background:rgba(79,140,255,0.1);color:var(--accent)">${c.school} ${c.year}</span></div><div class="radar-wrap">${svg}</div></div>`;
};

// ============================================================
// 3. SUBJECT CARDS
// ============================================================
W.renderSubjectCards = function(containerId) {
  const container = $(containerId);
  if (!container || !W.config.isVisible('subjects')) { if (container) container.innerHTML = ''; return; }
  const c = CONFIG.children[CONFIG.active];
  const cards = c.subjects.map(s => {
    const cls = s.status === 'risk' ? 'risk' : s.status === 'excellent' ? 'excellent' : '';
    const barColor = s.status === 'risk' ? 'var(--red)' : s.status === 'excellent' ? 'var(--accent2)' : 'var(--accent)';
    return `<div class="subject-card ${cls}"><div class="sj-name">${s.name}</div><div class="sj-grade ${cls}">${s.grade}</div><div class="sj-trend">${s.trend}</div><div class="sj-bar"><div class="sj-bar-fill" style="width:${s.bar}%;background:${barColor}"></div></div>${s.tag?`<span class="sj-tag ${s.tagClass}">${s.tag}</span>`:''}</div>`;
  }).join('');
  container.innerHTML = `<div class="widget"><div class="widget-header"><span class="widget-title">📊 Latest Grades</span><span class="widget-badge" style="background:rgba(16,185,129,0.1);color:var(--accent2)">${c.year}</span></div><div class="subject-grid">${cards}</div></div>`;
};

// ============================================================
// 4. RISK ALERTS + STRENGTHS & WEAKNESSES (merged full-width)
// ============================================================
W.renderRiskSW = function(containerId) {
  const container = $(containerId);
  if (!container) return;
  const c = CONFIG.children[CONFIG.active];
  
  var riskHtml = '';
  if (W.config.isVisible('risk') && c.risks && c.risks.length > 0) {
    const icons = { critical: '🔴', warning: '🟡', info: '🔵' };
    const items = c.risks.map(r =>
      '<div class="risk-item '+r.level+'"><div class="risk-icon">'+(icons[r.level]||'⚠️')+'</div><div class="risk-body"><div class="risk-text">'+r.text+'</div><div class="risk-action">→ '+r.action+'</div></div></div>'
    ).join('');
    riskHtml = '<div class="risk-banner">'+items+'</div>';
  }
  
  var swHtml = '';
  if (W.config.isVisible('sw')) {
    var strengthsHtml = c.strengths.map(s => 
      '<div class="sw-item strength"><div class="sw-icon">'+(s.icon||'💪')+'</div><div class="sw-text"><div class="sw-title">'+s.area+'</div><div class="sw-detail">'+s.detail+'</div></div></div>'
    ).join('');
    var weaknessesHtml = c.weaknesses.map(w => {
      var sevCls = w.severity === 'critical' ? 'weakness' : w.severity === 'medium' ? 'weakness medium' : 'weakness';
      return '<div class="sw-item '+sevCls+'"><div class="sw-icon">⚠️</div><div class="sw-text"><div class="sw-title">'+w.area+'</div><div class="sw-detail">'+w.detail+'</div></div></div>';
    }).join('');
    swHtml = '<div class="sw-split"><div class="sw-col"><div class="sw-col-title" style="color:var(--accent2)">💪 Strengths</div><div class="sw-list">'+strengthsHtml+'</div></div><div class="sw-col"><div class="sw-col-title" style="color:var(--red)">⚠️ Areas to Improve</div><div class="sw-list">'+weaknessesHtml+'</div></div></div>';
  }
  
  var title = riskHtml && swHtml ? '🚨 Risk Alerts & Strengths' : riskHtml ? '🚨 Risk Alerts' : '💪 Strengths & Areas to Improve';
  container.innerHTML = '<div class="widget"><div class="widget-header"><span class="widget-title">'+title+'</span></div>'+riskHtml+swHtml+'</div>';
  container.classList.add('widget-full');
  container.style.gridColumn = '1 / -1';
};

// ============================================================
// 5. SUMMER PLAN
// ============================================================
W.renderSummerPlan = function(containerId) {
  const container = $(containerId);
  if (!container || !W.config.isVisible('summer')) { if (container) container.innerHTML = ''; return; }
  const c = CONFIG.children[CONFIG.active];
  const now = new Date(); const curM = now.getMonth()+1;
  const mM = {Jan:1,Feb:2,Mar:3,Apr:4,May:5,Jun:6,Jul:7,Aug:8,Sep:9,Oct:10,Nov:11,Dec:12};
  const items = c.summerPlan.map(m => {
    const mn = mM[m.month.split(' ')[0]]||0;
    return `<div class="timeline-item ${m.done?'done':''}"><div class="timeline-meta">${m.month} ${mn===curM?'<span class="timeline-dot current"></span>NOW':''}</div><div class="timeline-title">${m.title}</div><div class="timeline-desc">${m.desc}</div></div>`;
  }).join('');
  container.innerHTML = `<div class="widget"><div class="widget-header"><span class="widget-title">📅 2026 Summer Plan</span></div><div class="timeline">${items}</div></div>`;
};

// ============================================================
// 6. LONG-TERM ROUTE
// ============================================================
W.renderLongTermRoute = function(containerId) {
  const container = $(containerId);
  if (!container || !W.config.isVisible('route')) { if (container) container.innerHTML = ''; return; }
  const c = CONFIG.children[CONFIG.active];
  const items = c.longTermRoute.map(r => `<div class="timeline-item"><div class="timeline-meta">${r.period}</div><div class="timeline-title">${r.focus}</div><div class="timeline-desc">🎯 ${r.milestone}</div></div>`).join('');
  container.innerHTML = `<div class="widget"><div class="widget-header"><span class="widget-title">🗺️ Long-Term Route</span></div><div class="timeline">${items}</div></div>`;
};

// ============================================================
// 7. STUDY TIPS
// ============================================================
W.StudyTips = {
  todayTips: [], currentTipIndex: 0,
  load: function(containerId, tipsData) {
    const el = $(containerId);
    if (!el || !W.config.isVisible('tips')) { if (el) el.innerHTML = ''; return; }
    const c = CONFIG.children[CONFIG.active];
    const key = c.storageKeys.tips; let viewed = {};
    try { viewed = JSON.parse(localStorage.getItem(key)||'{}'); } catch(e) {}
    let pool = tipsData.filter(t => (viewed[t.id]||0)<3);
    if (pool.length<5) { localStorage.removeItem(key); viewed={}; pool=[...tipsData]; }
    const s = [...pool].sort(()=>Math.random()-0.5); this.todayTips = s.slice(0,8); this.currentTipIndex=0;
    if (this.todayTips.length>0) { const t=this.todayTips[0]; viewed[t.id]=(viewed[t.id]||0)+1; try{localStorage.setItem(key,JSON.stringify(viewed))}catch(e){} }
    this.render(el);
  },
  render: function(el) {
    if (!el) el = $('widget-tips'); if (!el||this.todayTips.length===0) return;
    const t=this.todayTips[this.currentTipIndex];
    const stepsHtml = t.steps.map(function(s,i){ return '<div class="lm-step"><span class="lm-step-num">'+(i+1)+'</span><span class="lm-step-text">'+s+'</span></div>'; }).join('');
    const applyHtml = t.apply.map(function(a){ return '<div class="lm-apply-item">'+a+'</div>'; }).join('');
    el.innerHTML = '<div class="widget"><div class="widget-header"><span class="widget-title">'+t.icon+' '+t.title+'</span><span class="widget-badge" style="background:rgba(79,140,255,0.1);color:var(--accent)">Learning Method</span></div>'+
      '<div class="lm-card">'+
        '<div class="lm-concept">&ldquo;'+t.concept+'&rdquo;</div>'+
        '<div class="lm-section"><div class="lm-label">Why It Works</div><div class="lm-text">'+t.why+'</div></div>'+
        '<div class="lm-section"><div class="lm-label">How to Do It</div><div class="lm-steps">'+stepsHtml+'</div></div>'+
        '<div class="lm-section"><div class="lm-label">Try It Now</div><div class="lm-practice">'+t.practice+'</div></div>'+
        '<div class="lm-section"><div class="lm-label">Apply to Your Subjects</div><div class="lm-apply">'+applyHtml+'</div></div>'+
      '</div>'+
      '<div class="lm-nav"><button onclick="W.StudyTips.prev()" '+(this.currentTipIndex===0?'disabled':'')+'>← Previous</button><span class="lm-counter">'+(this.currentTipIndex+1)+' / '+this.todayTips.length+'</span><button onclick="W.StudyTips.next()" '+(this.currentTipIndex===this.todayTips.length-1?'disabled':'')+'>Next →</button></div>'+
    '</div>';
  },
  next: function(){if(this.currentTipIndex<this.todayTips.length-1){this.currentTipIndex++;const t=this.todayTips[this.currentTipIndex],c=CONFIG.children[CONFIG.active];let v={};try{v=JSON.parse(localStorage.getItem(c.storageKeys.tips)||'{}')}catch(e){}v[t.id]=(v[t.id]||0)+1;try{localStorage.setItem(c.storageKeys.tips,JSON.stringify(v))}catch(e){}this.render();}},
  prev: function(){if(this.currentTipIndex>0){this.currentTipIndex--;this.render();}}
};

// ============================================================
// 8. TASKS
// ============================================================
W.Tasks = {
  tasks: [],
  load: async function(containerId) {
    const el = $(containerId);
    if (!el || !W.config.isVisible('tasks')) { if (el) el.innerHTML = ''; return; }
    try {
      const c = CONFIG.children[CONFIG.active];
      // Try API first, fallback to localStorage
      let data;
      try { const r = await fetch(window.location.origin + '/api/tasks?user=' + CONFIG.active); data = await r.json(); } catch(e) {}
      // Normalize object format {key: {done,name}} → array [{name,done,subject}]
      if (data && !Array.isArray(data)) {
        var arr = [];
        Object.entries(data).forEach(function(e) {
          var key = e[0], val = e[1];
          var parts = key.split('-');
          var subj = parts.length > 1 ? parts[0] : 'General';
          arr.push({ name: val.name || key, done: val.done || false, subject: subj });
        });
        data = arr;
      }
      if (!data || !Array.isArray(data)) {
        data = JSON.parse(localStorage.getItem(c.storageKeys.tasks) || '[]');
        // Normalize
        data = data.map(t => typeof t === 'string' ? { name: t, done: false, subject: '' } : t);
      }
      this.tasks = data;
      this.render(el);
      // Also update dashboard quick-tasks widget
      this.renderDash('widget-tasks-dash');
    } catch(e) { if (el) el.innerHTML = '<div class="widget"><div class="widget-header"><span class="widget-title">✅ Tasks</span></div><div class="error-empty">Loading...</div></div>'; }
  },
  renderDash: function(containerId) {
    var el = document.getElementById(containerId);
    if (!el || !W.config.isVisible("tasks")) { if (el) el.innerHTML = ""; return; }
    if (!this.tasks || this.tasks.length === 0) {
      el.innerHTML = "<div class='widget'><div class='widget-header'><span class='drag-grip' title='Drag to reorder'>⠟</span><span class='widget-title'>✅ Tasks</span></div><div class='error-empty'>No tasks yet. Add some in the Tasks tab.</div></div>";
      return;
    }
    var active = this.tasks.filter(function(t){return !t.done;});
    var done = this.tasks.filter(function(t){return t.done;});
    
    // Category colors matching V1
    var colors = {English:'var(--red)',Maths:'var(--accent)',Science:'var(--accent2)',Physics:'var(--purple)',Chemistry:'var(--accent2)',Biology:'var(--green)',Computing:'var(--orange)',Art:'var(--orange)',competition:'var(--orange)',general:'var(--text2)'};
    
    // Group by subject
    var groups = {};
    active.forEach(function(t){ var s = t.subject||'General'; if(!groups[s]) groups[s]=[]; groups[s].push(t); });
    
    var html = "<div class='widget'><div class='widget-header'><span class='drag-grip' title='Drag to reorder'>⠟</span><span class='widget-title'>✅ Tasks</span><span style='font-size:11px;color:var(--text2)'>" + active.length + " active" + (done.length>0?", "+done.length+" done":"") + "</span></div>";
    
    var cats = Object.keys(groups);
    // Sort: known categories first, then alphabetical
    var order = ['English','Maths','Science','Physics','Chemistry','Biology','Computing'];
    cats.sort(function(a,b){
      var ai=order.indexOf(a), bi=order.indexOf(b);
      if(ai>=0&&bi>=0) return ai-bi;
      if(ai>=0) return -1; if(bi>=0) return 1;
      return a.localeCompare(b);
    });
    
    cats.forEach(function(cat){
      var items = groups[cat];
      var color = colors[cat] || colors.general;
      html += "<div class='task-section'><div class='task-section-header'><span class='task-section-title' style='color:"+color+"'>"+cat+"</span></div><div class='task-list'>";
      items.forEach(function(t){
        var idx = W.Tasks.tasks.indexOf(t);
        html += "<div class='task-item'><div class='task-check'></div><span class='task-name'>"+(t.name||"")+"</span></div>";
      });
      html += "</div></div>";
    });
    
    // Completed count
    if (done.length > 0) {
      html += "<div style='font-size:11px;color:var(--accent2);padding:4px 0 0'>"+done.length+" completed</div>";
    }
    html += "<div style='font-size:11px;color:var(--accent);padding:8px 0 0;cursor:pointer' onclick=\"switchTab('tasks')\">Open full Tasks tab →</div>";
    html += "</div>";
    el.innerHTML = html;
    // Re-attach drag after async re-render
    W._refreshWidgetDrag(el);
  },
  render: function(el) {
    if (!el) el = $('widget-tasks');
    const done = this.tasks.filter(t => t.done);
    const active = this.tasks.filter(t => !t.done);

    // Category colors
    const colors = {English:'var(--red)',Maths:'var(--accent)',Science:'var(--accent2)',Physics:'var(--purple)',Chemistry:'var(--accent2)',Biology:'var(--green)',Computing:'var(--orange)',Art:'var(--orange)',competition:'var(--orange)',general:'var(--text2)'};

    // Group by subject
    const groups = {};
    active.forEach(t => { const s = t.subject || 'General'; if (!groups[s]) groups[s] = []; groups[s].push(t); });

    // Sort categories
    const order = ['English','Maths','Science','Physics','Chemistry','Biology','Computing'];
    const cats = Object.keys(groups).sort((a,b)=>{
      var ai=order.indexOf(a), bi=order.indexOf(b);
      if(ai>=0&&bi>=0) return ai-bi;
      if(ai>=0) return -1; if(bi>=0) return 1;
      return a.localeCompare(b);
    });

    let html = '<div class="widget"><div class="widget-header"><span class="widget-title">✅ Tasks</span><span style="font-size:11px;color:var(--text2)">' + active.length + ' active</span></div>';

    // Active tasks in V1 2-column layout
    cats.forEach(cat => {
      const items = groups[cat];
      const color = colors[cat] || colors.general;
      html += '<div class="task-section"><div class="task-section-header"><span class="task-section-title" style="color:'+color+'">'+cat+'</span></div><div class="task-list">';
      items.forEach(t => {
        const idx = this.tasks.indexOf(t);
        html += '<div class="task-item"><div class="task-check" onclick="W.Tasks.toggle('+idx+')"></div><span class="task-name" onclick="W.Tasks.edit('+idx+')">'+(t.name||'')+'</span><span class="task-del" onclick="W.Tasks.remove('+idx+')">×</span></div>';
      });
      html += '</div></div>';
    });

    // No active tasks
    if (active.length === 0) {
      html += '<div class="error-empty">All done! Add a new task below.</div>';
    }

    // Collapsed completed
    if (done.length > 0) {
      html += '<div class="task-collapse" onclick="W.Tasks.toggleCollapse()">▸ Completed (' + done.length + ')</div>';
      html += '<div id="task-done-list" style="display:none">';
      done.forEach(t => {
        const idx = this.tasks.indexOf(t);
        html += '<div class="task-item done"><div class="task-check checked" onclick="W.Tasks.toggle('+idx+')">✓</div><span class="task-name">'+(t.name||'')+'</span><span class="task-del" style="opacity:1" onclick="W.Tasks.remove('+idx+')">×</span></div>';
      });
      html += '</div>';
    }

    // Add task input
    html += '<div class="task-input-row"><input id="task-new-input" placeholder="New task..." onkeydown="if(event.key===\'Enter\')W.Tasks.add()"><button onclick="W.Tasks.add()">+ Add</button></div>';

    html += '</div>';
    el.innerHTML = html;
  },
  toggle: function(idx) {
    this.tasks[idx].done = !this.tasks[idx].done;
    this.save();
    this.render();
  },
  remove: function(idx) {
    this.tasks.splice(idx, 1);
    this.save();
    this.render();
  },
  edit: function(idx) {
    const t = this.tasks[idx];
    const name = prompt('Edit task:', t.name || '');
    if (name !== null) { t.name = name; this.save(); this.render(); }
  },
  add: async function() {
    const input = document.getElementById('task-new-input');
    const name = input ? input.value.trim() : prompt('New task:');
    if (!name) return;
    if (input) input.value = '';
    this.tasks.push({ name, done: false, subject: '' });
    this.save();
    this.render();
  },
  toggleCollapse: function() {
    const list = $('task-done-list');
    const btn = document.querySelector('.task-collapse');
    if (list && btn) {
      const isHidden = list.style.display === 'none';
      list.style.display = isHidden ? 'block' : 'none';
      btn.textContent = isHidden ? `▾ Completed (${this.tasks.filter(t=>t.done).length})` : `▸ Completed (${this.tasks.filter(t=>t.done).length})`;
    }
  },
  save: function() {
    const c = CONFIG.children[CONFIG.active];
    try {
      fetch(window.location.origin + '/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user: CONFIG.active, tasks: this.tasks }) }).catch(() => {});
    } catch(e) {}
    localStorage.setItem(c.storageKeys.tasks, JSON.stringify(this.tasks));
  }
};

// ============================================================
// 9. ERROR BOOK
// ============================================================
W.ErrorBook = {
  errors: [],
  load: async function(containerId) {
    const el = $(containerId);
    if (!el || !W.config.isVisible('errors')) { if (el) el.innerHTML = ''; return; }
    try {
      const c = CONFIG.children[CONFIG.active];
      let data;
      try { const r = await fetch(window.location.origin + '/api/errors/' + CONFIG.active); data = (await r.json()).errors; } catch(e) {}
      if (!data || !Array.isArray(data)) {
        data = JSON.parse(localStorage.getItem('errors_' + CONFIG.active) || '[]');
      }
      this.errors = data;
      this.render(el);
    } catch(e) { if (el) el.innerHTML = '<div class="widget"><div class="widget-header"><span class="widget-title">❌ Error Book</span></div><div class="error-empty">Loading...</div></div>'; }
  },
  render: function(el) {
    if (!el) el = $('widget-errors');
    if (this.errors.length === 0) { el.innerHTML = '<div class="widget"><div class="widget-header"><span class="widget-title">❌ Error Book</span><button onclick="W.ErrorBook.add()" style="padding:4px 12px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px">+ Add</button></div><div class="error-empty">No errors yet — great job! 🎉</div></div>'; return; }

    const items = this.errors.map((e, i) => `<div class="error-card">
      <div class="err-subject">${e.subject||'General'}</div>
      <div class="err-question">${e.question||e.original_question||''}</div>
      ${(e.image||e.question_image) ? `<img class="err-image" src="${e.image||e.question_image}" alt="error screenshot" loading="lazy">` : ''}
      <div class="err-answer">${e.solution_display||e.solution||e.answer||e.explanation||''}</div>
      ${e.score_display ? `<div style="font-size:12px;font-weight:600;color:var(--accent);margin-top:6px">${e.score_display}</div>` : ''}
      <div class="err-meta">
        ${e.date ? `<span>📅 ${e.date}</span>` : ''}
        ${e.exam ? `<span>📝 ${e.exam}</span>` : ''}
        <span onclick="W.ErrorBook.remove(${i})" style="cursor:pointer;color:var(--red);margin-left:auto">Delete</span>
      </div>
    </div>`).join('');

    el.innerHTML = `<div class="widget">
      <div class="widget-header"><span class="widget-title">❌ Error Book</span><span style="font-size:11px;color:var(--text2)">${this.errors.length} entries</span></div>
      <div class="error-list">${items}</div>
    </div>`;
  },
  add: function() {
    const subject = prompt('Subject:') || 'General';
    const question = prompt('Question:') || '';
    const answer = prompt('Answer / Solution:') || '';
    if (!question) return;
    this.errors.unshift({ subject, question, answer, date: new Date().toISOString().split('T')[0] });
    this.save();
    this.render();
  },
  remove: function(idx) {
    if (!confirm('Delete this error entry?')) return;
    this.errors.splice(idx, 1);
    this.save();
    this.render();
  },
  save: function() {
    localStorage.setItem('errors_' + CONFIG.active, JSON.stringify(this.errors));
    try { fetch('/api/errors', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user: CONFIG.active, errors: this.errors }) }).catch(() => {}); } catch(e) {}
  }
};

// ============================================================
// 10. WEEKLY PLANNER
// ============================================================
W.Planner = {
  data: {},
  days: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
  periods: ['AM1', 'AM2', 'PM1', 'PM2', 'Eve'],
  load: async function(containerId) {
    const el = $(containerId);
    if (!el || !W.config.isVisible('planner')) { if (el) el.innerHTML = ''; return; }
    try {
      let data;
      try { const r = await fetch(window.location.origin + '/api/planner?user=' + CONFIG.active); data = await r.json(); } catch(e) {}
      if (!data || data.test) {
        data = JSON.parse(localStorage.getItem(CONFIG.children[CONFIG.active].storageKeys.planner) || '{}');
      }
      // Convert old day-name format → V2 dayIndex-slotIndex format
      data = W.Planner._convertFormat(data);
      this.data = data;
      this.render(el);
    } catch(e) { if (el) el.innerHTML = '<div class="widget"><div class="widget-header"><span class="widget-title">📋 Weekly Planner</span></div><div class="error-empty">Loading...</div></div>'; }
  },
  _convertFormat: function(data) {
    // If data already has V2 keys like "0-0", return as-is
    if (data['0-0'] !== undefined) return data;
    
    var dayMap = {mon:0, tue:1, wed:2, thu:3, fri:4, sat:5, sun:6};
    // Map time strings to period indices: AM1(0), AM2(1), PM1(2), PM2(3), Eve(4)
    var timeMap = {'9am':0,'10am':0,'11am':1,'12pm':1,'1pm':1,'2pm':1,'3pm':2,'4pm':2,'5pm':2,'6pm':3,'7pm':3,'8pm':4,'9pm':4,'10pm':4,'11pm':4};
    
    var result = {};
    Object.keys(data).forEach(function(k) {
      k = k.toLowerCase();
      if (dayMap[k] !== undefined) {
        var dayIdx = dayMap[k];
        var slots = data[k];
        if (typeof slots === 'object') {
          Object.keys(slots).forEach(function(time) {
            var t = time.toLowerCase().replace(/^0/,'');
            var slotIdx = timeMap[t];
            if (slotIdx !== undefined) {
              var key = dayIdx + '-' + slotIdx;
              if (!result[key]) result[key] = slots[time];
              else result[key] += ' / ' + slots[time];
            }
          });
        }
      }
    });
    // Also copy over any date-based keys for reference
    Object.keys(data).forEach(function(k) {
      if (k.indexOf('-') >= 0 && k.length >= 10) result[k] = data[k];
    });
    return Object.keys(result).length > 0 ? result : data;
  },
  render: function(el) {
    if (!el) el = $('widget-planner');
    let html = '<div class="widget"><div class="widget-header"><span class="widget-title">📋 Weekly Planner</span></div>';

    // Headers
    html += '<div class="planner-grid" style="grid-template-columns:60px repeat(6,1fr)"><div class="planner-header">Period</div>';
    this.days.forEach(d => html += `<div class="planner-header">${d}</div>`);

    this.periods.forEach(p => {
      html += `<div class="planner-header">${p}</div>`;
      for (let di = 0; di < 6; di++) {
        const key = `${di}-${this.periods.indexOf(p)}`;
        const val = this.data[key] || '';
        html += `<div class="planner-cell"><div class="pc-period"></div><div class="pc-content">${val}</div></div>`;
      }
    });

    html += '</div></div>';
    el.innerHTML = html;
  }
};

// ============================================================
// 11. WIKI LOGS
// ============================================================
W.Wiki = {
  entries: [],
  load: async function(containerId) {
    const el = $(containerId);
    if (!el || !W.config.isVisible('wiki')) { if (el) el.innerHTML = ''; return; }
    try {
      let data;
      try { const r = await fetch(window.location.origin + '/api/wiki/' + CONFIG.active); data = (await r.json()).entries; } catch(e) {}
      if (!data) data = [];
      this.entries = data;
      this.render(el);
    } catch(e) { if (el) el.innerHTML = '<div class="widget"><div class="widget-header"><span class="widget-title">📖 Wiki Logs</span></div><div class="error-empty">Loading...</div></div>'; }
  },
  render: function(el) {
    if (!el) el = $('widget-wiki');
    let html = `<div class="widget">
      <div class="widget-header"><span class="widget-title">📖 Wiki Logs</span><button onclick="W.Wiki.showForm()" style="padding:4px 12px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px">+ New</button></div>
      <div id="wiki-form-area" style="display:none"></div>`;

    if (this.entries.length === 0) {
      html += '<div class="error-empty">No logs yet</div>';
    } else {
      this.entries.slice(0, 20).forEach(e => {
        html += `<div class="wiki-entry">
          <div class="we-date">${e.date||''}</div>
          <div class="we-title">${e.title||''}</div>
          <div class="we-body">${(e.content||e.body||'').substring(0,300)}</div>
          ${e.tags ? `<div class="we-tags">${e.tags.map(t=>`<span class="wiki-tag">${t}</span>`).join('')}</div>` : ''}
        </div>`;
      });
    }

    html += '</div>';
    el.innerHTML = html;
  },
  showForm: function() {
    const area = $('wiki-form-area');
    if (!area) return;
    area.style.display = 'block';
    area.innerHTML = `<div class="wiki-form">
      <input id="wiki-title" placeholder="Title">
      <textarea id="wiki-body" placeholder="Write your log..."></textarea>
      <input id="wiki-tags" placeholder="Tags (comma separated)">
      <div style="display:flex;gap:8px">
        <button onclick="W.Wiki.submit()">Save</button>
        <button onclick="document.getElementById('wiki-form-area').style.display='none'" style="background:var(--border);color:var(--text)">Cancel</button>
      </div>
    </div>`;
  },
  submit: async function() {
    const title = $('wiki-title')?.value?.trim();
    const content = $('wiki-body')?.value?.trim();
    const tagsStr = $('wiki-tags')?.value?.trim();
    if (!title || !content) return;
    const tags = tagsStr ? tagsStr.split(',').map(t=>t.trim()).filter(Boolean) : [];
    const entry = { title, content, tags, date: new Date().toISOString().split('T')[0], child: CONFIG.active };
    try {
      await fetch(window.location.origin + '/api/wiki', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(entry) });
    } catch(e) {}
    this.entries.unshift(entry);
    this.render();
  }
};

// ============================================================
// 12. AI CHAT
// ============================================================
W.Chat = {
  messages: [],
  load: async function(containerId) {
    const el = $(containerId);
    if (!el || !W.config.isVisible('chat')) { if (el) el.innerHTML = ''; return; }
    try {
      let data;
      try { const r = await fetch(window.location.origin + '/api/chat/' + CONFIG.active + '?limit=30'); data = await r.json(); } catch(e) {}
      this.messages = Array.isArray(data) ? data : [];
    } catch(e) { this.messages = []; }
    this.render(el);
  },
  render: function(el) {
    if (!el) el = $('widget-chat');
    const msgs = this.messages.map(m => {
      const cls = m.role === 'user' ? 'user' : 'ai';
      const text = m.content || m.message || '';
      return `<div class="chat-bubble ${cls}">${this._formatMsg(text)}</div>`;
    }).join('');

    el.innerHTML = `<div class="widget">
      <div class="widget-header"><span class="widget-title">💬 AI Chat</span><span style="font-size:10px;color:var(--text2)">Socratic mode · guides thinking</span></div>
      <div class="chat-messages" id="chat-msgs">${msgs || '<div style="color:var(--text2);text-align:center;padding:20px">Ask me anything about your studies!</div>'}</div>
      <div class="chat-input-area">
        <input id="chat-input" placeholder="Ask a question..." onkeydown="if(event.key==='Enter')W.Chat.send()">
        <button onclick="W.Chat.send()">Send</button>
      </div>
    </div>`;
  },
  _formatMsg: function(text) {
    return text.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<b>$1</b>');
  },
  send: async function() {
    const input = $('chat-input');
    const msg = input?.value?.trim();
    if (!msg) return;
    input.value = '';

    this.messages.push({ role: 'user', content: msg });
    this.render();

    try {
      const r = await fetch(window.location.origin + '/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, user: CONFIG.active })
      });
      const data = await r.json();
      this.messages.push({ role: 'assistant', content: data.reply || 'Sorry, no response.' });
    } catch(e) {
      this.messages.push({ role: 'assistant', content: 'Connection error. Please try again.' });
    }
    this.render();
  }
};

// ============================================================
// MASTER RENDER
// ============================================================
W.renderAll = function() {
  W.renderRiskSW('widget-risksw');
  W.renderCountdown('widget-countdown');
  W.renderRadar('widget-radar');
  W.renderSubjectCards('widget-subjects');
  W.renderSummerPlan('widget-summer');
  W.renderLongTermRoute('widget-route');
  W.StudyTips.load('widget-tips', V2_TIPS_POOL || []);
  W.Tasks.load('widget-tasks');
  W.Tasks.renderDash('widget-tasks-dash');
  W.ErrorBook.load('widget-errors');
  W.Planner.load('widget-planner');
  W.Wiki.load('widget-wiki');
  W.Chat.load('widget-chat');
  // Inject status badges post-render
  setTimeout(() => W.injectStatusBadges(), 100);
  // Apply drag-and-drop + saved order
  setTimeout(() => W.initDashboardGrid(), 200);
};

// ============================================================
// Widget Status Badges — Elfsight-style markers
// ============================================================
W.injectStatusBadges = function() {
  const c = CONFIG.children[CONFIG.active];
  const badges = {}; // widget-container-id → {class, text}

  // Risk Alerts: mark if any critical risk exists
  if (c.risks && c.risks.some(r => r.level === 'critical')) {
    badges['widget-risksw'] = { cls: 'needs-review', text: 'NEEDS REVIEW' };
  } else {
    badges['widget-risksw'] = { cls: 'active', text: 'CORE' };
  }

  // Countdown: mark if any < 30 days
  const now = new Date();
  if (c.countdowns) {
    const urgent = c.countdowns.find(cd => {
      const t = new Date(cd.target);
      return (t - now) / 86400000 < 30 && (t - now) > 0;
    });
    if (urgent) badges['widget-countdown'] = { cls: 'active', text: 'ACTIVE' };
  }

  // Subject Cards: mark if any risk subject
  if (c.subjects && c.subjects.some(s => s.status === 'risk')) {
    badges['widget-subjects'] = { cls: 'needs-review', text: 'NEEDS REVIEW' };
  }

  // Tasks: check localStorage for recent activity
  const tasksKey = c.storageKeys.tasks;
  if (tasksKey) {
    const tasks = JSON.parse(localStorage.getItem(tasksKey) || '[]');
    const hasActive = Array.isArray(tasks) && tasks.some(t => !t.done && t.name);
    if (hasActive) badges['widget-tasks'] = { cls: 'active', text: 'ACTIVE' };
    if (hasActive) badges['widget-tasks-dash'] = { cls: 'active', text: 'ACTIVE' };
  }

  // Summer Plan: mark current month
  if (c.summerPlan) {
    const mm = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const curM = mm[now.getMonth()];
    if (c.summerPlan.some(m => m.month.startsWith(curM))) {
      badges['widget-summer'] = { cls: 'active', text: 'NOW' };
    }
  }

  // Study Tips: always show "NEW"
  badges['widget-tips'] = { cls: 'new', text: 'DAILY' };

  // Wiki: check entries
  if (c.storageKeys && c.storageKeys.wiki) {
    const wiki = JSON.parse(localStorage.getItem(c.storageKeys.wiki) || '[]');
    if (wiki.length > 0) badges['widget-wiki'] = { cls: 'updated', text: wiki.length.toString() };
  }

  // Inject badges into dashboard widgets
  Object.entries(badges).forEach(([id, b]) => {
    const container = document.getElementById(id);
    if (!container) return;
    const widget = container.querySelector('.widget');
    if (!widget) return;
    // Remove existing badge if any
    const existing = widget.querySelector('.widget-status');
    if (existing) existing.remove();
    const badge = document.createElement('span');
    badge.className = 'widget-status ' + b.cls;
    badge.textContent = b.text;
    widget.appendChild(badge);
  });
};

// ============================================================

// ============================================================
// Drag-and-Drop + Widget Grid
// ============================================================
W.defaultWidgetOrder = [
  'widget-risksw','widget-countdown','widget-radar','widget-subjects',
  'widget-summer','widget-route',
  'widget-tips','widget-tasks-dash'];

W.getWidgetOrder = function() {
  var key = 'widget_order_' + CONFIG.active;
  try {
    var saved = JSON.parse(localStorage.getItem(key));
    if (Array.isArray(saved) && saved.length > 0) return saved;
  } catch(e) {}
  return W.defaultWidgetOrder.slice();
};

W.saveWidgetOrder = function(order) {
  var key = 'widget_order_' + CONFIG.active;
  localStorage.setItem(key, JSON.stringify(order));
};


W.resetWidgetOrder = function() {
  var key = 'widget_order_' + CONFIG.active;
  localStorage.removeItem(key);
  W.renderAll();
  W.closeConfig();
  toast('Widget order reset to default');
};

// Re-attach drag to a widget container after async re-render
W._refreshWidgetDrag = function(container) {
  if (!container) return;
  var w = container.querySelector('.widget');
  if (!w) return;
  w.setAttribute('draggable', 'true');
  w.addEventListener('dragstart', W._onDragStart);
  w.addEventListener('dragend', W._onDragEnd);
  // Insert grip handle if needed
  var hdr = w.querySelector('.widget-header');
  if (hdr && !hdr.querySelector('.drag-grip')) {
    var grip = document.createElement('span');
    grip.className = 'drag-grip';
    grip.innerHTML = '⠟';
    grip.title = 'Drag to reorder';
    hdr.insertBefore(grip, hdr.firstChild);
  }
  // Apply full-width if in list
  if (['widget-risksw','widget-countdown','widget-subjects','widget-summer','widget-route','widget-tasks-dash'].indexOf(container.id) >= 0) {
    container.classList.add('widget-full');
  }
};

W.initDashboardGrid = function() {
  var grid = document.getElementById('widget-grid');
  if (!grid) return;
  
  var order = W.getWidgetOrder();
  
  // Full-width widgets (span both columns)
  var fullWidth = ['widget-risksw','widget-countdown','widget-subjects',
                    'widget-summer','widget-route','widget-tasks-dash'];
  
  // Reorder DOM children to match saved order
  order.forEach(function(id) {
    var el = document.getElementById(id);
    if (el) {
      grid.appendChild(el);
      // Apply full-width class
      if (fullWidth.indexOf(id) >= 0) {
        el.classList.add('widget-full');
      } else {
        el.classList.remove('widget-full');
      }
    }
  });
  
  // Add drag attributes to each widget
  grid.querySelectorAll('.widget').forEach(function(w) {
    w.setAttribute('draggable', 'true');
    w.addEventListener('dragstart', W._onDragStart);
    w.addEventListener('dragend', W._onDragEnd);
    // Insert grip handle
    var hdr = w.querySelector('.widget-header');
    if (hdr && !hdr.querySelector('.drag-grip')) {
      var grip = document.createElement('span');
      grip.className = 'drag-grip';
      grip.innerHTML = '⠟';
      grip.title = 'Drag to reorder';
      hdr.insertBefore(grip, hdr.firstChild);
    }
  });
  
  var g = grid;
  g.addEventListener('dragover', W._onDragOver);
  g.addEventListener('drop', W._onDrop);
};

W._dragSource = null;

W._onDragStart = function(e) {
  var w = e.target.closest('.widget');
  if (!w) return;
  W._dragSource = w;
  w.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
  setTimeout(function() { w.style.opacity = '0.4'; }, 0);
};

W._onDragEnd = function(e) {
  var w = e.target.closest('.widget');
  if (w) {
    w.classList.remove('dragging');
    w.style.opacity = '1';
  }
  document.querySelectorAll('.drag-over').forEach(function(el) {
    el.classList.remove('drag-over');
  });
  W._dragSource = null;
};

W._onDragOver = function(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  var grid = document.getElementById('widget-grid');
  if (!grid) return;
  grid.querySelectorAll('.drag-over').forEach(function(el) {
    el.classList.remove('drag-over');
  });
  var target = e.target.closest('#widget-grid > div');
  if (!target || target === W._dragSource?.parentElement) return;
  target.classList.add('drag-over');
};

W._onDrop = function(e) {
  e.preventDefault();
  var grid = document.getElementById('widget-grid');
  if (!grid || !W._dragSource) return;
  var target = grid.querySelector('.drag-over');
  if (!target) return;
  var srcContainer = W._dragSource.parentElement;
  if (srcContainer === target) { target.classList.remove('drag-over'); return; }
  var rect = target.getBoundingClientRect();
  var midY = rect.top + rect.height / 2;
  if (e.clientY < midY) {
    grid.insertBefore(srcContainer, target);
  } else {
    grid.insertBefore(srcContainer, target.nextSibling);
  }
  target.classList.remove('drag-over');
  W._dragSource.style.opacity = '1';
  W._dragSource.classList.remove('dragging');
  var order = [];
  grid.querySelectorAll(':scope > div').forEach(function(el) {
    if (el.id) order.push(el.id);
  });
  W.saveWidgetOrder(order);
  setTimeout(function() { W.injectStatusBadges(); }, 150);
};

// Streak
// ============================================================
W.Streak = {
  load: function() {
    const c = CONFIG.children[CONFIG.active], key = c.storageKeys.streak;
    let data; try { data = JSON.parse(localStorage.getItem(key)); } catch(e) {}
    if (!data) data = { count: 0, lastDate: null };
    const today = new Date().toISOString().split('T')[0];
    const yesterday = new Date(Date.now()-86400000).toISOString().split('T')[0];
    if (data.lastDate === yesterday) {} else if (data.lastDate !== today) { data.count = 0; }
    localStorage.setItem(key, JSON.stringify(data));
    const el = $('profile-streak');
    if (el) { el.textContent = `Streak ${data.count} day${data.count!==1?'s':''}`; el.style.color = data.count>=7?'var(--accent2)':data.count>=3?'var(--orange)':'var(--text2)'; }
  }
};

// ============================================================
// Profile
// ============================================================
W.Profile = {
  load: function() {
    const c = CONFIG.children[CONFIG.active], key = c.storageKeys.profile;
    let p; try { p = JSON.parse(localStorage.getItem(key)); } catch(e) {}
    if (!p) p = { avatar: '\uD83D\uDC67', name: c.name };
    const ae = $('profile-avatar'), ne = $('profile-name');
    if (ae) ae.textContent = p.avatar||'\uD83D\uDC67';
    if (ne) ne.value = p.name||c.name;
  },
  save: function() {
    const c = CONFIG.children[CONFIG.active];
    const name = ($('profile-name')?.value?.trim()) || c.name;
    const avatar = $('profile-avatar')?.textContent||'\uD83D\uDC67';
    localStorage.setItem(c.storageKeys.profile, JSON.stringify({name,avatar}));
  }
};

// ============================================================
// Config Panel
// ============================================================
W.openConfig = function() {
  const panel = $('config-panel'), overlay = $('config-overlay');
  let html = '<h3>⚙️ Widget Settings</h3><p style="font-size:11px;color:var(--text2);margin-bottom:12px">Drag ⠿ handles on dashboard to reorder</p><div class="config-list">';
  W.config.list.forEach(w => {
    const checked = W.config.isVisible(w.id) ? 'checked' : '';
    html += `<div class="config-item" onclick="W.config.toggle('${w.id}');this.querySelector('input').checked=!this.querySelector('input').checked">
      <span>${w.icon}</span><span style="flex:1;font-weight:600;font-size:13px">${w.label}</span>
      <input type="checkbox" ${checked} onclick="event.stopPropagation()">
    </div>`;
  });
  html += '</div>';
  html += '<button onclick="W.resetWidgetOrder()" style="width:100%;padding:8px;margin-top:12px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;cursor:pointer;font-size:12px;color:var(--text)">↺ Reset Widget Order</button>';
  if (panel) panel.innerHTML = html;
  if (panel) panel.classList.add('open');
  if (overlay) overlay.classList.add('show');
};
W.closeConfig = function() {
  $('config-panel')?.classList.remove('open');
  $('config-overlay')?.classList.remove('show');
};