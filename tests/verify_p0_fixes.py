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
