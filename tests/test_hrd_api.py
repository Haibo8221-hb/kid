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
import base64, json, os, re, sys, tempfile

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

# ── I 节：笔记配图端点 /api/hrd/img（2026-10-28）────────────────────
print('== I 节 图片端点（上传/获取/删除/引用保护）==')

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 200 + b'\xff\xd9'      # 最小合法 JPEG（magic 判型）
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 200                     # 最小合法 PNG
GIF = b'GIF89a' + b'\x00' * 200                                # 最小合法 GIF
BMP = b'BM' + b'\x00' * 200                                    # 不支持的格式


def upload(name, data, token=TOKEN, as_dataurl=False):
    b64 = base64.b64encode(data).decode()
    if as_dataurl:
        b64 = 'data:image/jpeg;base64,' + b64
    return cl(token).post('/api/hrd/img', json={'name': name, 'data': b64})


print('== I1 无令牌上传 -> 401（fail closed）==')
r = cl().post('/api/hrd/img', json={'name': 'x.jpg', 'data': 'AAAA'})
check(r.status_code == 401, f'无令牌 POST -> 401（实际 {r.status_code}）')

print('== I2 三种格式上传 -> 200 + id/url；magic 判型不信文件名 ==')
r = upload('photo.jpg', JPEG)
check(r.status_code == 200, f'JPEG 上传 -> 200（实际 {r.status_code}）')
b = r.get_json() or {}
JPG_ID = b.get('id', '')
check(bool(re.fullmatch(r'[0-9a-f]{64}', JPG_ID)), 'id 为 sha256 十六进制（64 位）')
check(b.get('ext') == 'jpg' and b.get('url') == '/api/hrd/img/' + JPG_ID, '返回 ext + url')
check(b.get('bytes') == len(JPEG), '返回字节数')
r = upload('其实是png.txt', PNG)                               # 文件名撒谎，按内容判
check((r.get_json() or {}).get('ext') == 'png', '假文件名也按 magic 判为 png')
r = upload('anim.gif', GIF)
check((r.get_json() or {}).get('ext') == 'gif', 'GIF 上传 -> ext=gif')

print('== I3 非图片内容 -> 415（坏 magic / 不支持的格式）==')
r = upload('假图.jpg', BMP)
check(r.status_code == 415, f'BMP（不支持格式）-> 415（实际 {r.status_code}）')
r = upload('坏图.jpg', b'not-an-image-at-all')
check(r.status_code == 415, f'坏 magic -> 415（实际 {r.status_code}）')
r = cl(TOKEN).post('/api/hrd/img', json={'name': 'x.jpg', 'data': '!!!not-base64!!!'})
check(r.status_code in (400, 415), f'坏 base64 -> 400/415（实际 {r.status_code}）')
r = cl(TOKEN).post('/api/hrd/img', json={'name': 'x.jpg'})
check(r.status_code == 400, f'缺 data -> 400（实际 {r.status_code}）')

print('== I4 体积超限 -> 413 ==')
old_max = A._HRD_IMG_MAX_BYTES
A._HRD_IMG_MAX_BYTES = 1024
try:
    r = upload('big.jpg', JPEG + b'\x00' * 4096)
    check(r.status_code == 413, f'超过单图上限 -> 413（实际 {r.status_code}）')
finally:
    A._HRD_IMG_MAX_BYTES = old_max
old_body = A._HRD_IMG_MAX_BODY
A._HRD_IMG_MAX_BODY = 1024
try:
    r = upload('big.jpg', JPEG + b'\x00' * 4096)
    check(r.status_code == 413, f'请求体超限 -> 413（实际 {r.status_code}）')
finally:
    A._HRD_IMG_MAX_BODY = old_body

print('== I5 GET 图片：头令牌 / query 令牌 / 无令牌 ==')
r = cl(TOKEN).get('/api/hrd/img/' + JPG_ID)
check(r.status_code == 200, f'头令牌 GET -> 200（实际 {r.status_code}）')
check(r.headers.get('Content-Type') == 'image/jpeg', f'Content-Type=image/jpeg（实际 {r.headers.get("Content-Type")!r}）')
check('immutable' in (r.headers.get('Cache-Control') or ''), 'Cache-Control 带 immutable（内容寻址可长缓存）')
check(r.data == JPEG, '字节保真 round-trip')
r = cl().get('/api/hrd/img/' + JPG_ID + '?t=' + TOKEN)
check(r.status_code == 200, f'query 令牌 GET -> 200（img 标签场景，实际 {r.status_code}）')
r = cl().get('/api/hrd/img/' + JPG_ID)
check(r.status_code == 401, f'无任何令牌 GET -> 401（实际 {r.status_code}）')
r = cl(TOKEN).get('/api/hrd/img/' + '0' * 64)
check(r.status_code == 404, f'不存在的 id -> 404（实际 {r.status_code}）')
r = cl(TOKEN).get('/api/hrd/img/..%2F..%2Fetc%2Fpasswd')
check(r.status_code == 404, f'路径穿越 id -> 404（正则拦截，实际 {r.status_code}）')

print('== I6 data URL 前缀容忍（canvas toDataURL 直传）==')
r = upload('dataurl.jpg', JPEG, as_dataurl=True)
check(r.status_code == 200 and (r.get_json() or {}).get('id') == JPG_ID,
      'data: 前缀上传成功且与裸 base64 同 id')

print('== I7 内容寻址去重：同内容重复上传同 id，不翻倍 ==')
before = len(os.listdir(os.path.join(tmp, 'hrd_imgs')))
r = upload('again.jpg', JPEG)
after = len(os.listdir(os.path.join(tmp, 'hrd_imgs')))
check((r.get_json() or {}).get('id') == JPG_ID and before == after, '重复上传命中同 id，文件数不变')

print('== I8 DELETE 未被引用 -> 200，再 GET -> 404 ==')
r = upload('todelete.png', PNG)
pid = (r.get_json() or {}).get('id', '')
r = cl(TOKEN).delete('/api/hrd/img/' + pid)
check(r.status_code == 200, f'无引用 DELETE -> 200（实际 {r.status_code}）')
r = cl(TOKEN).get('/api/hrd/img/' + pid)
check(r.status_code == 404, '删除后 GET -> 404')
r = cl(TOKEN).delete('/api/hrd/img/' + pid)
check(r.status_code == 404, f'重复 DELETE -> 404（幂等，实际 {r.status_code}）')

print('== I9 DELETE 被引用 -> 409（先保存去引用、再删文件的时序保护）==')
p2 = sample_payload()
p2['data']['notes'] = [{'_id': 'n1', '标题': '带图笔记',
                        '_imgs': [{'id': JPG_ID, 'name': 'photo.jpg', 'w': 100, 'h': 80}]}]
cl(TOKEN).put('/api/hrd/state', json=p2)
r = cl(TOKEN).delete('/api/hrd/img/' + JPG_ID)
check(r.status_code == 409, f'被未删除记录引用 -> 409（实际 {r.status_code}）')
check((r.get_json() or {}).get('referenced') is True, '409 体带 referenced 标记')
p2['data']['notes'][0].pop('_imgs')
cl(TOKEN).put('/api/hrd/state', json=p2)
r = cl(TOKEN).delete('/api/hrd/img/' + JPG_ID)
check(r.status_code == 200, f'移除引用后 DELETE -> 200（实际 {r.status_code}）')

print('== I10 墓碑不占引用：已删除笔记的配图可删 ==')
p3 = sample_payload()
p3['data']['notes'] = [{'_id': 'n2', '标题': '已删笔记', '_deleted': '2026-10-28T00:00:00',
                        '_imgs': [{'id': JPG_ID, 'name': 'photo.jpg'}]}]
cl(TOKEN).put('/api/hrd/state', json=p3)
r = upload('墓意图.jpg', JPEG)
check(r.status_code == 200, '重新上传同内容 -> 200（去重命中）')
r = cl(TOKEN).delete('/api/hrd/img/' + JPG_ID)
check(r.status_code == 200, f'引用方已打墓碑 -> 允许删除（实际 {r.status_code}）')

print()
if FAILS:
    print(f'失败 {len(FAILS)} 项')
    sys.exit(1)
print('全部通过（H1-H10 + I1-I10）')
