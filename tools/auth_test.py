# -*- coding: utf-8 -*-
"""应用层鉴权冒烟测试：登录/拦截/续期/失效全链路"""
import os, shutil, glob, sys, time

os.environ['DATA_DIR'] = '/tmp/authtest'
os.environ['APP_PASSWORD'] = 'test-pwd-123'
os.makedirs('/tmp/authtest', exist_ok=True)
for f in glob.glob('data/*.json'):
    shutil.copy(f, '/tmp/authtest/')

sys.path.insert(0, '.')
import api_server as A

c = A.app.test_client()
oks, fails = [], []
def chk(name, cond, extra=''):
    (oks if cond else fails).append((name, extra))

# 1) 未登录访问受保护 API -> 401
r = c.get('/api/dashboard')
chk('未登录 GET /api/dashboard 被拦截', r.status_code == 401, f'status={r.status_code}')

# 2) 登录错误密码 -> 401
r = c.post('/api/auth/login', json={'password': 'wrong'})
chk('错误密码被拒', r.status_code == 401, f'status={r.status_code}')

# 3) 登录正确密码 -> 200 + Set-Cookie
r = c.post('/api/auth/login', json={'password': 'test-pwd-123'})
setc = r.headers.get('Set-Cookie', '')
chk('正确密码登录成功', r.status_code == 200 and (r.get_json() or {}).get('ok') is True, f'status={r.status_code}')
chk('签发 httpOnly cookie', 'HttpOnly' in setc and 'kid_auth=' in setc, setc[:80])

# 4) 带 cookie 访问 -> 200
r = c.get('/api/dashboard')
chk('登录后访问受保护 API 通过', r.status_code == 200, f'status={r.status_code}')
r = c.get('/api/settings/llm')
chk('登录后访问 LLM 设置接口通过', r.status_code == 200, f'status={r.status_code}')

# 5) Bearer 头兼容
tok = setc.split('kid_auth=')[1].split(';')[0]
r = c.get('/api/dashboard', headers={'Authorization': f'Bearer {tok}'})
c2 = A.app.test_client()  # 干净客户端（无 cookie）
r = c2.get('/api/dashboard', headers={'Authorization': f'Bearer {tok}'})
chk('Bearer token 方式可用', r.status_code == 200, f'status={r.status_code}')

# 6) 篡改签名 -> 401
bad = tok.split('.')[0] + '.deadbeef' * 4
bad = bad[:len(tok)]
r = c2.get('/api/dashboard', headers={'Authorization': f'Bearer {bad}'})
chk('篡改签名被拒', r.status_code == 401, f'status={r.status_code}')

# 7) 过期 token -> 401
old = A._make_token(now=time.time() - A.AUTH_TTL - 10)
r = c2.get('/api/dashboard', headers={'Authorization': f'Bearer {old}'})
chk('过期 token 被拒', r.status_code == 401, f'status={r.status_code}')

# 8) OPTIONS 预检放行（未登录也可）
r = c2.options('/api/dashboard')
chk('OPTIONS 预检不受拦截', r.status_code in (200, 204), f'status={r.status_code}')

# 9) auth 接口本身不被拦截
r = c2.get('/api/auth/status')
chk('未登录 status 返回 401（而非被 gate 再包一层）', r.status_code == 401, f'status={r.status_code}')

# 10) 滚动续期：构造快过期的 token，status 后应 Set-Cookie 新 token
import hmac as _h, hashlib as _sh
exp = int(time.time()) + 3600  # 1 小时后过期 < 15 天阈值
sig = _h.new(A._auth_secret(), str(exp).encode(), _sh.sha256).hexdigest()[:32]
soon = f'{exp}.{sig}'
c3 = A.app.test_client()
c3.set_cookie('kid_auth', soon, domain='localhost')
r = c3.get('/api/auth/status')
chk('临近过期会话滚动续期', r.status_code == 200 and 'kid_auth=' in r.headers.get('Set-Cookie', ''), '')

# 11) 非 /api 路径不受影响（静态页由 nginx 服务）
r = c2.get('/nonexistent-page')
chk('非 API 路径不拦截（404 正常返回）', r.status_code == 404, f'status={r.status_code}')

# 12) 登出清 cookie
r = c.post('/api/auth/logout')
chk('登出成功', r.status_code == 200 and 'kid_auth=;' in r.headers.get('Set-Cookie', '').replace('Max-Age=0', ''), '')

print('\n===== 鉴权冒烟测试 =====')
for n, e in oks: print('  ✅', n, e)
for n, e in fails: print('  ❌', n, e)
print(f'\n通过 {len(oks)} / 失败 {len(fails)}')
print('RESULT:', 'ALL PASS ✅' if not fails else 'FAILED ❌')
shutil.rmtree('/tmp/authtest', ignore_errors=True)
sys.exit(0 if not fails else 1)
