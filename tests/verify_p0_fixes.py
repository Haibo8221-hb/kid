#!/usr/bin/env python3
"""P0 修复自检：#1 错题增删接口、#2 child 白名单、#3 密码/Cookie、#4 登出吊销。"""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp(prefix='kidverify_')
os.environ['DATA_DIR'] = tmp
os.environ['APP_PASSWORD'] = 'verify-pass-123'
os.environ['COOKIE_SECURE'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_server as A

app = A.app
app.config['TESTING'] = True
FAILS = []

def check(cond, msg):
    if cond:
        print('  PASS  ' + msg)
    else:
        print('  FAIL  ' + msg)
        FAILS.append(msg)

class Authed:
    """带会话 cookie 的客户端（Flask 3 需用 set_cookie 注入）"""
    def __init__(self, tok):
        self.cl = app.test_client()
        self.cl.set_cookie('kid_auth', tok, domain='localhost')
    def get(self, u, **kw):    return self.cl.get(u, **kw)
    def post(self, u, **kw):   return self.cl.post(u, **kw)
    def delete(self, u, **kw): return self.cl.delete(u, **kw)
    def put(self, u, **kw):    return self.cl.put(u, **kw)

c = app.test_client()

print('== #3 密码 / Cookie ==')
r = c.post('/api/auth/login', json={'password': 'wrong'})
check(r.status_code == 401, f'错误密码 401（实际 {r.status_code}）')
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'})
check(r.status_code == 200, f'正确密码 200（实际 {r.status_code}）')
sc = r.headers.get('Set-Cookie', '')
print('  Set-Cookie:', sc)
check('Secure' in sc, 'Cookie 带 Secure')
check('HttpOnly' in sc, 'Cookie 带 HttpOnly')
check('SameSite=Lax' in sc, 'Cookie 带 SameSite=Lax')
check(A.PASSWORD_CONFIGURED, 'APP_PASSWORD 已配置（非内置默认）')

print('== #4 登出吊销 ==')
tok = A._make_token()
cc = Authed(tok)
r = cc.get('/api/auth/status')
check(r.status_code == 200, f'登出前会话有效（实际 {r.status_code}）')
r = cc.post('/api/auth/logout')
check(r.status_code == 200, f'登出接口 200（实际 {r.status_code}）')
r = Authed(tok).get('/api/auth/status')
check(r.status_code == 401, f'登出后旧 token 401（实际 {r.status_code}）')
r = Authed(A._make_token()).get('/api/auth/status')
check(r.status_code == 200, f'登出后新登录 token 有效（实际 {r.status_code}）')

print('== #2 child/user 白名单（路径穿越）==')
cc = Authed(A._make_token())
for ep, payload in [('/api/planner', {'user': '../../evil', 'plan': {'a': 1}}),
                    ('/api/tasks', {'user': '../../evil', 'tasks': {}}),
                    ('/api/review', {'user': '../../evil', 'x': 1}),
                    ('/api/chat/stream', {'user': '../../evil', 'message': 'x'}),
                    ('/api/errors/diya', {'child': '../../evil', 'question': 'Q'})]:
    r = cc.post(ep, json=payload)
    writes = [p for p in os.listdir(tmp) if 'evil' in p]
    # 安全断言的核心：绝不能在 data/ 之外（或以其名）落盘。
    # 状态码不纳入断言：chat/stream 在无 LLM 配置时按设计返回 503，不代表白名单失效。
    check(not writes, f'{ep} 未写出穿越文件（返回 {r.status_code}，落盘 {writes}）')

print('== #1 错题 POST / DELETE ==')
import shutil
shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)   # 清掉前面用例写入的样例数据
cc = Authed(A._make_token())
r = cc.post('/api/errors/diya', json={'subject': 'Maths', 'question': 'Q1', 'solution': 'S1', 'type': 'algebra', 'tags': 't1'})
print('  POST ->', r.status_code, r.get_json())
check(r.status_code == 200, 'POST /api/errors/<child> 200（原 405）')
eid = (r.get_json() or {}).get('id')
check(bool(eid), '返回记录 id')
r = cc.get('/api/errors/diya')
n = len(r.get_json().get('errors', []))
check(n == 1, f'GET 可见新记录（实际 {n}）')
r = cc.delete(f'/api/errors/diya/{eid}')
print('  DELETE ->', r.status_code, r.get_json())
check(r.status_code == 200, 'DELETE /api/errors/<child>/<id> 200（原 405）')
r = cc.get('/api/errors/diya')
check(len(r.get_json().get('errors', [])) == 0, 'DELETE 后记录数归零')
r = cc.delete('/api/errors/diya/not-exist')
check(r.status_code == 404, f'删除不存在记录 404（实际 {r.status_code}）')
r = cc.post('/api/errors/diya', json={'subject': 'Maths', 'question': ''})
check(r.status_code == 400, f'空题目 400（实际 {r.status_code}）')

print('== 存量无 id 自动补齐 ==')
p = os.path.join(tmp, 'errors_diya.json')
open(p, 'w').write(json.dumps([{'subject': 'Phy', 'question': 'legacy', 'solution': '', 'type': 'x'}]))
A._json_cache.pop(p, None)
cc = Authed(A._make_token())
recs = cc.get('/api/errors/diya').get_json().get('errors', [])
check(bool(recs and recs[0].get('id')), '存量记录已自动补 id')
if recs:
    r = cc.delete(f"/api/errors/diya/{recs[0]['id']}")
    check(r.status_code == 200, f'存量记录可删除（实际 {r.status_code}）')


print('== #11 Notes 服务端持久化 ==')
_cc = Authed(A._make_token())
r = _cc.get('/api/notes/diya')
check(r.status_code == 200, f'GET notes 200（实际 {r.status_code}）')
check(r.get_json().get('payload') == {}, 'GET notes 初始 payload 为空对象')
r = _cc.put('/api/notes/diya', json={'payload': {'Maths': [{'id': 'n1', 'content': 'x'}], 'text': 'jemma-style'}, 'updatedAt': '2026-10-15T16:00:00'})
check(r.status_code == 200 and r.get_json().get('ok'), f'PUT notes 成功（实际 {r.status_code}）')
r = _cc.get('/api/notes/diya')
d = r.get_json()
check(d.get('updatedAt') == '2026-10-15T16:00:00', 'PUT 后 updatedAt 回读一致')
check(d['payload'].get('Maths') and d['payload']['Maths'][0]['id'] == 'n1', 'PUT 后 payload 回读一致（自由形态，不规定结构）')
r = _cc.put('/api/notes/diya', json={'payload': 'not-an-object'})
check(r.status_code == 400, f'payload 非对象应 400（实际 {r.status_code}）')
r = _cc.get('/api/notes/../../evil')
check(r.status_code != 200 or 'evil' not in str(r.get_json()), 'notes 接口受 _safe_child 白名单保护')
_np = os.path.join(tmp, 'notes_diya.json')
check(os.path.exists(_np), 'notes 落盘到 data/notes_<child>.json')
# 未登录不得读写（用全新客户端，避免复用前面用例的会话 cookie）
_fresh = app.test_client()
r = _fresh.get('/api/notes/diya')
check(r.status_code == 401, f'未登录 GET notes 应 401（实际 {r.status_code}）')
r = _fresh.put('/api/notes/diya', json={'payload': {}})
check(r.status_code == 401, f'未登录 PUT notes 应 401（实际 {r.status_code}）')

print('== #12 错题分页 ==')
_cc = Authed(A._make_token())
# 造 120 条样本，覆盖 113 条的真实规模
import shutil as _sh
_sh.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)
for i in range(120):
    _cc.post('/api/errors/diya', json={'question': f'Q{i}', 'subject': ['Maths','Physics'][i % 2],
                                       'priority': ['high','medium','low'][i % 3],
                                       'date': f'2026-01-{(i % 28) + 1:02d}'})
d = _cc.get('/api/errors/diya?page=1&limit=50').get_json()
check(d['total'] == 120, f'分页 total=120（实际 {d["total"]}）')
check(len(d['errors']) == 50, f'第1页 50 条（实际 {len(d["errors"])}）')
check(d['has_more'] is True, '第1页 has_more=True')
d2 = _cc.get('/api/errors/diya?page=3&limit=50').get_json()
check(len(d2['errors']) == 20, f'第3页 20 条（实际 {len(d2["errors"])}）')
check(d2['has_more'] is False, '末页 has_more=False')
# 分页切片顺序必须与不分页一致（服务端已统一排序）
allq = [e['question'] for e in _cc.get('/api/errors/diya').get_json()['errors']]
paged = []
for pg in (1, 2, 3):
    paged += [e['question'] for e in _cc.get(f'/api/errors/diya?page={pg}&limit=50').get_json()['errors']]
check(allq == paged, '分页拼接顺序与全量顺序一致（排序口径未漂移）')
# 科目筛选
d3 = _cc.get('/api/errors/diya?page=1&limit=50&subject=Maths').get_json()
check(d3['total'] == 60, f'subject=Maths 过滤后 60 条（实际 {d3["total"]}）')
check(all(e['subject'] == 'Maths' for e in d3['errors']), '筛选结果全部命中科目')
# 不传 page/limit 时保持旧行为（返回全部）
d4 = _cc.get('/api/errors/diya').get_json()
check(len(d4['errors']) == 120, f'不分页时返回全部 120 条（实际 {len(d4["errors"])}）')
# stats 增强字段
st = _cc.get('/api/errors/diya?stats=1').get_json()
check(st['total'] == 120 and 'mastered_total' in st and 'new_this_week' in st,
      'stats=1 提供 mastered_total / new_this_week（供分页前端计数）')
# 首屏传输量
import json as _json
first = len(_json.dumps(_cc.get('/api/errors/diya?page=1&limit=50').get_json(), ensure_ascii=False).encode())
full = len(_json.dumps(_cc.get('/api/errors/diya').get_json(), ensure_ascii=False).encode())
check(first < full * 0.6, f'首屏传输量显著下降：{first//1024}KB vs 全量 {full//1024}KB')

print('== 登录限流（新增-D）==')
_ip = '203.0.113.7'
for i in range(5):
    c.post('/api/auth/login', json={'password': 'definitely-wrong'}, environ_base={'REMOTE_ADDR': _ip})
r = c.post('/api/auth/login', json={'password': 'definitely-wrong'}, environ_base={'REMOTE_ADDR': _ip})
check(r.status_code == 429, f'连续失败达阈值后锁定（实际 {r.status_code}）')
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'}, environ_base={'REMOTE_ADDR': _ip})
check(r.status_code == 429, f'锁定期内正确密码也被拒（实际 {r.status_code}）')
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'}, environ_base={'REMOTE_ADDR': '198.51.100.9'})
check(r.status_code == 200, f'未失败来源不受影响（实际 {r.status_code}）')
A._login_rate_clear(_ip)
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'}, environ_base={'REMOTE_ADDR': _ip})
check(r.status_code == 200, f'清除计数后可正常登录（实际 {r.status_code}）')

print('\n结果:', 'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED -> {FAILS}')
sys.exit(1 if FAILS else 0)
