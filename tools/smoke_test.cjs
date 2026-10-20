/**
 * 子女教育工作台 — 前端冒烟回归测试（jsdom，双页：app.html + jemma.html）
 *
 * 用途：无需浏览器即可真实加载页面、执行页面 JS、解析 CSS，
 *   - 验证页面无顶层 JS 错误
 *   - 验证关键函数存在
 *   - 【核心】扫描全页所有 onclick 处理器引用的函数，确认都已定义
 *     （这类"悬空处理器"正是"点击无反应"的根因）
 *   - 模拟点击关键按钮，验证弹窗确实打开且 CSS 可见
 *   - 【决定卡持久化】点击语言/自主决定选项 → localStorage 落键 →
 *    多问卡互不覆盖 → 重新 init（模拟刷新）后已选项全部恢复
 *   - 【回归护栏】弹窗不得嵌套在另一个弹窗内 / 打开时祖先链不得有 display:none
 *     （jsdom 只算元素自身样式，不模拟「祖先不渲染则子树不可见」，
 *      历史上 avatar-modal 少写一个 </div> 曾让 llm-settings-modal 假阳性通过）
 *
 * 运行：node tools/smoke_test.cjs        （需在仓库根目录执行）
 * 退出码：0 = 全部通过；1 = 有失败
 */
const path = require('path');
const fs = require('fs');

const JSDOM_CANDIDATES = [
  process.env.JSDOM_PATH,
  'C:/Users/User/.workbuddy/binaries/node/workspace/node_modules/jsdom',
  'jsdom',
];
const JSDOM_PATH = JSDOM_CANDIDATES.find((p) => {
  if (!p) return false;
  try { require.resolve(p); return true; } catch (e) { return false; }
});
if (!JSDOM_PATH) {
  console.error('找不到 jsdom。请设置 JSDOM_PATH 或执行：cd C:/Users/User/.workbuddy/binaries/node/workspace && npm install jsdom');
  process.exit(2);
}
const { JSDOM, VirtualConsole } = require(JSDOM_PATH);

const PAGES = [
  {
    name: 'app.html (Diya)',
    file: path.resolve(__dirname, '..', 'app.html'),
    url: 'http://localhost:5000/app.html',
    requiredFns: [
      'openLLMSettings', 'llmSaveSettings', 'llmTestSettings', 'llmToggleKey',
      'sendChatMsg', 'loadDashboard', 'loadResources', 'toast',
      'renderTasks', 'markdownToHtml', 'switchTab', 'renderToday3Diya', 'checkReviewDue',
    ],
    modalCases: [
      { name: 'LLM 设置弹窗', btnId: 'llm-settings-btn', modalId: 'llm-settings-modal' },
      { name: '头像选择弹窗', btnId: 'profile-avatar', modalId: 'avatar-modal' },
    ],
    streakGuard: true,
    // url 感知 fetch mock：/api/twp 返回周计划（R5 异步化后三周卡由数据驱动渲染，
    // 不 mock 它则 .kid-task-card 不渲染，R1 的 Streak/Cheer 回归断言会被静默跳过）
    fetchMock: (url) => {
      if (String(url).includes('/api/twp')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            child: 'diya', updatedAt: '2026-10-19T00:00:00+03:00',
            nextCycleEnd: '2026-11-04', version: 1,
            weeks: [
              { id: 'w1', label: 'Week 1 · 启动与摸底', start: '2026-10-15', end: '2026-10-21',
                goal: '节奏立起来', focus: '摸底',
                actions: [{ id: 'w1-d1', label: 'ESAT 摸底', sub: '40 分钟' }] },
              { id: 'w2', label: 'Week 2 · 定向补漏', start: '2026-10-22', end: '2026-10-28',
                goal: '只补没懂的', focus: '补漏',
                actions: [{ id: 'w2-d1', label: '补一类错题', sub: '' }] },
              { id: 'w3', label: 'Week 3 · 收口与锁定', start: '2026-10-29', end: '2026-11-04',
                goal: '固化决定', focus: '锁定',
                actions: [{ id: 'w3-d1', label: '第二次 ESAT', sub: '' }] },
            ],
          }),
        });
      }
      if (String(url).includes('/api/errors/')) {
        if (String(url).includes('stats=1')) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({
              child: 'diya', total: 2, by_subject: { Maths: 2 }, by_cause: { careless: 1 },
              mastered_total: 0, active_total: 2, new_this_week: 2,
            }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { id: 'e1', subject: 'Maths', question: 'R7 mock Q1', solution: 'S1', date: '2026-10-18', type: 'concept' },
            { id: 'e2', subject: 'Physics', question: 'R7 mock Q2', solution: 'S2', date: '2026-10-19', type: 'careless' },
          ]),
        });
      }
      if (String(url).includes('/api/tasks')) {
        // 返回含 twp- 键的任务集：① 恢复 Task List onclick 护栏（toggleTask 等）
        // ② 验证 R2 过滤——twp 键不得渲染进 Task List（由 TWP 区渲染）
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            'general-demo-task': false,
            'english-petal': { done: false, name: 'PETAL' },
            'twp-diya-w1-d1': false,
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          endpoint: 'https://api.openai.com/v1', model: 'gpt-4o',
          temperature: 0.3, has_key: true, key_masked: 'sk-a…z',
        }),
      });
    },
  },
  {
    name: 'jemma.html (Jemma)',
    file: path.resolve(__dirname, '..', 'jemma.html'),
    url: 'http://localhost:5000/jemma.html',
    requiredFns: [
      'loadDashboard', 'switchTab', 'loadNotes', 'loadResources',
      'sendChatMsg', 'markdownToHtml', 'renderTasks', 'loadPlanner',
      'showAvatarPicker', 'openErrorModal',
    ],
    modalCases: [
      { name: '头像选择弹窗', btnId: 'profile-avatar', modalId: 'avatar-modal' },
      { name: '错题弹窗', btnId: 'add-error-btn', modalId: 'error-modal' },
    ],
    streakGuard: true,
    fetchMock: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  },
];

// JS 关键字 / 内置对象，出现在 onclick 里但不是"待校验的全局函数"
const SKIP = new Set([
  'if','for','while','switch','catch','function','return','typeof','new','delete',
  'void','await','async','do','else','case','in','of','this','var','let','const',
  'alert','confirm','prompt','open','close',
  'Number','String','Boolean','Array','Object','JSON','Math','Date','parseInt','parseFloat','isNaN',
]);

// jsdom 默认不加载外部 <script src>，而 loadTasks 等关键函数在 assets/kid-common.js。
// 把本地 assets 脚本内联进 HTML 再执行，行为更接近真实浏览器（外链 http(s) 保留原样）。
// 注意：JS 源码注释里可能含 </script> 字样（kid.js 头部用法示例就有），
// 直接内联会提前闭合标签——必须转义成 <\/script（JS 字符串里等价，HTML 不会误判）。
function inlineScripts(html, baseDir) {
  return html.replace(/<script\s+src="([^"]+)"[^>]*>\s*<\/script>/gi, (m, src) => {
    if (/^https?:\/\//i.test(src)) return m;
    const p = path.join(baseDir, src.split('?')[0].replace(/^\//, ''));
    try {
      const code = fs.readFileSync(p, 'utf-8')
        .replace(/<\/script/gi, '<\\/script')
        .replace(/<!--/g, '<\\!--');
      return '<script>\n' + code + '\n</script>';
    }
    catch (e) { return m; }
  });
}

function runPage(page) {
  return new Promise((resolve) => {
    const rawHtml = fs.readFileSync(page.file, 'utf-8');
    const html = inlineScripts(rawHtml, path.resolve(__dirname, '..'));
    const errors = [];
    const pass = [];
    const fail = [];
    const vc = new VirtualConsole();
    vc.on('jsdomError', (e) => errors.push('[jsdomError] ' + (e.detail?.stack || e.message)));
    vc.on('error', (...a) => errors.push('[console.error] ' + a.join(' ')));

    const dom = new JSDOM(html, {
      runScripts: 'dangerously',
      url: page.url,
      virtualConsole: vc,
    });
    const { window } = dom;
    window.fetch = page.fetchMock;
    // jsdom 无 canvas 实现（getContext 返回 null），雷达图等绘制会误报 TypeError——
    // 打桩一个啥都干的假 2d context，让绘制链路能跑完（冒烟只关心 JS 无错）。
    const fakeCtx = new Proxy({}, {
      get: (t, k) => {
        if (k === 'measureText') return () => ({ width: 0 });
        if (k === 'createLinearGradient' || k === 'createRadialGradient' || k === 'createPattern') {
          return () => ({ addColorStop: () => {} });
        }
        if (k === 'getImageData') return () => ({ data: [] });
        return () => {};
      },
    });
    window.HTMLCanvasElement.prototype.getContext = () => fakeCtx;
    // async 加载函数（loadDashboard 等）里的 rejection 不能拖垮整个测试进程
    window.addEventListener('unhandledrejection', (e) => {
      errors.push('[unhandledRejection] ' + ((e.reason && e.reason.stack) || e.reason || e));
      if (e.preventDefault) e.preventDefault();
    });

    setTimeout(() => {
      const doc = window.document;

      // 1) 顶层 JS 错误
      if (errors.length === 0) pass.push('页面 JS 无顶层错误');
      else errors.forEach((e) => fail.push(e));

      // 2) 关键函数存在
      page.requiredFns.forEach((fn) => {
        if (typeof window[fn] === 'function') pass.push(`函数存在 ${fn}`);
        else fail.push(`函数缺失 ${fn}`);
      });

      // 3) 【核心】全页 onclick 处理器引用的函数必须都已定义
      //    直接扫描「实时 DOM 中已渲染的 onclick 属性」而非源码：
      //    模板字符串生成的 onclick 在 DOM 里已被插值解析，扫源码会误报。
      const clickEls = [...doc.querySelectorAll('[onclick]')];
      const refs = new Set();
      clickEls.forEach((el) => {
        const attr = el.getAttribute('onclick') || '';
        [...attr.matchAll(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g)].forEach((m) => refs.add(m[1]));
      });
      let dangling = 0;
      [...refs].sort().forEach((n) => {
        if (SKIP.has(n)) return;
        if (typeof window[n] === 'function') pass.push(`onclick->${n} 已定义`);
        else { dangling++; fail.push(`onclick->${n} 悬空未定义（点击将无反应）`); }
      });
      if (dangling === 0) pass.push(`全页 ${refs.size} 个 onclick 处理器引用函数全部已定义`);

      // 4) 点击 -> 弹窗打开（含 CSS 可见性）
      page.modalCases.forEach((c) => {
        const btn = doc.getElementById(c.btnId);
        const modal = doc.getElementById(c.modalId);
        if (!btn) { fail.push(`${c.name}: 按钮 #${c.btnId} 不存在`); return; }
        if (!modal) { fail.push(`${c.name}: 弹窗 #${c.modalId} 不存在`); return; }
        let thrown = null;
        try { btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })); }
        catch (e) { thrown = e.message; }
        const hasOpen = modal.classList.contains('open');
        const disp = window.getComputedStyle(modal).display;
        if (thrown) fail.push(`${c.name}: 点击抛错 ${thrown}`);
        else if (hasOpen && disp === 'flex') pass.push(`${c.name}: 点击后打开且可见 (display=${disp})`);
        else fail.push(`${c.name}: 点击后未打开 (open=${hasOpen}, display=${disp})`);
      });

      // 5) 【回归护栏】所有弹窗必须是 main 的直接子元素（不得嵌套在别的弹窗内）
      const overlays = [...doc.querySelectorAll('.modal-overlay')];
      let nested = 0;
      overlays.forEach((o) => {
        const p = o.parentElement;
        if (p && p.classList && p.classList.contains('modal-overlay')) {
          nested++;
          fail.push(`弹窗 #${o.id} 被错误嵌套在 #${p.id || '(匿名)'} 内：父级 display:none 会使整棵子树不渲染`);
        }
      });
      if (nested === 0) {
        pass.push(`${overlays.length} 个弹窗均为独立顶层节点，无错误嵌套`);
        overlays.forEach((o) => pass.push(`弹窗 #${o.id} 挂载于 <${o.parentElement.tagName.toLowerCase()}>`));
      }

      // 6) 【回归护栏】弹窗打开时，祖先链上不得有 display:none（否则真实浏览器不可见）
      page.modalCases.forEach((c) => {
        const modal = doc.getElementById(c.modalId);
        if (!modal) return;
        let n = modal.parentElement, hidden = null;
        while (n && n.tagName !== 'BODY') {
          if (window.getComputedStyle(n).display === 'none') { hidden = n.tagName + (n.id ? '#' + n.id : ''); break; }
          n = n.parentElement;
        }
        if (hidden) fail.push(`${c.name}: 祖先 ${hidden} 为 display:none，弹窗在真实浏览器中不可见`);
        else pass.push(`${c.name}: 祖先链无 display:none 阻断`);
      });


      // 7) 【决定卡持久化 · 2026-10-19 回归护栏】Latin/自主决定刷新后不得丢失：
      //    点击 → picked + localStorage 落键；多问卡两组互不覆盖；
      //    重新 init（模拟 F5 / 部署流水线刷新）后已选项全部恢复
      try {
        var decideCards = [...doc.querySelectorAll('.kid-decide-card')];
        if (decideCards.length) {
          var dcard = decideCards[0];
          var dgroups = [...dcard.querySelectorAll('.kid-choices')];
          if (!dgroups.length) {
            fail.push('决定卡缺少 .kid-choices 选项组');
          } else {
            var dopt1 = dgroups[0].querySelector('[data-choice]');
            dopt1.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
            if (dopt1.classList.contains('picked')) pass.push('决定卡：点击后进入 picked 选中态');
            else fail.push('决定卡：点击后未进入 picked 选中态');
            var dkey1 = dgroups[0].dataset.kidStampKey;
            if (dkey1 && window.localStorage.getItem(dkey1) === dopt1.dataset.choice)
              pass.push('决定卡：localStorage 已写入 ' + dkey1 + '=' + dopt1.dataset.choice);
            else fail.push('决定卡：localStorage 未写入选择（key=' + dkey1 + '）');
            if (dgroups.length > 1) {
              var dopt2 = dgroups[1].querySelector('[data-choice]');
              dopt2.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
              if (dopt1.classList.contains('picked') && dopt2.classList.contains('picked'))
                pass.push('决定卡：多问卡两组选择互不覆盖（key 分组修复验证）');
              else fail.push('决定卡：第二问的选择覆盖了第一问（key 互斥 bug 复现）');
            }
            // 模拟刷新：重新 init（幂等）→ 已选项必须全部恢复
            window.KidStamp.init('body');
            var allBack = true, lost = [];
            dgroups.forEach(function (g) {
              var k = g.dataset.kidStampKey;
              var v = k ? window.localStorage.getItem(k) : null;
              var hit = v ? [...g.querySelectorAll('[data-choice]')].filter(function (o) { return o.dataset.choice === v; })[0] : null;
              if (!hit || !hit.classList.contains('picked')) { allBack = false; lost.push(k || '(无 key)'); }
            });
            if (allBack) pass.push('决定卡：重新 init（模拟 F5 刷新）后已选项全部恢复');
            else fail.push('决定卡：重新 init 后已选项丢失（刷新不持久）：' + lost.join('、'));
            var dstamp = dcard.querySelector('.kid-stamp');
            if (dstamp && dstamp.textContent && dstamp.textContent.trim() !== '待你决定')
              pass.push('决定卡：角标显示「' + dstamp.textContent.trim() + '」');
            else fail.push('决定卡：角标未反映已选状态');
            // R14：decide-once 卡已决归档条（全部问题已决 → 折叠 + 点开可改）
            if (dcard.dataset.decideOnce === 'true') {
              var dbar = dcard.querySelector('.kid-decided-bar');
              if (dbar && dbar.textContent.indexOf('已决定') !== -1)
                pass.push('R14：已决归档条（' + dbar.textContent.trim() + '）');
              else fail.push('R14：归档条未出现（decide-once 未生效）');
              if (dbar) {
                dbar.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
                if (dcard.classList.contains('kid-expanded')) pass.push('R14：点归档条可展开改选');
                else fail.push('R14：归档条点击未展开');
              }
            }
          }
        }
      } catch (e) { fail.push('决定卡持久化测试抛错：' + e.message); }

      // R8：KidAsk 周轮换问题池已应用（strong 文案非空且来自池）
      try {
        var pk = doc.querySelector('.kid-ask-card[data-question-pool] strong');
        if (pk && pk.textContent.trim()) pass.push('R8：周轮换问题池已应用（本周问：「' + pk.textContent.trim().slice(0, 18) + '」）');
        else fail.push('R8：问题池未应用（strong 为空或缺失）');
      } catch (e) { fail.push('R8：断言抛错 ' + e.message); }

      // 8) 【R1 回归护栏 · 2026-10-19】连续打卡链路：侧栏徽章由 KidStreak 渲染；
      //    勾选任务卡 → kid-streak key 写入 + 天数 +1（Diya 历史上勾选与 streak 脱钩，此为其回归护栏）
      try {
        if (page.streakGuard && typeof window.KidStreak === 'object') {
          var skey = 'kid-streak-' + (window.KID_CHILD || 'unknown');
          window.localStorage.removeItem(skey);
          window.KidStreak.render('#profile-streak');
          var sBox = doc.getElementById('profile-streak');
          if (sBox && /连打卡|开始/.test(sBox.textContent))
            pass.push('Streak：侧栏徽章已由 KidStreak 渲染（' + sBox.textContent.trim() + '）');
          else if (sBox)
            fail.push('Streak：侧栏 #profile-streak 仍是旧文案「' + sBox.textContent.trim() + '」（未接入 KidStreak）');
          // 勾选一张任务卡 → 当天打卡写入
          var tcard = doc.querySelector('.kid-task-card');
          if (tcard) {
            tcard.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
            var sraw = window.localStorage.getItem(skey);
            var st = sraw ? JSON.parse(sraw) : null;
            if (st && st.count === 1 && st.lastDate) pass.push('Streak：勾选任务卡当天打卡已写入（count=1）');
            else fail.push('Streak：勾选任务卡未写入打卡（key=' + skey + '，raw=' + sraw + '）——勾选与打卡脱钩');
          }
          // 全勾 → 停止提示可见（KidCheer.onAllDone 绑定验证）
          var cards = [...doc.querySelectorAll('.kid-task-card')];
          var stopHint = doc.getElementById('today-stop');
          if (cards.length && stopHint) {
            cards.forEach(function (c) { if (!c.classList.contains('checked')) c.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })); });
            if (!stopHint.hidden) pass.push('Cheer：任务全勾后「今天到此为止」停止提示已显示');
            else fail.push('Cheer：任务全勾后 #today-stop 仍隐藏（KidCheer.onAllDone 未接入）');
          }
          window.localStorage.removeItem(skey);
        }
      } catch (e) { fail.push('Streak/Cheer 回归测试抛错：' + e.message); }

      // R2 server 同步断言：data-sync="server" 的卡勾选 → KidTasksBridge → saveTasks
      // 落盘 tasks_<child>（twp- 前缀键）。saveTasks 在点击的微任务内完成，这里用下一个
      // 宏任务读取后再 resolve，保证断言进入汇总（此前把 setTimeout 嵌在测试体内、
      // 从外层定时器起算必然晚于 resolve，结果被静默吞掉——已修复）。
      setTimeout(function () {
        try {
          var child0 = window.KID_CHILD || 'unknown';
          var raw = window.localStorage.getItem('tasks_' + child0);
          if (raw && raw.indexOf('"twp-') !== -1)
            pass.push('Tasks：勾选已写入服务端任务存储（twp- 前缀键，单源化生效）');
          else
            fail.push('Tasks：勾选未写入 tasks_' + child0 + '（KidTasksBridge 未生效或 saveTasks 未落盘）');
          var taskEl = doc.getElementById(child0 + '-tasks');
          var taskHtml = taskEl ? taskEl.innerHTML : '';
          if (taskHtml.indexOf('twp-') === -1)
            pass.push('Tasks：twp- 键已被 Task List 过滤（不双显示）');
          else
            fail.push('Tasks：twp- 键泄漏进 Task List（过滤失效）');
          // R3：Diya 页「今天三件事」渲染护栏
          if (child0 === 'diya') {
            var t3 = doc.getElementById('diya-today-3');
            var t3n = t3 ? t3.querySelectorAll('.kid-task-card').length : 0;
            if (t3n >= 1) pass.push('R3：今天三件事已渲染（' + t3n + ' 张卡，含 server 同步标记=' + (t3.querySelector('[data-sync="server"]') ? '是' : '否') + '）');
            else fail.push('R3：#diya-today-3 未渲染 Today 卡（数据加载或钩子失效）');
            var tp = doc.getElementById('today-progress');
            if (tp && tp.classList.contains('kid-progress')) pass.push('R3：Today 进度条已渲染（scope=今天三件）');
            else fail.push('R3：#today-progress 未渲染 KidProgress');
          }
        } catch (e) { fail.push('Tasks：同步断言抛错 ' + e.message); }

        // R7：Test me 遮挡自测链路——需先 loadErrorBook 灌入 mock 错题（Error Book tab
        // 在 smoke 里未被打开过，池子为空），加载完成后再启动自测并断言
        if (child0 === 'diya' && typeof window.loadErrorBook === 'function' &&
            typeof window.startQuizMode === 'function') {
          window.loadErrorBook().then(function () {
            try {
              window.startQuizMode();
              var tm = doc.getElementById('test-mode-panel');
              if (tm && !tm.hidden && tm.innerHTML.indexOf('Test me') !== -1)
                pass.push('R7：Test me 自测面板已启动（题面渲染，mock 错题入池）');
              else { fail.push('R7：Test me 未启动（错题数据未加载或空池）'); return; }
              // 只检查题面框：答案在 hidden 的 #quiz-answer-reveal 里，innerHTML 全文匹配会误判
              var qBox = tm.querySelector('div[style*="pre-wrap"]');
              var leak = !!(qBox && qBox.textContent.indexOf('S1') !== -1);
              if (leak) fail.push('R7：题面直接泄漏了答案文本（遮挡失效）');
              else pass.push('R7：题面未泄漏答案文本（遮挡生效）');
              window.quizWrong();
              var crow = doc.getElementById('quiz-cause-row');
              var brow = doc.getElementById('quiz-btn-row');
              if (crow && !crow.hidden && brow && brow.style.display === 'none')
                pass.push('R7：「还是不会」后错因三选一出现且评价按钮收起（必选门槛生效）');
              else fail.push('R7：quizWrong 后错因行/按钮行状态不对');
              window.pickQuizCause('concept');
              window.endQuizMode();
              var tm2 = doc.getElementById('test-mode-panel');
              if (tm2 && tm2.hidden) pass.push('R7：退出自测后列表视图还原');
              else fail.push('R7：endQuizMode 未还原列表视图');
            } catch (e) { fail.push('R7：断言抛错 ' + e.message); }
            resolve({ pass, fail });
          });
          return;   // resolve 移入上方 then（loadErrorBook 微任务内完成，早于任何宏任务汇总）
        }
        resolve({ pass, fail });
      }, 150);
    }, 700);
  });
}

(async () => {
  console.log('\n===== 子女教育工作台 冒烟测试（双页回归）=====\n');
  let totalPass = 0, totalFail = 0;
  for (const page of PAGES) {
    const { pass, fail } = await runPage(page);
    totalPass += pass.length;
    totalFail += fail.length;
    console.log(`---- ${page.name} ----`);
    console.log(`通过 ${pass.length} 项：`);
    pass.forEach((p) => console.log('  ✅ ' + p));
    if (fail.length) {
      console.log(`失败 ${fail.length} 项：`);
      fail.forEach((f) => console.log('  ❌ ' + f));
    }
  }
  console.log('\n-----------------------------------');
  console.log(totalFail === 0 ? `RESULT: ALL PASS ✅ (${totalPass} 项)` : `RESULT: FAILED ❌ (${totalFail} 项失败)`);
  process.exit(totalFail === 0 ? 0 : 1);
})();
