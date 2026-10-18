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
          KidCheck._apply(card, on);
          store(key, on ? '1' : '0');
          // 同一任务在同页多处渲染（Today 打勾卡 + My Plan 三周卡）时保持视觉同步
          var tid = card.dataset.taskId;
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
     失焦或点「保存」写入 localStorage，key = kid-ask-<页面标识>-<卡片序号>；
     再打开时回填；「清除」可清空；已存内容以只读方式再显示一次。 */
  var KidAsk = {
    init: function (rootSelector) {
      var root = resolveRoot(rootSelector);
      $$(root, '.kid-ask-card').forEach(function (card) {
        var idx = docIndex(card, '.kid-ask-card');
        var key = 'kid-ask-' + pageId() + '-' + idx;
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
        ta.placeholder = card.dataset.placeholder || '写下你的想法…';
        ta.setAttribute('aria-label', q.textContent.trim().slice(0, 60) || '写下你的想法');

        var actions = el('div', 'kid-ask-actions');
        var saveBtn = el('button', 'kid-btn kid-btn-primary', '保存');
        saveBtn.type = 'button';
        var clearBtn = el('button', 'kid-btn kid-btn-ghost', '清除');
        clearBtn.type = 'button';
        actions.appendChild(saveBtn);
        actions.appendChild(clearBtn);

        var saved = el('div', 'kid-ask-saved');
        saved.hidden = true;
        var savedLabel = el('span', 'kid-ask-saved-label', '你之前写的');
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
          saveBtn.textContent = '已保存';
          setTimeout(function () { saveBtn.textContent = '保存'; }, 1400);
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
          saveBtn.textContent = '已清除';
          setTimeout(function () { saveBtn.textContent = '保存'; }, 1400);
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
      var label = el('span', 'kid-progress-label', box.dataset.kidLabel || '任务进度');
      var text = el('span', 'kid-progress-text', '已完成 0 / 0');
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
      if (text) text.textContent = '已完成 ' + done + ' / ' + total;
      if (fill) fill.style.width = (total ? Math.round(done / total * 100) : 0) + '%';
      box.dataset.state = (!total || done === total) ? 'done' : 'active';
      return { done: done, total: total };
    }
  };

  /* ================================================================ KidStamp */
  /* 给 .kid-decide-card 加「待你决定」角标，并支持卡片内 data-choice 单选互斥。
     这里是孩子自己拍板的事，只记录孩子的选择（key = kid-stamp-<页面标识>-<卡片序号>）。 */
  var KidStamp = {
    init: function (rootSelector) {
      var root = resolveRoot(rootSelector);
      $$(root, '.kid-decide-card').forEach(function (card) {
        var idx = docIndex(card, '.kid-decide-card');
        var key = 'kid-stamp-' + pageId() + '-' + idx;
        if (!claim(card)) { KidStamp._sync(card, key); return; }

        if (!card.querySelector('.kid-stamp')) {
          card.appendChild(el('span', 'kid-stamp', '待你决定'));
        }
        /* 标记「本卡已挂角标」，供 kid.css 的 .kid-has-stamp 让标题让位。
           不能用 .kid-stamp + .kid-h 相邻选择器——角标是 appendChild 到卡片末尾的。 */
        card.classList.add('kid-has-stamp');
        var options = $$(card, '[data-choice]');
        options.forEach(function (opt) {
          opt.setAttribute('role', 'radio');
          opt.setAttribute('aria-checked', 'false');
          opt.addEventListener('click', function (e) {
            e.stopPropagation();
            store(key, opt.dataset.choice);
            KidStamp._sync(card, key);
            notify();
          });
          opt.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
              e.preventDefault();
              store(key, opt.dataset.choice);
              KidStamp._sync(card, key);
              notify();
            }
          });
        });
        KidStamp._sync(card, key);
      });
      return true;
    },

    /** 回填孩子的选择：同卡互斥、选中加 .picked、角标显示已选项 */
    _sync: function (card, key) {
      if (!card) return;
      var picked = load(key);
      var opts = $$(card, '[data-choice]');
      var hit = null;
      opts.forEach(function (opt) {
        var on = picked != null && opt.dataset.choice === picked;
        opt.classList.toggle('picked', on);
        opt.setAttribute('aria-checked', on ? 'true' : 'false');
        if (on) hit = opt;
      });
      card.classList.toggle('kid-has-pick', picked != null);
      var stamp = card.querySelector('.kid-stamp');
      if (!stamp) return;
      if (picked == null) { stamp.textContent = '待你决定'; return; }
      var label = picked;
      if (hit) {
        label = hit.dataset.choiceLabel || (hit.textContent || '').trim() || picked;
        if (label.length > 12) label = label.slice(0, 12) + '…';
      }
      stamp.textContent = '你的选择：' + label;
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
      var box = el('div', 'kid-streak-toast', '🔥 连打卡 ' + n + ' 天！给自己鼓个掌 👏');
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
          st.lastDate === today ? '连打卡 ' + st.count + ' 天' :
          st.count > 0 ? '连打卡 ' + st.count + ' 天 · 今天还没打卡' :
          '今天完成一件事，就开始连打卡'));
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

    /** 今日任务全部完成：撒彩带 + 显示「今天到此为止」停止提示（考试年硬约束） */
    onAllDone: function (msgSelector) {
      if (document.documentElement && document.documentElement.dataset.kidCheerBound) return;
      document.documentElement.dataset.kidCheerBound = '1';
      document.addEventListener('kid:change', function () {
        var cards = document.querySelectorAll('.kid-task-card');
        var done = document.querySelectorAll('.kid-task-card.checked');
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
