/**
 * 子女教育工作台 — 前端冒烟回归测试（jsdom）
 *
 * 用途：无需浏览器即可真实加载 app.html、执行页面 JS、解析 CSS，
 *   - 验证页面无顶层 JS 错误
 *   - 验证关键函数存在
 *   - 【核心】扫描全页所有 onclick 处理器引用的函数，确认都已定义
 *     （这类"悬空处理器"正是"点击无反应"的根因）
 *   - 模拟点击关键按钮，验证弹窗确实打开且 CSS 可见
 *
 * 运行：node tools/smoke_test.cjs        （需在仓库根目录执行）
 * 退出码：0 = 全部通过；1 = 有失败
 */
const path = require('path');
const fs = require('fs');

const JSDOM_PATH = process.env.JSDOM_PATH ||
  'C:/Users/h00603061/.workbuddy/binaries/node/workspace/node_modules/jsdom';
const { JSDOM, VirtualConsole } = require(JSDOM_PATH);

const APP_HTML = path.resolve(__dirname, '..', 'app.html');

const REQUIRED_FNS = [
  'openLLMSettings', 'llmSaveSettings', 'llmTestSettings', 'llmToggleKey',
  'sendChatMsg', 'loadDashboard', 'loadResources', 'toast',
  'renderTasksEnhanced', 'markdownToHtml', 'switchTab',
];

const MODAL_CASES = [
  { name: 'LLM 设置弹窗', btnId: 'llm-settings-btn', modalId: 'llm-settings-modal' },
  { name: '头像选择弹窗', btnId: 'profile-avatar', modalId: 'avatar-modal' },
];

// JS 关键字 / 内置对象，出现在 onclick 里但不是"待校验的全局函数"
const SKIP = new Set([
  'if','for','while','switch','catch','function','return','typeof','new','delete',
  'void','await','async','do','else','case','in','of','this','var','let','const',
  'alert','confirm','prompt','open','close',
  'Number','String','Boolean','Array','Object','JSON','Math','Date','parseInt','parseFloat','isNaN',
]);

const html = fs.readFileSync(APP_HTML, 'utf-8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push('[jsdomError] ' + (e.detail?.stack || e.message)));
vc.on('error', (...a) => errors.push('[console.error] ' + a.join(' ')));

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost:5000/app.html',
  virtualConsole: vc,
});
const { window } = dom;
window.fetch = () => Promise.resolve({
  ok: true,
  json: () => Promise.resolve({
    endpoint: 'https://api.openai.com/v1', model: 'gpt-4o',
    temperature: 0.3, has_key: true, key_masked: 'sk-a…z',
  }),
});

const pass = [];
const fail = [];

setTimeout(() => {
  const doc = window.document;

  // 1) 顶层 JS 错误
  if (errors.length === 0) pass.push('页面 JS 无顶层错误');
  else errors.forEach(e => fail.push(e));

  // 2) 关键函数存在
  REQUIRED_FNS.forEach(fn => {
    if (typeof window[fn] === 'function') pass.push(`函数存在 ${fn}`);
    else fail.push(`函数缺失 ${fn}`);
  });

  // 3) 【核心】全页 onclick 处理器引用的函数必须都已定义
  // 注意：直接扫描「实时 DOM 中已渲染的 onclick 属性」，而非源码。
  // 因为 renderTasksEnhanced 等用模板字符串生成 onclick（含 _esc 插值），
  // 真正下发的 DOM 属性已被插值解析，不会保留 _esc 等调用，扫描源码会误报。
  const clickEls = [...doc.querySelectorAll('[onclick]')];
  const refs = new Set();
  clickEls.forEach(el => {
    const attr = el.getAttribute('onclick') || '';
    [...attr.matchAll(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g)].forEach(m => refs.add(m[1]));
  });
  let dangling = 0;
  [...refs].sort().forEach(n => {
    if (SKIP.has(n)) return;
    if (typeof window[n] === 'function') pass.push(`onclick->${n} 已定义`);
    else { dangling++; fail.push(`onclick->${n} 悬空未定义（点击将无反应）`); }
  });
  if (dangling === 0) pass.push(`全页 ${refs.size} 个 onclick 处理器引用函数全部已定义`);

  // 4) 点击 -> 弹窗打开（含 CSS 可见性）
  MODAL_CASES.forEach(c => {
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

  // 输出
  console.log('\n===== 子女教育工作台 冒烟测试 =====\n');
  console.log(`通过 ${pass.length} 项：`);
  pass.forEach(p => console.log('  ✅ ' + p));
  if (fail.length) {
    console.log(`\n失败 ${fail.length} 项：`);
    fail.forEach(f => console.log('  ❌ ' + f));
  }
  console.log('\n-----------------------------------');
  console.log(fail.length === 0 ? 'RESULT: ALL PASS ✅' : 'RESULT: FAILED ❌');
  process.exit(fail.length === 0 ? 0 : 1);
}, 400);
