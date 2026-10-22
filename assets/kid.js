/* kid.js — Diya / Jemma 两份工作台共用的「孩子向」互动层
 *
 * 用法（放在页面自身 <script> 之后，或 DOMContentLoaded 之后调用）：
 *   <script src="/assets/kid.js"></script>
 *   <script>
 *     window.KID_CHILD = 'diya';            // 建议：页面标识，决定 localStorage key 前缀
 *     KidCheck.init('#today-panel');
 *     KidAsk.init('#today-panel');
 *     KidStamp.init('#today-panel');
 *     KidProgress.render('#today-progress');
 *   </script>
 *
 * 约束：无任何外部依赖；四个函数全部幂等（重复调用不重复绑事件）；
 * 找不到元素时静默返回，绝不抛错；localStorage 不可用时降级为纯内存。
 */
(function (global) {
  'use strict';

  /* ---------------------------------------------------------------- 基础工具 */

  /** 页面标识：优先 window.KID_CHILD，其次 <body data-kid-page>，最后用路径名 */
  function pageId() {
    if (global.KID_CHILD) return String(global.KID_CHILD);
    var body = document.body;
    if (body && body.dataset && body.dataset.kidPage) return body.dataset.kidPage;
    var path = (location.pathname || '').split('/').pop() || 'page';
    return path.replace(/\.[a-z0-9]+$/i, '') || 'page';
  }

  /** 安全查询：支持选择器字符串或元素；找不到返回空数组 */
  function $$(root, sel) {
    try {
      if (!root) return [];
      if (typeof root === 'string') return Array.prototype.slice.call(document.querySelectorAll(root));
      if (root.nodeType === 1) {
        // 只传元素时把它自己当作唯一目标；传了 sel 才在内部搜
        if (!sel) return [root];
        return Array.prototype.slice.call(root.querySelectorAll(sel));
      }
      return [];
    } catch (e) { return []; }
  }

  /** 解析 root 参数为容器元素；找不到返回 null（不回退到 body，避免选择器写错时全页被绑） */
  function resolveRoot(root) {
    try {
      if (!root) return document.body;
      if (typeof root === 'string') return document.querySelector(root);
      if (root.nodeType === 1) return root;
      return document.body;
    } catch (e) { return null; }
  }

  /** localStorage 读写；不可用（隐私模式/配额满）时静默降级 */
  var memory = {};
  function store(key, val) {
    try {
      if (val === null || val === undefined) { localStorage.removeItem(key); delete memory[key]; return; }
      localStorage.setItem(key, val);
      delete memory[key];
    } catch (e) {
      if (val === null || val === undefined) { delete memory[key]; } else { memory[key] = val; }
    }
  }
  function load(key) {
    try {
      var v = localStorage.getItem(key);
      if (v !== null) return v;
    } catch (e) { /* 落到内存 */ }
    return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null;
  }

  /** 幂等守卫：已绑定过就返回 false */
  function claim(el) {
    if (!el || el.dataset.kidBound === '1') return false;
    el.dataset.kidBound = '1';
    return true;
  }

  /** 元素在整篇文档同类中的序号（跨页面稳定） */
  function docIndex(el, sel) {
    var all = $$(sel);
    for (var i = 0; i < all.length; i++) { if (all[i] === el) return i + 1; }
    return 0;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ------------------------------------------------ 浅/深背景自适应（配合 kid.css） */
  /* app.html 目前是浅色底、jemma.html 是深色底；同一批类名两边都要过 AA，
     所以这里读一下页面自己的 --bg，浅底时给 <html> 打 .kid-light。 */
  function syncTheme() {
    try {
      var cs = getComputedStyle(document.documentElement);
      var bg = (cs.getPropertyValue('--bg') || '').trim();
      if (!/^#|rgb/.test(bg)) return;
      var m = bg.match(/rgba?\(([^)]+)\)/);
      var rgb;
      if (m) { rgb = m[1].split(',').map(function (n) { return parseFloat(n); }); }
      else if (bg.length === 7) {
        rgb = [1, 3, 5].map(function (i) { return parseInt(bg.substr(i, 2), 16); });
      } else { return; }
      var lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
      document.documentElement.classList.toggle('kid-light', lum > 0.6);
    } catch (e) { /* 不影响功能 */ }
  }
  syncTheme();
  if (document.addEventListener) document.addEventListener('DOMContentLoaded', syncTheme);

  /* 勾选状态变化后广播，供 KidProgress 自动刷新 */
  function notify() {
    try { document.dispatchEvent(new CustomEvent('kid:change', { bubbles: true })); } catch (e) { /* 忽略 */ }
  }

  /* ================================================================ KidCheck */
  /* 把 .kid-task-card 变成可勾选任务；状态存 localStorage，
     key = kid-check-<页面标识>-<data-task-id 或顺序号>。 */
  var KidCheck = {
    init: function (rootSelector) {
      var root = resolveRoot(rootSelector);
      var cards = $$(root, '.kid-task-card');
      cards.forEach(function (card, i) {
        var id = card.dataset.taskId || String(docIndex(card, '.kid-task-card') || i + 1);
        var key = 'kid-check-' + pageId() + '-' + id;
        if (!claim(card)) { KidCheck._apply(card, load(key) === '1'); return; }

        card.setAttribute('role', 'checkbox');
        card.setAttribute('tabindex', '0');

        var toggle = function () {
          var on = !card.classList.contains('checked');
          var tid = card.dataset.taskId;
          KidCheck._apply(card, on);
          store(key, on ? '1' : '0');
          // R2：data-sync="server" 的卡（三周计划）勾选同步服务端 tasks_<child>.json（twp- 前缀键）
          if (card.dataset.sync === 'server' && global.KidTasksBridge && tid) {
            try { global.KidTasksBridge.setDone(tid, on); } catch (e) { /* 服务端不可达：本地已写 */ }
          }
          // 同一任务在同页多处渲染（Today 打勾卡 + My Plan 三周卡）时保持视觉同步
          if (tid) {
            $$('.kid-task-card').forEach(function (c) {
              if (c !== card && c.dataset.taskId === tid) KidCheck._apply(c, load(key) === '1');
            });
          }
          notify();
        };
        KidCheck._apply(card, load(key) === '1');

        card.addEventListener('click', function (e) {
          if (e.target.closest('a,button,input,textarea,select,label')) return;
          toggle();
        });
        card.addEventListener('keydown', function (e) {
          if (e.key === ' ' || e.key === 'Enter' || e.key === 'Spacebar') { e.preventDefault(); toggle(); }
        });
        var handle = card.querySelector('.kid-toggle');
        if (handle) { handle.setAttribute('aria-hidden', 'true'); handle.addEventListener('click', function (e) { e.stopPropagation(); toggle(); }); }
      });
      return cards.length;
    },

    /** 勾上时叠加 .kid-done-card 降权视觉，但仍然可点、可取消 */
    _apply: function (card, on) {
      if (!card) return;
      card.classList.toggle('checked', !!on);
      card.classList.toggle('kid-done-card', !!on);
      card.setAttribute('aria-checked', on ? 'true' : 'false');
    }
  };

  /* ================================================================== KidAsk */
  /* 把 .kid-ask-card 变成可展开问答卡：
     点击展开 textarea（placeholder 取 data-placeholder）；
     失焦或点「保存」写入 localStorage，key = kid-ask-<页面标识>-<卡片序号>[-<周序号>]；
     带轮换题池的卡 key 含 epoch 周序号（问题按周轮换，答案必须与问题同周对应，
     否则第 2 周起旧答案会错位显示在新问题下）；无题池的卡问题固定，key 不含周序号；
     再打开时回填；「清除」可清空；已存内容以只读方式再显示一次。 */
  var KidAsk = {
    init: function (rootSelector) {
      var root = resolveRoot(rootSelector);
      $$(root, '.kid-ask-card').forEach(function (card) {
        var idx = docIndex(card, '.kid-ask-card');
        // R8：周轮换问题池——data-question-pool 为 JSON 数组，按自然周（epoch 周）轮换，
        // 同一周内问题固定；无池或不合法时保持页面原文案
        var wi = Math.floor(Date.now() / 604800000);
        var hasPool = false;
        if (card.dataset.questionPool) {
          try {
            var pool = JSON.parse(card.dataset.questionPool);
            if (pool && pool.length) {
              hasPool = true;
              var qEl = card.querySelector('strong');
              if (qEl) qEl.textContent = pool[wi % pool.length];
            }
          } catch (e) { /* 池格式错误：保持原文案 */ }
        }
        // R8 修复（V3 计划 M5 既定方案）：轮换问题的答案 key 必须含周序号，
        // 旧格式 key 的数据保留不迁移——避免跨周错配
        var key = 'kid-ask-' + pageId() + '-' + idx + (hasPool ? '-' + wi : '');
        if (!claim(card)) { KidAsk._sync(card, key); return; }

        /* 问题本体：没有 .kid-ask-q 就把原有内容收进去，保证斜体样式与点击区都在 */
        var q = card.querySelector('.kid-ask-q');
        if (!q) {
          q = el('div', 'kid-ask-q');
          while (card.firstChild) q.appendChild(card.firstChild);
          card.insertBefore(q, card.firstChild);
        }
        q.setAttribute('role', 'button');
        q.setAttribute('tabindex', '0');
        q.setAttribute('aria-expanded', 'false');

        var body = el('div', 'kid-ask-body');
        body.hidden = true;

        var ta = el('textarea', 'kid-ask-input');
        ta.rows = 4;
        ta.placeholder = card.dataset.placeholder || 'Write your thoughts…';
        ta.setAttribute('aria-label', q.textContent.trim().slice(0, 60) || 'Write your thoughts');

        var actions = el('div', 'kid-ask-actions');
        var saveBtn = el('button', 'kid-btn kid-btn-primary', 'Save');
        saveBtn.type = 'button';
        var clearBtn = el('button', 'kid-btn kid-btn-ghost', 'Clear');
        clearBtn.type = 'button';
        actions.appendChild(saveBtn);
        actions.appendChild(clearBtn);

        var saved = el('div', 'kid-ask-saved');
        saved.hidden = true;
        var savedLabel = el('span', 'kid-ask-saved-label', 'You wrote before');
        var savedText = el('p', 'kid-ask-saved-text');
        saved.appendChild(savedLabel);
        saved.appendChild(savedText);

        body.appendChild(ta);
        body.appendChild(actions);
        body.appendChild(saved);
        card.appendChild(body);

        var save = function () {
          store(key, ta.value);
          KidAsk._sync(card, key);
          saveBtn.textContent = 'Saved';
          setTimeout(function () { saveBtn.textContent = 'Save'; }, 1400);
        };

        var toggle = function () {
          var open = body.hidden;
          body.hidden = !open;
          q.setAttribute('aria-expanded', open ? 'true' : 'false');
          if (open) {
            ta.value = load(key) || '';
            KidAsk._sync(card, key);
            try { ta.focus(); } catch (e) { /* 忽略 */ }
          }
        };

        q.addEventListener('click', toggle);
        q.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') { e.preventDefault(); toggle(); }
        });
        card.addEventListener('click', function (e) {
          // 问题本体由自己的处理器负责（否则冒泡到这里会二次切换）
          if (e.target.closest('.kid-ask-q')) return;
          if (body.hidden || e.target.closest('textarea,button,input,select,a,label')) return;
          toggle();
        });
        ta.addEventListener('blur', save);
        ta.addEventListener('input', function () { savedText.textContent = ta.value; saved.hidden = !ta.value; });
        saveBtn.addEventListener('click', function () { save(); ta.focus(); });
        clearBtn.addEventListener('click', function () {
          ta.value = '';
          store(key, null);
          saved.hidden = true;
          savedText.textContent = '';
          saveBtn.textContent = 'Cleared';
          setTimeout(function () { saveBtn.textContent = 'Save'; }, 1400);
          ta.focus();
        });

        KidAsk._sync(card, key);
      });
      return true;
    },

    /** 刷新只读回显：把已存内容再显示一次，让孩子看到自己之前写了什么 */
    _sync: function (card, key) {
      if (!card) return;
      var saved = card.querySelector('.kid-ask-saved');
      var text = card.querySelector('.kid-ask-saved-text');
      var ta = card.querySelector('.kid-ask-input');
      if (!saved || !text) return;
      var val = load(key) || '';
      saved.hidden = !val;
      text.textContent = val;
      if (ta && !ta.value) ta.value = val;
    }
  };

  /* ============================================================= KidProgress */
  /* 在 elSelector 内渲染「已完成 3 / 7」+ 进度条（--kid-done 填充）。
     统计范围：该元素最近的上级容器内所有 .kid-task-card；找不到则统计整篇文档。 */
  var KidProgress = {
    render: function (elSelector) {
      var targets = $$(elSelector);
      targets.forEach(function (box) {
        var scope = KidProgress._scope(box);
        if (!KidProgress._build(box)) return;
        KidProgress._update(box, scope);
      });
      if (!targets.length) return 0;   /* 本页没有进度条容器：静默返回，不抛错 */
      if (document.documentElement &&
          !document.documentElement.dataset.kidProgressBound) {
        document.documentElement.dataset.kidProgressBound = '1';
        document.addEventListener('kid:change', function () { KidProgress.refresh(); });
      }
      return targets.length;
    },

    /** 就地刷新全部已渲染的进度条（勾选变化后自动调用） */
    refresh: function () {
      $$('.kid-progress').forEach(function (box) {
        if (box.dataset.kidBound === '1') KidProgress._update(box, KidProgress._scope(box));
      });
    },

    /** 最近的、含 .kid-task-card 的上级容器；没有则用 document */
    _scope: function (box) {
      var node = box && box.parentElement;
      while (node) {
        if (node.querySelector && node.querySelector('.kid-task-card')) return node;
        node = node.parentElement;
      }
      return document;
    },

    /** 幂等建骨架；返回 false 表示无法渲染 */
    _build: function (box) {
      if (!box || box.nodeType !== 1) return false;
      if (box.classList.contains('kid-progress') && box.querySelector('.kid-progress-bar')) {
        box.dataset.kidBound = '1';
        return true;
      }
      box.classList.add('kid-progress');
      box.innerHTML = '';
      var top = el('div', 'kid-progress-top');
      var label = el('span', 'kid-progress-label', box.dataset.kidLabel || 'Task progress');
      var text = el('span', 'kid-progress-text', 'Done 0 / 0');
      top.appendChild(label);
      top.appendChild(text);
      var bar = el('div', 'kid-progress-bar');
      bar.appendChild(el('div', 'kid-progress-fill'));
      box.appendChild(top);
      box.appendChild(bar);
      box.dataset.kidBound = '1';
      return true;
    },

    _update: function (box, scope) {
      var cards = (scope && scope.querySelectorAll) ? scope.querySelectorAll('.kid-task-card') : [];
      var total = cards.length;
      var done = 0;
      Array.prototype.forEach.call(cards, function (c) { if (c.classList.contains('checked')) done++; });
      var text = box.querySelector('.kid-progress-text');
      var fill = box.querySelector('.kid-progress-fill');
      if (text) text.textContent = 'Done ' + done + ' / ' + total;
      if (fill) fill.style.width = (total ? Math.round(done / total * 100) : 0) + '%';
      box.dataset.state = (!total || done === total) ? 'done' : 'active';
      return { done: done, total: total };
    }
  };

/* ================================================================ KidStamp */
/* 给 .kid-decide-card 加「待你决定」角标，并支持卡片内 data-choice 单选互斥。
   这里是孩子自己拍板的事，只记录孩子的选择。

   key 规则（2026-10-19 加固，修复「刷新后选择丢失/互覆」）：
     kid-stamp-<页面标识>-<卡片ID>[-<分组ID>]
   - 卡片ID：取卡片 data-decide-id；缺省退回文档序号（兼容未标注的旧页）。
     显式 ID 让 key 不随 DOM 增删/排序漂移——旧版按序号命名，改版插一张卡
     就会让所有已选状态"失踪"。
   - 分组ID：一组 .kid-choices 一个 key。一张卡里问多个问题时各组互不覆盖
     （旧版整卡共用一个 key：选第二问会把第一问的选择当场抹掉，刷新后只剩
     最后选的那个）。分组 data-group-id 缺省时，多组卡按组序 1/2/…；
     单组卡不带分组后缀——与旧 key 格式保持兼容。
   - 旧 key 迁移：新 key 还没有值、且旧整卡 key 存的选择属于本组选项时，
     自动继承旧值，版本升级不丢孩子已经做出的选择。

   持久化两层（与 Notes 同一协议）：
     1) localStorage：本机即时渲染、断网可用——浏览器刷新（F5）不回退
     2) 服务端 /api/decide/<child>：跨设备/换浏览器/清缓存后的真源；
        部署流水线 rsync --exclude='data/'，绝不会覆盖它
   首次 init 时拉取一次（本地优先，仅当服务端 updatedAt 更新才覆盖）；
   每次选择防抖 300ms 推送全量快照。任一层失败都静默降级，不影响另一层。 */
var KidStamp = {
  _pulled: false,

  init: function (rootSelector) {
    var root = resolveRoot(rootSelector);
    $$(root, '.kid-decide-card').forEach(function (card) {
      var cardIdx = docIndex(card, '.kid-decide-card');
      var cardId = card.dataset.decideId || String(cardIdx);
      /* 角标（幂等：卡片 DOM 被重建后重新 init 会补上） */
      if (!card.querySelector('.kid-stamp')) {
        card.appendChild(el('span', 'kid-stamp', 'Your call'));
      }
      card.classList.add('kid-has-stamp');
      var legacyKey = 'kid-stamp-' + pageId() + '-' + cardIdx; /* 旧版整卡一个 key */
      var legacyPicked = load(legacyKey);
      var groups = $$(card, '.kid-choices');
      if (!groups.length) groups = [card];
      groups.forEach(function (g, gi) {
        var gid = g.dataset.groupId || (groups.length > 1 ? String(gi + 1) : '');
        var key = 'kid-stamp-' + pageId() + '-' + cardId + (gid ? '-' + gid : '');
        var opts = $$(g, '[data-choice]');
        if (!opts.length) return;
        g.dataset.kidStampKey = key;
        /* 旧 key 迁移：新 key 未写值，且旧整卡 key 存的选择属于本组 → 继承 */
        if (load(key) == null && legacyPicked != null &&
            opts.some(function (o) { return o.dataset.choice === legacyPicked; })) {
          store(key, legacyPicked);
        }
        if (!claim(g)) { KidStamp._syncGroup(g); return; }
        opts.forEach(function (opt) {
          opt.setAttribute('role', 'radio');
          opt.setAttribute('aria-checked', 'false');
          var pick = function () {
            store(key, opt.dataset.choice);
            store(key + '__at', new Date().toISOString());   // R14：记录决定时间（归档条显示）
            KidStamp._syncGroup(g);
            KidStamp._syncBadge(card);
            notify();
            KidStamp.push();
          };
          opt.addEventListener('click', function (e) { e.stopPropagation(); pick(); });
          opt.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
              e.preventDefault(); pick();
            }
          });
        });
        KidStamp._syncGroup(g);
      });
      KidStamp._syncBadge(card);
    });
    if (!KidStamp._pulled) { KidStamp._pulled = true; KidStamp.pull(); }
    return true;
  },

  /** 回填一组的选择：同组互斥、选中加 .picked */
  _syncGroup: function (group) {
    if (!group) return;
    var key = group.dataset.kidStampKey;
    var picked = key ? load(key) : null;
    $$(group, '[data-choice]').forEach(function (opt) {
      var on = picked != null && opt.dataset.choice === picked;
      opt.classList.toggle('picked', on);
      opt.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  },

  /** 角标文案：单问卡「你的选择：…」；多问卡「已决定 N/M」；未选「待你决定」 */
  _syncBadge: function (card) {
    if (!card) return;
    var groups = $$(card, '.kid-choices');
    var picked = 0, firstLabel = '';
    groups.forEach(function (g) {
      var key = g.dataset.kidStampKey;
      var val = key ? load(key) : null;
      if (val == null) return;
      picked++;
      if (!firstLabel) {
        var hit = $$(g, '[data-choice]').filter(function (o) { return o.dataset.choice === val; })[0];
        if (hit) firstLabel = hit.dataset.choiceLabel || (hit.textContent || '').trim() || val;
      }
    });
    card.classList.toggle('kid-has-pick', picked > 0);
    var stamp = card.querySelector('.kid-stamp');
    if (!picked) { if (stamp) stamp.textContent = 'Your call'; return; }
    if (groups.length > 1) { if (stamp) stamp.textContent = 'Decided ' + picked + '/' + groups.length; }
    else {
      var label0 = firstLabel || '';
      if (label0.length > 12) label0 = label0.slice(0, 12) + '…';
      if (stamp) stamp.textContent = 'Your pick: ' + label0;
    }
    // R14：data-decide-once 卡——全部问题已决时折叠为归档条（点开可改选）
    if (card.dataset.decideOnce === 'true') {
      var bar = card.querySelector('.kid-decided-bar');
      var allPicked = picked >= groups.length;
      if (allPicked) {
        card.classList.add('kid-decided');
        if (!bar) {
          bar = el('button', 'kid-decided-bar');
          bar.type = 'button';
          bar.setAttribute('aria-expanded', 'false');
          bar.addEventListener('click', function () {
            var open = card.classList.toggle('kid-expanded');
            bar.setAttribute('aria-expanded', open ? 'true' : 'false');
          });
          card.insertBefore(bar, card.firstChild);
        }
        var at = '';
        try { at = load(groups[0].dataset.kidStampKey + '__at') || ''; } catch (e) {}
        var dstr = at ? '（' + at.slice(0, 10) + '）' : '';
        bar.textContent = '✅ Decided: ' + (firstLabel || '') + dstr + ' · tap to change';
      } else {
        card.classList.remove('kid-decided');
        card.classList.remove('kid-expanded');
        if (bar) bar.remove();
      }
    }
  },

  /* ---- 服务端同步：localStorage 即时渲染，服务端做跨设备真源 ---- */
  _child: function () {
    try {
      return String(window.KID_CHILD || (document.body && document.body.dataset.kidPage) || 'diya');
    } catch (e) { return 'diya'; }
  },
  _syncAtKey: function () { return 'kid-decide-sync-at-' + this._child(); },
  _pushTimer: null,

  /** 全量快照：{localStorage key: choice}，只收集本页真实存在的组 */
  snapshot: function () {
    var out = {};
    $$('.kid-decide-card .kid-choices').forEach(function (g) {
      var k = g.dataset.kidStampKey;
      if (!k) return;
      var v = load(k);
      if (v != null) out[k] = v;
    });
    return out;
  },

  /** 防抖推送（每次选择后调用；失败静默降级，本地已写） */
  push: function () {
    if (typeof fetch !== 'function') return;
    clearTimeout(this._pushTimer);
    var self = this;
    this._pushTimer = setTimeout(function () {
      var payload = self.snapshot();
      if (!Object.keys(payload).length) return;
      var at = new Date().toISOString();
      fetch('/api/decide/' + self._child(), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload: payload, updatedAt: at })
      }).then(function (r) {
        if (r && r.ok) { try { localStorage.setItem(self._syncAtKey(), at); } catch (e) {} }
      }).catch(function () { /* 静默降级：本地已写，下次选择再试 */ });
    }, 300);
  },

  /** 首次 init 拉取一次：本地优先，仅当服务端 updatedAt 更新才覆盖 */
  pull: function () {
    if (typeof fetch !== 'function') return;
    var self = this;
    fetch('/api/decide/' + this._child())
      .then(function (r) { return (r && r.ok) ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.payload || typeof d.payload !== 'object') return;
        var localAt = '';
        try { localAt = localStorage.getItem(self._syncAtKey()) || ''; } catch (e) {}
        if (localAt && d.updatedAt && d.updatedAt <= localAt) return;
        var changed = false;
        Object.keys(d.payload).forEach(function (k) {
          var v = d.payload[k];
          if (typeof v !== 'string' || !v) return;
          if (load(k) !== v) { store(k, v); changed = true; }
        });
        if (d.updatedAt) { try { localStorage.setItem(self._syncAtKey(), d.updatedAt); } catch (e) {} }
        if (changed) {
          $$('.kid-decide-card').forEach(function (card) {
            $$(card, '.kid-choices').forEach(function (g) { self._syncGroup(g); });
            self._syncBadge(card);
          });
        }
      })
      .catch(function () { /* 静默降级：用本地状态 */ });
  }
};

  /* ============================================================== KidStreak */
  /* 连续打卡：记录「今天完成了任务」的天数；支持每周 1 个休息日
     （漏打一天不断签，防止孩子为保连签带病学习）。
     key = kid-streak-<页面标识>，值 JSON {count, lastDate, restWeek}。
     用法：
       KidStreak.render('#streak-box');   // 渲染「🔥 连打卡 N 天」
       KidStreak.record();                // 今天完成打卡（幂等，可反复调）
       KidStreak.listen('#streak-box');   // 自动：有任务勾着时 record + 刷新徽章
     里程碑：3/7/14/30/50/100 天弹 toast。 */
  var KidStreak = {
    _today: function () {
      var d = new Date();
      return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    },
    _daysBetween: function (a, b) { /* yyyy-mm-dd 字符串差，按 UTC 天数 */
      var t = function (s) { var p = s.split('-'); return Date.UTC(+p[0], +p[1] - 1, +p[2]); };
      return Math.round((t(b) - t(a)) / 86400000);
    },
    _weekKey: function (d) { /* ISO 周标识（周一为一周起点） */
      var t = new Date(d + 'T00:00:00Z');
      t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7) + 3); /* 本周四 */
      var first = Date.UTC(t.getUTCFullYear(), 0, 4);
      var firstThu = new Date(first);
      firstThu.setUTCDate(firstThu.getUTCDate() - ((firstThu.getUTCDay() + 6) % 7) + 3);
      var week = 1 + Math.round((t - firstThu) / (7 * 86400000));
      return t.getUTCFullYear() + '-W' + week;
    },
    _read: function () {
      var raw = load('kid-streak-' + pageId());
      if (!raw) return { count: 0, lastDate: null, restWeek: null };
      try {
        var st = JSON.parse(raw);
        return { count: st.count | 0, lastDate: st.lastDate || null, restWeek: st.restWeek || null };
      } catch (e) { return { count: 0, lastDate: null, restWeek: null }; }
    },
    _write: function (st) { store('kid-streak-' + pageId(), JSON.stringify(st)); },

    /** 今天完成打卡（幂等：同一天重复调用不叠加）。返回最新状态 */
    record: function () {
      var today = KidStreak._today();
      var st = KidStreak._read();
      if (st.lastDate === today) return st;             /* 今天已打 */
      if (!st.lastDate) {
        st = { count: 1, lastDate: today, restWeek: null };
      } else {
        var diff = KidStreak._daysBetween(st.lastDate, today);
        if (diff === 1) st.count += 1;                  /* 连续 */
        else if (diff === 2 && st.restWeek !== KidStreak._weekKey(today)) {
          st.count += 1;                                /* 漏一天：用掉本周休息日，不断签 */
          st.restWeek = KidStreak._weekKey(today);
        } else st.count = 1;                            /* 断签，重新开始 */
        st.lastDate = today;
      }
      KidStreak._write(st);
      KidStreak._milestone(st.count);
      return st;
    },

    /** 里程碑 toast：3/7/14/30/50/100 天 */
    _milestone: function (n) {
      if ([3, 7, 14, 30, 50, 100].indexOf(n) === -1) return;
      var box = el('div', 'kid-streak-toast', '🔥 ' + n + '-day streak! Give yourself a hand 👏');
      document.body.appendChild(box);
      setTimeout(function () { box.classList.add('show'); }, 16);
      setTimeout(function () {
        box.classList.remove('show');
        setTimeout(function () { box.remove(); }, 400);
      }, 3200);
    },

    /** 渲染 streak 徽章到目标容器（幂等） */
    render: function (elSelector) {
      $$(elSelector).forEach(function (box) {
        if (!box || box.nodeType !== 1) return;
        box.classList.add('kid-streak');
        var st = KidStreak._read();
        var today = KidStreak._today();
        box.dataset.count = st.count;
        box.classList.toggle('kid-streak-medal', st.count >= 3);
        box.innerHTML = '';
        box.appendChild(el('span', 'kid-streak-fire', '🔥'));
        box.appendChild(el('span', 'kid-streak-text',
          st.lastDate === today ? st.count + '-day streak' :
          st.count > 0 ? st.count + '-day streak · not logged in today' :
          'Finish one thing today to start a streak'));
      });
    },

    /** 自动模式：监听勾选变化，有任务勾着时 record；加载时 render */
    listen: function (elSelector) {
      KidStreak.render(elSelector);
      if (document.documentElement && !document.documentElement.dataset.kidStreakBound) {
        document.documentElement.dataset.kidStreakBound = '1';
        document.addEventListener('kid:change', function () {
          if (document.querySelector('.kid-task-card.checked')) KidStreak.record();
          KidStreak.render(elSelector);
        });
      }
    }
  };

  /* =============================================================== KidCheer */
  /* 撒彩带：纯 CSS 动画，无外部库、无图片；同设备同一天最多 3 次（防刷）。
     key = kid-cheer-<页面标识>-<日期>（计数）。
     用法：
       KidCheer.cheer();                     // 立即撒一次
       KidCheer.onAllDone('#today-stop');    // 今日任务全勾时自动撒 + 显示停止提示 */
  var KidCheer = {
    _today: KidStreak._today,
    _key: function () { return 'kid-cheer-' + pageId() + '-' + KidCheer._today(); },
    _quota: function () {
      var n = parseInt(load(KidCheer._key()) || '0', 10);
      return isNaN(n) ? 0 : n;
    },

    cheer: function () {
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      if (KidCheer._quota() >= 3) return;
      store(KidCheer._key(), String(KidCheer._quota() + 1));
      var layer = el('div', 'kid-cheer-layer');
      layer.setAttribute('aria-hidden', 'true');
      var colors = ['#f59e0b', '#8b5cf6', '#10b981', '#06b6d4', '#ef4444', '#f97316', '#3b82f6'];
      for (var i = 0; i < 36; i++) {
        var bit = el('i', 'kid-cheer-bit');
        bit.style.left = (Math.random() * 100) + 'vw';
        bit.style.background = colors[i % colors.length];
        bit.style.animationDelay = (Math.random() * 0.25) + 's';
        bit.style.animationDuration = (0.9 + Math.random() * 0.7) + 's';
        if (i % 3 === 0) bit.style.borderRadius = '50%';
        if (i % 5 === 0) { bit.style.width = '7px'; bit.style.height = '12px'; }
        layer.appendChild(bit);
      }
      document.body.appendChild(layer);
      setTimeout(function () { layer.remove(); }, 2200);
    },

    /** 今日任务全部完成：撒彩带 + 显示「今天到此为止」停止提示（考试年硬约束）。
        scopeSelector 可选：只统计该容器内的任务卡（如 Today's 3 区块），
        缺省统计全页——避免周级/历史卡挡住「今天完成」的判定。 */
    onAllDone: function (msgSelector, scopeSelector) {
      if (document.documentElement && document.documentElement.dataset.kidCheerBound) return;
      document.documentElement.dataset.kidCheerBound = '1';
      document.addEventListener('kid:change', function () {
        var scope = scopeSelector ? document.querySelector(scopeSelector) : null;
        var cards = scope
          ? scope.querySelectorAll('.kid-task-card')
          : document.querySelectorAll('.kid-task-card');
        var done = [];
        for (var i = 0; i < cards.length; i++) {
          if (cards[i].classList.contains('checked')) done.push(cards[i]);
        }
        if (!cards.length || cards.length !== done.length) return;
        KidCheer.cheer();
        var box = msgSelector ? document.querySelector(msgSelector) : null;
        if (box) { box.hidden = false; box.classList.add('show'); }
      });
    }
  };

  /* ------------------------------------------------------------------ 导出 */
  global.KidCheck = KidCheck;
  global.KidAsk = KidAsk;
  global.KidProgress = KidProgress;
  global.KidStamp = KidStamp;
  global.KidStreak = KidStreak;
  global.KidCheer = KidCheer;

})(window);
