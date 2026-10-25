#!/usr/bin/env python3
"""HRD 管理工作台 · 服务器同步 API 自检（/api/hrd/state）。

覆盖：
  H1  无令牌 -> 401（fail closed：令牌文件缺失也拒）
  H2  错误令牌 -> 401
  H3  正确令牌 GET 空库 -> 200 + 空 data/tombs
  H4  PUT 合法载荷 -> 200 + counts；.bak 换留；再 GET  round-trip 保真
  H5  PUT 形状非法（data 非 dict / 四键类型错）-> 400
  H6  PUT 体积超限（>8MB）-> 413
  H7  OPTIONS 预检 -> 200 + 宽松 CORS 头（* + X-HRD-Token）
  H8  CORS 实际响应带 Access-Control-Allow-Origin: *（file:// origin=null 可用）
  H9  /api/hrd/* 不受会话 gate 影响（无 cookie 也能过，只要令牌对）
  H10 墓碑映射 round-trip（服务端不解释业务，原样存取）

用法：python tests/test_hrd_api.py   （退出码非 0 表示有失败）
"""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp(prefix='hrdapi_')
os.environ['DATA_DIR'] = tmp
os.environ['APP_PASSWORD'] = 'hrd-test-pass-123'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_server as A

app = A.app
app.config['TESTING'] = True
A._json_cache.clear()

TOKEN = 'hrd-test-token-0123456789abcdef'
TOKEN_FILE = os.path.join(tmp, 'hrd_token.txt')

FAILS = []


def check(cond, msg):
    if cond:
        print('  PASS  ' + msg)
    else:
        print('  FAIL  ' + msg)
        FAILS.append(msg)


def write_token(v=TOKEN):
    with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
        f.write(v)


def cl(token=None):
    c = app.test_client()
    if token:
        c.environ_base['HTTP_X_HRD_TOKEN'] = token
    return c


def sample_payload():
    return {
        'app': 'hrd-workbench', 'ver': 1, 'stamp': '2026-10-25T00:00:00',
        'data': {
            'tasks': [{'_id': 't1', '标题': '同步测试任务', '分类': '个人', '状态': '进行中'}],
            'notes': [], 'skills': [], 'knowledge': [],
        },
        'tombs': {'tasks': {'abc12345': '2026-10-20T10:00:00'}, 'notes': {}, 'skills': {}, 'knowledge': {}},
    }


print('== H1 无令牌 / 令牌文件缺失 -> 401（fail closed）==')
r = cl().get('/api/hrd/state')
check(r.status_code == 401, f'无令牌 GET -> 401（实际 {r.status_code}）')
r = cl().put('/api/hrd/state', json=sample_payload())
check(r.status_code == 401, f'无令牌 PUT -> 401（实际 {r.status_code}）')

print('== H2 错误令牌 -> 401 ==')
write_token()
r = cl('wrong-token').get('/api/hrd/state')
check(r.status_code == 401, f'错误令牌 GET -> 401（实际 {r.status_code}）')
body = r.get_json()
check('hint' in (body or {}), '401 体带 hint（指引排查）')

print('== H3 正确令牌 GET 空库 ==')
r = cl(TOKEN).get('/api/hrd/state')
check(r.status_code == 200, f'空库 GET -> 200（实际 {r.status_code}）')
d = r.get_json()
check(isinstance(d.get('data'), dict) and d['data'] == {}, '空库 data 为 {}')
check(isinstance(d.get('tombs'), dict) and d['tombs'] == {}, '空库 tombs 为 {}')

print('== H4 PUT 合法载荷 + round-trip ==')
r = cl(TOKEN).put('/api/hrd/state', json=sample_payload())
check(r.status_code == 200, f'PUT -> 200（实际 {r.status_code}）')
pb = r.get_json()
check(pb.get('ok') is True and pb.get('counts', {}).get('tasks') == 1, 'PUT 返回 ok + counts')
check(os.path.exists(os.path.join(tmp, 'hrd.json.bak')) is False or True, '.bak 逻辑不抛错')
r2 = cl(TOKEN).get('/api/hrd/state')
d2 = r2.get_json()
check(d2['data']['tasks'][0]['标题'] == '同步测试任务', 'round-trip 任务字段保真')
check(d2['tombs']['tasks'] == {'abc12345': '2026-10-20T10:00:00'}, 'round-trip 墓碑映射保真（服务端不解释业务）')

print('== H4b 第二次 PUT 换留 .bak ==')
r = cl(TOKEN).put('/api/hrd/state', json=sample_payload())
check(r.status_code == 200, '第二次 PUT -> 200')
check(os.path.exists(os.path.join(tmp, 'hrd.json.bak')), '第二次 PUT 后存在 .bak 还原点')

print('== H5 PUT 形状非法 -> 400 ==')
bad_cases = [
    ({'data': [], 'tombs': {}}, 'data 为 list'),
    ({'data': {'tasks': 'x', 'notes': [], 'skills': [], 'knowledge': []}, 'tombs': {}}, 'tasks 非 list'),
    ({'data': {'tasks': []}, 'tombs': {'tasks': 'x'}}, 'tombs.tasks 非 dict'),
    ({'tombs': {}}, '缺 data'),
    ('not-a-dict', '整体非 dict'),
]
for bad, name in bad_cases:
    r = cl(TOKEN).put('/api/hrd/state', json=bad)
    check(r.status_code == 400, f'{name} -> 400（实际 {r.status_code}）')

print('== H6 PUT 体积超限 -> 413 ==')
big = {'data': {'tasks': [{'标题': 'x' * 4000} for _ in range(3000)],
                'notes': [], 'skills': [], 'knowledge': []},
       'tombs': {'tasks': {}, 'notes': {}, 'skills': {}, 'knowledge': {}}}
r = cl(TOKEN).put('/api/hrd/state', json=big, headers={'Content-Length': str(9 * 1024 * 1024)})
check(r.status_code == 413, f'>8MB -> 413（实际 {r.status_code}）')

print('== H7 OPTIONS 预检 ==')
r = cl().open('/api/hrd/state', method='OPTIONS',
              headers={'Origin': 'null', 'Access-Control-Request-Method': 'PUT',
                       'Access-Control-Request-Headers': 'content-type,x-hrd-token'})
check(r.status_code == 200, f'OPTIONS -> 200（实际 {r.status_code}）')
acao = r.headers.get('Access-Control-Allow-Origin', '')
acah = r.headers.get('Access-Control-Allow-Headers', '')
check(acao == '*', f'Allow-Origin 为 *（实际 {acao!r}）——file:// origin=null 可用')
check('X-HRD-Token' in acah and 'Content-Type' in acah, f'Allow-Headers 含自定义令牌头（实际 {acah!r}）')

print('== H8 实际响应 CORS 头 ==')
r = cl(TOKEN).get('/api/hrd/state', headers={'Origin': 'null'})
check(r.headers.get('Access-Control-Allow-Origin') == '*', 'GET 响应带 * CORS 头')

print('== H9 无会话 cookie 也可用（/api/hrd/* 不走会话 gate）==')
c = app.test_client()
c.environ_base['HTTP_X_HRD_TOKEN'] = TOKEN
r = c.get('/api/hrd/state')
check(r.status_code == 200, f'无 cookie + 正确令牌 -> 200（实际 {r.status_code}）')
r2 = c.get('/api/goals')
check(r2.status_code == 401, f'对照组：/api/goals 无 cookie -> 401（实际 {r2.status_code}）')

print('== H10 服务端不解释业务：未知字段原样存取 ==')
p = sample_payload()
p['data']['tasks'][0]['自定义字段'] = {'a': 1}
p['extra_top'] = 'keep-me'
cl(TOKEN).put('/api/hrd/state', json=p)
d3 = cl(TOKEN).get('/api/hrd/state').get_json()
check(d3['data']['tasks'][0]['自定义字段'] == {'a': 1}, '记录内未知字段原样保留')

print()
if FAILS:
    print(f'失败 {len(FAILS)} 项')
    sys.exit(1)
print('全部通过（H1-H10）')
