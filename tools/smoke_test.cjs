/**
 * 子女教育工作台 — 前端冒烟回归测试（jsdom，双页：app.html + jemma.html）
 *
 * 用途：无需浏览器即可真实加载页面、执行页面 JS、解析 CSS，
 *   - 验证页面无顶层 JS 错误
 *   - 验证关键函数存在
 *   - 【核心】扫描全页所有 onclick 处理器引用的函数，确认都已定义
 *     （这类"悬空处理器"正是"点击无反应"的根因）
 *   - 模拟点击关键按钮，验证弹窗确实打开且 CSS 可见
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
      'renderTasks', 'markdownToHtml', 'switchTab',
    ],
    modalCases: [
      { name: 'LLM 设置弹窗', btnId: 'llm-settings-btn', modalId: 'llm-settings-modal' },
      { name: '头像选择弹窗', btnId: 'profile-avatar', modalId: 'avatar-modal' },
    ],
    fetchMock: () => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        endpoint: 'https://api.openai.com/v1', model: 'gpt-4o',
        temperature: 0.3, has_key: true, key_masked: 'sk-a…z',
      }),
    }),
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

      resolve({ pass, fail });
    }, 400);
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
