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

  /* ------------------------------------------------------------------ 导出 */
  global.KidCheck = KidCheck;
  global.KidAsk = KidAsk;
  global.KidProgress = KidProgress;
  global.KidStamp = KidStamp;

})(window);
