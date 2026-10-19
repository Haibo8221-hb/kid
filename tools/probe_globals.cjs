/* 探针：jsdom 加载 app.html，检查各全局对象是否就位（诊断 smoke 中 KidTasksBridge 缺失问题） */
const path = require('path');
const fs = require('fs');
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || 'C:/Users/h00603061/.workbuddy/binaries/node/workspace/node_modules/jsdom');

let html = fs.readFileSync(path.resolve(__dirname, '..', 'app.html'), 'utf8');
html = html.replace(/<script src="(assets\/[^"]+)"><\/script>/g, (m, src) => {
  const p = path.resolve(__dirname, '..', src.split('?')[0]);
  try {
    return '<script>' + fs.readFileSync(p, 'utf8') + '</' + 'script>';
  } catch (e) {
    console.log('INLINE MISS:', src);
    return m;
  }
});
const vc = new VirtualConsole();
vc.on('jsdomError', e => console.log('JSDOM ERR:', e.message));
vc.on('error', (...a) => console.log('CONSOLE ERR:', a.join(' ')));
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost:5000/app.html', virtualConsole: vc });
dom.window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
setTimeout(() => {
  const w = dom.window;
  console.log('KidStreak:', typeof w.KidStreak, '| KidTasksBridge:', typeof w.KidTasksBridge,
    '| loadTasks:', typeof w.loadTasks, '| saveTasks:', typeof w.saveTasks,
    '| KidCheck:', typeof w.KidCheck);
  console.log('KID_CHILD:', JSON.stringify(w.KID_CHILD), '| tasksData:', typeof w.tasksData);
  const cards = w.document.querySelectorAll('.kid-task-card');
  console.log('kid-task-card 数量:', cards.length);
  const twp = w.document.querySelector('.kid-task-card[data-sync="server"]');
  console.log('server 卡存在:', !!twp);
  process.exit(0);
}, 800);
